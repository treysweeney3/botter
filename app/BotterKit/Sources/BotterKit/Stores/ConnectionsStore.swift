import Foundation
import Observation

@MainActor
@Observable
public final class ConnectionsStore {
    /// Every credential and setting, curated apps first (`GET /v1/integrations`).
    public private(set) var integrations: [Integration] = []
    public private(set) var isLoading = false
    public private(set) var lastError: String?
    /// Set when Google OAuth needs user steps to finish connecting.
    public private(set) var pendingAuthorization: (id: String, auth: Authorization)?

    /// MCP servers available to every bot, and the presets not yet added.
    public private(set) var mcpServers: [McpServer] = []
    public private(set) var mcpPresets: [McpServer] = []
    public private(set) var mcpError: String?
    /// Set while a browser OAuth flow is open for an MCP server.
    public private(set) var mcpAuthorization: McpAuthorization?
    /// Server name whose flow has been asked for but has not come back yet.
    ///
    /// Starting a flow means spawning Hermes' management child and registering
    /// an OAuth client with the provider, which is not instant. Without this the
    /// UI has nothing to show for that whole stretch and reads as hung.
    public private(set) var mcpAuthorizationPreparing: String?
    /// Server name currently being added, removed, or authorized. Each of
    /// those restarts the Hermes gateway, so the UI must show it is busy.
    public private(set) var busyMcpName: String?

    private let client: BotterClient

    public init(client: BotterClient) {
        self.client = client
    }

    // MARK: - Reads

    public func refresh() async {
        async let integrationsTask: Void = refreshIntegrations()
        async let mcpTask: Void = refreshMcp()
        _ = await (integrationsTask, mcpTask)
    }

    public func refreshMcp() async {
        do {
            let result = try await client.mcpServers()
            mcpServers = result.servers
            mcpPresets = result.presets
            mcpError = nil
        } catch {
            mcpError = error.localizedDescription
        }
    }

