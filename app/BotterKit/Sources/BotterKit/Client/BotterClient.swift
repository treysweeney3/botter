import Foundation

public struct ClientConfiguration: Sendable {
    public var baseURL: URL
    /// Called per-request so token rotation (or the v3 relay's Access headers)
    /// needs no client rebuild.
    public var token: @Sendable () -> String?
    public var extraHeaders: @Sendable () -> [String: String]

    public init(
        baseURL: URL = URL(string: "http://127.0.0.1:8674")!,
        token: @escaping @Sendable () -> String? = ClientConfiguration.localTokenFile,
        extraHeaders: @escaping @Sendable () -> [String: String] = { [:] }
    ) {
        self.baseURL = baseURL
        self.token = token
        self.extraHeaders = extraHeaders
    }

    /// v1 default: read the bearer token botterd writes to ~/.botter/token.
    public static let localTokenFile: @Sendable () -> String? = {
        let url = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".botter/token")
        return (try? String(contentsOf: url, encoding: .utf8))?
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

public enum BotterClientError: Error, LocalizedError {
    case http(status: Int, code: String?, message: String?)
    case invalidResponse

    public var errorDescription: String? {
        switch self {
        case .http(let status, let code, let message):
            message ?? code.map { "\($0) (HTTP \(status))" } ?? "HTTP \(status)"
        case .invalidResponse:
            "Invalid response from botterd"
        }
    }
}

/// Async client for the botterd HTTP contract (docs/SPEC.md §4).
public final class BotterClient: Sendable {
    public let configuration: ClientConfiguration
    private let session: URLSession
    private let decoder: JSONDecoder
    private let encoder: JSONEncoder

    public init(configuration: ClientConfiguration = ClientConfiguration(), session: URLSession = .shared) {
        self.configuration = configuration
        self.session = session
        self.decoder = Self.makeDecoder()
        self.encoder = Self.makeEncoder()
    }

    static func makeDecoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .custom { decoder in
            let raw = try decoder.singleValueContainer().decode(String.self)
            if let date = (try? Date(raw, strategy: Self.isoFractional)) ?? (try? Date(raw, strategy: Self.iso)) {
                return date
            }
            throw DecodingError.dataCorrupted(.init(
                codingPath: decoder.codingPath,
                debugDescription: "Unrecognized date: \(raw)"
            ))
        }
        return decoder
    }

