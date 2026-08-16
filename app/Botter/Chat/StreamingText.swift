import AppKit
import SwiftUI
import BotterKit

// MARK: - Tokens

/// One rendered unit of streaming prose: a word plus the inline style it
/// inherited from the markdown around it. Words are the animation unit — they
/// resolve out of blur one at a time, so each needs its own view.
struct StreamToken: Equatable {
    /// How this token starts relative to the previous one.
    enum Break: Int, Comparable {
        case none, line, paragraph

        static func < (lhs: Break, rhs: Break) -> Bool { lhs.rawValue < rhs.rawValue }
    }

    struct Style: Equatable {
        var bold = false
        var italic = false
        var code = false
        var link = false
    }

    var text: String
    var style = Style()
    var lineBreak: Break = .none
}

/// Splits streaming markdown into styled words. Deliberately small: it covers
/// what a chat reply actually uses mid-stream — emphasis, inline code, links,
/// bullets, headings — and leaves everything else as literal text. Block
/// structure (fences, tables) is handled upstream by `MessageBlockParser`.
enum StreamTokenizer {
    static func tokens(_ text: String) -> [StreamToken] {
        var result: [StreamToken] = []
        var pendingBreak: StreamToken.Break = .none
        var sawContent = false

        for rawLine in text.split(separator: "\n", omittingEmptySubsequences: false) {
            var line = rawLine.trimmingCharacters(in: .whitespaces)
            if line.isEmpty {
                if sawContent { pendingBreak = max(pendingBreak, .paragraph) }
                continue
            }
            if sawContent { pendingBreak = max(pendingBreak, .line) }

            var lineStyle = StreamToken.Style()
            var bullet: String?

            // Heading: "## Title" reads as an emphasized line.
            while line.hasPrefix("#") {
                line.removeFirst()
                lineStyle.bold = true
            }
            if lineStyle.bold { line = line.trimmingCharacters(in: .whitespaces) }

            // Blockquote marker is dropped; the text carries the meaning.
            while line.hasPrefix(">") {
                line.removeFirst()
                line = line.trimmingCharacters(in: .whitespaces)
            }

            if line.hasPrefix("- ") || line.hasPrefix("* ") || line.hasPrefix("+ ") {
                bullet = "•"
                line.removeFirst(2)
            }

            var words = self.words(in: line, base: lineStyle)
            if let bullet {
                words.insert(StreamToken(text: bullet, style: .init()), at: 0)
            }
            guard !words.isEmpty else { continue }

            words[0].lineBreak = pendingBreak
            pendingBreak = .none
            sawContent = true
            result.append(contentsOf: words)
        }
        return result
    }

    /// Walks one line, toggling inline styles at markers and emitting a token
    /// per whitespace-separated word. A word keeps the style it had when it
    /// started, so a closing marker never un-styles the word it closes.
    private static func words(in line: String, base: StreamToken.Style) -> [StreamToken] {
        let characters = Array(line)
        var tokens: [StreamToken] = []
        var buffer = ""
        var style = base
        var bufferStyle = base
        /// Index where the current link label ends (the "]" of "[label](url)").
        var linkLabelEnd: Int?
        var linkSkipTo: Int?

        func append(_ character: Character) {
            if buffer.isEmpty { bufferStyle = style }
            buffer.append(character)
        }

        func flush() {
            guard !buffer.isEmpty else { return }
            tokens.append(StreamToken(text: buffer, style: bufferStyle))
            buffer = ""
        }

        var index = 0
        while index < characters.count {
            if let end = linkLabelEnd, index == end {
                flush()
                style.link = base.link
                linkLabelEnd = nil
                index = linkSkipTo ?? index + 1
                continue
            }

            let character = characters[index]

            // Escapes: "\*" is a literal asterisk.
            if character == "\\", index + 1 < characters.count {
                append(characters[index + 1])
                index += 2
                continue
            }

            if character.isWhitespace {
                flush()
                index += 1
                continue
            }

            if character == "`" {
                flush()
                style.code.toggle()
                index += 1
                continue
            }

            if character == "*" || character == "_" {
                let double = index + 1 < characters.count && characters[index + 1] == character
                // Underscores inside a word (snake_case) are not emphasis.
                let atBoundary = buffer.isEmpty || style.italic || style.bold
                if character == "_" && !atBoundary {
                    append(character)
                    index += 1
                    continue
                }
                flush()
                if double {
                    style.bold.toggle()
                    index += 2
                } else {
                    style.italic.toggle()
                    index += 1
                }
                continue
            }

            if character == "[", linkLabelEnd == nil,
               let label = characters[index...].firstIndex(of: "]"),
               label + 1 < characters.count, characters[label + 1] == "(",
               let close = characters[(label + 1)...].firstIndex(of: ")") {
                flush()
                style.link = true
                linkLabelEnd = label
                linkSkipTo = close + 1
                index += 1
                continue
            }

            append(character)
            index += 1
        }
        flush()
        return tokens
    }
}

