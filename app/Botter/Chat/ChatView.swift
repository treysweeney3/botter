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
                    if case .active(let text) = chat.streaming {
                        StreamingBubble(text: text, since: chat.streamingSince ?? .now)
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
            .defaultScrollAnchor(.bottom, for: .initialOffset)
            // Growth during a stream is followed by the scroll view itself.
            // Driving it imperatively from `chat.streaming` meant a hard
            // `scrollTo` on every token, which fought the word-reveal animation
            // and made the transcript stutter.
            .defaultScrollAnchor(.bottom, for: .sizeChanges)
            .onChange(of: chat.messages.count) {
                withAnimation(animateChanges ? .easeOut(duration: 0.15) : nil) {
                    proxy.scrollTo("bottom", anchor: .bottom)
                }
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

    @State private var isHovering = false

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
                    VStack(alignment: .leading, spacing: 6) {
                        // Steps are only visible here, so never hide a trace
                        // that has any; a bare think still needs to be slow
                        // enough to be worth mentioning.
                        if let trace = chat.traces[message.id],
                           !trace.steps.isEmpty || trace.duration >= 2 {
                            ThinkingTraceView(trace: trace)
                        }
                        MessageBlocksView(text: message.text ?? "")
                        MessageActionsRow(text: message.text ?? "")
                            .opacity(isHovering ? 1 : 0)
                            .allowsHitTesting(isHovering)
                            .animation(.easeOut(duration: 0.18), value: isHovering)
                    }
                    Spacer(minLength: 48)
                }
                .onHover { isHovering = $0 }
            }
        }
    }
}

/// Actions under a finished reply. Revealed on hover so a quiet transcript
/// stays quiet.
struct MessageActionsRow: View {
    let text: String

    @State private var copied = false

    var body: some View {
        HStack(spacing: 2) {
            Button {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(text, forType: .string)
                withAnimation(.easeOut(duration: 0.15)) { copied = true }
                Task {
                    try? await Task.sleep(for: .seconds(1.5))
                    withAnimation(.easeOut(duration: 0.3)) { copied = false }
                }
            } label: {
                Image(systemName: copied ? "checkmark" : "square.on.square")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(copied ? Color(hex: 0x22C55E) : Tokens.textSecondary)
                    .frame(width: 24, height: 22)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.pressable)
            .help(copied ? "Copied" : "Copy reply")
        }
        .padding(.leading, 2)
        .opacity(text.isEmpty ? 0 : 1)
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
                        // Stretch only to meet an image above it. On its own the
                        // text sets the bubble's width, so a short line does not
                        // sit in a 480pt slab of empty space.
                        .frame(
                            maxWidth: attachments.isEmpty ? nil : .infinity,
                            alignment: .leading
                        )
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

/// The in-flight turn: whatever prose has streamed since the last tool call,
/// with the "Thinking" indicator pinned underneath it for the whole turn.
///
/// The indicator never leaves until the reply lands. It used to be swapped out
/// the moment any text arrived and swapped back in when a tool call cleared
/// that text, which made the bottom of the transcript jump on every step.
struct StreamingBubble: View {
    let text: String
    var since: Date = .now

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if !text.isEmpty {
                HStack {
                    StreamingBlocksView(text: text)
                    Spacer(minLength: 48)
                }
            }
            HStack {
                AgentWorkingIndicator(since: since)
                Spacer()
            }
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
    /// Inline code gets the same monospaced-on-a-tint treatment the streaming
    /// renderer gives it, so a span does not restyle when the stream settles.
    init(markdown: String) {
        guard var attributed = try? AttributedString(
            markdown: markdown,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        ) else {
            self.init(verbatim: markdown)
            return
        }
        let codeRanges = attributed.runs
            .filter { $0.inlinePresentationIntent?.contains(.code) == true }
            .map(\.range)
        for range in codeRanges {
            attributed[range].font = .system(size: 13, design: .monospaced)
            attributed[range].backgroundColor = Color.white.opacity(0.07)
        }
        self.init(attributed)
    }
}
