import SwiftUI
import BotterKit

/// Debug screen (⌘⇧D): every token, glyph, and message style at a glance.
/// Step-1 verification surface — compare against the reference screenshots.
struct TokenGalleryView: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 26) {
                section("Surfaces") {
                    HStack(spacing: 10) {
                        swatch("window", Tokens.windowBackground)
                        swatch("sidebar", Tokens.sidebarBackground)
                        swatch("card", Tokens.cardBackground)
                        swatch("hairline", Tokens.hairline)
                        swatch("user bubble", Tokens.userBubbleBackground)
                    }
                }

                section("Botter palette") {
                    HStack(spacing: 10) {
                        ForEach(BotPalette.colors, id: \.hex) { entry in
                            swatch(entry.name, BotPalette.color(for: entry.hex))
                        }
                    }
                }

                section("Glyphs × palette") {
                    let palette = BotPalette.colors
                    FlowLayout(spacing: 10) {
                        ForEach(Array(Glyph.allCases.enumerated()), id: \.element) { index, glyph in
                            VStack(spacing: 5) {
                                AvatarView(
                                    colorHex: palette[index % palette.count].hex,
                                    glyphName: glyph.rawValue
                                )
                                Text(glyph.rawValue)
                                    .font(Tokens.timestamp)
                                    .foregroundStyle(Tokens.textSecondary)
                            }
                        }
                    }
                }

                section("Type") {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Sidebar name — semibold 13").font(Tokens.sidebarName)
                        Text("Sidebar body — 13").font(Tokens.sidebarBody).foregroundStyle(Tokens.textSecondary)
                        Text("Chat body — 14").font(Tokens.chatBody)
                        Text("timestamp — 11").font(Tokens.timestamp).foregroundStyle(Tokens.textSecondary)
                    }
                    .foregroundStyle(Tokens.textPrimary)
                }

                section("Message styles") {
                    VStack(spacing: 12) {
                        AssistantBubble(text: "Morning. Overnight I pulled the **Q3 pipeline** — 52 accounts, 8 need follow-up today.")
                        UserBubble(
                            text: "great — draft the follow-ups and queue them for my review",
                            attachments: []
                        )
                        TaskReportCard(message: Message(
                            id: "demo", sessionId: "s", botId: "b", role: .assistant, kind: .taskReport,
                            text: "Done. Follow-ups drafted for all 8 accounts.",
                            taskItems: [
                                TaskItem(label: "Let me pull the pipeline first, then draft from it.", state: "note"),
                                TaskItem(label: "Salesforce", detail: "list pulled · 52 accounts", state: "done"),
                                TaskItem(label: "Drafts", detail: "8 written, tone matched", state: "done"),
                                TaskItem(label: "Queue", detail: "awaiting your review", state: "running"),
                            ]
                        ))
                        SystemChip(icon: "clock", text: "Created routine  Overnight outbound")
                        StreamingBubble(text: "")
                        ThinkingTraceView(trace: ExchangeTrace(duration: 7, steps: [
                            ToolStep(id: 0, name: "salesforce", status: "ok", summary: "Ran `salesforce export`"),
                            ToolStep(id: 1, name: "sheets", status: "ok", summary: "Wrote 52 rows to Pipeline Q3"),
                            ToolStep(id: 2, name: "gmail", status: "error", summary: "Draft rejected — no recipient"),
                        ]))
                    }
                }

                section("Streaming") {
                    StreamingDemo()
                }
            }
            .padding(24)
        }
        .background(Tokens.windowBackground)
        .frame(minWidth: 640, minHeight: 600)
    }

    @ViewBuilder
    private func section(_ title: String, @ViewBuilder content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(Tokens.textSecondary)
                .textCase(.uppercase)
            content()
        }
    }

    /// Replays a canned reply in irregular bursts — the way SSE deltas
    /// actually land — so the streaming cadence can be judged without a
    /// backend. Loops with a hold at the end.
    private struct StreamingDemo: View {
        private static let script = """
        Pistachio is your **fastest-growing** flavor — sales are up 23% this \
        month and margins beat vanilla by 8 points. Stone-fruit flavors are \
        trending in the same range, and [the index](https://example.com) backs it up.

        - Restock `pistachio-base` before Thursday
        - Hold the vanilla order at 60%
        """

        @State private var text = ""

        var body: some View {
            StreamingBubble(text: text)
                .task { await loop() }
        }

        private func loop() async {
            let words = Self.script.split(separator: " ", omittingEmptySubsequences: false)
            while !Task.isCancelled {
                text = ""
                var index = 0
                while index < words.count {
                    let burst = Int.random(in: 1...4)
                    let end = min(index + burst, words.count)
                    let chunk = words[index..<end].joined(separator: " ")
                    text += text.isEmpty ? chunk : " " + chunk
                    index = end
                    try? await Task.sleep(for: .milliseconds(Int.random(in: 40...170)))
                }
                try? await Task.sleep(for: .seconds(3.4))
            }
        }
    }

    private func swatch(_ name: String, _ color: Color) -> some View {
        VStack(spacing: 5) {
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(color)
                .frame(width: 64, height: 40)
                .overlay(
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .stroke(Tokens.hairline, lineWidth: 1)
                )
            Text(name)
                .font(Tokens.timestamp)
                .foregroundStyle(Tokens.textSecondary)
        }
    }
}