// MARK: - Layout

private struct StreamBreakKey: LayoutValueKey {
    static let defaultValue: StreamToken.Break = .none
}

private extension View {
    func streamBreak(_ value: StreamToken.Break) -> some View {
        layoutValue(key: StreamBreakKey.self, value: value)
    }
}

/// Flows word views like a paragraph of text: baseline-aligned rows, a real
/// space between words, honest line and paragraph breaks. Needed because per-
/// word blur is impossible inside a single `Text` — each word must be a view.
struct WordFlowLayout: Layout {
    var spaceWidth: CGFloat
    var lineSpacing: CGFloat = 3
    /// Matches the blank line the settled `Text` render keeps, so the swap to
    /// the finished message does not shift the paragraphs.
    var paragraphSpacing: CGFloat = 16

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        arrange(maxWidth: proposal.width ?? .infinity, subviews: subviews).size
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        // Safe to re-arrange at the granted width: every row already fits
        // within the width reported by sizeThatFits, so nothing re-wraps.
        let origins = arrange(maxWidth: bounds.width, subviews: subviews).origins
        for (subview, origin) in zip(subviews, origins) {
            subview.place(
                at: CGPoint(x: bounds.minX + origin.x, y: bounds.minY + origin.y),
                anchor: .topLeading,
                proposal: Self.sizing(maxWidth: bounds.width)
            )
        }
    }

    private static func sizing(maxWidth: CGFloat) -> ProposedViewSize {
        ProposedViewSize(width: maxWidth.isFinite ? maxWidth : nil, height: nil)
    }

    private func arrange(maxWidth: CGFloat, subviews: Subviews) -> (size: CGSize, origins: [CGPoint]) {
        // Proposing the line width (rather than .unspecified) keeps a word too
        // long for one line — a bare URL, say — inside the bubble instead of
        // letting it overflow.
        let sizing = Self.sizing(maxWidth: maxWidth)
        let sizes = subviews.map { $0.sizeThatFits(sizing) }
        let baselines = subviews.indices.map { index -> CGFloat in
            let value = subviews[index].dimensions(in: sizing)[.firstTextBaseline]
            return value.isFinite ? value : sizes[index].height
        }

        var origins = Array(repeating: CGPoint.zero, count: subviews.count)
        var row: [Int] = []
        var x: CGFloat = 0
        var y: CGFloat = 0
        var width: CGFloat = 0

        func flushRow(extraGap: CGFloat) {
            defer { y += extraGap }
            guard !row.isEmpty else { return }
            let above = row.map { baselines[$0] }.max() ?? 0
            let below = row.map { sizes[$0].height - baselines[$0] }.max() ?? 0
            for index in row { origins[index].y = y + above - baselines[index] }
            width = max(width, x - spaceWidth)
            y += above + below + lineSpacing
            x = 0
            row = []
        }

        for index in subviews.indices {
            let lineBreak = subviews[index][StreamBreakKey.self]
            if lineBreak != .none, index > 0 {
                flushRow(extraGap: lineBreak == .paragraph ? paragraphSpacing : 0)
            }
            if !row.isEmpty, x + sizes[index].width > maxWidth {
                flushRow(extraGap: 0)
            }
            origins[index].x = x
            row.append(index)
            x += sizes[index].width + spaceWidth
        }
        flushRow(extraGap: 0)

        return (CGSize(width: min(width, maxWidth), height: max(0, y - lineSpacing)), origins)
    }
}

// MARK: - Streaming prose

/// Live assistant prose: words resolve out of blur at a steady reading cadence
/// while the caret rides along behind the last one.
///
/// The cadence is decoupled from token arrival. Deltas land in bursts; a burst
/// that popped in at once would read as a jump, so words are released on a
/// paced clock that speeds up as the backlog grows (and so never falls more
/// than a few hundred milliseconds behind the stream).
struct StreamingProse: View {
    let text: String
    var showsCaret = true

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// Words handed to the view so far. `nil` before the pacer takes over, so
    /// a bubble that appears with text already in it shows that text at once
    /// instead of replaying it.
    @State private var released: Int?
    /// Words eligible for release — everything except the one still being
    /// assembled by the stream, which would flicker as it grows.
    @State private var releasable = 0

    /// Width of a space in the chat font, so word views read as a paragraph.
    static let spaceWidth: CGFloat = NSAttributedString(
        string: " ",
        attributes: [.font: NSFont.systemFont(ofSize: 14)]
    ).size().width

    private static let reveal = Animation.timingCurve(0.22, 0.61, 0.25, 1, duration: 0.42)

