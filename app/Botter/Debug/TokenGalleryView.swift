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
                        StreamingBubble(text: "", toolActivity: "Runs `salesforce export`")
                    }
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
