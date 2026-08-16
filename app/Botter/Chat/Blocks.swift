import SwiftUI
import Charts
import BotterKit

/// Renders an assistant message as parsed blocks: prose bubbles, code cards,
/// tables, and charts.
struct MessageBlocksView: View {
    let text: String

    var body: some View {
        let blocks = MessageBlockParser.parse(text)
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                switch block {
                case .text(let prose):
                    ProseBubble(text: prose)
                case .code(let language, let filename, let code):
                    CodeBlockView(language: language, filename: filename, code: code)
                case .table(let headers, let rows):
                    TableBlockView(headers: headers, rows: rows)
                case .chart(let spec):
                    ChartCardView(spec: spec)
                }
            }
        }
        .frame(maxWidth: 620, alignment: .leading)
    }
}

struct ProseBubble: View {
    let text: String

    var body: some View {
        Text(markdown: text)
            .font(Tokens.chatBody)
            .lineSpacing(3)
            .foregroundStyle(Tokens.textPrimary)
            .textSelection(.enabled)
            .padding(.horizontal, 15)
            .padding(.vertical, 11)
            .background(
                RoundedRectangle(cornerRadius: Tokens.bubbleRadius, style: .continuous)
                    .fill(Tokens.cardBackground)
            )
            .overlay(
                RoundedRectangle(cornerRadius: Tokens.bubbleRadius, style: .continuous)
                    .strokeBorder(.white.opacity(0.045), lineWidth: 1)
            )
    }
}

// MARK: - Code

struct CodeBlockView: View {
    let language: String?
    let filename: String?
    let code: String

    @State private var copied = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                Text(filename ?? language?.capitalized ?? "Code")
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .foregroundStyle(Tokens.textPrimary)
                if filename != nil, let language {
                    Text(languageDisplayName(language))
                        .font(.system(size: 11))
                        .foregroundStyle(Tokens.textSecondary)
                }
                Spacer()
                Button {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(code, forType: .string)
                    withAnimation(.easeOut(duration: 0.15)) { copied = true }
                    Task {
                        try? await Task.sleep(for: .seconds(1.5))
                        withAnimation(.easeOut(duration: 0.3)) { copied = false }
                    }
                } label: {
                    HStack(spacing: 4) {
                        Image(systemName: copied ? "checkmark" : "doc.on.doc")
                            .font(.system(size: 10, weight: .semibold))
                        Text(copied ? "Copied" : "Copy")
                            .font(.system(size: 11, weight: .medium))
                    }
                    .foregroundStyle(copied ? Color(hex: 0x22C55E) : Tokens.textSecondary)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 9)
            .background(Color.white.opacity(0.03))

            Divider().overlay(Tokens.hairline)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(alignment: .top, spacing: 12) {
                    VStack(alignment: .trailing, spacing: 0) {
                        ForEach(1...max(lines.count, 1), id: \.self) { number in
                            Text("\(number)")
                                .font(.system(size: 12, design: .monospaced))
                                .foregroundStyle(Tokens.textSecondary.opacity(0.45))
                                .frame(height: 19)
                        }
                    }
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                            Text(CodeHighlighter.highlight(String(line), language: language))
                                .font(.system(size: 12, design: .monospaced))
                                .frame(height: 19, alignment: .leading)
                        }
                    }
                    .textSelection(.enabled)
                }
                .padding(14)
            }
        }
        .background(
            RoundedRectangle(cornerRadius: Tokens.cardRadius, style: .continuous)
                .fill(Color(hex: 0x151517))
        )
        .overlay(
            RoundedRectangle(cornerRadius: Tokens.cardRadius, style: .continuous)
                .strokeBorder(Tokens.hairline, lineWidth: 1)
        )
    }

    private var lines: [Substring] {
        code.split(separator: "\n", omittingEmptySubsequences: false)
    }

    private func languageDisplayName(_ language: String) -> String {
        switch language.lowercased() {
        case "ts", "tsx": "TypeScript"
        case "js", "jsx": "JavaScript"
        case "py": "Python"
        case "sh", "bash", "zsh": "Shell"
        case "yml": "YAML"
        default: language.count <= 3 ? language.uppercased() : language.capitalized
        }
    }
}

/// Lightweight regex-free tokenizer: keywords, strings, numbers, comments.
/// Not a real parser; tuned to read well, not to be exact.
enum CodeHighlighter {
    private static let keywords: Set<String> = [
        "func", "let", "var", "const", "return", "if", "else", "for", "while", "in",
        "import", "from", "export", "async", "await", "def", "class", "struct", "enum",
        "public", "private", "static", "true", "false", "nil", "null", "None", "self",
        "this", "new", "try", "catch", "except", "switch", "case", "guard", "type",
        "interface", "extends", "with", "as", "raise", "lambda", "match", "fn", "mut",
    ]

