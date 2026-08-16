import Foundation
import Observation

/// Sidebar state: the roster feed, selection, search, and live refresh from
/// the events firehose.
@MainActor
@Observable
public final class RosterStore {
    public private(set) var entries: [FeedEntry] = []
    public private(set) var isLoading = false
    public private(set) var lastError: String?
    public var searchText = ""
    public var selectedBotId: String?

    private let client: BotterClient

    public init(client: BotterClient) {
        self.client = client
    }

    public var activeEntries: [FeedEntry] {
        filtered(entries.filter { !$0.bot.archived })
    }

    public var archivedEntries: [FeedEntry] {
        filtered(entries.filter { $0.bot.archived })
    }

    public var selectedEntry: FeedEntry? {
        entries.first { $0.bot.id == selectedBotId }
    }

    public var totalUnread: Int {
        entries.reduce(0) { $0 + $1.unreadCount }
    }

    private func filtered(_ list: [FeedEntry]) -> [FeedEntry] {
        let query = searchText.trimmingCharacters(in: .whitespaces)
        guard !query.isEmpty else { return list }
        return list.filter {
            $0.bot.displayName.localizedCaseInsensitiveContains(query)
                || $0.bot.title.localizedCaseInsensitiveContains(query)
        }
    }

    public func refresh() async {
        isLoading = entries.isEmpty
        defer { isLoading = false }
        do {
            entries = try await client.bots()
                .sorted { ($0.previewAt ?? .distantPast) > ($1.previewAt ?? .distantPast) }
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
    }

    /// Firehose dispatch — the app-level event loop calls this for every event.
    public func handle(_ event: ServerEvent) async {
        switch event {
        case .feedUpdated, .botUpdated, .routineFired:
            await refresh()
        default:
            break
        }
    }

    public func markSelectedRead() {
        guard let id = selectedBotId,
              let index = entries.firstIndex(where: { $0.bot.id == id }),
              entries[index].unreadCount > 0
        else { return }
        entries[index].unreadCount = 0
    }
}
