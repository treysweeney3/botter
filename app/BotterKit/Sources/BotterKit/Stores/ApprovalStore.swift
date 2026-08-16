import Foundation
import Observation

@MainActor
@Observable
public final class ApprovalStore {
    public private(set) var pending: [Approval] = []
    public private(set) var lastError: String?

    private let client: BotterClient

    public init(client: BotterClient) {
        self.client = client
    }

    public var badgeCount: Int { pending.count }

    public func refresh() async {
        do {
            pending = try await client.approvals()
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
    }

    public func decide(runId: String, decision: ApprovalDecision) async {
        do {
            try await client.decide(runId: runId, decision: decision)
            pending.removeAll { $0.runId == runId }
        } catch {
            lastError = error.localizedDescription
        }
    }

    /// Reconciliation from the events firehose.
    public func apply(_ event: ServerEvent) {
        switch event {
        case .approvalPending(let approval):
            if !pending.contains(where: { $0.runId == approval.runId }) {
                pending.append(approval)
            }
        case .approvalResolved(let runId, _):
            pending.removeAll { $0.runId == runId }
        default:
            break
        }
    }
}
