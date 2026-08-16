import SwiftUI
import ServiceManagement
import BotterKit

struct SettingsView: View {
    @Environment(AppModel.self) private var model

    @State private var health: Health?
    @State private var healthError: String?
    @State private var launchAtLogin = SMAppService.mainApp.status == .enabled
    @State private var loginToggleError: String?

    private var tokenPath: String {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".botter/token").path
    }

    var body: some View {
        Form {
            Section("botterd") {
                LabeledContent("Status") {
                    HStack(spacing: 6) {
                        Circle()
                            .fill(statusColor)
                            .frame(width: 8, height: 8)
                        Text(statusText)
                    }
                }
                LabeledContent("Hermes") {
                    Text(hermesText)
                }
                LabeledContent("Endpoint") {
                    Text(model.client.configuration.baseURL.absoluteString)
                        .textSelection(.enabled)
                }
                LabeledContent("Token file") {
                    Text(tokenPath)
                        .textSelection(.enabled)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
            }
            Section("App") {
                Toggle("Launch at login", isOn: $launchAtLogin)
                    .onChange(of: launchAtLogin) { _, enabled in
                        do {
                            if enabled {
                                try SMAppService.mainApp.register()
                            } else {
                                try SMAppService.mainApp.unregister()
                            }
                            loginToggleError = nil
                        } catch {
                            loginToggleError = error.localizedDescription
                            launchAtLogin = SMAppService.mainApp.status == .enabled
                        }
                    }
                if let loginToggleError {
                    Text(loginToggleError)
                        .font(.caption)
                        .foregroundStyle(.red)
                }
            }
        }
        .formStyle(.grouped)
        .frame(width: 420)
        .task { await refreshHealth() }
    }

    private var statusColor: Color {
        if healthError != nil { return .red }
        return health?.status == "ok" ? Color(hex: 0x22C55E) : .orange
    }

    private var statusText: String {
        if let healthError { return "Unreachable — \(healthError)" }
        guard let health else { return "Checking…" }
        return "\(health.status ?? "?") · v\(health.version ?? "?")"
    }

    private var hermesText: String {
        guard let hermes = health?.hermes else { return "—" }
        let reachable = hermes.reachable == true ? "reachable" : "unreachable"
        return "\(reachable) · v\(hermes.version ?? "?")"
    }

    private func refreshHealth() async {
        do {
            health = try await model.client.health()
            healthError = nil
        } catch {
            healthError = error.localizedDescription
        }
    }
}
