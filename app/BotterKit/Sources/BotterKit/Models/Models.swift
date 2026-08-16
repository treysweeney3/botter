import Foundation

// Wire models for the botterd contract (docs/SPEC.md §4). Decoded with
// .convertFromSnakeCase, so property names are the camelCase mirror of the
// wire fields and none of these types declare CodingKeys.

public struct Bot: Codable, Identifiable, Hashable, Sendable {
    public var id: String
    public var slug: String
    public var displayName: String
    public var title: String
    public var description: String
    public var avatarColor: String
    public var avatarGlyph: String
    public var approvalBoundary: String?
    public var defaultSessionId: String?
    public var archived: Bool
    public var createdAt: Date?
    public var updatedAt: Date?

    public init(
        id: String,
        slug: String,
        displayName: String,
        title: String,
        description: String,
        avatarColor: String,
        avatarGlyph: String,
        approvalBoundary: String? = nil,
        defaultSessionId: String? = nil,
        archived: Bool = false,
        createdAt: Date? = nil,
        updatedAt: Date? = nil
    ) {
        self.id = id
        self.slug = slug
        self.displayName = displayName
        self.title = title
        self.description = description
        self.avatarColor = avatarColor
        self.avatarGlyph = avatarGlyph
        self.approvalBoundary = approvalBoundary
        self.defaultSessionId = defaultSessionId
        self.archived = archived
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }
}

/// One sidebar row from `GET /v1/bots`: a bot object with the roster fields
/// (`latest_message_preview`, `latest_message_at`, `unread_count`) flattened on.
public struct FeedEntry: Codable, Identifiable, Hashable, Sendable {
    public var id: String
    public var slug: String
    public var displayName: String
    public var title: String
    public var description: String
    public var avatarColor: String
    public var avatarGlyph: String
    public var approvalBoundary: String?
    public var defaultSessionId: String?
    public var archived: Bool
    public var createdAt: Date?
    public var updatedAt: Date?
    public var latestMessagePreview: String?
    public var latestMessageAt: Date?
    public var unreadCount: Int

    /// Convenience projection for views/API calls that want a plain Bot.
    public var bot: Bot {
        Bot(
            id: id, slug: slug, displayName: displayName, title: title,
            description: description, avatarColor: avatarColor, avatarGlyph: avatarGlyph,
            approvalBoundary: approvalBoundary, defaultSessionId: defaultSessionId,
            archived: archived, createdAt: createdAt, updatedAt: updatedAt
        )
    }

    public var preview: String? { latestMessagePreview }
    public var previewAt: Date? { latestMessageAt }

    public init(bot: Bot, preview: String? = nil, previewAt: Date? = nil, unreadCount: Int = 0) {
        self.id = bot.id
        self.slug = bot.slug
        self.displayName = bot.displayName
        self.title = bot.title
        self.description = bot.description
        self.avatarColor = bot.avatarColor
        self.avatarGlyph = bot.avatarGlyph
        self.approvalBoundary = bot.approvalBoundary
        self.defaultSessionId = bot.defaultSessionId
        self.archived = bot.archived
        self.createdAt = bot.createdAt
        self.updatedAt = bot.updatedAt
        self.latestMessagePreview = preview
        self.latestMessageAt = previewAt
        self.unreadCount = unreadCount
    }
}

public struct Session: Codable, Identifiable, Hashable, Sendable {
    public var id: String
    public var botId: String
    public var title: String?
    public var model: String?
    public var messageCount: Int?
    public var createdAt: Date?
    public var updatedAt: Date?

    public init(
        id: String, botId: String, title: String? = nil, model: String? = nil,
        messageCount: Int? = nil, createdAt: Date? = nil, updatedAt: Date? = nil
    ) {
        self.id = id
        self.botId = botId
        self.title = title
        self.model = model
        self.messageCount = messageCount
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }
}

public enum MessageRole: String, Codable, Sendable {
    case user, assistant, system
}

/// Unknown kinds decode as `.text` so a newer botterd never crashes an older app.
public enum MessageKind: RawRepresentable, Codable, Hashable, Sendable {
    case text
    case taskReport
    case routineCreated
    case approvalRequest
    case attachment
    case unknown(String)

