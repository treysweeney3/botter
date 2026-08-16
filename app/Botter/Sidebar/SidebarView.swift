import SwiftUI
import BotterKit

struct SidebarView: View {
    @Environment(AppModel.self) private var model
    @State private var archivedExpanded = false
    @State private var messageResults: [Message]?
    @State private var isShowingConnections = false
    @FocusState private var listFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            // Header row: clears the traffic lights, hosts the new-bot button,
            // and doubles as the window drag area.
            HStack {
                Spacer()
                Button {
                    model.isCreatingBot = true
                } label: {
                    Image(systemName: "plus")
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(Tokens.textSecondary)
                        .frame(width: 28, height: 28)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.pressable)
                .help("New Botter (⌘N)")
            }
            .padding(.horizontal, 10)
            .frame(height: 44)
            .contentShape(Rectangle())
            .gesture(WindowDragGesture())

            searchField
                .padding(.horizontal, 12)
                .padding(.bottom, 8)

            if model.approvals.badgeCount > 0 {
                ApprovalsPill(count: model.approvals.badgeCount)
                    .padding(.horizontal, 12)
                    .padding(.bottom, 8)
            }

            ScrollView {
                LazyVStack(spacing: 2) {
                    if let messageResults {
                        SectionLabel("Messages")
                        if messageResults.isEmpty {
                            Text("No messages found")
                                .font(Tokens.sidebarBody)
                                .foregroundStyle(Tokens.textSecondary)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.horizontal, 14)
                                .padding(.vertical, 6)
                        }
                        ForEach(messageResults) { message in
                            MessageSearchRow(message: message)
                                .onTapGesture { select(message.botId) }
                        }
                        SectionLabel("Botters").padding(.top, 8)
                    }

                    ForEach(model.roster.activeEntries) { entry in
                        BotRow(entry: entry, isSelected: model.roster.selectedBotId == entry.id) {
                            select(entry.id)
                        }
                    }

                    if !model.roster.archivedEntries.isEmpty {
                        Button {
                            withAnimation(.easeOut(duration: 0.18)) { archivedExpanded.toggle() }
                        } label: {
                            HStack(spacing: 5) {
                                Text("Archived")
                                Image(systemName: "chevron.right")
                                    .font(.system(size: 8, weight: .semibold))
                                    .rotationEffect(.degrees(archivedExpanded ? 90 : 0))
                                Spacer()
                            }
                            .font(Tokens.chip)
                            .foregroundStyle(Tokens.textSecondary)
                            .padding(.horizontal, 14)
                            .padding(.vertical, 8)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .padding(.top, 6)

                        if archivedExpanded {
                            ForEach(model.roster.archivedEntries) { entry in
                                BotRow(entry: entry, isSelected: model.roster.selectedBotId == entry.id) {
                                    select(entry.id)
                                }
                                .opacity(0.55)
                            }
                        }
                    }
                }
                .padding(.horizontal, 8)
                .padding(.bottom, 8)
            }
            .focusable()
            .focusEffectDisabled()
            .focused($listFocused)
            .onMoveCommand { direction in
                moveSelection(direction == .up ? -1 : direction == .down ? 1 : 0)
            }

            UserFooter { isShowingConnections = true }
        }
        .background(Tokens.sidebarBackground)
        .sheet(isPresented: $isShowingConnections) {
            ConnectionsSheet()
                .environment(model)
        }
    }

    private func select(_ botId: String) {
        model.roster.selectedBotId = botId
        model.roster.markSelectedRead()
    }

    private func moveSelection(_ delta: Int) {
        guard delta != 0 else { return }
        let visible = model.roster.activeEntries + (archivedExpanded ? model.roster.archivedEntries : [])
        guard !visible.isEmpty else { return }
        let current = visible.firstIndex { $0.id == model.roster.selectedBotId } ?? -1
        let next = min(max(current + delta, 0), visible.count - 1)
        select(visible[next].id)
    }

    private var searchField: some View {
        @Bindable var roster = model.roster
        return HStack(spacing: 6) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 12))
                .foregroundStyle(Tokens.textSecondary)
            TextField("Search", text: $roster.searchText)
                .textFieldStyle(.plain)
                .font(Tokens.sidebarBody)
                .foregroundStyle(Tokens.textPrimary)
                .onSubmit {
                    let query = roster.searchText.trimmingCharacters(in: .whitespaces)
                    guard !query.isEmpty else { return }
                    Task {
                        messageResults = (try? await model.client.search(query)) ?? []
                    }
                }
                .onChange(of: roster.searchText) {
                    if roster.searchText.isEmpty { messageResults = nil }
                }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(Capsule().fill(Tokens.cardBackground))
        .overlay(Capsule().strokeBorder(Tokens.hairline, lineWidth: 1))
    }
}

