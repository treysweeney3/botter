import Foundation
import Observation

@MainActor
@Observable
public final class RoutineStore {
    public private(set) var routines: [Routine] = []
    public private(set) var executions: [String: [RoutineExecution]] = [:]
    public private(set) var lastError: String?
    public let botId: String

    private let client: BotterClient

    public init(client: BotterClient, botId: String) {
        self.client = client
        self.botId = botId
    }

    public func refresh() async {
        do {
            routines = try await client.routines(botId: botId)
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
    }

    public func loadExecutions(routineId: String) async {
        do {
            executions[routineId] = try await client.routineExecutions(id: routineId)
        } catch {
            lastError = error.localizedDescription
        }
    }

    public func create(name: String, schedule: String, prompt: String) async {
        do {
            let routine = try await client.createRoutine(botId: botId, name: name, schedule: schedule, prompt: prompt)
            routines.append(routine)
        } catch {
            lastError = error.localizedDescription
        }
    }

    public func update(id: String, fields: [String: String]) async {
        do {
            let updated = try await client.updateRoutine(id: id, fields: fields)
            if let index = routines.firstIndex(where: { $0.id == id }) {
                routines[index] = updated
            }
        } catch {
            lastError = error.localizedDescription
        }
    }

    public func setPaused(_ routine: Routine, paused: Bool) async {
        do {
            if paused {
                try await client.pauseRoutine(id: routine.id)
            } else {
                try await client.resumeRoutine(id: routine.id)
            }
            await refresh()
        } catch {
            lastError = error.localizedDescription
        }
    }

    public func runNow(_ routine: Routine) async {
        do {
            try await client.runRoutine(id: routine.id)
        } catch {
            lastError = error.localizedDescription
        }
    }

    public func delete(_ routine: Routine) async {
        do {
            try await client.deleteRoutine(id: routine.id)
            routines.removeAll { $0.id == routine.id }
        } catch {
            lastError = error.localizedDescription
        }
    }
}
