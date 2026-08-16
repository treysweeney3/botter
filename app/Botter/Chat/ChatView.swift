import AppKit
import SwiftUI
import BotterKit

struct ChatView: View {
    @Environment(AppModel.self) private var model
    let bot: Bot
    let chat: ChatStore

    @State private var isEditingBot = false
    @State private var isShowingRoutines = false

    var body: some View {
        VStack(spacing: 0) {
            ChatHeader(
                bot: bot,
                editBot: { isEditingBot = true },
                showRoutines: { isShowingRoutines = true }
            )
            MessageListView(bot: bot, chat: chat)
            ComposerView(bot: bot, chat: chat)
        }
        .background(Tokens.windowBackground)
        .sheet(isPresented: $isEditingBot) {
            BotEditorSheet(mode: .edit(bot))
                .environment(model)
        }
        .sheet(isPresented: $isShowingRoutines) {
            RoutinesPanel(bot: bot)
                .environment(model)
        }
    }
}

/// Custom in-content header replacing the system title bar (Grok-style).
struct ChatHeader: View {
    let bot: Bot
    let editBot: () -> Void
    let showRoutines: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Button(action: editBot) {
                HStack(spacing: 8) {
                    AvatarView(bot: bot, size: 24)
                    Text(bot.displayName)
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(Tokens.textPrimary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help("Edit \(bot.displayName)")

            Spacer()

            headerIcon("clock", help: "Routines", action: showRoutines)
            headerIcon("display", help: "Computer view — coming soon", action: {})
                .disabled(true)
                .opacity(0.4)
        }
        .padding(.horizontal, 16)
        .frame(height: 44)
        .background(Tokens.windowBackground)
        .overlay(alignment: .bottom) {
            Rectangle().fill(Tokens.hairline).frame(height: 1)
        }
        .contentShape(Rectangle())
        .gesture(WindowDragGesture())
    }

    private func headerIcon(_ symbol: String, help: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(Tokens.textSecondary)
                .frame(width: 30, height: 30)
                .background(
                    RoundedRectangle(cornerRadius: 7, style: .continuous)
                        .fill(Tokens.cardBackground.opacity(0.001))
                )
                .contentShape(Rectangle())
        }
        .buttonStyle(.pressable)
        .help(help)
    }
}

struct MessageListView: View {
    let bot: Bot
    let chat: ChatStore

    /// Suppresses the entrance animation for the initial history load — only
    /// messages that arrive while the view is on screen animate in.
    @State private var animateChanges = false

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 14) {
                    if chat.messages.isEmpty, !chat.isLoadingHistory, case .idle = chat.streaming {
                        EmptyChatState(bot: bot)
                    }
                    ForEach(rows) { row in
                        switch row {
                        case .day(let date, let id):
                            DaySeparator(date: date).id(id)
                        case .time(let date, let id):
                            Text(date.formatted(.dateTime.hour().minute()))
                                .font(Tokens.timestamp)
                                .monospacedDigit()
                                .foregroundStyle(Tokens.textSecondary)
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 2)
                                .id(id)
                        case .message(let message):
                            MessageRow(bot: bot, message: message, chat: chat)
                                .id(message.id)
                                .transition(.opacity.combined(with: .offset(y: 8)))
                        }
                    }
                    if case .active(let text, let tool) = chat.streaming {
                        StreamingBubble(text: text, toolActivity: tool, since: chat.streamingSince ?? .now)
                            .id("streaming")
                    }
                    if case .failed(let error) = chat.streaming {
                        StreamErrorRow(message: error) { chat.clearError() }
                            .id("stream-error")
                    }
                    Color.clear.frame(height: 1).id("bottom")
                }
                .frame(maxWidth: 760)
                .frame(maxWidth: .infinity)
                .padding(.horizontal, 24)
                .padding(.vertical, 18)
                .animation(animateChanges ? .easeOut(duration: 0.18) : nil, value: chat.messages.count)
            }
            .defaultScrollAnchor(.bottom)
            .onChange(of: chat.messages.count) {
                withAnimation(animateChanges ? .easeOut(duration: 0.15) : nil) {
                    proxy.scrollTo("bottom", anchor: .bottom)
                }
            }
            .onChange(of: chat.streaming) {
                proxy.scrollTo("bottom", anchor: .bottom)
            }
            .task {
                // History load happens before first frame settles; enable
                // animations one tick later so the backlog doesn't cascade.
                try? await Task.sleep(for: .milliseconds(400))
                animateChanges = true
            }
        }
    }

    private enum Row: Identifiable {
        case day(Date, id: String)
        case time(Date, id: String)
        case message(Message)

        var id: String {
            switch self {
            case .day(_, let id): id
            case .time(_, let id): id
            case .message(let message): message.id
            }
        }
    }

    private var rows: [Row] {
        var result: [Row] = []
        var lastDay: DateComponents?
        var lastTime: Date?
        for message in chat.messages {
            if let created = message.createdAt {
                let day = Calendar.current.dateComponents([.year, .month, .day], from: created)
                if day != lastDay {
                    result.append(.day(created, id: "day-\(day.year ?? 0)-\(day.month ?? 0)-\(day.day ?? 0)"))
                    lastDay = day
                    lastTime = created
                } else if let last = lastTime, created.timeIntervalSince(last) > 20 * 60 {
                    result.append(.time(created, id: "time-\(created.timeIntervalSinceReferenceDate)"))
                    lastTime = created
                }
            }
            result.append(.message(message))
        }
        return result
    }
}