    public func refreshIntegrations() async {
        isLoading = integrations.isEmpty
        defer { isLoading = false }
        do {
            integrations = try await client.integrations()
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
    }

    // MARK: - Grouping

    /// Rows the app renders as one card, in server order. A grouped card keeps
    /// its required key first; an ungrouped row is a card of one.
    public var cards: [[Integration]] {
        var order: [String] = []
        var buckets: [String: [Integration]] = [:]
        for item in integrations where item.kind == "integration" {
            let key = item.group ?? item.key
            if buckets[key] == nil { order.append(key) }
            buckets[key, default: []].append(item)
        }
        return order.map { key in
            buckets[key, default: []].sorted { left, right in
                left.required && !right.required
            }
        }
    }

    /// Plain Hermes settings. The app renders these on their own tab.
    public var configRows: [Integration] {
        integrations.filter { $0.kind == "config" }
    }

    // MARK: - Writes

    /// Returns nil on success, or the error message for inline display.
    public func setIntegration(key: String, value: String) async -> String? {
        do {
            upsert(try await client.setIntegration(key: key, value: value))
            lastError = nil
            return nil
        } catch {
            return error.localizedDescription
        }
    }

    public func removeIntegration(key: String) async {
        do {
            let removed = try await client.removeIntegration(key: key)
            if removed.custom {
                integrations.removeAll { $0.key == key }
            } else {
                upsert(removed)
            }
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
    }

    /// Drives the Google OAuth flow. An empty call starts it.
    public func connectGoogle(code: String? = nil, clientSecretJSON: String? = nil) async {
        do {
            switch try await client.connectGoogle(code: code, clientSecretJSON: clientSecretJSON) {
            case .connected(let integration):
                upsert(integration)
                pendingAuthorization = nil
            case .authorizationRequired(let auth):
                pendingAuthorization = ("google", auth)
            }
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
    }

    public func disconnectGoogle() async {
        do {
            upsert(try await client.disconnectGoogle())
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
    }

    // MARK: - MCP servers

    /// Adds a server and rides out the gateway restart. Returns the new row.
    @discardableResult
    public func addMcpServer(
        name: String,
        url: String,
        auth: String = "oauth",
        headers: [String: String] = [:]
    ) async -> McpServer? {
        busyMcpName = name
        defer { busyMcpName = nil }
        do {
            let server = try await client.addMcpServer(
                name: name, url: url, auth: auth, headers: headers
            )
            upsertMcp(server)
            mcpPresets.removeAll { $0.name == name }
            mcpError = nil
            return server
        } catch {
            mcpError = error.localizedDescription
            return nil
        }
    }

    public func removeMcpServer(name: String) async {
        busyMcpName = name
        defer { busyMcpName = nil }
        do {
            _ = try await client.removeMcpServer(name: name)
            mcpServers.removeAll { $0.name == name }
            await refreshMcp()
            mcpError = nil
        } catch {
            mcpError = error.localizedDescription
        }
    }

    /// Starts the browser OAuth flow and polls until it settles.
    ///
    /// The provider redirects to a loopback callback on the Hermes management
    /// service, so nothing comes back through this app — polling is how the
    /// result arrives. After the provider approves, botterd copies the grant to
    /// every bot and restarts the gateway in the background and reports
    /// `finishing`, so this keeps polling past the approval until that lands.
    public func authorizeMcpServer(name: String) async {
        busyMcpName = name
        mcpAuthorizationPreparing = name
        defer {
            busyMcpName = nil
            mcpAuthorizationPreparing = nil
        }
        do {
            var flow = try await client.authorizeMcpServer(name: name)
            mcpAuthorizationPreparing = nil
            mcpAuthorization = flow
            var elapsed: Duration = .zero
            // Five minutes, which covers a slow sign-in without polling a dead
            // flow for ever. Hermes expires the flow itself.
            while elapsed < .seconds(300) {
                if flow.isSettled { break }
                let wait = Self.pollInterval(after: elapsed)
                try await Task.sleep(for: wait)
                if Task.isCancelled { return }
                elapsed += wait
                let result = try await client.mcpAuthorization(flowId: flow.flowId)
                flow = result.authorization
                mcpAuthorization = flow
                if let server = result.server { upsertMcp(server) }
            }
            if flow.status == "error" {
                mcpError = flow.error ?? "Authorization did not finish."
            } else {
                mcpError = nil
            }
            if flow.isSettled {
                mcpAuthorization = nil
                await refreshMcp()
            }
        } catch {
            mcpError = error.localizedDescription
            mcpAuthorization = nil
        }
    }

    /// Poll quickly at first, then back off.
    ///
    /// The two moments a person is watching for — the link appearing and the
    /// approval registering — both land early in their own phase, so a flat
    /// interval spends its latency exactly where it is felt. Backing off keeps
    /// a long wait in the browser cheap.
    nonisolated static func pollInterval(after elapsed: Duration) -> Duration {
        if elapsed < .seconds(6) { return .milliseconds(400) }
        if elapsed < .seconds(30) { return .seconds(1) }
        return .seconds(2)
    }

    public func cancelMcpAuthorization() {
        mcpAuthorization = nil
    }

    private func upsertMcp(_ server: McpServer) {
        if let index = mcpServers.firstIndex(where: { $0.name == server.name }) {
            mcpServers[index] = server
        } else {
            mcpServers.append(server)
        }
    }

    public func clearAuthorization() {
        pendingAuthorization = nil
    }

    public func apply(_ event: ServerEvent) {
        switch event {
        case .integrationUpdated:
            Task { await refreshIntegrations() }
        case .mcpUpdated(let name, _):
            // Skip while our own add/remove is in flight — those responses
            // already carry the authoritative state. An authorization is the
            // exception: botterd finishes it in the background, and this event
            // is how that ending arrives.
            let finishing = mcpAuthorization?.isFinishing == true && mcpAuthorization?.server == name
            guard busyMcpName == nil || finishing else { return }
            Task { await refreshMcp() }
        default:
            break
        }
    }

    /// Seeds rows without a server round-trip. Tests only.
    func setIntegrationsForTesting(_ rows: [Integration]) {
        integrations = rows
    }

    private func upsert(_ integration: Integration) {
        if let index = integrations.firstIndex(where: { $0.key == integration.key }) {
            integrations[index] = integration
        } else {
            integrations.insert(integration, at: 0)
        }
    }
}
