import SwiftUI
import BotterKit

struct RoutinesPanel: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let bot: Bot

    @State private var store: RoutineStore?
    @State private var editing: RoutineDraft?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                AvatarView(bot: bot, size: 28)
                VStack(alignment: .leading, spacing: 0) {
                    Text("Routines")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(Tokens.textPrimary)
                    Text(bot.displayName)
                        .font(Tokens.sidebarBody)
                        .foregroundStyle(Tokens.textSecondary)
                }
                Spacer()
                Button {
                    editing = RoutineDraft()
                } label: {
                    Label("New routine", systemImage: "plus")
                        .font(Tokens.sidebarName)
                }
                .buttonStyle(.bordered)
                Button("Done") { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }
            .padding(.horizontal, 18)
            .padding(.vertical, 14)
            Divider().overlay(Tokens.hairline)

            if let store {
                if store.routines.isEmpty {
                    VStack(spacing: 8) {
                        Image(systemName: "clock")
                            .font(.system(size: 22))
                            .foregroundStyle(Tokens.textSecondary)
                        Text("No routines yet")
                            .font(Tokens.sidebarName)
                            .foregroundStyle(Tokens.textPrimary)
                        Text("Routines run on a schedule and report into this Botter's thread.")
                            .font(Tokens.sidebarBody)
                            .foregroundStyle(Tokens.textSecondary)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    ScrollView {
                        VStack(spacing: 10) {
                            ForEach(store.routines) { routine in
                                RoutineRow(routine: routine, store: store) {
                                    editing = RoutineDraft(routine: routine)
                                }
                            }
                        }
                        .padding(16)
                    }
                }
                if let error = store.lastError {
                    Text(error)
                        .font(Tokens.timestamp)
                        .foregroundStyle(.red)
                        .padding(.horizontal, 18)
                        .padding(.bottom, 10)
                }
            } else {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(width: 520, height: 460)
        .background(Tokens.sidebarBackground)
        .task {
            let created = RoutineStore(client: model.client, botId: bot.id)
            await created.refresh()
            store = created
        }
        .sheet(item: $editing) { draft in
            RoutineEditorSheet(draft: draft, store: store)
        }
    }
}

struct RoutineRow: View {
    let routine: Routine
    let store: RoutineStore
    let edit: () -> Void

    @State private var justQueued = false

    var body: some View {
        HStack(spacing: 12) {
            Circle()
                .fill(statusColor)
                .frame(width: 8, height: 8)
            VStack(alignment: .leading, spacing: 2) {
                Text(routine.name)
                    .font(Tokens.sidebarName)
                    .foregroundStyle(Tokens.textPrimary)
                Text(CronText.describe(routine.schedule) + lastRunSuffix)
                    .font(Tokens.sidebarBody)
                    .foregroundStyle(Tokens.textSecondary)
                    .lineLimit(1)
            }
            Spacer()
            if justQueued {
                Text("Queued")
                    .font(Tokens.chip)
                    .foregroundStyle(Tokens.textSecondary)
            } else {
                Button {
                    justQueued = true
                    Task {
                        await store.runNow(routine)
                        try? await Task.sleep(for: .seconds(4))
                        justQueued = false
                    }
                } label: {
                    Image(systemName: "play.circle")
                }
                .buttonStyle(.plain)
                .foregroundStyle(Tokens.textSecondary)
                .help("Run now (fires within a few minutes)")
            }
            Toggle("", isOn: Binding(
                get: { !routine.paused },
                set: { enabled in Task { await store.setPaused(routine, paused: !enabled) } }
            ))
            .toggleStyle(.switch)
            .controlSize(.mini)
            .labelsHidden()
            .help(routine.paused ? "Resume" : "Pause")
            Button {
                edit()
            } label: {
                Image(systemName: "chevron.right")
                    .font(.system(size: 11, weight: .semibold))
            }
            .buttonStyle(.plain)
            .foregroundStyle(Tokens.textSecondary)
        }
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: Tokens.cardRadius, style: .continuous)
                .fill(Tokens.cardBackground)
        )
        .overlay(
            RoundedRectangle(cornerRadius: Tokens.cardRadius, style: .continuous)
                .stroke(Tokens.hairline, lineWidth: 1)
        )
        .opacity(routine.paused ? 0.6 : 1)
    }

    private var statusColor: Color {
        if routine.paused { return Tokens.textSecondary }
        switch routine.lastStatus {
        case "ok": return Color(hex: 0x22C55E)
        case nil: return Tokens.textSecondary
        default: return Color(hex: 0xEF4444)
        }
    }

    private var lastRunSuffix: String {
        guard let at = routine.lastRunAt else { return "" }
        return "  ·  last run \(at.relativeShort)"
    }
}