    public init(rawValue: String) {
        switch rawValue {
        case "text": self = .text
        case "task_report": self = .taskReport
        case "routine_created": self = .routineCreated
        case "approval_request": self = .approvalRequest
        case "attachment": self = .attachment
        default: self = .unknown(rawValue)
        }
    }

    public var rawValue: String {
        switch self {
        case .text: "text"
        case .taskReport: "task_report"
        case .routineCreated: "routine_created"
        case .approvalRequest: "approval_request"
        case .attachment: "attachment"
        case .unknown(let raw): raw
        }
    }

    public init(from decoder: Decoder) throws {
        self.init(rawValue: try decoder.singleValueContainer().decode(String.self))
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(rawValue)
    }
}

public struct TaskItem: Codable, Hashable, Sendable {
    public var label: String
    public var detail: String?
    // "done" | "failed" | "running" | "note" (display maps unknowns to neutral).
    // "note" is a sentence the agent said, not a tool step.
    public var state: String

    public init(label: String, detail: String? = nil, state: String = "done") {
        self.label = label
        self.detail = detail
        self.state = state
    }
}

public struct RoutineRef: Codable, Hashable, Sendable {
    public var id: String
    public var name: String

    public init(id: String, name: String) {
        self.id = id
        self.name = name
    }
}

public struct ImageAttachment: Codable, Hashable, Sendable {
    public var type: String
    public var url: String
    public var mediaType: String
    public var filename: String?

    public init(
        url: String,
        mediaType: String,
        filename: String? = nil,
        type: String = "image"
    ) {
        self.type = type
        self.url = url
        self.mediaType = mediaType
        self.filename = filename
    }
}

public struct Message: Codable, Identifiable, Hashable, Sendable {
    public var id: String
    public var sessionId: String
    public var botId: String
    public var role: MessageRole
    public var kind: MessageKind
    public var text: String?
    public var attachments: [ImageAttachment]?
    public var taskItems: [TaskItem]?
    public var routine: RoutineRef?
    public var createdAt: Date?

    public init(
        id: String,
        sessionId: String,
        botId: String,
        role: MessageRole,
        kind: MessageKind = .text,
        text: String? = nil,
        attachments: [ImageAttachment]? = nil,
        taskItems: [TaskItem]? = nil,
        routine: RoutineRef? = nil,
        createdAt: Date? = nil
    ) {
        self.id = id
        self.sessionId = sessionId
        self.botId = botId
        self.role = role
        self.kind = kind
        self.text = text
        self.attachments = attachments
        self.taskItems = taskItems
        self.routine = routine
        self.createdAt = createdAt
    }
}

public struct Routine: Codable, Identifiable, Hashable, Sendable {
    public var id: String
    public var botId: String
    public var name: String
    public var schedule: String  // cron expression
    public var prompt: String
    public var paused: Bool
    public var state: String?
    public var lastRunAt: Date?
    public var lastStatus: String?
    public var nextRunAt: Date?

    public init(
        id: String,
        botId: String,
        name: String,
        schedule: String,
        prompt: String,
        paused: Bool = false,
        state: String? = nil,
        lastRunAt: Date? = nil,
        lastStatus: String? = nil,
        nextRunAt: Date? = nil
    ) {
        self.id = id
        self.botId = botId
        self.name = name
        self.schedule = schedule
        self.prompt = prompt
        self.paused = paused
        self.state = state
        self.lastRunAt = lastRunAt
        self.lastStatus = lastStatus
        self.nextRunAt = nextRunAt
    }
}

public struct RoutineExecution: Codable, Identifiable, Hashable, Sendable {
    public var id: String
    public var routineId: String
    public var startedAt: Date?
    public var status: String
    public var summary: String?

    public init(id: String, routineId: String, startedAt: Date? = nil, status: String, summary: String? = nil) {
        self.id = id
        self.routineId = routineId
        self.startedAt = startedAt
        self.status = status
        self.summary = summary
    }
}

public struct Approval: Codable, Identifiable, Hashable, Sendable {
    public var runId: String
    public var botId: String
    public var sessionId: String?
    public var summary: String
    public var requestedAt: Date?