    var body: some View {
        let tokens = StreamTokenizer.tokens(text)
        let shown = min(released ?? tokens.count, tokens.count)

        WordFlowLayout(spaceWidth: Self.spaceWidth) {
            ForEach(Array(0..<shown), id: \.self) { index in
                StreamWord(token: tokens[index])
                    .streamBreak(tokens[index].lineBreak)
                    .transition(reduceMotion ? .identity : .streamReveal)
            }
            if showsCaret {
                StreamCaret()
                    .streamBreak(.none)
            }
        }
        .onChange(of: text, initial: true) { _, _ in
            let count = StreamTokenizer.tokens(text).count
            releasable = max(0, count - 1)
            // Only shrink when the stream itself rewinds (a tool call clears
            // the live text); never claw back words already on screen.
            if let released, released > count { self.released = count }
        }
        .task { await release() }
    }

    /// Releases one word at a time, draining any backlog inside ~0.3s so the
    /// display never trails the stream by more than a moment.
    private func release() async {
        // Adopt whatever is already on screen, then pace everything after it.
        released = StreamTokenizer.tokens(text).count
        guard !reduceMotion else { return }
        while !Task.isCancelled {
            let backlog = releasable - (released ?? 0)
            guard backlog > 0 else {
                try? await Task.sleep(for: .milliseconds(16))
                continue
            }
            withAnimation(Self.reveal) { released = (released ?? 0) + 1 }
            let interval = min(0.055, max(0.008, 0.3 / Double(backlog)))
            try? await Task.sleep(for: .seconds(interval))
        }
    }
}

extension AnyTransition {
    /// Words resolve out of blur, rising slightly into place.
    static var streamReveal: AnyTransition {
        .modifier(active: StreamReveal(progress: 0), identity: StreamReveal(progress: 1))
    }
}

/// The reveal itself, as a function of progress, so the endpoints and any
/// point between them can be rendered and inspected.
struct StreamReveal: ViewModifier {
    var progress: Double

    func body(content: Content) -> some View {
        content
            .blur(radius: 3.5 * (1 - progress))
            .opacity(progress)
            .offset(y: 3 * (1 - progress))
    }
}

struct StreamWord: View {
    let token: StreamToken

    var body: some View {
        Text(token.text)
            .font(font)
            // Accent, matching how the settled `Text` renders a markdown link.
            .foregroundStyle(token.style.link ? Color.accentColor : Tokens.textPrimary)
            // No padding: the settled render tints the glyph run itself, and a
            // padded chip here would shift the line when the stream ends.
            .background {
                if token.style.code {
                    RoundedRectangle(cornerRadius: 3, style: .continuous)
                        .fill(Color.white.opacity(0.07))
                }
            }
    }

    private var font: Font {
        if token.style.code {
            return .system(size: 13, design: .monospaced)
        }
        let base = Font.system(size: 14, weight: token.style.bold ? .semibold : .regular)
        return token.style.italic ? base.italic() : base
    }
}

/// Text cursor at the end of the released words: a hairline bar that breathes
/// rather than hard-blinks, and sits on the text baseline like a real caret.
private struct StreamCaret: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30.0, paused: reduceMotion)) { context in
            let phase = context.date.timeIntervalSinceReferenceDate / 1.05
            let wave = (1 + cos(2 * .pi * phase)) / 2
            RoundedRectangle(cornerRadius: 1.25, style: .continuous)
                .fill(Tokens.textPrimary)
                .frame(width: 2.5, height: 15)
                .opacity(reduceMotion ? 0.7 : 0.25 + 0.75 * pow(wave, 0.65))
        }
        .frame(width: 2.5, height: 15)
        .alignmentGuide(.firstTextBaseline) { dimensions in dimensions[.bottom] - 1 }
        .accessibilityHidden(true)
    }
}

// MARK: - Bubble

/// The streaming counterpart of `MessageBlocksView`: completed blocks render
/// exactly as they will after the stream ends, and only the trailing prose —
/// the part still growing — animates.
struct StreamingBlocksView: View {
    let text: String

    var body: some View {
        let blocks = MessageBlockParser.parse(text)
        let trailing: String? = {
            if case .text(let prose) = blocks.last { return prose }
            return nil
        }()
        let settled = trailing == nil ? blocks : Array(blocks.dropLast())

        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(settled.enumerated()), id: \.offset) { _, block in
                BlockView(block: block)
            }
            if let trailing {
                StreamingProseBubble(text: trailing)
            }
        }
        .frame(maxWidth: 620, alignment: .leading)
    }
}

/// Prose bubble whose contents stream in. Matches `ProseBubble` exactly so the
/// swap to the finished message is invisible.
struct StreamingProseBubble: View {
    let text: String

    var body: some View {
        StreamingProse(text: text)
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
