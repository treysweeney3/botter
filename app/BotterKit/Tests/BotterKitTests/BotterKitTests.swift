import Foundation
import Testing
@testable import BotterKit

@Suite struct SSEParserTests {
    @Test func parsesNamedEvents() {
        var parser = SSEParser()
        var messages: [SSEMessage] = []
        let lines = [
            "event: delta",
            "data: {\"text\": \"hi\"}",
            "",
            ": heartbeat",
            "event: message_complete",
            "data: {\"message\": {}}",
            "",
        ]
        for line in lines {
            if let message = parser.consume(line: line) { messages.append(message) }
        }
        #expect(messages.count == 2)
        #expect(messages[0].event == "delta")
        #expect(messages[0].data == "{\"text\": \"hi\"}")
        #expect(messages[1].event == "message_complete")
    }

    @Test func multilineDataJoinsWithNewline() {
        var parser = SSEParser()
        _ = parser.consume(line: "data: line1")
        _ = parser.consume(line: "data: line2")
        let message = parser.consume(line: "")
        #expect(message?.data == "line1\nline2")
    }

    @Test func commentOnlyBlockEmitsNothing() {
        var parser = SSEParser()
        _ = parser.consume(line: ": keepalive")
        #expect(parser.consume(line: "") == nil)
    }

    @Test func byteStreamParsing() async throws {
        let raw = "event: delta\ndata: {\"text\": \"a\"}\n\nevent: delta\r\ndata: {\"text\": \"b\"}\r\n\r\n"
        let stream = AsyncThrowingStream<UInt8, Error> { continuation in
            for byte in Array(raw.utf8) { continuation.yield(byte) }
            continuation.finish()
        }
        var events: [SSEMessage] = []
        for try await message in stream.sseMessages() {
            events.append(message)
        }
        #expect(events.count == 2)
        #expect(events[1].data == "{\"text\": \"b\"}")
    }
}

@Suite struct ModelDecodingTests {
    let decoder = BotterClient.makeDecoder()

    @Test func decodesEveryMessageKind() throws {
        let json = """
        [
          {"id": "1", "session_id": "s", "bot_id": "b", "role": "assistant", "kind": "text",
           "text": "hello", "created_at": "2026-08-13T20:00:00Z"},
          {"id": "2", "session_id": "s", "bot_id": "b", "role": "assistant", "kind": "task_report",
           "text": "done", "task_items": [{"label": "Salesforce", "detail": "52 accounts", "state": "done"}],
           "created_at": "2026-08-13T20:00:01.123456Z"},
          {"id": "3", "session_id": "s", "bot_id": "b", "role": "system", "kind": "routine_created",
           "routine": {"id": "r1", "name": "Overnight outbound"}},
          {"id": "4", "session_id": "s", "bot_id": "b", "role": "assistant", "kind": "approval_request",
           "text": "Send 8 emails?"},
          {"id": "5", "session_id": "s", "bot_id": "b", "role": "user", "kind": "attachment",
           "text": "What is this?", "attachments": [{"type": "image", "url": "data:image/png;base64,aA==",
           "media_type": "image/png", "filename": "sample.png"}]},
          {"id": "6", "session_id": "s", "bot_id": "b", "role": "assistant", "kind": "somefuturething",
           "text": "future"}
        ]
        """
        let messages = try decoder.decode([Message].self, from: Data(json.utf8))
        #expect(messages.count == 6)
        #expect(messages[0].kind == .text)
        #expect(messages[1].kind == .taskReport)
        #expect(messages[1].taskItems?.first?.label == "Salesforce")
        #expect(messages[1].createdAt != nil)  // fractional-seconds ISO date
        #expect(messages[2].routine?.name == "Overnight outbound")
        #expect(messages[3].kind == .approvalRequest)
        #expect(messages[4].kind == .attachment)
        #expect(messages[4].attachments?.first?.filename == "sample.png")
        #expect(messages[4].attachments?.first?.mediaType == "image/png")
        #expect(messages[5].kind == .unknown("somefuturething"))
    }

