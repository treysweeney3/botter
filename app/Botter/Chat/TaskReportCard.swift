import SwiftUI
import BotterKit

/// Task report: one reply, with the work behind it collapsed above the answer.
/// The trace holds what the agent said it would do (`note`) and the tool steps
/// it ran. Tool-derived noise is sanitized before display.
struct TaskReportCard: View {
    let message: Message

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let items = message.taskItems, !items.isEmpty {
                TaskTraceDisclosure(items: items)
            }
            if let text = message.text, !text.isEmpty {
                ProseBubble(text: text.collapsedBlankLines)
            }
        }
        .frame(maxWidth: 620, alignment: .leading)
    }
}

/// Collapsed "Worked through N steps" line; expands to the trace itself.
struct TaskTraceDisclosure: View {
    let items: [TaskItem]

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
                    Image(systemName: "chevron.down")
                        .font(.system(size: 8, weight: .semibold))
                        .rotationEffect(.degrees(expanded ? 180 : 0))
                }
                .foregroundStyle(Tokens.textSecondary)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help(expanded ? "Hide the work" : "Show the work")

            if expanded {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                        TaskLine(item: item)
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
                .transition(.opacity.combined(with: .offset(y: -4)))
            }
        }
        .padding(.leading, 4)
    }

    private var label: String {
        let steps = items.filter { $0.state != "note" }.count
        guard steps > 0 else { return "Show the thinking" }
        return "Worked through \(steps) step\(steps == 1 ? "" : "s")"
    }
}

struct TaskLine: View {
    let item: TaskItem

    var body: some View {
        if item.state == "note" {
            // What the agent said it would do next — a sentence, not a step.
            HStack(alignment: .firstTextBaseline, spacing: 7) {
                Text("›")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Tokens.textSecondary.opacity(0.7))
                    .frame(width: 12, alignment: .center)
                Text(item.label)
                    .font(.system(size: 13))
                    .foregroundStyle(Tokens.textSecondary)
                    .lineSpacing(2)
                    .lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .accessibilityElement(children: .combine)
            .accessibilityLabel("Said: \(item.label)")
        } else {
            let display = TaskItemSanitizer.sanitize(item)
            HStack(alignment: .firstTextBaseline, spacing: 7) {
                Text(marker)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(markerColor)
                    .frame(width: 12, alignment: .center)
                (Text(display.label).font(.system(size: 13, weight: .semibold))
                    + Text(display.detail.map { "  →  \($0)" } ?? "")
                        .font(.system(size: 13))
                        .foregroundStyle(Tokens.textSecondary))
                    .foregroundStyle(Tokens.textPrimary)
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
            .accessibilityElement(children: .combine)
            .accessibilityLabel("\(statusWord) \(display.label), \(display.detail ?? "")")
        }
    }

    private var marker: String {
        switch item.state {
        case "failed", "error": "✕"
        case "running": "•"
        default: "✓"
        }
    }

    private var markerColor: Color {
        switch item.state {
        case "failed", "error": Color(hex: 0xEF4444)
        case "running": Tokens.textSecondary
        default: Tokens.textPrimary
        }
    }

    private var statusWord: String {
        switch item.state {
        case "failed", "error": "failed"
        case "running": "running"
        default: "done"
        }
    }
}

/// Backend derives task items from raw tool calls; labels can carry command
/// arguments and details can be raw JSON output. Reduce both to human scale.
enum TaskItemSanitizer {
    static func sanitize(_ item: TaskItem) -> (label: String, detail: String?) {
        (label: clean(item.label, limit: 44), detail: cleanDetail(item.detail))
    }

    private static func clean(_ raw: String, limit: Int) -> String {
        var text = raw.replacingOccurrences(of: "\n", with: " ")
        // "terminal → GSETUP=\"python /Users/…\"" → "terminal → GSETUP=…"
        if let quote = text.firstIndex(where: { $0 == "\"" || $0 == "'" }) {
            text = String(text[..<quote]).trimmingCharacters(in: .whitespaces) + "…"
        }
        if text.count > limit {
            text = String(text.prefix(limit)).trimmingCharacters(in: .whitespaces) + "…"
        }
        return text
    }

    private static func cleanDetail(_ raw: String?) -> String? {
        guard var text = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty else { return nil }
        // Raw JSON payloads are logs, not summaries — drop them.
        if text.hasPrefix("{") || text.hasPrefix("[") || text.contains("\":") { return nil }
        text = text.replacingOccurrences(of: "\n", with: " · ")
        if text.count > 64 {
            text = String(text.prefix(64)).trimmingCharacters(in: .whitespaces) + "…"
        }
        return text
    }
}

/// Approval request: a floating card with the question, context, and actions.
struct ApprovalBubble: View {
    @Environment(AppModel.self) private var model
    let message: Message

    @State private var resolved: ApprovalDecision?

    private var runId: String {
        // ChatStore synthesizes approval messages with id "approval-<run_id>".
        message.id.hasPrefix("approval-") ? String(message.id.dropFirst("approval-".count)) : message.id
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Needs your approval")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(Tokens.textSecondary)
                .textCase(.uppercase)
                .kerning(0.6)

            Text(message.text ?? "")
                .font(.system(size: 14, weight: .medium))
                .lineSpacing(3)
                .foregroundStyle(Tokens.textPrimary)

            if let resolved {
                HStack(spacing: 6) {
                    Image(systemName: resolved == .deny ? "xmark.circle.fill" : "checkmark.circle.fill")
                        .font(.system(size: 12))
                        .foregroundStyle(resolved == .deny ? Color(hex: 0xEF4444) : Color(hex: 0x22C55E))
                    Text(resolved == .deny ? "Denied" : "Approved")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(Tokens.textSecondary)
                }
                .transition(.opacity)
            } else {
                HStack(spacing: 8) {
                    Button {
                        decide(.once)
                    } label: {
                        Text("Approve")
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundStyle(.black)
                            .padding(.horizontal, 14)
                            .padding(.vertical, 6)
                            .background(Capsule().fill(.white))
                    }
                    .buttonStyle(.pressable)

                    subtleAction("For this task") { decide(.session) }
                    subtleAction("Always") { decide(.always) }

                    Spacer()

                    Button {
                        decide(.deny)
                    } label: {
                        Text("Deny")
                            .font(.system(size: 12, weight: .medium))
                            .foregroundStyle(Color(hex: 0xEF4444))
                            .padding(.horizontal, 12)
                            .padding(.vertical, 6)
                            .background(Capsule().fill(Color(hex: 0xEF4444).opacity(0.12)))
                    }
                    .buttonStyle(.pressable)
                }
            }
        }
        .padding(16)
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(Color(hex: 0x1A1A1C))
                .shadow(color: .black.opacity(0.35), radius: 14, y: 4)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(.white.opacity(0.07), lineWidth: 1)
        )
        .frame(maxWidth: 480, alignment: .leading)
        .animation(.easeOut(duration: 0.2), value: resolved)
    }

    private func subtleAction(_ label: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(label)
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(Tokens.textPrimary)
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
                .background(Capsule().fill(Tokens.cardBackground))
                .overlay(Capsule().strokeBorder(Tokens.hairline, lineWidth: 1))
        }
        .buttonStyle(.pressable)
    }

    private func decide(_ decision: ApprovalDecision) {
        resolved = decision
        Task { await model.approvals.decide(runId: runId, decision: decision) }
    }
}
