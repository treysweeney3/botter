import SwiftUI
import BotterKit

/// Manage what every bot runs on: the credentials its model and terminal need,
/// the MCP tool catalogs it can reach, and general Hermes settings. Opened from
/// the profile button in the sidebar footer.
struct ConnectionsSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss

    /// Two surfaces that look alike but sit at different layers.
    ///
    /// Credentials are what a bot runs *on*: the model provider key that makes
    /// it think, and the tokens its shell needs for `git`, `vercel`, and
    /// `supabase`. Apps are what a bot reaches *out to* over MCP. A shell
    /// command needs the token present in the sandbox, so an MCP catalog can
    /// never replace one.
    enum Tab: String, CaseIterable {
        case credentials = "Credentials"
        case apps = "Apps"
        case config = "Config"
    }

    @State private var tab: Tab = .credentials
    @State private var isAddingIntegration = false
    @State private var replacingIntegration: Integration?
    @State private var configQuery = ""
    @State private var editingConfig: Integration?
    @State private var isAddingMcpServer = false

    private var store: ConnectionsStore { model.connections }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 1) {
                    Text("Hermes")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundStyle(Tokens.textPrimary)
                    Text(subtitle)
                        .font(Tokens.sidebarBody)
                        .foregroundStyle(Tokens.textSecondary)
                }
                Spacer()
                Button("Done") { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }
            .padding(.horizontal, 20)
            .padding(.top, 16)
            .padding(.bottom, 10)
            Picker("", selection: $tab) {
                ForEach(Tab.allCases, id: \.self) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .padding(.horizontal, 20)
            .padding(.bottom, 12)
            Divider().overlay(Tokens.hairline)

            switch tab {
            case .credentials: credentialsList
            case .apps: appsList
            case .config: configList
            }
        }
        .frame(width: 520, height: 600)
        .background(Tokens.sidebarBackground)
        .task { await store.refresh() }
        .sheet(isPresented: $isAddingMcpServer) {
            McpServerSheet(store: store)
        }
        .sheet(isPresented: $isAddingIntegration) {
            IntegrationPickerSheet(store: store)
        }
        .sheet(item: $replacingIntegration) { integration in
            IntegrationValueSheet(integration: integration, store: store)
        }
        .sheet(item: $editingConfig) { integration in
            IntegrationValueSheet(integration: integration, store: store)
        }
    }

    private var subtitle: String {
        switch tab {
        case .credentials: "Keys your bots' models and terminal commands run on"
        case .apps: "Tool catalogs every bot can reach, over MCP"
        case .config: "General agent settings, stored in ~/.hermes/.env"
        }
    }

    /// MCP servers. One entry reaches a whole catalog, so this list stays short
    /// where the credential list grows one row per app.
    private var appsList: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .firstTextBaseline) {
                    sectionHeader(
                        "Connected catalogs",
                        subtitle: "Each one gives every bot a set of tools"
                    )
                    Spacer()
                    Button {
                        isAddingMcpServer = true
                    } label: {
                        Label("Add", systemImage: "plus")
                            .font(Tokens.timestamp)
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                }
                ForEach(store.mcpServers) { server in
                    McpServerCard(server: server, store: store)
                }
                if store.mcpServers.isEmpty && store.mcpError == nil {
                    Text("No catalogs connected yet. Add one below to give every bot its tools.")
                        .font(Tokens.timestamp)
                        .foregroundStyle(Tokens.textSecondary)
                        .padding(.horizontal, 2)
                }
                if let flow = store.mcpAuthorization {
                    McpAuthorizationCallout(flow: flow, store: store)
                }
                if let error = store.mcpError {
                    errorText(error)
                }
                if !store.mcpPresets.isEmpty {
                    sectionHeader("Available", subtitle: "One connection, many apps")
                        .padding(.top, 10)
                    ForEach(store.mcpPresets) { preset in
                        McpPresetCard(preset: preset, store: store)
                    }
                }
            }
            .padding(16)
        }
    }

    /// The one credential list. Curated apps arrive first from the server, then
    /// every configured key. Unset catalog entries stay behind the Add picker
    /// so the list shows what you have, not what you could have.
    private var visibleCards: [[Integration]] {
        store.cards.filter { card in
            card.contains { $0.isSet || $0.syncStatus == "out_of_sync" || $0.group != nil }
        }
    }

    private var credentialsList: some View {
            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    HStack(alignment: .firstTextBaseline) {
                        sectionHeader(
                            "Credentials",
                            subtitle: "Read by every bot's model and by its terminal commands"
                        )
                        Spacer()
                        Button {
                            isAddingIntegration = true
                        } label: {
                            Label("Add", systemImage: "plus")
                                .font(Tokens.timestamp)
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                    }
                    if store.isLoading && store.integrations.isEmpty {
                        loadingRow("Loading credentials…")
                    }
                    ForEach(visibleCards, id: \.first!.key) { card in
                        CredentialCard(card: card, store: store) { integration in
                            replacingIntegration = integration
                        }
                    }
                    if let auth = store.pendingAuthorization {
                        AuthorizationCallout(authorization: auth.auth, store: store) {
                            store.clearAuthorization()
                            Task { await store.refreshIntegrations() }
                        }
                    }
                    if let error = store.lastError {
                        errorText(error)
                    }

                }
                .padding(16)
            }
    }

    private var filteredConfig: [Integration] {
        let trimmed = configQuery.trimmingCharacters(in: .whitespaces).lowercased()
        return store.integrations.filter { integration in
            guard integration.kind == "config" else { return false }
            if trimmed.isEmpty { return true }
            return integration.label.lowercased().contains(trimmed)
                || integration.key.lowercased().contains(trimmed)
                || integration.description.lowercased().contains(trimmed)
        }
    }

    private var configList: some View {
        VStack(alignment: .leading, spacing: 0) {
            TextField("Search settings", text: $configQuery)
                .textFieldStyle(.plain)
                .font(Tokens.sidebarBody)
                .foregroundStyle(Tokens.textPrimary)
                .padding(.horizontal, 10)
                .padding(.vertical, 8)
                .background(
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .fill(Tokens.cardBackground)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .strokeBorder(Tokens.hairline, lineWidth: 1)
                )
                .padding(.horizontal, 16)
                .padding(.top, 12)
                .padding(.bottom, 8)
            ScrollView {
                VStack(alignment: .leading, spacing: 6) {
                    if store.isLoading && store.integrations.isEmpty {
                        loadingRow("Loading settings…")
                    }
                    ForEach(filteredConfig) { integration in
                        ConfigRow(integration: integration, store: store) {
                            editingConfig = integration
                        }
                    }
                    if let error = store.lastError {
                        errorText(error)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 16)
            }
        }
    }

    private func sectionHeader(_ title: String, subtitle: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(title)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(Tokens.textPrimary)
            Text(subtitle)
                .font(Tokens.timestamp)
                .foregroundStyle(Tokens.textSecondary)
        }
        .padding(.horizontal, 2)
    }

    private func loadingRow(_ label: String) -> some View {
        HStack(spacing: 8) {
            ProgressView().controlSize(.small)
            Text(label)
                .font(Tokens.sidebarBody)
                .foregroundStyle(Tokens.textSecondary)
        }
        .padding(.vertical, 8)
        .padding(.horizontal, 2)
    }

    private func errorText(_ error: String) -> some View {
        Text(error)
            .font(Tokens.timestamp)
            .foregroundStyle(.red)
            .padding(.horizontal, 2)
    }
}

