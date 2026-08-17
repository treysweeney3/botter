import SwiftUI
import BotterKit

struct BotEditorSheet: View {
    enum Mode {
        case create
        case edit(Bot)
    }

    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let mode: Mode

    @State private var displayName = ""
    @State private var title = ""
    @State private var description = ""
    @State private var approvalBoundary = ""
    @State private var colorHex = BotPalette.colors[0].hex
    @State private var glyph: Glyph = .fallback
    @State private var isSaving = false
    @State private var isConfirmingPurge = false
    @State private var error: String?
    @State private var tab: Tab = .details
    @State private var memory: BotMemory?
    @State private var memoryError: String?
    @State private var isBrowsingSuggestions = false
    @State private var appliedSuggestion: RoleSuggestion?

    enum Tab: String, CaseIterable {
        case details = "Details"
        case memory = "Memory"
    }

    private var editedBot: Bot? {
        if case .edit(let bot) = mode { bot } else { nil }
    }

    private var trimmedName: String { displayName.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var trimmedTitle: String { title.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var trimmedDescription: String { description.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var trimmedBoundary: String { approvalBoundary.trimmingCharacters(in: .whitespacesAndNewlines) }

    /// botterd rejects a blank description — it is the whole role paragraph of
    /// the Botter's SOUL.md — so the form refuses to send one.
    private var isComplete: Bool {
        !trimmedName.isEmpty && !trimmedDescription.isEmpty
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            if editedBot != nil {
                Picker("", selection: $tab) {
                    ForEach(Tab.allCases, id: \.self) { Text($0.rawValue) }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .padding(.horizontal, 20)
                .padding(.bottom, 12)
            }
            Divider().overlay(Tokens.hairline)

            if tab == .memory, let bot = editedBot {
                memoryView(bot: bot)
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        if editedBot == nil {
                            suggestionsRow
                        }
                        labeledField("Name") {
                            TextField("e.g. Outbound SDR", text: $displayName)
                        }
                        labeledField("Job title") {
                            TextField("e.g. Sales Development Representative", text: $title)
                        }
                        labeledField(
                            "Description",
                            hint: "Becomes this Botter's brief: what it owns, how it works, what to report."
                        ) {
                            TextField("Describe the role in operational terms", text: $description, axis: .vertical)
                                .lineLimit(2...5)
                        }
                        avatarPickers
                        labeledField("Approval boundary") {
                            TextField(
                                "What this Botter must ask before doing (e.g. sending external email, spending money)",
                                text: $approvalBoundary, axis: .vertical
                            )
                            .lineLimit(2...5)
                        }
                    }
                    .padding(20)
                }
            }

            Divider().overlay(Tokens.hairline)
            // Pinned above the footer, not appended to the form: the form
            // scrolls, and an error rendered under the last field sits below
            // the fold — the save looked like it did nothing at all.
            if let error {
                errorBanner(error)
            }
            footer
        }
        .frame(width: 460, height: 560)
        .background(Tokens.sidebarBackground)
        .onAppear(perform: populate)
        .sheet(isPresented: $isBrowsingSuggestions) {
            SuggestionCatalogSheet(onPick: apply)
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            AvatarView(colorHex: colorHex, glyphName: glyph.rawValue, size: 40)
            VStack(alignment: .leading, spacing: 1) {
                Text(editedBot == nil ? "New Botter" : "Edit Botter")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(Tokens.textPrimary)
                Text(editedBot == nil
                     ? "A Botter is a coworker with one job"
                     : editedBot!.slug)
                    .font(Tokens.sidebarBody)
                    .foregroundStyle(Tokens.textSecondary)
            }
            Spacer()
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 16)
    }

    /// Entry point to the catalog. The roles themselves live in their own sheet:
    /// there are far too many to sit inline above the form.
    private var suggestionsRow: some View {
        Button {
            isBrowsingSuggestions = true
        } label: {
            HStack(spacing: 10) {
                Image(systemName: "square.grid.2x2")
                    .font(.system(size: 13))
                    .foregroundStyle(Tokens.textSecondary)
                VStack(alignment: .leading, spacing: 1) {
                    Text(appliedSuggestion.map { "Started from \($0.name)" } ?? "Start from a role")
                        .font(Tokens.sidebarName)
                        .foregroundStyle(Tokens.textPrimary)
                    Text("\(SuggestionCatalog.all.count) roles across sales, finance, ops, support, research…")
                        .font(.system(size: 11))
                        .foregroundStyle(Tokens.textSecondary)
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.right")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(Tokens.textSecondary)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Tokens.cardBackground)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .stroke(Tokens.hairline, lineWidth: 1)
            )
            .contentShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        }
        .buttonStyle(.plain)
    }

    private func apply(_ role: RoleSuggestion) {
        appliedSuggestion = role
        displayName = role.name
        title = role.title
        description = role.description
        approvalBoundary = role.approvalBoundary
    }

    private var avatarPickers: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Avatar")
                .font(Tokens.chip)
                .foregroundStyle(Tokens.textSecondary)
            HStack(spacing: 8) {
                ForEach(BotPalette.colors, id: \.hex) { entry in
                    Button {
                        colorHex = entry.hex
                    } label: {
                        Circle()
                            .fill(BotPalette.color(for: entry.hex))
                            .frame(width: 22, height: 22)
                            .overlay {
                                if colorHex == entry.hex {
                                    Circle().stroke(.white, lineWidth: 2).padding(-3)
                                }
                            }
                    }
                    .buttonStyle(.plain)
                    .help(entry.name)
                }
            }
            HStack(spacing: 8) {
                ForEach(Glyph.allCases) { candidate in
                    Button {
                        glyph = candidate
                    } label: {
                        AvatarView(
                            colorHex: glyph == candidate ? colorHex : "#3A3A3C",
                            glyphName: candidate.rawValue,
                            size: 30
                        )
                        .overlay {
                            if glyph == candidate {
                                Circle().stroke(.white, lineWidth: 2).padding(-2)
                            }
                        }
                    }
                    .buttonStyle(.plain)
                    .help(candidate.label)
                    .accessibilityLabel(candidate.label)
                }
            }
        }
    }