    @Test func decodesRosterEntry() throws {
        // Flattened roster shape from GET /v1/bots (SPEC §4 envelope pin),
        // with pydantic-style dates (+00:00 offset, microsecond fractions).
        let json = """
        {"bots": [
          {"id": "b1", "slug": "sales-outbound", "display_name": "Sales Outbound",
           "title": "Pipeline Development", "description": "Runs outbound",
           "avatar_color": "#2EC7A6", "avatar_glyph": "bolt",
           "approval_boundary": "Ask first.", "default_session_id": "s1", "archived": false,
           "created_at": "2026-07-14T20:00:00+00:00", "updated_at": "2026-08-13T20:00:00.123456+00:00",
           "latest_message_preview": "queued 8 follow-ups",
           "latest_message_at": "2026-08-13T20:00:00.123456+00:00", "unread_count": 2}
        ]}
        """
        struct Envelope: Decodable { var bots: [FeedEntry] }
        let entry = try #require(try decoder.decode(Envelope.self, from: Data(json.utf8)).bots.first)
        #expect(entry.bot.displayName == "Sales Outbound")
        #expect(entry.preview == "queued 8 follow-ups")
        #expect(entry.previewAt != nil)
        #expect(entry.unreadCount == 2)
        #expect(entry.id == "b1")
    }
}

@Suite struct StreamEventDecodingTests {
    let decoder = BotterClient.makeDecoder()

    private func chatEvent(_ name: String, _ json: String) throws -> ChatEvent? {
        try StreamEventDecoding.chatEvent(name: name, data: Data(json.utf8), decoder: decoder)
    }

    @Test func decodesChatEvents() throws {
        #expect(try chatEvent("delta", #"{"text": "hi"}"#) == .delta(text: "hi"))
        #expect(
            try chatEvent("tool_event", #"{"name": "terminal", "status": "started", "summary": "Runs echo"}"#)
                == .toolEvent(name: "terminal", status: "started", summary: "Runs echo")
        )
        #expect(
            try chatEvent("approval_required", #"{"run_id": "run_1", "summary": "Send emails?"}"#)
                == .approvalRequired(runId: "run_1", summary: "Send emails?")
        )
        if case .messageComplete(let message)? = try chatEvent(
            "message_complete",
            #"{"message": {"id": "m1", "session_id": "s", "bot_id": "b", "role": "assistant", "kind": "text", "text": "done"}}"#
        ) {
            #expect(message.id == "m1")
        } else {
            Issue.record("message_complete failed to decode")
        }
        #expect(try chatEvent("unknown_event", "{}") == nil)
    }

    @Test func decodesServerEvents() throws {
        let feed = try StreamEventDecoding.serverEvent(
            name: "feed_updated", data: Data(#"{"bot_id": null}"#.utf8), decoder: decoder
        )
        #expect(feed == .feedUpdated(botId: nil))

        let approval = try StreamEventDecoding.serverEvent(
            name: "approval_pending",
            data: Data(#"{"approval": {"run_id": "run_9", "bot_id": "b", "summary": "s", "requested_at": "2026-08-13T20:00:00Z"}}"#.utf8),
            decoder: decoder
        )
        #expect(approval == .approvalPending(Approval(
            runId: "run_9", botId: "b", summary: "s",
            requestedAt: ISO8601DateFormatter().date(from: "2026-08-13T20:00:00Z")
        )))

        let mcp = try StreamEventDecoding.serverEvent(
            name: "mcp_updated", data: Data(#"{"name": "composio", "status": "connected"}"#.utf8), decoder: decoder
        )
        #expect(mcp == .mcpUpdated(name: "composio", status: "connected"))

        let unknown = try StreamEventDecoding.serverEvent(name: "next_big_thing", data: Data("{}".utf8), decoder: decoder)
        #expect(unknown == .unknown(event: "next_big_thing"))
    }

