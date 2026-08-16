import Foundation

public enum Slug {
    /// Display name → Hermes profile slug: `[a-z0-9-]`, max 32 chars.
    public static func make(_ name: String) -> String {
        let lowered = name.lowercased()
            .folding(options: .diacriticInsensitive, locale: nil)
        let mapped = lowered.map { character -> Character in
            (character.isLetter || character.isNumber) && character.isASCII ? character : "-"
        }
        let collapsed = String(mapped)
            .split(separator: "-", omittingEmptySubsequences: true)
            .joined(separator: "-")
        return String(collapsed.prefix(32))
    }
}