/// Editing model for the sheet — nil id means "create".
struct RoutineDraft: Identifiable {
    var id: String
    var routineId: String?
    var name: String
    var schedule: String
    var prompt: String

    init() {
        self.id = "new"
        self.routineId = nil
        self.name = ""
        self.schedule = "0 9 * * *"
        self.prompt = ""
    }

    init(routine: Routine) {
        self.id = routine.id
        self.routineId = routine.id
        self.name = routine.name
        self.schedule = routine.schedule
        self.prompt = routine.prompt
    }
}

struct RoutineEditorSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State var draft: RoutineDraft
    let store: RoutineStore?

    @State private var isSaving = false

    private static let presets: [(label: String, cron: String)] = [
        ("Hourly", "0 * * * *"),
        ("Daily 9am", "0 9 * * *"),
        ("Weekday mornings", "0 9 * * 1-5"),
        ("Weekly Monday", "0 9 * * 1"),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(draft.routineId == nil ? "New Routine" : "Edit Routine")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(Tokens.textPrimary)

            field("Name") {
                TextField("e.g. Overnight outbound", text: $draft.name)
            }

            VStack(alignment: .leading, spacing: 8) {
                Text("Schedule")
                    .font(Tokens.chip)
                    .foregroundStyle(Tokens.textSecondary)
                FlowLayout(spacing: 6) {
                    ForEach(Self.presets, id: \.cron) { preset in
                        Button(preset.label) { draft.schedule = preset.cron }
                            .buttonStyle(.plain)
                            .font(Tokens.sidebarBody)
                            .foregroundStyle(draft.schedule == preset.cron ? Tokens.userBubbleText : Tokens.textPrimary)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 5)
                            .background(
                                Capsule().fill(draft.schedule == preset.cron ? Color.white : Tokens.cardBackground)
                            )
                            .overlay(Capsule().stroke(Tokens.hairline, lineWidth: 1))
                    }
                }
                field(nil) {
                    TextField("cron expression", text: $draft.schedule)
                        .font(.system(size: 13, design: .monospaced))
                }
                Text(CronText.describe(draft.schedule))
                    .font(Tokens.timestamp)
                    .foregroundStyle(Tokens.textSecondary)
            }

            field("Prompt") {
                TextField("What should the Botter do each run?", text: $draft.prompt, axis: .vertical)
                    .lineLimit(3...6)
            }

            HStack {
                if let store, let routineId = draft.routineId,
                   let routine = store.routines.first(where: { $0.id == routineId }) {
                    Button("Delete", role: .destructive) {
                        Task {
                            await store.delete(routine)
                            dismiss()
                        }
                    }
                    .buttonStyle(.bordered)
                }
                Spacer()
                Button("Cancel") { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button(draft.routineId == nil ? "Create" : "Save") {
                    Task { await save() }
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(draft.name.isEmpty || draft.prompt.isEmpty || isSaving)
            }
        }
        .padding(20)
        .frame(width: 420)
        .background(Tokens.sidebarBackground)
    }

    @ViewBuilder
    private func field(_ label: String?, @ViewBuilder content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            if let label {
                Text(label)
                    .font(Tokens.chip)
                    .foregroundStyle(Tokens.textSecondary)
            }
            content()
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
        }
    }

    private func save() async {
        guard let store else { return }
        isSaving = true
        defer { isSaving = false }
        if let routineId = draft.routineId {
            _ = try? await store.update(
                id: routineId,
                fields: ["name": draft.name, "schedule": draft.schedule, "prompt": draft.prompt]
            )
            await store.refresh()
        } else {
            await store.create(name: draft.name, schedule: draft.schedule, prompt: draft.prompt)
        }
        dismiss()
    }
}