    static func highlight(_ line: String, language: String?) -> AttributedString {
        var result = AttributedString()
        let keywordColor = Color(hex: 0x6CA1F7)
        let stringColor = Color(hex: 0x87C989)
        let numberColor = Color(hex: 0x56B6C2)
        let commentColor = Color(hex: 0x6B6B70)

        // Whole-line comment
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        if trimmed.hasPrefix("//") || trimmed.hasPrefix("#") && language?.lowercased() != "swift" {
            var comment = AttributedString(line)
            comment.foregroundColor = commentColor
            return comment
        }

        var token = ""
        var inString: Character? = nil
        var stringBuffer = ""

        func flushToken() {
            guard !token.isEmpty else { return }
            var attributed = AttributedString(token)
            if keywords.contains(token) {
                attributed.foregroundColor = keywordColor
            } else if Double(token) != nil {
                attributed.foregroundColor = numberColor
            } else {
                attributed.foregroundColor = Tokens.textPrimary
            }
            result += attributed
            token = ""
        }

        for character in line {
            if let quote = inString {
                stringBuffer.append(character)
                if character == quote {
                    var attributed = AttributedString(stringBuffer)
                    attributed.foregroundColor = stringColor
                    result += attributed
                    stringBuffer = ""
                    inString = nil
                }
                continue
            }
            if character == "\"" || character == "'" || character == "`" {
                flushToken()
                inString = character
                stringBuffer = String(character)
                continue
            }
            if character.isLetter || character.isNumber || character == "_" || character == "." {
                token.append(character)
            } else {
                flushToken()
                var plain = AttributedString(String(character))
                plain.foregroundColor = Tokens.textPrimary.opacity(0.85)
                result += plain
            }
        }
        flushToken()
        if !stringBuffer.isEmpty {
            var attributed = AttributedString(stringBuffer)
            attributed.foregroundColor = stringColor
            result += attributed
        }
        return result
    }
}

// MARK: - Table

struct TableBlockView: View {
    let headers: [String]
    let rows: [[String]]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Grid(alignment: .leading, horizontalSpacing: 0, verticalSpacing: 0) {
                GridRow {
                    ForEach(Array(headers.enumerated()), id: \.offset) { _, header in
                        Text(header)
                            .font(.system(size: 12, weight: .medium))
                            .foregroundStyle(Tokens.textSecondary)
                            .padding(.horizontal, 14)
                            .padding(.vertical, 9)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                .background(Color.white.opacity(0.03))

                ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                    Divider().overlay(Tokens.hairline.opacity(0.6)).gridCellColumns(headers.count)
                    GridRow {
                        ForEach(Array(row.enumerated()), id: \.offset) { _, cell in
                            TableCell(text: cell)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                }
            }
        }
        .background(
            RoundedRectangle(cornerRadius: Tokens.cardRadius, style: .continuous)
                .fill(Color(hex: 0x151517))
        )
        .overlay(
            RoundedRectangle(cornerRadius: Tokens.cardRadius, style: .continuous)
                .strokeBorder(Tokens.hairline, lineWidth: 1)
        )
    }
}

struct TableCell: View {
    let text: String

    private static let statusStyles: [String: Color] = [
        "to do": Color(hex: 0xE8833A),
        "todo": Color(hex: 0xE8833A),
        "in progress": Color(hex: 0x38BDF8),
        "running": Color(hex: 0x38BDF8),
        "completed": Color(hex: 0x22C55E),
        "done": Color(hex: 0x22C55E),
        "ok": Color(hex: 0x22C55E),
        "failed": Color(hex: 0xEF4444),
        "error": Color(hex: 0xEF4444),
        "blocked": Color(hex: 0xEF4444),
    ]

    var body: some View {
        Group {
            if let color = Self.statusStyles[text.lowercased()] {
                Text(text)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(color)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(Capsule().fill(color.opacity(0.13)))
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
            } else {
                Text(text)
                    .font(.system(size: 13))
                    .foregroundStyle(Tokens.textPrimary)
                    .lineLimit(1)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 8)
            }
        }
    }
}

// MARK: - Chart

struct ChartCardView: View {
    let spec: ChartSpec

    private static let fallbackPalette: [Color] = [
        Color(hex: 0xE8833A), Color(hex: 0x3B82F6), Color(hex: 0x2EC7A6),
        Color(hex: 0x8B5CF6), Color(hex: 0xEAB308),
    ]

    private func color(for index: Int) -> Color {
        if let hex = spec.series[index].color, let color = Color(hexString: hex) {
            return color
        }
        return Self.fallbackPalette[index % Self.fallbackPalette.count]
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                if let title = spec.title {
                    Text(title)
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(Tokens.textSecondary)
                }
                Spacer()
                HStack(spacing: 10) {
                    ForEach(Array(spec.series.enumerated()), id: \.offset) { index, series in
                        HStack(spacing: 4) {
                            Circle().fill(color(for: index)).frame(width: 6, height: 6)
                            Text(series.name)
                                .font(.system(size: 11))
                                .foregroundStyle(Tokens.textSecondary)
                        }
                    }
                }
            }

            Chart {
                ForEach(Array(spec.series.enumerated()), id: \.offset) { index, series in
                    ForEach(Array(series.points.enumerated()), id: \.offset) { pointIndex, value in
                        LineMark(
                            x: .value("x", pointIndex),
                            y: .value("y", value),
                            series: .value("series", series.name)
                        )
                        .foregroundStyle(color(for: index))
                        .interpolationMethod(.catmullRom)
                        .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round))
                    }
                    if let last = series.points.indices.last {
                        PointMark(
                            x: .value("x", last),
                            y: .value("y", series.points[last])
                        )
                        .foregroundStyle(color(for: index))
                        .symbolSize(28)
                    }
                }
            }
            .chartXAxis(.hidden)
            .chartYAxis {
                AxisMarks(values: .automatic(desiredCount: 3)) { _ in
                    AxisGridLine().foregroundStyle(Tokens.hairline.opacity(0.6))
                }
            }
            .chartLegend(.hidden)
            .frame(height: 150)
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: Tokens.cardRadius, style: .continuous)
                .fill(Color(hex: 0x151517))
        )
        .overlay(
            RoundedRectangle(cornerRadius: Tokens.cardRadius, style: .continuous)
                .strokeBorder(Tokens.hairline, lineWidth: 1)
        )
    }
}