    public var id: String { runId }

    public init(runId: String, botId: String, sessionId: String? = nil, summary: String, requestedAt: Date? = nil) {
        self.runId = runId
        self.botId = botId
        self.sessionId = sessionId
        self.summary = summary
        self.requestedAt = requestedAt
    }
}

public enum ApprovalDecision: String, Codable, Sendable {
    case once, session, always, deny
}

public struct Health: Codable, Sendable {
    public struct HermesInfo: Codable, Sendable {
        public var reachable: Bool?
        public var status: String?
        public var version: String?
    }
    public var status: String?
    public var version: String?
    public var hermes: HermesInfo?
}

/// `GET /v1/bots/{id}/memory` — two markdown documents.
public struct BotMemory: Codable, Sendable {
    public var botId: String
    public var memory: String
    public var user: String
}

/// An OAuth flow returns this when user steps are required to finish.
public struct Authorization: Codable, Hashable, Sendable {
    public var url: String?
    public var instructions: String
    /// Finish by re-POSTing `code` (the pasted redirect URL).
    public var codeEntry: Bool?
    /// Provide `client_secret_json` before the flow can start.
    public var needsClientSecret: Bool?
}

/// One Hermes credential or setting (`GET /v1/integrations`).
///
/// This is the single credential row. It covers the generic env catalog and the
/// curated apps that used to live behind `/v1/connections`. Values never
/// round-trip; `redactedValue` is display-only.
public struct Integration: Codable, Identifiable, Hashable, Sendable {
    public var key: String
    public var label: String
    public var description: String
    public var url: String?
    public var category: String   // "tool" | "skill" | "provider" | "setting" | "custom"
    public var kind: String       // "integration" (service credential) | "config" (plain setting)
    public var isSet: Bool
    public var redactedValue: String?
    public var isPassword: Bool
    public var advanced: Bool
    public var custom: Bool
    public var syncStatus: String?  // "synced" | "out_of_sync" for global auth rows
    public var syncDetail: String?
    /// Rolled-up row state: "connected" | "not_connected" | "error".
    public var status: String
    public var detail: String?
    /// Several keys the app renders as one card (Vercel = token + team id).
    public var group: String?
    public var groupLabel: String?
    /// False marks an optional field inside a group.
    public var required: Bool
    public var restartAfterWrite: Bool
    /// "value" = pasted secret; "oauth" = Google; "external" = Hermes-owned.
    public var auth: String

    public var id: String { key }

    /// True when the app must not offer Save or Remove for this row.
    public var isReadOnly: Bool { auth == "external" }
}

/// One MCP server available to every bot (`GET /v1/mcp`).
///
/// An MCP server reaches a whole tool catalog behind one entry, so it needs
/// none of the per-app key plumbing an `Integration` carries.
public struct McpServer: Codable, Identifiable, Hashable, Sendable {
    public var name: String
    public var label: String
    public var description: String
    public var url: String?
    public var command: String?
    public var auth: String        // "none" | "oauth" | "header"
    public var enabled: Bool
    /// Set when the entry matches a known preset, for example "composio".
    public var preset: String?
    public var docsUrl: String?
    /// True when Hermes holds an OAuth grant for this server.
    public var authorized: Bool
    public var status: String      // "connected" | "not_connected" | "error"
    public var detail: String?
    public var syncStatus: String?
    public var syncDetail: String?

    public var id: String { name }

    /// True when the server is added but still needs the browser step.
    public var needsAuthorization: Bool { auth == "oauth" && !authorized }
}

/// One in-flight MCP OAuth flow. The app polls until it settles.
public struct McpAuthorization: Codable, Hashable, Sendable {
    public var flowId: String
    public var server: String
    /// "starting" | "authorization_required" | "approved" | "error"
    public var status: String
    public var url: String?
    public var instructions: String
    public var error: String?

    public var isSettled: Bool { status == "approved" || status == "error" }
}

public enum ConnectResult: Sendable {
    case connected(Integration)
    case authorizationRequired(Authorization)
}

public struct APIErrorPayload: Codable, Sendable {
    public struct Detail: Codable, Sendable {
        public var code: String
        public var message: String
    }
    public var error: Detail
}
