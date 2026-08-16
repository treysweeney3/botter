import SwiftUI

/// Design tokens from docs/SPEC.md §5. The app is dark-only by design —
/// these are absolute values, not semantic light/dark pairs.
public enum Tokens {
    // MARK: Surfaces
    public static let windowBackground = Color(hex: 0x0D0D0D)
    public static let sidebarBackground = Color(hex: 0x161618)
    public static let cardBackground = Color(hex: 0x1E1E20)
    public static let hairline = Color(hex: 0x2A2A2C)

    // MARK: Text
    public static let textPrimary = Color(hex: 0xECECEC)
    public static let textSecondary = Color(hex: 0x8A8A8E)

    // MARK: User bubble (inverted)
    public static let userBubbleBackground = Color.white
    public static let userBubbleText = Color.black

    // MARK: Radii
    public static let bubbleRadius: CGFloat = 16
    public static let cardRadius: CGFloat = 12

    // MARK: Type
    public static let sidebarBody = Font.system(size: 13)
    public static let sidebarName = Font.system(size: 13, weight: .semibold)
    public static let chatBody = Font.system(size: 14)
    public static let timestamp = Font.system(size: 11)
    public static let chip = Font.system(size: 11, weight: .medium)

    // MARK: Layout
    public static let sidebarWidth: CGFloat = 285
    public static let avatarSize: CGFloat = 36
}

/// The 8-color bot avatar palette (SPEC §5). Wire format is the hex string.
public enum BotPalette {
    public static let colors: [(name: String, hex: String)] = [
        ("teal", "#2EC7A6"),
        ("orange", "#E8833A"),
        ("purple", "#8B5CF6"),
        ("blue", "#3B82F6"),
        ("red", "#EF4444"),
        ("green", "#22C55E"),
        ("yellow", "#EAB308"),
        ("pink", "#EC4899"),
    ]

    public static func color(for hex: String) -> Color {
        Color(hexString: hex) ?? Tokens.textSecondary
    }
}

extension Color {
    public init(hex: UInt32) {
        self.init(
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255
        )
    }

    public init?(hexString: String) {
        var text = hexString.trimmingCharacters(in: .whitespaces)
        if text.hasPrefix("#") { text.removeFirst() }
        guard text.count == 6, let value = UInt32(text, radix: 16) else { return nil }
        self.init(hex: value)
    }
}
