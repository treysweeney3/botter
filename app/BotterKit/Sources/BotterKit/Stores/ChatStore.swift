import Foundation
import Observation

/// One live step in an agent exchange (tool call, thinking phase).
public struct ToolStep: Hashable, Sendable, Identifiable {
    public var id: Int
    public var name: String
    public var status: String  // "started" | "ok" | "error"
    public var summary: String?

    public init(id: Int, name: String, status: String, summary: String? = nil) {
        self.id = id
        self.name = name
        self.status = status
        self.summary = summary
    }
}

/// What happened between sending a message and its completed reply.
public struct ExchangeTrace: Hashable, Sendable {
    public var duration: TimeInterval
    public var steps: [ToolStep]

    public init(duration: TimeInterval, steps: [ToolStep]) {
        self.duration = duration
        self.steps = steps
    }
}

/// One conversation: history, optimistic sends, and live stream application.
@MainActor
@Observable
public final class ChatStore {
    /// Live state of one exchange. Tool activity is deliberately absent: the
    /// bubble shows a single steady "Thinking" while the turn runs, and what
    /// the agent actually did is read from `currentSteps` afterwards.
    public enum Streaming: Equatable {
        case idle
        case active(text: String)
        case failed(String)
    }

    public private(set) var messages: [Message] = []
    public private(set) var streaming: Streaming = .idle
    public private(set) var isLoadingHistory = false
    /// When the in-flight exchange started (drives elapsed-time display).
    public private(set) var streamingSince: Date?
    /// Steps observed so far in the in-flight exchange.
    public private(set) var currentSteps: [ToolStep] = []
    /// Completed-message id → what the agent did to produce it.
    public private(set) var traces: [String: ExchangeTrace] = [:]
    public let sessionId: String
    public let botId: String

    private let client: BotterClient
    // Not observed; written only on the main actor, read in deinit for cleanup.
    @ObservationIgnored nonisolated(unsafe) private var streamTask: Task<Void, Never>?

    public init(client: BotterClient, sessionId: String, botId: String) {
        self.client = client
        self.sessionId = sessionId
        self.botId = botId
    }

    deinit {
        streamTask?.cancel()
    }

    public var isStreaming: Bool {
        if case .active = streaming { true } else { false }
    }

    public func loadHistory() async {
        isLoadingHistory = true
        defer { isLoadingHistory = false }
        do {
            messages = try await client.messages(sessionId: sessionId)
            syncReadMarker()
        } catch {
            streaming = .failed(error.localizedDescription)
        }
    }

    /// Persist the last-read marker server-side (fire and forget) so unread
    /// counts stay correct across restarts and, later, devices.
    private func syncReadMarker() {
        guard let last = messages.last, !last.id.hasPrefix("local-") else { return }
        Task { [client, sessionId] in
            try? await client.markRead(sessionId: sessionId, lastMessageId: last.id)
        }
    }

    public func send(_ text: String, images: [ImageAttachment] = []) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard (!trimmed.isEmpty || !images.isEmpty), !isStreaming else { return }

        messages.append(Message(
            id: "local-\(UUID().uuidString)",
            sessionId: sessionId,
            botId: botId,
            role: .user,
            kind: images.isEmpty ? .text : .attachment,
            text: trimmed.isEmpty ? nil : trimmed,
            attachments: images.isEmpty ? nil : images,
            createdAt: .now
        ))
        streaming = .active(text: "")
        streamingSince = .now
        currentSteps = []

        streamTask = Task { [weak self, client, sessionId] in
            do {
                for try await event in client.chatStream(sessionId: sessionId, text: trimmed, images: images) {
                    guard let self else { return }
                    self.apply(event)
                }
                guard let self else { return }
                // Stream ended without message_complete (e.g. server restart):
                // keep whatever streamed as a plain message so nothing is lost.
                if case .active(let text) = self.streaming {
                    if !text.isEmpty {
                        self.messages.append(Message(
                            id: "local-\(UUID().uuidString)",
                            sessionId: self.sessionId,
                            botId: self.botId,
                            role: .assistant,
                            text: text,
                            createdAt: .now
                        ))
                    }
                    self.streaming = .idle
                }
            } catch is CancellationError {
                self?.streaming = .idle
            } catch {
                self?.streaming = .failed(error.localizedDescription)
            }
        }
    }

    private func apply(_ event: ChatEvent) {
        switch event {
        case .delta(let text):
            if case .active(let current) = streaming {
                streaming = .active(text: current + text)
            }
        case .toolEvent(let name, let status, let summary):
            if status == "started" {
                currentSteps.append(ToolStep(id: currentSteps.count, name: name, status: status, summary: summary))
            } else if let index = currentSteps.lastIndex(where: { $0.name == name && $0.status == "started" }) {
                currentSteps[index].status = status
                if let summary { currentSteps[index].summary = summary }
            } else {
                currentSteps.append(ToolStep(id: currentSteps.count, name: name, status: status, summary: summary))
            }
            // Text streamed before a tool call is narration, not the reply. The
            // backend files it in the trace, so the live bubble drops it too and
            // one turn stays one bubble.
            if status == "started", case .active = streaming {
                streaming = .active(text: "")
            }
        case .approvalRequired(let runId, let summary):
            messages.append(Message(
                id: "approval-\(runId)",
                sessionId: sessionId,
                botId: botId,
                role: .assistant,
                kind: .approvalRequest,
                text: summary,
                createdAt: .now
            ))
        case .messageComplete(let message):
            if let start = streamingSince {
                traces[message.id] = ExchangeTrace(duration: Date.now.timeIntervalSince(start), steps: currentSteps)
            }
            messages.append(message)
            streaming = .idle
            streamingSince = nil
            currentSteps = []
            syncReadMarker()
        case .error(_, let message):
            streaming = .failed(message)
        }
    }

    public func stop() {
        streamTask?.cancel()
        streamTask = nil
        Task { [client, sessionId] in
            try? await client.stop(sessionId: sessionId)
        }
        if case .active(let text) = streaming, !text.isEmpty {
            messages.append(Message(
                id: "local-\(UUID().uuidString)",
                sessionId: sessionId,
                botId: botId,
                role: .assistant,
                text: text,
                createdAt: .now
            ))
        }
        streaming = .idle
        streamingSince = nil
        currentSteps = []
    }

    public func clearError() {
        if case .failed = streaming { streaming = .idle }
    }
}
