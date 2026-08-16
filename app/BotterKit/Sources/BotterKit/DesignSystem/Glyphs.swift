import SwiftUI

/// The bundled avatar set: ten ASCII-mosaic sea otters, matching the app
/// icon's character-texture style (repo rule: bundled artwork, never emoji).
/// `avatar_glyph` on the wire is one of `Glyph.allCases`' raw values.
///
/// Each case is backed by a template image in `Resources/Otters.xcassets`,
/// rendered white on the bot's palette color by `AvatarView`. The artwork is
/// deliberately coarse and near-solid: the avatar is drawn as small as 18pt,
/// where fine character texture averages out to an illegible wash.
public enum Glyph: String, CaseIterable, Identifiable, Sendable {
    /// Belly-up on the water, paws folded — the app icon's pose.
    case float
    case swim, dive, stand, sprawl, peek, groom, shell, wave, raft

    public var id: String { rawValue }

    /// The default for a brand-new bot and the fallback for unknown names.
    public static let fallback: Glyph = .float

    /// Asset name in `Otters.xcassets`.
    var imageName: String { "otter-\(rawValue)" }

    /// Human-readable pose, used for picker tooltips and accessibility.
    public var label: String {
        switch self {
        case .float: "Floating"
        case .swim: "Swimming"
        case .dive: "Diving"
        case .stand: "Standing"
        case .sprawl: "Sprawling"
        case .peek: "Peeking"
        case .groom: "Grooming"
        case .shell: "Holding a shell"
        case .wave: "Waving"
        case .raft: "Rafting"
        }
    }

    /// Glyph names predating the otter set, kept so bots created against the
    /// old vector vocabulary keep a stable avatar instead of all collapsing
    /// onto the fallback. `wave` is intentionally absent — it exists in both
    /// sets, so its raw value already resolves.
    private static let legacyNames: [String: Glyph] = [
        "bolt": .dive,
        "orbit": .raft,
        "prism": .stand,
        "spark": .float,
        "arc": .swim,
        "stack": .shell,
        "target": .peek,
        "branch": .groom,
        "grid": .sprawl,
    ]

    /// Resolve a wire value: current name, then legacy name, then fallback.
    public static func resolve(_ name: String) -> Glyph {
        Glyph(rawValue: name) ?? legacyNames[name] ?? fallback
    }
}

/// The otter artwork on its own, untinted by a disc — for empty states.
public struct GlyphImage: View {
    let glyph: Glyph

    public init(_ glyph: Glyph) {
        self.glyph = glyph
    }

    public var body: some View {
        Image(glyph.imageName, bundle: .module)
            .renderingMode(.template)
            .resizable()
            .scaledToFit()
    }
}

/// Circular bot avatar: solid bot color, white otter.
public struct AvatarView: View {
    let colorHex: String
    let glyphName: String
    var size: CGFloat

    /// Fraction of the disc the otter occupies. Larger than the old vector
    /// glyphs used (0.5) — these are silhouettes rather than line marks, and
    /// need the extra area to stay readable at small sizes.
    private static let glyphFraction: CGFloat = 0.72

    public init(colorHex: String, glyphName: String, size: CGFloat = Tokens.avatarSize) {
        self.colorHex = colorHex
        self.glyphName = glyphName
        self.size = size
    }

    public init(bot: Bot, size: CGFloat = Tokens.avatarSize) {
        self.init(colorHex: bot.avatarColor, glyphName: bot.avatarGlyph, size: size)
    }

    public var body: some View {
        ZStack {
            Circle().fill(BotPalette.color(for: colorHex))
            // Subtle top sheen + inner rim give the flat disc physical depth.
            Circle().fill(
                LinearGradient(
                    stops: [
                        .init(color: .white.opacity(0.22), location: 0),
                        .init(color: .white.opacity(0), location: 0.55),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            )
            GlyphImage(Glyph.resolve(glyphName))
                .frame(width: size * Self.glyphFraction, height: size * Self.glyphFraction)
                .foregroundStyle(.white)
        }
        .overlay(Circle().strokeBorder(.white.opacity(0.14), lineWidth: 1))
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}