/// One credential card. A curated app with several keys (Vercel = token +
/// team id) renders as one card with its optional fields listed underneath.
/// Google drives OAuth; Slack is display-only.
struct CredentialCard: View {
    /// Required key first, then any optional fields in the same group.
    let card: [Integration]
    let store: ConnectionsStore
    let edit: (Integration) -> Void

    @State private var isWorking = false

    private var primary: Integration { card[0] }
    private var extras: [Integration] { Array(card.dropFirst()) }

    private var statusColor: Color {
        switch primary.status {
        case "connected": Color(hex: 0x22C55E)
        case "error": Color(hex: 0xEF4444)
        default: Tokens.textSecondary.opacity(0.5)
        }
    }

    private var statusText: String {
        switch primary.status {
        case "connected": "Connected"
        case "error": "Needs attention"
        default: "Not connected"
        }
    }

    private var title: String { primary.groupLabel ?? primary.label }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 12) {
                Circle()
                    .fill(statusColor)
                    .frame(width: 8, height: 8)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(Tokens.sidebarName)
                        .foregroundStyle(Tokens.textPrimary)
                    Text(primary.detail ?? primary.syncDetail ?? statusText)
                        .font(Tokens.sidebarBody)
                        .foregroundStyle(primary.status == "error" ? .orange : Tokens.textSecondary)
                        .lineLimit(1)
                }
                Spacer()
                trailingControl
            }
            ForEach(extras) { extra in
                ExtraFieldRow(integration: extra, store: store) { edit(extra) }
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 11)
        .background(
            RoundedRectangle(cornerRadius: Tokens.cardRadius, style: .continuous)
                .fill(Tokens.cardBackground)
        )
        .overlay(
            RoundedRectangle(cornerRadius: Tokens.cardRadius, style: .continuous)
                .strokeBorder(Tokens.hairline, lineWidth: 1)
        )
    }

    @ViewBuilder
    private var trailingControl: some View {
        if isWorking {
            ProgressView().controlSize(.small)
        } else if primary.isReadOnly {
            Text("Managed by Hermes")
                .font(Tokens.timestamp)
                .foregroundStyle(Tokens.textSecondary)
        } else if primary.isSet {
            Menu {
                Button(primary.auth == "oauth" ? "Reconnect…" : "Replace key…") {
                    run(primary)
                }
                Button(primary.auth == "oauth" ? "Disconnect" : "Remove", role: .destructive) {
                    Task {
                        isWorking = true
                        if primary.auth == "oauth" {
                            await store.disconnectGoogle()
                        } else {
                            await store.removeIntegration(key: primary.key)
                        }
                        isWorking = false
                    }
                }
            } label: {
                Image(systemName: "ellipsis")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Tokens.textSecondary)
                    .frame(width: 24, height: 24)
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
        } else {
            Button("Connect") { run(primary) }
                .buttonStyle(.bordered)
                .controlSize(.small)
        }
    }

    private func run(_ integration: Integration) {
        guard integration.auth == "oauth" else {
            edit(integration)
            return
        }
        Task {
            isWorking = true
            await store.connectGoogle()
            isWorking = false
        }
    }
}