    @Test func decodesMergedCredentialShapes() throws {
        let authJSON = #"""
        {"url": "https://accounts.google.com/x", "instructions": "Paste the redirect URL.",
         "code_entry": true, "needs_client_secret": false}
        """#
        let auth = try decoder.decode(Authorization.self, from: Data(authJSON.utf8))
        #expect(auth.codeEntry == true)
        #expect(auth.needsClientSecret == false)

        let integrationJSON = #"""
        {"key": "BRAVE_API_KEY", "label": "Brave API key", "description": "Search.",
         "url": null, "category": "tool", "kind": "integration", "is_set": true,
         "redacted_value": "••••1234", "is_password": true, "advanced": false, "custom": false,
         "sync_status": "synced", "sync_detail": "Available to every Botter bot.",
         "status": "connected", "detail": null, "group": null, "group_label": null,
         "required": true, "restart_after_write": false, "auth": "value"}
        """#
        let integration = try decoder.decode(Integration.self, from: Data(integrationJSON.utf8))
        #expect(integration.kind == "integration")
        #expect(integration.isPassword == true)
        #expect(integration.syncStatus == "synced")
        #expect(integration.syncDetail == "Available to every Botter bot.")
        #expect(integration.status == "connected")
        #expect(integration.isReadOnly == false)

        // A curated app is the same row shape, with the grouping overlay.
        let curatedJSON = #"""
        {"key": "VERCEL_TEAM_ID", "label": "Vercel Team ID", "description": "",
         "url": null, "category": "tool", "kind": "integration", "is_set": false,
         "redacted_value": null, "is_password": true, "advanced": false, "custom": false,
         "sync_status": null, "sync_detail": null, "status": "not_connected",
         "detail": null, "group": "vercel", "group_label": "Vercel",
         "required": false, "restart_after_write": false, "auth": "value"}
        """#
        let curated = try decoder.decode(Integration.self, from: Data(curatedJSON.utf8))
        #expect(curated.group == "vercel")
        #expect(curated.required == false)

        // Slack is display-only.
        let slackJSON = #"""
        {"key": "SLACK", "label": "Slack", "description": "", "url": null,
         "category": "tool", "kind": "integration", "is_set": true, "redacted_value": null,
         "is_password": false, "advanced": false, "custom": false, "sync_status": null,
         "sync_detail": null, "status": "connected", "detail": "Managed by Hermes.",
         "group": "slack", "group_label": "Slack", "required": true,
         "restart_after_write": false, "auth": "external"}
        """#
        let slack = try decoder.decode(Integration.self, from: Data(slackJSON.utf8))
        #expect(slack.isReadOnly == true)

        // An MCP server added but not yet authorized cannot be used by any bot.
        let mcpJSON = #"""
        {"name": "composio", "label": "Composio", "description": "About a thousand apps.",
         "url": "https://connect.composio.dev/mcp", "command": null, "auth": "oauth",
         "enabled": true, "preset": "composio", "docs_url": "https://docs.composio.dev",
         "authorized": false, "status": "not_connected",
         "detail": "Authorization is required before bots can use it.",
         "sync_status": "synced", "sync_detail": "Available to every Botter bot."}
        """#
        let server = try decoder.decode(McpServer.self, from: Data(mcpJSON.utf8))
        #expect(server.preset == "composio")
        #expect(server.needsAuthorization == true)

        let flowJSON = #"""
        {"flow_id": "abc", "server": "composio", "status": "authorization_required",
         "url": "https://login.composio.dev/authorize", "instructions": "Sign in.", "error": null}
        """#
        let flow = try decoder.decode(McpAuthorization.self, from: Data(flowJSON.utf8))
        #expect(flow.isSettled == false)
        #expect(flow.url != nil)

        // Approved by the provider, but botterd is still fanning the grant out
        // and restarting the gateway. Not settled: the app keeps polling.
        let finishingJSON = #"""
        {"flow_id": "abc", "server": "composio", "status": "finishing",
         "url": null, "instructions": "Approved. Copying the grant.", "error": null}
        """#
        let finishing = try decoder.decode(McpAuthorization.self, from: Data(finishingJSON.utf8))
        #expect(finishing.isFinishing == true)
        #expect(finishing.isSettled == false)

        let approvedJSON = #"""
        {"flow_id": "abc", "server": "composio", "status": "approved",
         "url": null, "instructions": "", "error": null}
        """#
        #expect(try decoder.decode(McpAuthorization.self, from: Data(approvedJSON.utf8)).isSettled)
    }

    /// The link appearing and the approval registering both land early in their
    /// phase, so the poll has to be quick there and may relax afterwards.
    @Test func mcpPollingIsQuickWhileSomeoneIsWatching() {
        #expect(ConnectionsStore.pollInterval(after: .zero) == .milliseconds(400))
        #expect(ConnectionsStore.pollInterval(after: .seconds(3)) == .milliseconds(400))
        #expect(ConnectionsStore.pollInterval(after: .seconds(10)) == .seconds(1))
        #expect(ConnectionsStore.pollInterval(after: .seconds(120)) == .seconds(2))
    }

    @MainActor
    @Test func groupsCuratedKeysIntoOneCardWithRequiredFirst() {
        let store = ConnectionsStore(
            client: BotterClient(configuration: ClientConfiguration(
                baseURL: URL(string: "http://127.0.0.1:1")!,
                token: { "test-token" }
            ))
        )
        store.setIntegrationsForTesting([
            Integration.stub(key: "VERCEL_TEAM_ID", group: "vercel", required: false),
            Integration.stub(key: "VERCEL_TOKEN", group: "vercel", required: true),
            Integration.stub(key: "BRAVE_API_KEY"),
            Integration.stub(key: "WEATHER_UNITS", kind: "config"),
        ])

        let cards = store.cards
        #expect(cards.count == 2)
        #expect(cards[0].map(\Integration.key) == ["VERCEL_TOKEN", "VERCEL_TEAM_ID"])
        #expect(cards[1].map(\Integration.key) == ["BRAVE_API_KEY"])
        #expect(store.configRows.map(\Integration.key) == ["WEATHER_UNITS"])
    }
}

extension Integration {
    static func stub(
        key: String,
        kind: String = "integration",
        group: String? = nil,
        required: Bool = true
    ) -> Integration {
        Integration(
            key: key, label: key, description: "", url: nil, category: "tool", kind: kind,
            isSet: false, redactedValue: nil, isPassword: true, advanced: false, custom: false,
            syncStatus: nil, syncDetail: nil, status: "not_connected", detail: nil,
            group: group, groupLabel: group, required: required,
            restartAfterWrite: false, auth: "value"
        )
    }
}

@Suite struct TextHygieneTests {
    @Test func collapsesBlankLineRuns() {
        #expect("a\n\n\n\nb".collapsedBlankLines == "a\n\nb")
        #expect("a\nb".collapsedBlankLines == "a\nb")
        #expect("\n\n a \n\n".collapsedBlankLines == "a")
        #expect("one\n\ntwo".collapsedBlankLines == "one\n\ntwo")
    }
}

@Suite struct MessageBlockParserTests {
    @Test func parsesCodeFence() {
        let blocks = MessageBlockParser.parse("Here you go:\n```ts:churn.ts\nconst a = 1;\n```\nDone.")
        #expect(blocks.count == 3)
        #expect(blocks[0] == .text("Here you go:"))
        #expect(blocks[1] == .code(language: "ts", filename: "churn.ts", code: "const a = 1;"))
        #expect(blocks[2] == .text("Done."))
    }

    @Test func parsesTable() {
        let text = """
        Results:
        | Task | Status |
        | --- | :---: |
        | Restock | To do |
        | Churn | Done |
        """
        let blocks = MessageBlockParser.parse(text)
        #expect(blocks.count == 2)
        #expect(blocks[1] == .table(headers: ["Task", "Status"], rows: [["Restock", "To do"], ["Churn", "Done"]]))
    }

    @Test func parsesChartFence() {
        let text = """
        ```chart
        {"title": "Trend", "series": [{"name": "Mint Chip", "points": [1, 2, 1.5]}]}
        ```
        """
        let blocks = MessageBlockParser.parse(text)
        guard case .chart(let spec)? = blocks.first else {
            Issue.record("expected chart block")
            return
        }
        #expect(spec.title == "Trend")
        #expect(spec.series.first?.points == [1, 2, 1.5])
    }

    @Test func invalidChartFallsBackToCode() {
        let blocks = MessageBlockParser.parse("```chart\nnot json\n```")
        guard case .code? = blocks.first else {
            Issue.record("expected code fallback")
            return
        }
    }

    @Test func plainTextIsOneBlock() {
        #expect(MessageBlockParser.parse("hello\n\nworld") == [.text("hello\n\nworld")])
    }
}

@Suite struct CronTextTests {
    @Test func describesCommonSchedules() {
        #expect(CronText.describe("0 * * * *") == "Every hour")
        #expect(CronText.describe("*/5 * * * *") == "Every 5 minutes")
        #expect(CronText.describe("0 9 * * *") == "Daily at 9am")
        #expect(CronText.describe("30 14 * * *") == "Daily at 2:30pm")
        #expect(CronText.describe("0 9 * * 1-5") == "Weekdays at 9am")
        #expect(CronText.describe("0 9 * * 1") == "Mondays at 9am")
        #expect(CronText.describe("0 0 1 1 *") == "0 0 1 1 *")  // unsupported → verbatim
        #expect(CronText.describe("garbage") == "garbage")
    }
}

@Suite struct SlugTests {
    @Test func slugifiesNames() {
        #expect(Slug.make("Sales Outbound") == "sales-outbound")
        #expect(Slug.make("Chief of Staff!") == "chief-of-staff")
        #expect(Slug.make("  Émile's   Bot  ") == "emile-s-bot")
        #expect(Slug.make("x") == "x")
    }
}

@Suite struct AvatarContrastTests {
    /// Resolve a palette entry the way `AvatarView` draws it.
    private func disc(_ hex: String) -> RGB {
        try! #require(RGB(hexString: hex)).darkened(
            toContrast: BotPalette.minimumContrast, against: .white
        )
    }

    @Test func everyPaletteColorCarriesTheWhiteOtterAtThreeToOne() {
        for entry in BotPalette.colors {
            let ratio = disc(entry.hex).contrast(with: .white)
            #expect(
                ratio >= BotPalette.minimumContrast,
                "\(entry.name) (\(entry.hex)) draws its otter at \(ratio):1"
            )
        }
    }

    /// The reason the adjustment exists. If this ever stops holding, the
    /// darkening can be deleted.
    @Test func rawPaletteWouldFailOnHalfTheColors() {
        let failing = BotPalette.colors.filter { entry in
            guard let raw = RGB(hexString: entry.hex) else { return false }
            return raw.contrast(with: .white) < BotPalette.minimumContrast
        }
        #expect(failing.map(\.name).sorted() == ["green", "orange", "teal", "yellow"])
    }

    /// Darkening must stop at the threshold — overshooting would mute the
    /// palette more than legibility requires.
    @Test func colorsAreDarkenedNoFurtherThanNeeded() {
        for entry in BotPalette.colors {
            let ratio = disc(entry.hex).contrast(with: .white)
            #expect(ratio <= BotPalette.minimumContrast + 0.01 || ratio == rawRatio(entry.hex))
        }
    }

    private func rawRatio(_ hex: String) -> Double {
        (RGB(hexString: hex) ?? .white).contrast(with: .white)
    }

    /// Colors that already pass are returned untouched.
    @Test func alreadyLegibleColorsAreLeftAlone() {
        for name in ["purple", "blue", "red", "pink"] {
            let hex = BotPalette.colors.first { $0.name == name }!.hex
            #expect(disc(hex) == RGB(hexString: hex))
        }
    }

    @Test func unknownColorDoesNotCrash() {
        _ = BotPalette.color(for: "nonsense")
    }
}
