import Foundation

// Typed mirrors of the pinned SSE payload schemas in docs/SPEC.md §4.

/// Events on `POST /v1/sessions/{sid}/chat`.
public enum ChatEvent: Hashable, Sendable {
    case delta(text: String)
    case toolEvent(name: String, status: String, summary: String?)
    case approvalRequired(runId: String, summary: String)
    case messageComplete(Message)
    case error(code: String, message: String)
}

/// Events on `GET /v1/events`.
public enum ServerEvent: Hashable, Sendable {
    case botUpdated(botId: String)
    case feedUpdated(botId: String?)
    case approvalPending(Approval)
    case approvalResolved(runId: String, decision: ApprovalDecision)
    case routineFired(botId: String, routineId: String, name: String)
    case integrationUpdated(key: String, isSet: Bool)
    case mcpUpdated(name: String, status: String)
    case unknown(event: String)
}

enum StreamEventDecoding {
    struct DeltaPayload: Codable { var text: String }
    struct ToolEventPayload: Codable { var name: String; var status: String; var summary: String? }
    struct ApprovalRequiredPayload: Codable { var runId: String; var summary: String }
    struct MessageCompletePayload: Codable { var message: Message }
    struct ErrorPayload: Codable { var code: String; var message: String }

    struct BotUpdatedPayload: Codable { var botId: String }
    struct FeedUpdatedPayload: Codable { var botId: String? }
    struct ApprovalPendingPayload: Codable { var approval: Approval }
    struct ApprovalResolvedPayload: Codable { var runId: String; var decision: ApprovalDecision }
    struct RoutineFiredPayload: Codable { var botId: String; var routineId: String; var name: String }
    struct McpUpdatedPayload: Codable { var name: String; var status: String }
    struct IntegrationUpdatedPayload: Codable { var key: String; var isSet: Bool }

    static func chatEvent(name: String, data: Data, decoder: JSONDecoder) throws -> ChatEvent? {
        switch name {
        case "delta":
            let p = try decoder.decode(DeltaPayload.self, from: data)
            return .delta(text: p.text)
        case "tool_event":
            let p = try decoder.decode(ToolEventPayload.self, from: data)
            return .toolEvent(name: p.name, status: p.status, summary: p.summary)
        case "approval_required":
            let p = try decoder.decode(ApprovalRequiredPayload.self, from: data)
            return .approvalRequired(runId: p.runId, summary: p.summary)
        case "message_complete":
            let p = try decoder.decode(MessageCompletePayload.self, from: data)
            return .messageComplete(p.message)
        case "error":
            let p = try decoder.decode(ErrorPayload.self, from: data)
            return .error(code: p.code, message: p.message)
        default:
            return nil
        }
    }

    static func serverEvent(name: String, data: Data, decoder: JSONDecoder) throws -> ServerEvent {
        switch name {
        case "bot_updated":
            let p = try decoder.decode(BotUpdatedPayload.self, from: data)
            return .botUpdated(botId: p.botId)
        case "feed_updated":
            let p = try decoder.decode(FeedUpdatedPayload.self, from: data)
            return .feedUpdated(botId: p.botId)
        case "approval_pending":
            let p = try decoder.decode(ApprovalPendingPayload.self, from: data)
            return .approvalPending(p.approval)
        case "approval_resolved":
            let p = try decoder.decode(ApprovalResolvedPayload.self, from: data)
            return .approvalResolved(runId: p.runId, decision: p.decision)
        case "routine_fired":
            let p = try decoder.decode(RoutineFiredPayload.self, from: data)
            return .routineFired(botId: p.botId, routineId: p.routineId, name: p.name)
        case "integration_updated":
            let p = try decoder.decode(IntegrationUpdatedPayload.self, from: data)
            return .integrationUpdated(key: p.key, isSet: p.isSet)
        case "mcp_updated":
            let p = try decoder.decode(McpUpdatedPayload.self, from: data)
            return .mcpUpdated(name: p.name, status: p.status)
        default:
            return .unknown(event: name)
        }
    }
}
