import Foundation

extension String {
    /// Chat text hygiene: Hermes multi-step replies arrive with runs of blank
    /// lines between segments. Collapse 3+ consecutive newlines to one blank
    /// line and trim outer whitespace so bubbles keep a tight rhythm.
    public var collapsedBlankLines: String {
        var result = ""
        var newlineRun = 0
        for character in self {
            if character == "\n" {
                newlineRun += 1
                if newlineRun <= 2 { result.append(character) }
            } else {
                newlineRun = 0
                result.append(character)
            }
        }
        return result.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

/// Human-readable descriptions for the cron shapes the routine editor
/// produces. Anything unrecognized falls back to showing the expression.
public enum CronText {
    public static func describe(_ expression: String) -> String {
        let fields = expression.split(separator: " ").map(String.init)
        guard fields.count == 5 else { return expression }
        let (minute, hour, dayOfMonth, month, dayOfWeek) = (fields[0], fields[1], fields[2], fields[3], fields[4])
        guard month == "*", dayOfMonth == "*" || dayOfWeek == "*" else { return expression }

        // */N minutes
        if hour == "*", dayOfMonth == "*", dayOfWeek == "*" {
            if minute == "*" { return "Every minute" }
            if minute.hasPrefix("*/"), let n = Int(minute.dropFirst(2)) {
                return "Every \(n) minutes"
            }
            if let m = Int(minute) {
                return m == 0 ? "Every hour" : "Hourly at :\(String(format: "%02d", m))"
            }
            return expression
        }

        guard let m = Int(minute), let h = Int(hour), dayOfMonth == "*" else { return expression }
        let time = timeString(hour: h, minute: m)

        switch dayOfWeek {
        case "*": return "Daily at \(time)"
        case "1-5": return "Weekdays at \(time)"
        case "0", "7": return "Sundays at \(time)"
        case "1": return "Mondays at \(time)"
        case "2": return "Tuesdays at \(time)"
        case "3": return "Wednesdays at \(time)"
        case "4": return "Thursdays at \(time)"
        case "5": return "Fridays at \(time)"
        case "6": return "Saturdays at \(time)"
        case "0,6", "6,0": return "Weekends at \(time)"
        default: return expression
        }
    }

    private static func timeString(hour: Int, minute: Int) -> String {
        guard (0..<24).contains(hour), (0..<60).contains(minute) else { return "\(hour):\(minute)" }
        let suffix = hour < 12 ? "am" : "pm"
        let display = hour % 12 == 0 ? 12 : hour % 12
        return minute == 0 ? "\(display)\(suffix)" : "\(display):\(String(format: "%02d", minute))\(suffix)"
    }
}
