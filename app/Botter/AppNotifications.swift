import Foundation
import UserNotifications

/// Thin wrapper over UNUserNotificationCenter for approval alerts.
/// UNUserNotificationCenter requires a signed bundle context; guard every call
/// so a bare dev build degrades to no-op instead of crashing.
enum AppNotifications {
    private static var available: Bool {
        Bundle.main.bundleIdentifier != nil
    }

    static func requestPermission() {
        guard available else { return }
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound]) { _, _ in }
    }

    static func postApproval(botName: String, summary: String) {
        guard available else { return }
        let content = UNMutableNotificationContent()
        content.title = "\(botName) needs approval"
        content.body = summary
        content.sound = .default
        let request = UNNotificationRequest(
            identifier: "approval-\(UUID().uuidString)",
            content: content,
            trigger: nil
        )
        UNUserNotificationCenter.current().add(request)
    }
}
