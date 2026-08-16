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

    /// Minimum contrast between the white otter artwork and the disc under it.
    /// 3:1 is the WCAG floor for non-text graphics, which is what an avatar is.
    static let minimumContrast = 3.0

    /// The bot's color as drawn.
    ///
    /// Half the palette is too light for a white otter to read against it —
    /// yellow sat at 1.9:1 and teal at 2.1:1, which is why those avatars
    /// looked like a pale smudge. Any color that misses the threshold is
    /// darkened just far enough to meet it and no further, so it keeps its
    /// hue and the sidebar's color coding still works.
    ///
    /// This lives here rather than in `colors` so that bots which stored a
    /// light hex before this change are corrected too, and so the editor's
    /// swatches show the same color as the avatar they produce.
    public static func color(for hex: String) -> Color {
        guard let rgb = RGB(hexString: hex) else { return Tokens.textSecondary }
        return Color(rgb.darkened(toContrast: minimumContrast, against: .white))
    }
}

/// Minimal sRGB triple, for the relative-luminance math `Color` does not
/// expose portably across macOS and iOS.
struct RGB: Equatable {
    var r: Double, g: Double, b: Double

    static let white = RGB(r: 1, g: 1, b: 1)

    init(r: Double, g: Double, b: Double) {
        self.r = r
        self.g = g
        self.b = b
    }

    init(hex: UInt32) {
        self.init(
            r: Double((hex >> 16) & 0xFF) / 255,
            g: Double((hex >> 8) & 0xFF) / 255,
            b: Double(hex & 0xFF) / 255
        )
    }

    init?(hexString: String) {
        var text = hexString.trimmingCharacters(in: .whitespaces)
        if text.hasPrefix("#") { text.removeFirst() }
        guard text.count == 6, let value = UInt32(text, radix: 16) else { return nil }
        self.init(hex: value)
    }

    private static func toLinear(_ c: Double) -> Double {
        c <= 0.03928 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4)
    }

    private static func toGamma(_ c: Double) -> Double {
        c <= 0.0031308 ? c * 12.92 : 1.055 * pow(c, 1 / 2.4) - 0.055
    }

    /// WCAG relative luminance.
    var luminance: Double {
        0.2126 * Self.toLinear(r) + 0.7152 * Self.toLinear(g) + 0.0722 * Self.toLinear(b)
    }

    /// WCAG contrast ratio, 1...21.
    func contrast(with other: RGB) -> Double {
        let a = luminance, b = other.luminance
        return (max(a, b) + 0.05) / (min(a, b) + 0.05)
    }

    /// This color dimmed until it is `target`:1 against `other`, or unchanged
    /// if it already clears it.
    ///
    /// Scaling happens in linear light, where luminance is a linear function
    /// of the scale factor — so the exact factor is a division rather than a
    /// search. Only defined for dimming against a lighter color; if `other`
    /// is the darker of the two, dimming cannot help and the color is
    /// returned as-is.
    func darkened(toContrast target: Double, against other: RGB) -> RGB {
        let ceiling = (other.luminance + 0.05) / target - 0.05
        guard ceiling > 0, luminance > ceiling else { return self }
        let factor = ceiling / luminance
        return RGB(
            r: Self.toGamma(Self.toLinear(r) * factor),
            g: Self.toGamma(Self.toLinear(g) * factor),
            b: Self.toGamma(Self.toLinear(b) * factor)
        )
    }
}

extension Color {
    init(_ rgb: RGB) {
        self.init(red: rgb.r, green: rgb.g, blue: rgb.b)
    }

    public init(hex: UInt32) {
        self.init(RGB(hex: hex))
    }

    public init?(hexString: String) {
        guard let rgb = RGB(hexString: hexString) else { return nil }
        self.init(rgb)
    }
}