    static func makeEncoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }

    private static let iso = Date.ISO8601FormatStyle()
    private static let isoFractional = Date.ISO8601FormatStyle(includingFractionalSeconds: true)

    // MARK: - Requests

    private func request(_ method: String, _ path: String, query: [URLQueryItem] = [], body: Data? = nil) -> URLRequest {
        var components = URLComponents(
            url: configuration.baseURL.appendingPathComponent(path),
            resolvingAgainstBaseURL: false
        )!
        if !query.isEmpty { components.queryItems = query }
        var request = URLRequest(url: components.url!)
        request.httpMethod = method
        if let token = configuration.token() {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        for (key, value) in configuration.extraHeaders() {
            request.setValue(value, forHTTPHeaderField: key)
        }
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return request
    }

    private func send<T: Decodable>(_ request: URLRequest, as type: T.Type) async throws -> T {
        let (data, response) = try await session.data(for: request)
        try Self.checkStatus(response: response, data: data, decoder: decoder)
        return try decoder.decode(T.self, from: data)
    }

    private func sendExpectingNoContent(_ request: URLRequest) async throws {
        let (data, response) = try await session.data(for: request)
        try Self.checkStatus(response: response, data: data, decoder: decoder)
    }

    static func checkStatus(response: URLResponse, data: Data, decoder: JSONDecoder) throws {
        guard let http = response as? HTTPURLResponse else { throw BotterClientError.invalidResponse }
        guard !(200..<300).contains(http.statusCode) else { return }
        let payload = try? decoder.decode(APIErrorPayload.self, from: data)
        throw BotterClientError.http(
            status: http.statusCode,
            code: payload?.error.code,
            message: payload?.error.message
        )
    }

    private func body(_ fields: [String: Any?]) throws -> Data {
        let compacted = fields.compactMapValues { $0 }
        return try JSONSerialization.data(withJSONObject: compacted)
    }

    // MARK: - Response envelopes (SPEC §4, pinned)

    private struct BotsEnvelope: Decodable { var bots: [FeedEntry] }
    private struct BotEnvelope: Decodable { var bot: Bot }
    private struct SessionsEnvelope: Decodable { var sessions: [Session] }
    private struct SessionEnvelope: Decodable { var session: Session }
    private struct MessagesEnvelope: Decodable { var messages: [Message]; var hasMore: Bool? }
    private struct RoutinesEnvelope: Decodable { var routines: [Routine] }
    private struct RoutineEnvelope: Decodable { var routine: Routine }
    private struct ExecutionsEnvelope: Decodable { var executions: [RoutineExecution] }
    private struct ApprovalsEnvelope: Decodable { var approvals: [Approval] }

    // MARK: - Health

    public func health() async throws -> Health {
        try await send(request("GET", "v1/health"), as: Health.self)
    }

    // MARK: - Bots

    public func bots() async throws -> [FeedEntry] {
        try await send(request("GET", "v1/bots"), as: BotsEnvelope.self).bots
    }

    public struct BotDraft: Sendable {
        public var slug: String
        public var displayName: String
        public var title: String
        public var description: String
        public var avatarColor: String
        public var avatarGlyph: String
        public var approvalBoundary: String?

        public init(
            slug: String, displayName: String, title: String, description: String,
            avatarColor: String, avatarGlyph: String, approvalBoundary: String? = nil
        ) {
            self.slug = slug
            self.displayName = displayName
            self.title = title
            self.description = description
            self.avatarColor = avatarColor
            self.avatarGlyph = avatarGlyph
            self.approvalBoundary = approvalBoundary
        }
    }

    public func createBot(_ draft: BotDraft) async throws -> Bot {
        let payload = try body([
            "slug": draft.slug,
            "display_name": draft.displayName,
            "title": draft.title,
            "description": draft.description,
            "avatar_color": draft.avatarColor,
            "avatar_glyph": draft.avatarGlyph,
            "approval_boundary": draft.approvalBoundary ?? "",
        ])
        return try await send(request("POST", "v1/bots", body: payload), as: BotEnvelope.self).bot
    }

    public func bot(id: String) async throws -> Bot {
        try await send(request("GET", "v1/bots/\(id)"), as: BotEnvelope.self).bot
    }

    /// PATCH with only the provided fields.
    public func updateBot(id: String, fields: [String: Any?]) async throws -> Bot {
        try await send(request("PATCH", "v1/bots/\(id)", body: body(fields)), as: BotEnvelope.self).bot
    }

    public func deleteBot(id: String, purge: Bool = false) async throws {
        let query = purge ? [URLQueryItem(name: "purge", value: "true")] : []
        try await sendExpectingNoContent(request("DELETE", "v1/bots/\(id)", query: query))
    }

    public func memory(botId: String) async throws -> BotMemory {
        try await send(request("GET", "v1/bots/\(botId)/memory"), as: BotMemory.self)
    }

    // MARK: - Sessions & messages

    public func sessions(botId: String) async throws -> [Session] {
        try await send(request("GET", "v1/bots/\(botId)/sessions"), as: SessionsEnvelope.self).sessions
    }

    public func createSession(botId: String, title: String? = nil) async throws -> Session {
        try await send(
            request("POST", "v1/bots/\(botId)/sessions", body: body(["title": title])),
            as: SessionEnvelope.self
        ).session
    }

    public func messages(sessionId: String, before: String? = nil, limit: Int = 50) async throws -> [Message] {
        var query = [URLQueryItem(name: "limit", value: String(limit))]
        if let before { query.append(URLQueryItem(name: "before", value: before)) }
        return try await send(request("GET", "v1/sessions/\(sessionId)/messages", query: query), as: MessagesEnvelope.self).messages
    }

    public func markRead(sessionId: String, lastMessageId: String) async throws {
        try await sendExpectingNoContent(
            request("POST", "v1/sessions/\(sessionId)/read", body: body(["message_id": lastMessageId]))
        )
    }

    public func stop(sessionId: String) async throws {
        try await sendExpectingNoContent(request("POST", "v1/sessions/\(sessionId)/stop"))
    }

    // MARK: - Chat streaming

    public func chatStream(
        sessionId: String,
        text: String,
        images: [ImageAttachment] = []
    ) -> AsyncThrowingStream<ChatEvent, Error> {
        let request: URLRequest
        do {
            let message: Any
            if images.isEmpty {
                message = text
            } else {
                var parts: [[String: Any]] = []
                if !text.isEmpty {
                    parts.append(["type": "text", "text": text])
                }
                parts.append(contentsOf: images.map { image in
                    [
                        "type": "image_url",
                        "image_url": ["url": image.url, "detail": "auto"],
                    ]
                })
                message = parts
            }
            request = self.request("POST", "v1/sessions/\(sessionId)/chat", body: try body(["message": message]))
        } catch {
            return AsyncThrowingStream { $0.finish(throwing: error) }
        }
        let session = self.session
        let decoder = self.decoder
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let (bytes, response) = try await session.bytes(for: request)
                    try Self.checkStatus(response: response, data: Data(), decoder: decoder)
                    for try await sse in bytes.sseMessages() {
                        guard let name = sse.event, let data = sse.data.data(using: .utf8) else { continue }
                        if let event = try StreamEventDecoding.chatEvent(name: name, data: data, decoder: decoder) {
                            continuation.yield(event)
                            if case .messageComplete = event { break }
                            if case .error = event { break }
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    // MARK: - Events firehose

    /// Auto-reconnecting global event stream. Never throws; reconnects with
    /// capped exponential backoff until the consuming task is cancelled.
    public func eventsStream() -> AsyncStream<ServerEvent> {
        let session = self.session
        let decoder = self.decoder
        let makeRequest: @Sendable () -> URLRequest = { self.request("GET", "v1/events") }
        return AsyncStream { continuation in
            let task = Task {
                var backoff: Duration = .seconds(1)
                while !Task.isCancelled {
                    do {
                        let (bytes, response) = try await session.bytes(for: makeRequest())
                        try Self.checkStatus(response: response, data: Data(), decoder: decoder)
                        backoff = .seconds(1)
                        for try await sse in bytes.sseMessages() {
                            guard let name = sse.event, let data = sse.data.data(using: .utf8) else { continue }
                            if let event = try? StreamEventDecoding.serverEvent(name: name, data: data, decoder: decoder) {
                                continuation.yield(event)
                            }
                        }
                    } catch is CancellationError {
                        break
                    } catch {
                        // fall through to backoff
                    }
                    try? await Task.sleep(for: backoff)
                    backoff = min(backoff * 2, .seconds(30))
                }
                continuation.finish()
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    // MARK: - Routines

    public func routines(botId: String) async throws -> [Routine] {
        try await send(request("GET", "v1/bots/\(botId)/routines"), as: RoutinesEnvelope.self).routines
    }

    public func createRoutine(botId: String, name: String, schedule: String, prompt: String) async throws -> Routine {
        try await send(
            request("POST", "v1/bots/\(botId)/routines",
                    body: body(["name": name, "schedule": schedule, "prompt": prompt])),
            as: RoutineEnvelope.self
        ).routine
    }

    public func updateRoutine(id: String, fields: [String: Any?]) async throws -> Routine {
        try await send(request("PATCH", "v1/routines/\(id)", body: body(fields)), as: RoutineEnvelope.self).routine
    }

    public func deleteRoutine(id: String) async throws {
        try await sendExpectingNoContent(request("DELETE", "v1/routines/\(id)"))
    }

    public func runRoutine(id: String) async throws {
        try await sendExpectingNoContent(request("POST", "v1/routines/\(id)/run"))
    }

    public func pauseRoutine(id: String) async throws {
        try await sendExpectingNoContent(request("POST", "v1/routines/\(id)/pause"))
    }

    public func resumeRoutine(id: String) async throws {
        try await sendExpectingNoContent(request("POST", "v1/routines/\(id)/resume"))
    }

    public func routineExecutions(id: String, limit: Int = 20) async throws -> [RoutineExecution] {
        try await send(
            request("GET", "v1/routines/\(id)/executions", query: [URLQueryItem(name: "limit", value: String(limit))]),
            as: ExecutionsEnvelope.self
        ).executions
    }

    // MARK: - Google authentication

    private struct AuthorizationEnvelope: Decodable { var authorization: Authorization }

    /// Drives `/v1/auth/google`. An empty call starts the flow. Pass `code`
    /// (the pasted redirect URL) or `clientSecretJSON` to continue it.
    public func connectGoogle(
        code: String? = nil,
        clientSecretJSON: String? = nil
    ) async throws -> ConnectResult {
        var payload: [String: Any?] = [:]
        if let code { payload["code"] = code }
        if let clientSecretJSON { payload["client_secret_json"] = clientSecretJSON }
        let request = self.request("POST", "v1/auth/google", body: try body(payload))
        let (data, response) = try await session.data(for: request)
        try Self.checkStatus(response: response, data: data, decoder: decoder)
        if let auth = try? decoder.decode(AuthorizationEnvelope.self, from: data) {
            return .authorizationRequired(auth.authorization)
        }
        return .connected(try decoder.decode(IntegrationEnvelope.self, from: data).integration)
    }

    public func disconnectGoogle() async throws -> Integration {
        try await send(request("DELETE", "v1/auth/google"), as: IntegrationEnvelope.self).integration
    }

    // MARK: - MCP servers

    private struct McpServersEnvelope: Decodable {
        var servers: [McpServer]
        var presets: [McpServer]
    }
    private struct McpServerEnvelope: Decodable { var server: McpServer; var restarted: Bool? }
    private struct McpAuthorizationEnvelope: Decodable {
        var authorization: McpAuthorization
        var serverState: McpServer?
    }

    /// Returns the installed servers and the presets not yet added.
    public func mcpServers() async throws -> (servers: [McpServer], presets: [McpServer]) {
        let envelope = try await send(request("GET", "v1/mcp"), as: McpServersEnvelope.self)
        return (envelope.servers, envelope.presets)
    }

    public func addMcpServer(
        name: String,
        url: String,
        auth: String = "oauth",
        headers: [String: String] = [:]
    ) async throws -> McpServer {
        var payload: [String: Any?] = ["url": url, "auth": auth]
        if !headers.isEmpty { payload["headers"] = headers }
        return try await send(
            request("PUT", "v1/mcp/\(name)", body: try body(payload)),
            as: McpServerEnvelope.self
        ).server
    }

    public func removeMcpServer(name: String) async throws -> McpServer {
        try await send(request("DELETE", "v1/mcp/\(name)"), as: McpServerEnvelope.self).server
    }

    /// Starts the browser OAuth flow. Poll `mcpAuthorization` until it settles.
    public func authorizeMcpServer(name: String) async throws -> McpAuthorization {
        try await send(
            request("POST", "v1/mcp/\(name)/authorize", body: try body([:])),
            as: McpAuthorizationEnvelope.self
        ).authorization
    }

    public func mcpAuthorization(
        flowId: String
    ) async throws -> (authorization: McpAuthorization, server: McpServer?) {
        let envelope = try await send(
            request("GET", "v1/mcp/authorizations/\(flowId)"),
            as: McpAuthorizationEnvelope.self
        )
        return (envelope.authorization, envelope.serverState)
    }

    // MARK: - Integrations

    private struct IntegrationsEnvelope: Decodable { var integrations: [Integration] }
    private struct IntegrationEnvelope: Decodable { var integration: Integration }

    public func integrations() async throws -> [Integration] {
        try await send(request("GET", "v1/integrations"), as: IntegrationsEnvelope.self).integrations
    }

    public func setIntegration(key: String, value: String) async throws -> Integration {
        try await send(
            request("PUT", "v1/integrations/\(key)", body: try body(["value": value])),
            as: IntegrationEnvelope.self
        ).integration
    }

    public func removeIntegration(key: String) async throws -> Integration {
        try await send(request("DELETE", "v1/integrations/\(key)"), as: IntegrationEnvelope.self).integration
    }

    // MARK: - Search

    public func search(_ query: String, botId: String? = nil) async throws -> [Message] {
        var items = [URLQueryItem(name: "q", value: query)]
        if let botId { items.append(URLQueryItem(name: "bot_id", value: botId)) }
        return try await send(request("GET", "v1/search", query: items), as: MessagesEnvelope.self).messages
    }

    // MARK: - Approvals

    public func approvals() async throws -> [Approval] {
        try await send(request("GET", "v1/approvals"), as: ApprovalsEnvelope.self).approvals
    }

    public func decide(runId: String, decision: ApprovalDecision) async throws {
        try await sendExpectingNoContent(
            request("POST", "v1/approvals/\(runId)", body: body(["decision": decision.rawValue]))
        )
    }
}