struct EmptyChatState: View {
    let bot: Bot

    var body: some View {
        VStack(spacing: 12) {
            AvatarView(bot: bot, size: 56)
            Text(bot.displayName)
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(Tokens.textPrimary)
            Text(bot.description.isEmpty ? bot.title : bot.description)
                .font(Tokens.chatBody)
                .foregroundStyle(Tokens.textSecondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 380)
        }
        .padding(.top, 120)
        .padding(.bottom, 40)
        .frame(maxWidth: .infinity)
    }
}

struct MessageRow: View {
    @Environment(AppModel.self) private var model
    let bot: Bot
    let message: Message
    let chat: ChatStore

    var body: some View {
        switch message.kind {
        case .routineCreated:
            // Backend uses this kind for all routine system chips ("Created
            // routine X", "Routine completed: X") with self-describing text.
            SystemChip(
                icon: "clock",
                text: message.text?.isEmpty == false
                    ? message.text!
                    : "Created routine \(message.routine?.name ?? "")"
            )
        case .taskReport:
            TaskReportCard(message: message)
                .frame(maxWidth: .infinity, alignment: .leading)
        case .approvalRequest:
            ApprovalBubble(message: message)
        default:
            if message.role == .user {
                UserBubble(text: message.text ?? "", attachments: message.attachments ?? [])
            } else {
                HStack {
                    VStack(alignment: .leading, spacing: 8) {
                        if let trace = chat.traces[message.id], trace.duration >= 2 {
                            ThinkingTraceView(trace: trace)
                        }
                        MessageBlocksView(text: message.text ?? "")
                    }
                    Spacer(minLength: 48)
                }
            }
        }
    }
}

struct AssistantBubble: View {
    let text: String

    var body: some View {
        HStack {
            ProseBubble(text: text.collapsedBlankLines)
                .frame(maxWidth: 560, alignment: .leading)
            Spacer(minLength: 48)
        }
    }
}

struct UserBubble: View {
    let text: String
    let attachments: [ImageAttachment]