/// An optional key inside a grouped card (Vercel team id, Supabase project ref).
private struct ExtraFieldRow: View {
    let integration: Integration
    let store: ConnectionsStore
    let edit: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Text(integration.label)
                .font(Tokens.timestamp)
                .foregroundStyle(Tokens.textSecondary)
            Text(integration.isSet ? (integration.redactedValue ?? "Set") : "Optional")
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(integration.isSet ? Tokens.textPrimary : Tokens.textSecondary.opacity(0.7))
                .lineLimit(1)
            Spacer()
            Button(integration.isSet ? "Change" : "Add", action: edit)
                .buttonStyle(.plain)
                .font(Tokens.timestamp)
                .foregroundStyle(Tokens.textSecondary)
        }
        .padding(.leading, 20)
    }
}

/// Shown when Google OAuth needs user steps to finish. It walks the full in-app
/// flow: open the consent page, then paste the redirect URL back (or paste the
/// OAuth client JSON first when Hermes has none).
struct AuthorizationCallout: View {
    let authorization: Authorization
    let store: ConnectionsStore
    let done: () -> Void

    @State private var pastedCode = ""
    @State private var pastedClientSecret = ""
    @State private var isSubmitting = false

    private var needsCode: Bool { authorization.codeEntry == true }
    private var needsClientSecret: Bool { authorization.needsClientSecret == true }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Image(systemName: "person.badge.key")
                    .font(.system(size: 11, weight: .semibold))
                Text("Finish connecting")
                    .font(Tokens.sidebarName)
            }
            .foregroundStyle(Tokens.textPrimary)
            Text(authorization.instructions)
                .font(Tokens.sidebarBody)
                .foregroundStyle(Tokens.textSecondary)
                .textSelection(.enabled)
            if let url = authorization.url.flatMap(URL.init(string:)) {
                Link("Open authorization page", destination: url)
                    .font(Tokens.sidebarName)
            }
            if needsClientSecret {
                calloutField("Paste the OAuth client JSON", text: $pastedClientSecret, secure: false)
            }
            if needsCode {
                calloutField("Paste the full redirect URL", text: $pastedCode, secure: false)
            }
            HStack {
                Spacer()
                Button("Cancel", action: done)
                    .controlSize(.small)
                if needsCode || needsClientSecret {
                    Button(needsCode ? "Complete" : "Continue") {
                        Task {
                            isSubmitting = true
                            await store.connectGoogle(
                                code: needsCode ? pastedCode.trimmingCharacters(in: .whitespacesAndNewlines) : nil,
                                clientSecretJSON: needsClientSecret ? pastedClientSecret : nil
                            )
                            isSubmitting = false
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                    .disabled(
                        isSubmitting
                        || (needsCode && pastedCode.trimmingCharacters(in: .whitespaces).isEmpty)
                        || (needsClientSecret && !needsCode && pastedClientSecret.trimmingCharacters(in: .whitespaces).isEmpty)
                    )
                }
            }
        }
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: Tokens.cardRadius, style: .continuous)
                .fill(Tokens.cardBackground)
        )
        .overlay(
            RoundedRectangle(cornerRadius: Tokens.cardRadius, style: .continuous)
                .strokeBorder(Color.orange.opacity(0.35), lineWidth: 1)
        )
    }

    private func calloutField(_ placeholder: String, text: Binding<String>, secure: Bool) -> some View {
        Group {
            if secure {
                SecureField(placeholder, text: text)
            } else {
                TextField(placeholder, text: text)
            }
        }
        .textFieldStyle(.plain)
        .font(.system(size: 12, design: .monospaced))
        .foregroundStyle(Tokens.textPrimary)
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(Tokens.sidebarBackground)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .strokeBorder(Tokens.hairline, lineWidth: 1)
        )
    }
}

