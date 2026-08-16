import Foundation

/// Assistant message content, parsed into renderable blocks: prose, fenced
/// code, pipe tables, and ```chart fences carrying a small JSON spec.
public enum MessageBlock: Hashable, Sendable {
    case text(String)
    case code(language: String?, filename: String?, code: String)
    case table(headers: [String], rows: [[String]])
    case chart(ChartSpec)
}

public struct ChartSpec: Codable, Hashable, Sendable {
    public struct Series: Codable, Hashable, Sendable {
        public var name: String
        public var color: String?
        public var points: [Double]

        public init(name: String, color: String? = nil, points: [Double]) {
            self.name = name
            self.color = color
            self.points = points
        }
    }

    public var title: String?
    public var series: [Series]

    public init(title: String? = nil, series: [Series]) {
        self.title = title
        self.series = series
    }
}

public enum MessageBlockParser {
    public static func parse(_ text: String) -> [MessageBlock] {
        var blocks: [MessageBlock] = []
        var prose: [String] = []
        var tableLines: [String] = []

        func flushProse() {
            let joined = prose.joined(separator: "\n").collapsedBlankLines
            if !joined.isEmpty { blocks.append(.text(joined)) }
            prose = []
        }

        func flushTable() {
            defer { tableLines = [] }
            guard tableLines.count >= 2 else {
                prose.append(contentsOf: tableLines)
                return
            }
            let headers = tableCells(tableLines[0])
            guard isSeparatorRow(tableLines[1]) else {
                prose.append(contentsOf: tableLines)
                return
            }
            let rows = tableLines.dropFirst(2).map(tableCells)
            flushProse()
            blocks.append(.table(headers: headers, rows: Array(rows)))
        }

        var lines = text.split(separator: "\n", omittingEmptySubsequences: false)[...]
        while let line = lines.first {
            lines = lines.dropFirst()
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            if trimmed.hasPrefix("```") {
                flushTable()
                var fenceBody: [String] = []
                while let next = lines.first, !next.trimmingCharacters(in: .whitespaces).hasPrefix("```") {
                    fenceBody.append(String(next))
                    lines = lines.dropFirst()
                }
                if lines.first != nil { lines = lines.dropFirst() }  // closing fence

                let info = trimmed.dropFirst(3).trimmingCharacters(in: .whitespaces)
                let code = fenceBody.joined(separator: "\n")
                flushProse()
                if info.lowercased() == "chart",
                   let spec = try? JSONDecoder().decode(ChartSpec.self, from: Data(code.utf8)),
                   !spec.series.isEmpty {
                    blocks.append(.chart(spec))
                } else {
                    // Info string forms: "swift", "ts:churn.ts", "churn.ts"
                    var language: String?
                    var filename: String?
                    if info.contains(":") {
                        let parts = info.split(separator: ":", maxSplits: 1)
                        language = String(parts[0])
                        filename = parts.count > 1 ? String(parts[1]) : nil
                    } else if info.contains(".") {
                        filename = String(info)
                        language = info.split(separator: ".").last.map(String.init)
                    } else if !info.isEmpty {
                        language = String(info)
                    }
                    blocks.append(.code(language: language, filename: filename, code: code))
                }
                continue
            }

            if trimmed.hasPrefix("|"), trimmed.hasSuffix("|"), trimmed.count > 2 {
                tableLines.append(String(line))
                continue
            }
            flushTable()
            prose.append(String(line))
        }
        flushTable()
        flushProse()
        return blocks
    }

    private static func tableCells(_ line: String) -> [String] {
        line.trimmingCharacters(in: .whitespaces)
            .trimmingCharacters(in: CharacterSet(charactersIn: "|"))
            .split(separator: "|", omittingEmptySubsequences: false)
            .map { $0.trimmingCharacters(in: .whitespaces) }
    }

    private static func isSeparatorRow(_ line: String) -> Bool {
        let cells = tableCells(line)
        guard !cells.isEmpty else { return false }
        return cells.allSatisfy { cell in
            !cell.isEmpty && cell.allSatisfy { "-:".contains($0) }
        }
    }
}