struct SectionLabel: View {
    let text: String

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        Text(text)
            .font(Tokens.chip)
            .foregroundStyle(Tokens.textSecondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 14)
            .padding(.vertical, 4)
    }
}

struct MessageSearchRow: View {
    @Environment(AppModel.self) private var model
    let message: Message

    @State private var isHovered = false

    private var botName: String {
        model.roster.entries.first { $0.id == message.botId }?.displayName ?? "Unknown Botter"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(botName)
                    .font(Tokens.sidebarName)
                    .foregroundStyle(Tokens.textPrimary)
                Spacer()
                if let at = message.createdAt {
                    Text(at.relativeShort)
                        .font(Tokens.timestamp)
                        .monospacedDigit()
                        .foregroundStyle(Tokens.textSecondary)
                }
            }
            Text(message.text ?? "")
                .font(Tokens.sidebarBody)
                .foregroundStyle(Tokens.textSecondary)
                .lineLimit(2)
        }
        .padding(.vertical, 6)
        .padding(.horizontal, 8)
        .background(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(Tokens.cardBackground.opacity(isHovered ? 0.6 : 0))
        )
        .onHover { isHovered = $0 }
        .contentShape(Rectangle())
    }
}

struct BotRow: View {
    let entry: FeedEntry
    let isSelected: Bool
    let select: () -> Void

    @State private var isHovered = false

    var body: some View {
        Button(action: select) {
            HStack(spacing: 10) {
                AvatarView(colorHex: entry.avatarColor, glyphName: entry.avatarGlyph)

                VStack(alignment: .leading, spacing: 2) {
                    HStack(alignment: .firstTextBaseline) {
                        Text(entry.displayName)
                            .font(Tokens.sidebarName)
                            .foregroundStyle(Tokens.textPrimary)
                            .lineLimit(1)
                        Spacer(minLength: 6)
                        if let at = entry.previewAt {
                            Text(at.relativeShort)
                                .font(Tokens.timestamp)
                                .monospacedDigit()
                                .foregroundStyle(Tokens.textSecondary)
                        }
                    }
                    HStack(spacing: 6) {
                        Text(entry.preview ?? entry.title)
                            .font(Tokens.sidebarBody)
                            .foregroundStyle(Tokens.textSecondary)
                            .lineLimit(1)
                        Spacer(minLength: 0)
                        if entry.unreadCount > 0 {
                            Circle()
                                .fill(BotPalette.color(for: entry.avatarColor))
                                .frame(width: 7, height: 7)
                                .transition(.scale.combined(with: .opacity))
                                .accessibilityLabel("\(entry.unreadCount) unread")
                        }
                    }
                }
            }
            .animation(.easeOut(duration: 0.2), value: entry.unreadCount)
            .padding(.vertical, 7)
            .padding(.horizontal, 8)
            .background(
                RoundedRectangle(cornerRadius: 9, style: .continuous)
                    .fill(rowFill)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 9, style: .continuous)
                    .strokeBorder(isSelected ? Tokens.hairline : .clear, lineWidth: 1)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { isHovered = $0 }
        .accessibilityLabel("\(entry.displayName), \(entry.title)")
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }

    private var rowFill: Color {
        if isSelected { return Tokens.cardBackground }
        if isHovered { return Tokens.cardBackground.opacity(0.55) }
        return .clear
    }
}

struct ApprovalsPill: View {
    @Environment(AppModel.self) private var model
    let count: Int

    @State private var isShowingList = false