/// Searchable catalog of every integration Hermes knows about, plus custom keys.
/// Selecting an entry reveals inline key entry — paste and save, setup is automatic.
struct IntegrationPickerSheet: View {
    @Environment(\.dismiss) private var dismiss
    let store: ConnectionsStore

    @State private var query = ""
    @State private var selected: Integration?
    @State private var isAddingCustom = false
    @State private var customKey = ""
    @State private var value = ""
    @State private var isSaving = false
    @State private var errorMessage: String?

    private static let categoryLabels: [(String, String)] = [
        ("tool", "Tools & services"),
        ("skill", "Skills"),
        ("provider", "Model providers"),
        ("setting", "Settings"),
        ("custom", "Custom"),
    ]

    private var filtered: [Integration] {
        let trimmed = query.trimmingCharacters(in: .whitespaces).lowercased()
        return store.integrations.filter { integration in
            guard integration.kind == "integration" else { return false }
            if trimmed.isEmpty {
                // Advanced entries stay reachable through search without
                // burying the common tools under provider overrides.
                return !integration.advanced
            }
            return integration.label.lowercased().contains(trimmed)
                || integration.key.lowercased().contains(trimmed)
                || integration.description.lowercased().contains(trimmed)
        }
    }

    private func rows(in category: String) -> [Integration] {
        filtered.filter { $0.category == category }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Add integration")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(Tokens.textPrimary)
                Spacer()
                Button("Close") { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }
            TextField("Search services (Notion, Brave, ElevenLabs, …)", text: $query)
                .textFieldStyle(.plain)
                .font(Tokens.sidebarBody)
                .foregroundStyle(Tokens.textPrimary)
                .padding(.horizontal, 10)
                .padding(.vertical, 8)
                .background(
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .fill(Tokens.cardBackground)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .strokeBorder(Tokens.hairline, lineWidth: 1)
                )

            ScrollView {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(Self.categoryLabels, id: \.0) { category, label in
                        let entries = rows(in: category)
                        if !entries.isEmpty {
                            Text(label)
                                .font(Tokens.timestamp)
                                .foregroundStyle(Tokens.textSecondary)
                                .padding(.top, 6)
                            ForEach(entries) { integration in
                                pickerRow(integration)
                            }
                        }
                    }
                    Button {
                        isAddingCustom = true
                        selected = nil
                        value = ""
                        errorMessage = nil
                    } label: {
                        Label("Add a custom key…", systemImage: "plus.circle")
                            .font(Tokens.sidebarBody)
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(Tokens.textSecondary)
                    .padding(.top, 8)
                }
            }
            .frame(maxHeight: 260)

            if selected != nil || isAddingCustom {
                VStack(alignment: .leading, spacing: 8) {
                    Divider().overlay(Tokens.hairline)
                    if isAddingCustom {
                        Text("Custom key — UPPER_SNAKE_CASE, stored in ~/.hermes/.env")
                            .font(Tokens.timestamp)
                            .foregroundStyle(Tokens.textSecondary)
                        entryField("KEY_NAME", text: $customKey, secure: false)
                    } else if let selected {
                        HStack(spacing: 6) {
                            Text(selected.label)
                                .font(Tokens.sidebarName)
                                .foregroundStyle(Tokens.textPrimary)
                            if let url = selected.url.flatMap(URL.init(string:)) {
                                Link("Get key", destination: url)
                                    .font(Tokens.timestamp)
                            }
                        }
                        if !selected.description.isEmpty {
                            Text(selected.description)
                                .font(Tokens.timestamp)
                                .foregroundStyle(Tokens.textSecondary)
                        }
                    }
                    entryField(
                        selected?.isPassword == false ? "Value" : "Paste key",
                        text: $value,
                        secure: selected?.isPassword != false
                    )
                    if let errorMessage {
                        Text(errorMessage)
                            .font(Tokens.timestamp)
                            .foregroundStyle(.red)
                    }
                    HStack {
                        Spacer()
                        Button("Save") {
                            Task {
                                isSaving = true
                                let key = isAddingCustom
                                    ? customKey.trimmingCharacters(in: .whitespaces).uppercased()
                                    : (selected?.key ?? "")
                                let failure = await store.setIntegration(
                                    key: key,
                                    value: value.trimmingCharacters(in: .whitespacesAndNewlines)
                                )
                                isSaving = false
                                if let failure {
                                    errorMessage = failure
                                } else {
                                    dismiss()
                                }
                            }
                        }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.small)
                        .keyboardShortcut(.defaultAction)
                        .disabled(
                            isSaving
                            || value.trimmingCharacters(in: .whitespaces).isEmpty
                            || (isAddingCustom && customKey.trimmingCharacters(in: .whitespaces).isEmpty)
                        )
                    }
                }
            }
        }
        .padding(20)
        .frame(width: 460, height: 480)
        .background(Tokens.sidebarBackground)
        .task {
            if store.integrations.isEmpty { await store.refreshIntegrations() }
        }
    }

    private func pickerRow(_ integration: Integration) -> some View {
        Button {
            selected = integration
            isAddingCustom = false
            value = ""
            errorMessage = nil
        } label: {
            HStack(spacing: 8) {
                VStack(alignment: .leading, spacing: 1) {
                    Text(integration.label)
                        .font(Tokens.sidebarBody)
                        .foregroundStyle(Tokens.textPrimary)
                    if !integration.description.isEmpty {
                        Text(integration.description)
                            .font(Tokens.timestamp)
                            .foregroundStyle(Tokens.textSecondary)
                            .lineLimit(1)
                    }
                }
                Spacer()
                if integration.isSet {
                    Text("Configured")
                        .font(Tokens.timestamp)
                        .foregroundStyle(Color(hex: 0x22C55E))
                }
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 6)
            .background(
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .fill(selected?.key == integration.key ? Tokens.cardBackground : .clear)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private func entryField(_ placeholder: String, text: Binding<String>, secure: Bool) -> some View {
        Group {
            if secure {
                SecureField(placeholder, text: text)
            } else {
                TextField(placeholder, text: text)
            }
        }
        .textFieldStyle(.plain)
        .font(.system(size: 13, design: .monospaced))
        .foregroundStyle(Tokens.textPrimary)
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(Tokens.cardBackground)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .strokeBorder(Tokens.hairline, lineWidth: 1)
        )
    }
}

/// Replace the value of an already-configured integration key.
struct IntegrationValueSheet: View {
    @Environment(\.dismiss) private var dismiss
    let integration: Integration
    let store: ConnectionsStore

    @State private var value = ""
    @State private var isSaving = false
    @State private var errorMessage: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(integration.isSet
                 ? "Replace \(integration.label)"
                 : "Connect \(integration.groupLabel ?? integration.label)")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(Tokens.textPrimary)
            Text("Stored in ~/.hermes/.env on this Mac. Never leaves your machine.")
                .font(Tokens.sidebarBody)
                .foregroundStyle(Tokens.textSecondary)
            Group {
                if integration.isPassword {
                    SecureField(integration.redactedValue ?? "Paste key", text: $value)
                } else {
                    TextField(integration.redactedValue ?? "Value", text: $value)
                }
            }
            .textFieldStyle(.plain)
            .font(.system(size: 13, design: .monospaced))
            .foregroundStyle(Tokens.textPrimary)
            .padding(.horizontal, 10)
            .padding(.vertical, 9)
            .background(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(Tokens.cardBackground)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .strokeBorder(Tokens.hairline, lineWidth: 1)
            )
            if let errorMessage {
                Text(errorMessage)
                    .font(Tokens.timestamp)
                    .foregroundStyle(.red)
            }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button("Save") {
                    Task {
                        isSaving = true
                        let failure = await store.setIntegration(
                            key: integration.key,
                            value: value.trimmingCharacters(in: .whitespacesAndNewlines)
                        )
                        isSaving = false
                        if let failure {
                            errorMessage = failure
                        } else {
                            dismiss()
                        }
                    }
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(value.trimmingCharacters(in: .whitespaces).isEmpty || isSaving)
            }
        }
        .padding(20)
        .frame(width: 380)
        .background(Tokens.sidebarBackground)
    }
}

