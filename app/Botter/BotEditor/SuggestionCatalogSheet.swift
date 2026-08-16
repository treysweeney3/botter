import SwiftUI
import BotterKit

/// The full catalog of starter roles, opened from the New Botter sheet.
/// Picking one fills the form and closes; nothing is saved from here.
struct SuggestionCatalogSheet: View {
    @Environment(\.dismiss) private var dismiss
    let onPick: (RoleSuggestion) -> Void

    @State private var query = ""
    @State private var category: SuggestionCategory?
    @FocusState private var searchFocused: Bool

    private var results: [RoleSuggestion] {
        SuggestionCatalog.filtered(query: query, category: category)
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            filters
            Divider().overlay(Tokens.hairline)
            if results.isEmpty {
                emptyState
            } else {
                resultsList
            }
            Divider().overlay(Tokens.hairline)
            footer
        }
        .frame(width: 660, height: 640)
        .background(Tokens.sidebarBackground)
        .onAppear { searchFocused = true }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Start from a role")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(Tokens.textPrimary)
                Text("\(SuggestionCatalog.all.count) roles. Picking one fills the form — edit anything before you save.")
                    .font(Tokens.sidebarBody)
                    .foregroundStyle(Tokens.textSecondary)
            }
            searchField
        }
        .padding(.horizontal, 20)
        .padding(.top, 16)
        .padding(.bottom, 10)
    }

    private var searchField: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 12))
                .foregroundStyle(Tokens.textSecondary)
            TextField("Search roles — \"invoice\", \"support\", \"research\"", text: $query)
                .textFieldStyle(.plain)
                .font(Tokens.chatBody)
                .foregroundStyle(Tokens.textPrimary)
                .focused($searchFocused)
            if !query.isEmpty {
                Button {
                    query = ""
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 12))
                        .foregroundStyle(Tokens.textSecondary)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Clear search")
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(
            RoundedRectangle(cornerRadius: 8, style: .continuous).fill(Tokens.cardBackground)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous).stroke(Tokens.hairline, lineWidth: 1)
        )
    }

    private var filters: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                filterChip(label: "All", isSelected: category == nil) { category = nil }
                ForEach(SuggestionCategory.allCases) { candidate in
                    filterChip(label: candidate.rawValue, isSelected: category == candidate) {
                        category = category == candidate ? nil : candidate
                    }
                }
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 12)
        }
    }

    private func filterChip(label: String, isSelected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(label)
                .font(Tokens.chip)
                .foregroundStyle(isSelected ? Tokens.userBubbleText : Tokens.textSecondary)
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(
                    Capsule().fill(isSelected ? Tokens.userBubbleBackground : Tokens.cardBackground)
                )
                .overlay(
                    Capsule().stroke(isSelected ? .clear : Tokens.hairline, lineWidth: 1)
                )
        }
        .buttonStyle(.plain)
    }

    private var resultsList: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 18, pinnedViews: []) {
                ForEach(SuggestionCatalog.categories(in: results)) { section in
                    VStack(alignment: .leading, spacing: 8) {
                        Text(section.rawValue.uppercased())
                            .font(Tokens.chip)
                            .foregroundStyle(Tokens.textSecondary)
                            .kerning(0.6)
                        LazyVGrid(
                            columns: [
                                GridItem(.flexible(), spacing: 10),
                                GridItem(.flexible(), spacing: 10),
                            ],
                            alignment: .leading,
                            spacing: 10
                        ) {
                            ForEach(results.filter { $0.category == section }) { suggestion in
                                SuggestionCard(suggestion: suggestion) {
                                    onPick(suggestion)
                                    dismiss()
                                }
                            }
                        }
                    }
                }
            }
            .padding(20)
        }
    }

    private var emptyState: some View {
        VStack(spacing: 6) {
            Text("No roles match “\(query.trimmingCharacters(in: .whitespaces))”")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(Tokens.textPrimary)
            Text("Close this and describe the job yourself — the form takes anything.")
                .font(Tokens.sidebarBody)
                .foregroundStyle(Tokens.textSecondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var footer: some View {
        HStack {
            Spacer()
            Button("Cancel") { dismiss() }
                .keyboardShortcut(.cancelAction)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 14)
    }
}

/// One starter role: what it owns, and the boundary that comes with it.
struct SuggestionCard: View {
    let suggestion: RoleSuggestion
    let action: () -> Void

    @State private var isHovering = false

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 6) {
                VStack(alignment: .leading, spacing: 1) {
                    Text(suggestion.name)
                        .font(Tokens.sidebarName)
                        .foregroundStyle(Tokens.textPrimary)
                    Text(suggestion.title)
                        .font(.system(size: 11))
                        .foregroundStyle(Tokens.textSecondary)
                }
                Text(suggestion.description)
                    .font(.system(size: 11))
                    .foregroundStyle(Tokens.textSecondary)
                    .lineSpacing(1.5)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(alignment: .top, spacing: 5) {
                    Image(systemName: "hand.raised")
                        .font(.system(size: 9))
                        .foregroundStyle(Tokens.textSecondary)
                        .padding(.top, 1)
                    Text(suggestion.approvalBoundary)
                        .font(.system(size: 10))
                        .foregroundStyle(Tokens.textSecondary.opacity(0.85))
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                }
                .padding(.top, 1)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .padding(12)
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(Tokens.cardBackground.opacity(isHovering ? 1 : 0.7))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .stroke(isHovering ? Tokens.textSecondary.opacity(0.6) : Tokens.hairline, lineWidth: 1)
            )
            .contentShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        }
        .buttonStyle(.plain)
        .onHover { isHovering = $0 }
        .animation(.easeOut(duration: 0.12), value: isHovering)
        .accessibilityLabel("\(suggestion.name), \(suggestion.title). \(suggestion.description)")
    }
}
