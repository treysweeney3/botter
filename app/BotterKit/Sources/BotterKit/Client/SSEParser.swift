import Foundation

/// One parsed server-sent event.
public struct SSEMessage: Hashable, Sendable {
    public var event: String?
    public var data: String
    public var id: String?

    public init(event: String? = nil, data: String, id: String? = nil) {
        self.event = event
        self.data = data
        self.id = id
    }
}

/// Incremental SSE parser (WHATWG framing). Feed it raw lines (without
/// terminators); it emits a message at each blank-line dispatch. Comment
/// lines (leading ":") are heartbeats and are dropped.
public struct SSEParser: Sendable {
    private var eventName: String?
    private var dataLines: [String] = []
    private var lastId: String?

    public init() {}

    public mutating func consume(line: String) -> SSEMessage? {
        if line.isEmpty {
            defer {
                eventName = nil
                dataLines = []
            }
            guard !dataLines.isEmpty else { return nil }
            return SSEMessage(event: eventName, data: dataLines.joined(separator: "\n"), id: lastId)
        }
        if line.hasPrefix(":") { return nil }

        let field: Substring
        let value: Substring
        if let colon = line.firstIndex(of: ":") {
            field = line[..<colon]
            var v = line[line.index(after: colon)...]
            if v.hasPrefix(" ") { v = v.dropFirst() }
            value = v
        } else {
            field = line[...]
            value = ""
        }
        switch field {
        case "event": eventName = String(value)
        case "data": dataLines.append(String(value))
        case "id": lastId = String(value)
        default: break  // "retry" and unknown fields are ignored
        }
        return nil
    }
}

extension AsyncSequence where Element == UInt8, Self: Sendable {
    /// Byte stream → parsed SSE messages. Handles LF and CRLF terminators.
    func sseMessages() -> AsyncThrowingStream<SSEMessage, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                var parser = SSEParser()
                var lineBuffer: [UInt8] = []
                do {
                    for try await byte in self {
                        if byte == 0x0A {  // \n
                            if lineBuffer.last == 0x0D { lineBuffer.removeLast() }
                            let line = String(decoding: lineBuffer, as: UTF8.self)
                            lineBuffer.removeAll(keepingCapacity: true)
                            if let message = parser.consume(line: line) {
                                continuation.yield(message)
                            }
                        } else {
                            lineBuffer.append(byte)
                        }
                    }
                    // Dispatch a final unterminated event, if any.
                    if !lineBuffer.isEmpty {
                        let line = String(decoding: lineBuffer, as: UTF8.self)
                        _ = parser.consume(line: line)
                    }
                    if let message = parser.consume(line: "") {
                        continuation.yield(message)
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}
