import SwiftUI
import BotterKit

@main
struct BotterApp: App {
    @State private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(model)
                .preferredColorScheme(.dark)
                .frame(minWidth: 800, minHeight: 520)
        }
        .windowStyle(.hiddenTitleBar)
        .commands {
            CommandGroup(after: .newItem) {
                Button("New Botter") { model.isCreatingBot = true }
                    .keyboardShortcut("n", modifiers: .command)
            }
        }

        Window("Design Gallery", id: "token-gallery") {
            TokenGalleryView()
                .preferredColorScheme(.dark)
        }
        .keyboardShortcut("d", modifiers: [.command, .shift])

        Settings {
            SettingsView()
                .environment(model)
                .preferredColorScheme(.dark)
        }
    }
}

/// App-level composition root: one client, shared stores, one firehose loop.
@MainActor
@Observable
final class AppModel {
    let client: BotterClient
    let roster: RosterStore
    let approvals: ApprovalStore
    // App-level so firehose events reach it even while the sheet is closed;
    // it stays lazy — nothing loads until the Connections sheet first opens.
    let connections: ConnectionsStore

    var isCreatingBot = false

    @ObservationIgnored nonisolated(unsafe) private var eventsTask: Task<Void, Never>?

    init(client: BotterClient = BotterClient()) {
        self.client = client
        self.roster = RosterStore(client: client)
        self.approvals = ApprovalStore(client: client)
        self.connections = ConnectionsStore(client: client)
    }

    deinit {
        eventsTask?.cancel()
    }

    func start() async {
        startEventLoop()
        AppNotifications.requestPermission()
        await roster.refresh()
        await approvals.refresh()
        updateDockBadge()
    }

    private func startEventLoop() {
        guard eventsTask == nil else { return }
        eventsTask = Task { [weak self, client] in
            for await event in client.eventsStream() {
                guard let self else { return }
                await self.roster.handle(event)
                self.approvals.apply(event)
                self.connections.apply(event)
                if case .approvalPending(let approval) = event {
                    let botName = self.roster.entries
                        .first { $0.id == approval.botId }?.displayName ?? "A Botter"
                    AppNotifications.postApproval(botName: botName, summary: approval.summary)
                }
                self.updateDockBadge()
            }
        }
    }

    func updateDockBadge() {
        let count = approvals.badgeCount
        NSApp.dockTile.badgeLabel = count > 0 ? String(count) : nil
    }
}