    var body: some View {
        Button {
            isShowingList = true
        } label: {
            HStack(spacing: 8) {
                Image(systemName: "checkmark.shield")
                    .font(.system(size: 12, weight: .semibold))
                Text(count == 1 ? "1 approval waiting" : "\(count) approvals waiting")
                    .font(Tokens.sidebarName)
                Spacer()
                Image(systemName: "chevron.right")
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundStyle(Tokens.textSecondary)
            }
            .foregroundStyle(Tokens.textPrimary)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(Capsule().fill(Tokens.cardBackground))
            .overlay(Capsule().strokeBorder(Tokens.hairline, lineWidth: 1))
            .contentShape(Capsule())
        }
        .buttonStyle(.pressable)
        .popover(isPresented: $isShowingList, arrowEdge: .bottom) {
            ApprovalsListView()
                .environment(model)
        }
    }
}

struct ApprovalsListView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Pending approvals")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(Tokens.textPrimary)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
            Divider().overlay(Tokens.hairline)
            if model.approvals.pending.isEmpty {
                Text("All clear")
                    .font(Tokens.sidebarBody)
                    .foregroundStyle(Tokens.textSecondary)
                    .padding(14)
            } else {
                ScrollView {
                    VStack(spacing: 10) {
                        ForEach(model.approvals.pending) { approval in
                            ApprovalListRow(approval: approval)
                        }
                    }
                    .padding(12)
                }
                .frame(maxHeight: 320)
            }
        }
        .frame(width: 330)
        .background(Tokens.sidebarBackground)
    }
}

struct ApprovalListRow: View {
    @Environment(AppModel.self) private var model
    let approval: Approval

    private var botName: String {
        model.roster.entries.first { $0.id == approval.botId }?.displayName ?? approval.botId
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                if let entry = model.roster.entries.first(where: { $0.id == approval.botId }) {
                    AvatarView(colorHex: entry.avatarColor, glyphName: entry.avatarGlyph, size: 18)
                }
                Text(botName)
                    .font(Tokens.sidebarName)
                    .foregroundStyle(Tokens.textPrimary)
                Spacer()
                if let at = approval.requestedAt {
                    Text(at.relativeShort)
                        .font(Tokens.timestamp)
                        .monospacedDigit()
                        .foregroundStyle(Tokens.textSecondary)
                }
            }
            Text(approval.summary)
                .font(Tokens.sidebarBody)
                .foregroundStyle(Tokens.textSecondary)
                .lineLimit(3)
            HStack(spacing: 6) {
                Button("Approve") { decide(.once) }
                    .buttonStyle(.borderedProminent)
                    .tint(.white)
                    .foregroundStyle(.black)
                Button("For this task") { decide(.session) }
                    .buttonStyle(.bordered)
                Button("Always") { decide(.always) }
                    .buttonStyle(.bordered)
                Button("Deny") { decide(.deny) }
                    .buttonStyle(.bordered)
                    .tint(.red)
            }
            .controlSize(.small)
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

    private func decide(_ decision: ApprovalDecision) {
        Task {
            await model.approvals.decide(runId: approval.runId, decision: decision)
            model.updateDockBadge()
        }
    }
}

struct UserFooter: View {
    let openConnections: () -> Void

    @State private var isHovered = false

    var body: some View {
        Button(action: openConnections) {
            HStack(spacing: 10) {
                ZStack {
                    Circle().fill(Tokens.cardBackground)
                    Text(initials)
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(Tokens.textPrimary)
                }
                .frame(width: 28, height: 28)
                .overlay(Circle().strokeBorder(Tokens.hairline, lineWidth: 1))

                VStack(alignment: .leading, spacing: 0) {
                    Text(NSFullUserName())
                        .font(Tokens.sidebarName)
                        .foregroundStyle(Tokens.textPrimary)
                    Text("Connections & Config")
                        .font(Tokens.timestamp)
                        .foregroundStyle(Tokens.textSecondary)
                }
                Spacer()
                Image(systemName: "chevron.up")
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundStyle(Tokens.textSecondary)
                    .opacity(isHovered ? 1 : 0.4)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(Tokens.cardBackground.opacity(isHovered ? 0.5 : 0))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { isHovered = $0 }
        .overlay(alignment: .top) {
            Rectangle().fill(Tokens.hairline).frame(height: 1)
        }
        .help("Manage connections and Hermes configuration")
    }

    private var initials: String {
        let parts = NSFullUserName().split(separator: " ")
        let letters = parts.prefix(2).compactMap(\.first)
        return letters.isEmpty ? "U" : String(letters).uppercased()
    }
}

extension Date {
    /// Compact relative timestamp for sidebar rows ("2m", "3h", "Tue", "8/12").
    var relativeShort: String {
        let interval = Date.now.timeIntervalSince(self)
        if interval < 60 { return "now" }
        if interval < 3600 { return "\(Int(interval / 60))m" }
        if interval < 86_400 { return "\(Int(interval / 3600))h" }
        if interval < 7 * 86_400 {
            return self.formatted(.dateTime.weekday(.abbreviated))
        }
        return self.formatted(.dateTime.month(.defaultDigits).day())
    }
}
