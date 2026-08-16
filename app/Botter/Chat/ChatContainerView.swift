import SwiftUI
import BotterKit

/// Resolves the bot's default session, then hosts the chat. One instance per
/// selected bot (RootView applies `.id(bot.id)`).
struct ChatContainerView: View {
    @Environment(AppModel.self) private var model
    let entry: FeedEntry

    @State private var chat: ChatStore?
    @State private var resolutionError: String?

    var body: some View {
        Group {
            if let chat {
                ChatView(bot: entry.bot, chat: chat)
            } else if let resolutionError {
                ChatUnavailableView(message: resolutionError) {
                    self.resolutionError = nil
                    await resolveSession()
                }
            } else {
                ProgressView()
                    .controlSize(.small)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(Tokens.windowBackground)
            }
        }
        .task { await resolveSession() }
    }

    private func resolveSession() async {
        do {
            let sessionId: String
            if let existing = entry.bot.defaultSessionId {
                sessionId = existing
            } else {
                sessionId = try await model.client.createSession(botId: entry.bot.id).id
            }
            let store = ChatStore(client: model.client, sessionId: sessionId, botId: entry.bot.id)
            await store.loadHistory()
            chat = store
            model.roster.markSelectedRead()
        } catch {
            resolutionError = error.localizedDescription
        }
    }
}

struct ChatUnavailableView: View {
    let message: String
    let retry: () async -> Void

    var body: some View {
        VStack(spacing: 12) {
            Text("Can't open this conversation")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(Tokens.textPrimary)
            Text(message)
                .font(Tokens.sidebarBody)
                .foregroundStyle(Tokens.textSecondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 360)
            Button("Retry") {
                Task { await retry() }
            }
            .buttonStyle(.bordered)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Tokens.windowBackground)
    }
}