/// One general Hermes setting on the Config tab. Non-secret values show as-is
/// (Hermes redaction keeps them short); unset rows read "Default".
struct ConfigRow: View {
    let integration: Integration
    let store: ConnectionsStore
    let edit: () -> Void

    @State private var isWorking = false

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(integration.label)
                    .font(Tokens.sidebarName)
                    .foregroundStyle(Tokens.textPrimary)
                if !integration.description.isEmpty {
                    Text(integration.description)
                        .font(Tokens.timestamp)
                        .foregroundStyle(Tokens.textSecondary)
                        .lineLimit(2)
                }
            }
            Spacer()
            if isWorking {
                ProgressView().controlSize(.small)
            } else {
                Text(integration.isSet ? (integration.redactedValue ?? "Set") : "Default")
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(integration.isSet ? Tokens.textPrimary : Tokens.textSecondary.opacity(0.7))
                    .lineLimit(1)
                Menu {
                    Button(integration.isSet ? "Change…" : "Set…", action: edit)
                    if integration.isSet {
                        Button("Reset to default", role: .destructive) {
                            Task {
                                isWorking = true
                                await store.removeIntegration(key: integration.key)
                                isWorking = false
                            }
                        }
                    }
                } label: {
                    Image(systemName: "ellipsis")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(Tokens.textSecondary)
                        .frame(width: 24, height: 24)
                }
                .menuStyle(.borderlessButton)
                .menuIndicator(.hidden)
                .fixedSize()
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
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

// MARK: - MCP servers

/// One connected MCP server. A server whose OAuth grant is missing shows the
/// Authorize action rather than a green dot — the entry exists, but no bot can
/// use it yet.
struct McpServerCard: View {
    let server: McpServer
    let store: ConnectionsStore

    private var isBusy: Bool { store.busyMcpName == server.name }

    private var statusColor: Color {
        switch server.status {
        case "connected": Color(hex: 0x22C55E)
        case "error": Color(hex: 0xEF4444)
        default: Tokens.textSecondary.opacity(0.5)
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 12) {
                Circle()
                    .fill(statusColor)
                    .frame(width: 8, height: 8)
                VStack(alignment: .leading, spacing: 2) {
                    Text(server.label)
                        .font(Tokens.sidebarName)
                        .foregroundStyle(Tokens.textPrimary)
                    Text(server.detail ?? server.url ?? "")
                        .font(Tokens.sidebarBody)
                        .foregroundStyle(server.status == "error" ? .orange : Tokens.textSecondary)
                        .lineLimit(2)
                }
                Spacer()
                if isBusy {
                    ProgressView().controlSize(.small)
                } else {
                    trailingControl
                }
            }
            if !server.description.isEmpty {
                Text(server.description)
                    .font(Tokens.timestamp)
                    .foregroundStyle(Tokens.textSecondary)
                    .padding(.leading, 20)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 11)
        .background(
            RoundedRectangle(cornerRadius: Tokens.cardRadius, style: .continuous)
                .fill(Tokens.cardBackground)
        )
        .overlay(
            RoundedRectangle(cornerRadius: Tokens.cardRadius, style: .continuous)
                .strokeBorder(
                    server.needsAuthorization ? Color.orange.opacity(0.35) : Tokens.hairline,
                    lineWidth: 1
                )
        )
    }

    @ViewBuilder
    private var trailingControl: some View {
        HStack(spacing: 8) {
            if server.needsAuthorization {
                Button("Authorize") {
                    Task { await store.authorizeMcpServer(name: server.name) }
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
            }
            Menu {
                if server.auth == "oauth" {
                    Button(server.authorized ? "Reauthorize…" : "Authorize…") {
                        Task { await store.authorizeMcpServer(name: server.name) }
                    }
                }
                if let docs = server.docsUrl.flatMap(URL.init(string:)) {
                    Link("Open documentation", destination: docs)
                }
                Button("Remove", role: .destructive) {
                    Task { await store.removeMcpServer(name: server.name) }
                }
            } label: {
                Image(systemName: "ellipsis")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Tokens.textSecondary)
                    .frame(width: 24, height: 24)
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
        }
    }
}

/// A known catalog not yet added. One click adds the entry and starts the
/// browser authorization, because adding without authorizing leaves a server
/// no bot can use.
struct McpPresetCard: View {
    let preset: McpServer
    let store: ConnectionsStore

    private var isBusy: Bool { store.busyMcpName == preset.name }

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(preset.label)
                    .font(Tokens.sidebarName)
                    .foregroundStyle(Tokens.textPrimary)
                Text(preset.description)
                    .font(Tokens.sidebarBody)
                    .foregroundStyle(Tokens.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            if isBusy {
                ProgressView().controlSize(.small)
            } else {
                Button("Connect") {
                    Task {
                        guard let url = preset.url else { return }
                        let added = await store.addMcpServer(
                            name: preset.name, url: url, auth: preset.auth
                        )
                        if added?.needsAuthorization == true {
                            await store.authorizeMcpServer(name: preset.name)
                        }
                    }
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 11)
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

/// Shown while a browser OAuth flow is open. The provider redirects to a
/// loopback callback on the Hermes management service, so this card waits for
/// the result rather than collecting anything from the user.
struct McpAuthorizationCallout: View {
    let flow: McpAuthorization
    let store: ConnectionsStore

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Image(systemName: "person.badge.key")
                    .font(.system(size: 11, weight: .semibold))
                Text("Finish connecting \(flow.server)")
                    .font(Tokens.sidebarName)
            }
            .foregroundStyle(Tokens.textPrimary)
            if !flow.instructions.isEmpty {
                Text(flow.instructions)
                    .font(Tokens.sidebarBody)
                    .foregroundStyle(Tokens.textSecondary)
            }
            if let url = flow.url.flatMap(URL.init(string:)) {
                Link("Open authorization page", destination: url)
                    .font(Tokens.sidebarName)
            }
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text(flow.status == "starting" ? "Starting…" : "Waiting for the provider…")
                    .font(Tokens.timestamp)
                    .foregroundStyle(Tokens.textSecondary)
                Spacer()
                Button("Cancel") { store.cancelMcpAuthorization() }
                    .controlSize(.small)
            }
        }
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: Tokens.cardRadius, style: .continuous)
                .fill(Tokens.cardBackground)
        )
        .overlay(
            RoundedRectangle(cornerRadius: Tokens.cardRadius, style: .continuous)
                .strokeBorder(Color.orange.opacity(0.35), lineWidth: 1)
        )
    }
}

