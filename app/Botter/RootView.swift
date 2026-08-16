import SwiftUI
import BotterKit

/// Custom two-column layout: the macOS split view draws the sidebar as a
/// floating glass panel, which fights the flat reference design. A plain
/// HStack gives the flush full-height column the product calls for.
struct RootView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        HStack(spacing: 0) {
            SidebarView()
                .frame(width: Tokens.sidebarWidth)

            Rectangle()
                .fill(Tokens.hairline)
                .frame(width: 1)

            Group {
                if let entry = model.roster.selectedEntry {
                    ChatContainerView(entry: entry)
                        .id(entry.bot.id)
                } else {
                    EmptyDetailView()
                }
            }
            .frame(maxWidth: .infinity)
        }
        .ignoresSafeArea()
        .background(Tokens.windowBackground)
        .background(WindowChromeConfigurator())
        .sheet(isPresented: $model.isCreatingBot) {
            BotEditorSheet(mode: .create)
                .environment(model)
        }
        .task { await model.start() }
    }
}

struct EmptyDetailView: View {
    var body: some View {
        ZStack {
            Tokens.windowBackground.ignoresSafeArea()
            VStack {
                Color.clear
                    .frame(height: 44)
                    .contentShape(Rectangle())
                    .gesture(WindowDragGesture())
                Spacer()
            }
            VStack(spacing: 10) {
                Circle()
                    .stroke(Tokens.hairline, lineWidth: 1.5)
                    .frame(width: 56, height: 56)
                    .overlay {
                        GlyphImage(.float)
                            .foregroundStyle(Tokens.textSecondary)
                            .frame(width: 30, height: 30)
                    }
                Text("Select a Botter")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(Tokens.textPrimary)
                Text("or create one with ⌘N")
                    .font(Tokens.sidebarBody)
                    .foregroundStyle(Tokens.textSecondary)
            }
        }
    }
}
