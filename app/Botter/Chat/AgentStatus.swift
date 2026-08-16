import SwiftUI
import BotterKit

/// 3×3 pixel-grid loader: a soft wave travels diagonally across the cells,
/// lighting and swelling each one as it passes.
struct PixelGridLoader: View {
    var size: CGFloat = 14

    /// Seconds for one wave to cross the grid and reset.
    private let period: Double = 1.35
    /// How far the wave lags from the top-left cell to the bottom-right one.
    private let spread: Double = 0.55

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 60.0, paused: reduceMotion)) { context in
            let time = context.date.timeIntervalSinceReferenceDate
            let cell = (size - 4) / 3
            VStack(spacing: 2) {
                ForEach(0..<3, id: \.self) { row in
                    HStack(spacing: 2) {
                        ForEach(0..<3, id: \.self) { column in
                            let energy = energy(row: row, column: column, time: time)
                            RoundedRectangle(cornerRadius: 1.5, style: .continuous)
                                .fill(Tokens.textPrimary)
                                .frame(width: cell, height: cell)
                                .opacity(0.18 + 0.82 * energy)
                                .scaleEffect(0.8 + 0.2 * energy)
                        }
                    }
                }
            }
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }

    /// 0…1 brightness for a cell: a raised-cosine pulse whose phase is offset by
    /// the cell's distance along the top-left → bottom-right diagonal.
    private func energy(row: Int, column: Int, time: Double) -> Double {
        guard !reduceMotion else { return 0.55 }
        let diagonal = Double(row + column) / 4.0            // 0…1 across the grid
        let phase = (time / period - diagonal * spread)
            .truncatingRemainder(dividingBy: 1)
        let wrapped = phase < 0 ? phase + 1 : phase
        // Smooth 0→1→0 sweep, biased so cells spend longer dim than lit.
        let pulse = (1 - cos(2 * .pi * wrapped)) / 2
        return pulse * pulse
    }
}

/// Label with a light band sweeping across it.
struct ShimmerText: View {
    let text: String
    var font: Font = Tokens.chatBody

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30.0)) { context in
            let phase = context.date.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: 1.6) / 1.6
            Text(text)
                .font(font)
                .foregroundStyle(Tokens.textSecondary)
                .overlay {
                    LinearGradient(
                        stops: [
                            .init(color: .clear, location: 0),
                            .init(color: .white.opacity(0.9), location: 0.5),
                            .init(color: .clear, location: 1),
                        ],
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                    .frame(width: 60)
                    .offset(x: (phase * 220) - 110)
                    .mask(Text(text).font(font))
                }
        }
    }
}

/// Pre-reply state: pixel loader + shimmering verb + elapsed seconds.
struct AgentWorkingIndicator: View {
    let since: Date
    let activity: String?

    var body: some View {
        HStack(spacing: 8) {
            PixelGridLoader()
            ShimmerText(text: activity ?? "Thinking")
            TimelineView(.periodic(from: since, by: 1)) { context in
                Text(Self.elapsedLabel(seconds: context.date.timeIntervalSince(since)))
                    .font(.system(size: 12))
                    .monospacedDigit()
                    .foregroundStyle(Tokens.textSecondary.opacity(0.6))
            }
        }
        .padding(.horizontal, 15)
        .padding(.vertical, 12)
        .background(
            RoundedRectangle(cornerRadius: Tokens.bubbleRadius, style: .continuous)
                .fill(Tokens.cardBackground)
        )
        .overlay(
            RoundedRectangle(cornerRadius: Tokens.bubbleRadius, style: .continuous)
                .strokeBorder(.white.opacity(0.045), lineWidth: 1)
        )
        .accessibilityLabel(activity ?? "Thinking")
    }

    /// Whole seconds under a minute ("42s"), then minutes and seconds
    /// ("1m 13s", "5m 5s"), then hours and minutes ("1h 4m").
    static func elapsedLabel(seconds interval: TimeInterval) -> String {
        let total = Int(max(0, interval))
        if total < 60 { return "\(total)s" }
        if total < 3600 { return "\(total / 60)m \(total % 60)s" }
        return "\(total / 3600)h \((total % 3600) / 60)m"
    }
}

/// Collapsed "Thought for Ns" line above a completed reply; expands to the
/// tool steps the agent took.
struct ThinkingTraceView: View {
    let trace: ExchangeTrace

    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button {
                withAnimation(.easeOut(duration: 0.18)) { expanded.toggle() }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "sparkle")
                        .font(.system(size: 10, weight: .semibold))
                    Text(label)
                        .font(.system(size: 12, weight: .medium))
                    if !trace.steps.isEmpty {
                        Image(systemName: "chevron.down")
                            .font(.system(size: 8, weight: .semibold))
                            .rotationEffect(.degrees(expanded ? 180 : 0))
                    }
                }
                .foregroundStyle(Tokens.textSecondary)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(trace.steps.isEmpty)

            if expanded {
                VStack(alignment: .leading, spacing: 5) {
                    ForEach(trace.steps) { step in
                        HStack(spacing: 7) {
                            Image(systemName: stepIcon(step))
                                .font(.system(size: 9, weight: .semibold))
                                .foregroundStyle(stepColor(step))
                                .frame(width: 12)
                            Text(step.summary ?? step.name)
                                .font(.system(size: 12))
                                .foregroundStyle(Tokens.textSecondary)
                                .lineLimit(1)
                        }
                    }
                }
                .padding(.leading, 3)
                .transition(.opacity.combined(with: .offset(y: -4)))
            }
        }
        .padding(.leading, 4)
    }

    private var label: String {
        let seconds = max(1, Int(trace.duration.rounded()))
        return "Thought for \(seconds) second\(seconds == 1 ? "" : "s")"
    }

    private func stepIcon(_ step: ToolStep) -> String {
        switch step.status {
        case "ok": "checkmark"
        case "error": "xmark"
        default: "circle.dotted"
        }
    }

    private func stepColor(_ step: ToolStep) -> Color {
        switch step.status {
        case "ok": Color(hex: 0x22C55E)
        case "error": Color(hex: 0xEF4444)
        default: Tokens.textSecondary
        }
    }
}

/// Fades newly-appended streamed text in, instead of popping it.
struct StreamedText: View {
    let text: String

    @State private var committed = ""
    @State private var tail = ""
    @State private var tailVisible = true

    var body: some View {
        (Text(markdown: committed.collapsedBlankLines)
            + Text(tail).foregroundStyle(Tokens.textPrimary.opacity(tailVisible ? 1 : 0)))
            .font(Tokens.chatBody)
            .lineSpacing(3)
            .foregroundStyle(Tokens.textPrimary)
            .onChange(of: text) { _, newValue in
                let previous = committed + tail
                if newValue.hasPrefix(previous) {
                    committed = previous
                    tail = String(newValue.dropFirst(previous.count))
                } else {
                    committed = newValue
                    tail = ""
                }
                tailVisible = false
                withAnimation(.easeOut(duration: 0.25)) { tailVisible = true }
            }
            .onAppear {
                committed = text
            }
    }
}