    @ViewBuilder
    private func errorBanner(_ message: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 12))
                .foregroundStyle(.red)
            Text(message)
                .font(Tokens.sidebarBody)
                .foregroundStyle(Tokens.textPrimary)
                .textSelection(.enabled)
                // Bounded so a long botterd message cannot crowd out the form.
                .lineLimit(4)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .background(Color.red.opacity(0.12))
    }

    private var footer: some View {
        HStack {
            if let bot = editedBot {
                Button(bot.archived ? "Unarchive" : "Archive") {
                    Task { await archive(bot, archived: !bot.archived) }
                }
                .buttonStyle(.bordered)
                Button("Delete…", role: .destructive) {
                    isConfirmingPurge = true
                }
                .buttonStyle(.bordered)
                .confirmationDialog(
                    "Delete \(bot.displayName)? This permanently removes its profile, memory, and history.",
                    isPresented: $isConfirmingPurge
                ) {
                    Button("Delete permanently", role: .destructive) {
                        Task { await purge(bot) }
                    }
                }
            }
            Spacer()
            if isSaving {
                ProgressView().controlSize(.small)
                // Creating a Botter clones a Hermes profile and opens its first
                // session. That is tens of seconds of work, so the wait gets a
                // label instead of a button that is merely dimmed.
                Text(editedBot == nil ? "Creating…" : "Saving…")
                    .font(Tokens.sidebarBody)
                    .foregroundStyle(Tokens.textSecondary)
            }
            // Stays enabled while saving: a create runs for tens of seconds and
            // trapping the sheet open for that long is worse than letting it
            // close while botterd finishes.
            Button("Cancel") { dismiss() }
                .keyboardShortcut(.cancelAction)
            Button(editedBot == nil ? "Create Botter" : "Save") {
                Task { await save() }
            }
            .keyboardShortcut(.defaultAction)
            .buttonStyle(.borderedProminent)
            .disabled(!isComplete || isSaving)
            .help(isComplete ? "" : "A Botter needs a name and a description of its job")
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 14)
    }

    @ViewBuilder
    private func memoryView(bot: Bot) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let memory {
                    memorySection("MEMORY.md", memory.memory)
                    memorySection("USER.md", memory.user)
                } else if let memoryError {
                    Text(memoryError)
                        .font(Tokens.sidebarBody)
                        .foregroundStyle(.red)
                } else {
                    ProgressView().controlSize(.small)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(20)
        }
        .task {
            do {
                memory = try await model.client.memory(botId: bot.id)
            } catch {
                memoryError = error.localizedDescription
            }
        }
    }

    @ViewBuilder
    private func memorySection(_ title: String, _ content: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(Tokens.chip)
                .foregroundStyle(Tokens.textSecondary)
            Text(markdown: content.isEmpty ? "*Empty*" : content)
                .font(.system(size: 12, design: .monospaced))
                .foregroundStyle(Tokens.textPrimary)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(12)
                .background(
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .fill(Tokens.cardBackground)
                )
        }
    }

    @ViewBuilder
    private func labeledField(
        _ label: String,
        hint: String? = nil,
        @ViewBuilder field: () -> some View
    ) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(Tokens.chip)
                .foregroundStyle(Tokens.textSecondary)
            field()
                .textFieldStyle(.plain)
                .font(Tokens.chatBody)
                .foregroundStyle(Tokens.textPrimary)
                .padding(.horizontal, 10)
                .padding(.vertical, 8)
                .background(
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .fill(Tokens.cardBackground)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .stroke(Tokens.hairline, lineWidth: 1)
                )
            if let hint {
                Text(hint)
                    .font(.system(size: 11))
                    .foregroundStyle(Tokens.textSecondary)
            }
        }
    }

    private func populate() {
        guard let bot = editedBot else { return }
        displayName = bot.displayName
        title = bot.title
        description = bot.description
        approvalBoundary = bot.approvalBoundary ?? ""
        colorHex = bot.avatarColor
        glyph = Glyph.resolve(bot.avatarGlyph)
    }

    private func save() async {
        guard isComplete else {
            error = "Give this Botter a name and a description of its job."
            return
        }
        isSaving = true
        error = nil
        defer { isSaving = false }
        do {
            if let bot = editedBot {
                _ = try await model.client.updateBot(id: bot.id, fields: [
                    "display_name": trimmedName,
                    "title": trimmedTitle.isEmpty ? trimmedName : trimmedTitle,
                    "description": trimmedDescription,
                    "avatar_color": colorHex,
                    "avatar_glyph": glyph.rawValue,
                    "approval_boundary": trimmedBoundary.isEmpty ? nil : trimmedBoundary,
                ])
            } else {
                let draft = BotterClient.BotDraft(
                    slug: Slug.make(trimmedName),
                    displayName: trimmedName,
                    title: trimmedTitle.isEmpty ? trimmedName : trimmedTitle,
                    description: trimmedDescription,
                    avatarColor: colorHex,
                    avatarGlyph: glyph.rawValue,
                    approvalBoundary: trimmedBoundary.isEmpty ? nil : trimmedBoundary
                )
                let bot = try await model.client.createBot(draft)
                model.roster.selectedBotId = bot.id
            }
            await model.roster.refresh()
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func archive(_ bot: Bot, archived: Bool) async {
        do {
            _ = try await model.client.updateBot(id: bot.id, fields: ["archived": archived])
            await model.roster.refresh()
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func purge(_ bot: Bot) async {
        do {
            try await model.client.deleteBot(id: bot.id, purge: true)
            model.roster.selectedBotId = nil
            await model.roster.refresh()
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
    }

}

/// Minimal wrapping layout for suggestion chips.
struct FlowLayout: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        arrange(proposal: proposal, subviews: subviews).size
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let positions = arrange(proposal: proposal, subviews: subviews).positions
        for (subview, position) in zip(subviews, positions) {
            subview.place(
                at: CGPoint(x: bounds.minX + position.x, y: bounds.minY + position.y),
                proposal: .unspecified
            )
        }
    }

    private func arrange(proposal: ProposedViewSize, subviews: Subviews) -> (size: CGSize, positions: [CGPoint]) {
        let maxWidth = proposal.width ?? .infinity
        var positions: [CGPoint] = []
        var x: CGFloat = 0, y: CGFloat = 0, rowHeight: CGFloat = 0, totalWidth: CGFloat = 0
        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x > 0, x + size.width > maxWidth {
                x = 0
                y += rowHeight + spacing
                rowHeight = 0
            }
            positions.append(CGPoint(x: x, y: y))
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
            totalWidth = max(totalWidth, x - spacing)
        }
        return (CGSize(width: totalWidth, height: y + rowHeight), positions)
    }
}