    var body: some View {
        HStack {
            Spacer(minLength: 48)
            VStack(alignment: .trailing, spacing: 8) {
                ForEach(Array(attachments.enumerated()), id: \.offset) { _, attachment in
                    AttachedImageView(attachment: attachment)
                }
                if !text.isEmpty {
                    Text(text)
                        .font(Tokens.chatBody)
                        .lineSpacing(3)
                        .foregroundStyle(Tokens.userBubbleText)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
                .padding(.horizontal, attachments.isEmpty ? 15 : 5)
                .padding(.top, attachments.isEmpty ? 11 : 5)
                .padding(.bottom, attachments.isEmpty ? 11 : (text.isEmpty ? 5 : 11))
                .background(
                    RoundedRectangle(cornerRadius: Tokens.bubbleRadius, style: .continuous)
                        .fill(Tokens.userBubbleBackground)
                        .shadow(color: .black.opacity(0.25), radius: 6, y: 2)
                )
                .frame(maxWidth: 480, alignment: .trailing)
        }
    }
}

private struct AttachedImageView: View {
    let attachment: ImageAttachment

    private var image: NSImage? {
        guard let separator = attachment.url.firstIndex(of: ",") else { return nil }
        let encoded = attachment.url[attachment.url.index(after: separator)...]
        guard let data = Data(base64Encoded: String(encoded)) else { return nil }
        return NSImage(data: data)
    }

    var body: some View {
        Group {
            if let image {
                Image(nsImage: image)
                    .resizable()
                    .scaledToFit()
                    .frame(maxWidth: 360, maxHeight: 280)
            } else {
                HStack(spacing: 8) {
                    Image(systemName: "photo")
                    Text(attachment.filename ?? "Image")
                        .lineLimit(1)
                }
                .font(Tokens.sidebarBody)
                .foregroundStyle(Tokens.userBubbleText.opacity(0.65))
                .frame(width: 180, height: 72)
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .accessibilityLabel(attachment.filename ?? "Attached image")
    }
}

struct StreamingBubble: View {
    let text: String
    let toolActivity: String?
    var since: Date = .now

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if !text.isEmpty {
                if let toolActivity {
                    HStack(spacing: 6) {
                        PixelGridLoader(size: 10)
                        ShimmerText(text: toolActivity, font: Tokens.timestamp)
                    }
                    .padding(.leading, 4)
                    .transition(.opacity)
                }
                StreamingTextBubble(text: text)
            } else {
                HStack {
                    AgentWorkingIndicator(since: since, activity: toolActivity)
                    Spacer()
                }
            }
        }
    }
}

/// The live bubble while tokens stream in: fading appended text + caret.
struct StreamingTextBubble: View {
    let text: String

    var body: some View {
        HStack {
            HStack(alignment: .bottom, spacing: 3) {
                StreamedText(text: text)
                TimelineView(.periodic(from: .now, by: 0.5)) { context in
                    let visible = Int(context.date.timeIntervalSinceReferenceDate * 2) % 2 == 0
                    RoundedRectangle(cornerRadius: 1)
                        .fill(Tokens.textSecondary.opacity(visible ? 0.8 : 0))
                        .frame(width: 7, height: 14)
                }
            }
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
            .frame(maxWidth: 560, alignment: .leading)
            Spacer(minLength: 48)
        }
    }
}

struct StreamErrorRow: View {
    let message: String
    let dismiss: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle")
                .foregroundStyle(.orange)
            Text(message)
                .font(Tokens.sidebarBody)
                .foregroundStyle(Tokens.textSecondary)
                .lineLimit(2)
            Button("Dismiss", action: dismiss)
                .buttonStyle(.plain)
                .font(Tokens.sidebarName)
                .foregroundStyle(Tokens.textPrimary)
        }
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: Tokens.cardRadius, style: .continuous)
                .fill(Tokens.cardBackground)
        )
        .overlay(
            RoundedRectangle(cornerRadius: Tokens.cardRadius, style: .continuous)
                .strokeBorder(Tokens.hairline, lineWidth: 1)
        )
    }
}

struct SystemChip: View {
    let icon: String
    let text: String

    var body: some View {
        HStack(spacing: 5) {
            Image(systemName: icon).font(.system(size: 10))
            Text(text).font(Tokens.chip)
        }
        .foregroundStyle(Tokens.textSecondary)
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(Capsule().fill(Tokens.cardBackground))
        .frame(maxWidth: .infinity)
    }
}

extension Text {
    /// Markdown-rendering Text that degrades to plain text on parse failure.
    init(markdown: String) {
        if let attributed = try? AttributedString(
            markdown: markdown,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        ) {
            self.init(attributed)
        } else {
            self.init(verbatim: markdown)
        }
    }
}