/// Adds any MCP server by URL, for catalogs Botter has no preset for.
struct McpServerSheet: View {
    @Environment(\.dismiss) private var dismiss
    let store: ConnectionsStore

    @State private var name = ""
    @State private var url = ""
    @State private var usesOAuth = true
    @State private var headerName = ""
    @State private var headerValue = ""
    @State private var isSaving = false

    private var canSave: Bool {
        !name.trimmingCharacters(in: .whitespaces).isEmpty
            && url.trimmingCharacters(in: .whitespaces).hasPrefix("https://")
            && (usesOAuth || !headerName.trimmingCharacters(in: .whitespaces).isEmpty)
            && !isSaving
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Add an MCP server")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(Tokens.textPrimary)
            Text("Written to every bot's Hermes config. The gateway restarts to pick it up.")
                .font(Tokens.sidebarBody)
                .foregroundStyle(Tokens.textSecondary)
            field("Name, for example acme", text: $name)
            field("https://mcp.example.com/mcp", text: $url)
            Picker("", selection: $usesOAuth) {
                Text("Sign in (OAuth)").tag(true)
                Text("API key header").tag(false)
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            if !usesOAuth {
                field("Header name, for example X-Api-Key", text: $headerName)
                field("Value, or \(Self.envHint) to read it from Hermes", text: $headerValue)
            }
            if let error = store.mcpError {
                Text(error)
                    .font(Tokens.timestamp)
                    .foregroundStyle(.red)
            }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button("Add") {
                    Task {
                        isSaving = true
                        let trimmed = name.trimmingCharacters(in: .whitespaces)
                        let added = await store.addMcpServer(
                            name: trimmed,
                            url: url.trimmingCharacters(in: .whitespaces),
                            auth: usesOAuth ? "oauth" : "header",
                            headers: usesOAuth ? [:] : [
                                headerName.trimmingCharacters(in: .whitespaces): headerValue
                            ]
                        )
                        isSaving = false
                        guard added != nil else { return }
                        dismiss()
                        if added?.needsAuthorization == true {
                            await store.authorizeMcpServer(name: trimmed)
                        }
                    }
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(!canSave)
            }
        }
        .padding(20)
        .frame(width: 400)
        .background(Tokens.sidebarBackground)
    }

    private static let envHint = "${ENV_KEY}"

    private func field(_ placeholder: String, text: Binding<String>) -> some View {
        TextField(placeholder, text: text)
            .textFieldStyle(.plain)
            .font(.system(size: 13, design: .monospaced))
            .foregroundStyle(Tokens.textPrimary)
            .padding(.horizontal, 10)
            .padding(.vertical, 9)
            .background(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(Tokens.cardBackground)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .strokeBorder(Tokens.hairline, lineWidth: 1)
            )
    }
}
