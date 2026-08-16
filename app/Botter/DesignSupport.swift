import SwiftUI
import AppKit
import BotterKit

/// Enforces the window chrome the design needs: hidden title, transparent
/// title bar, full-size content — and the standard close/minimize/zoom
/// buttons ALWAYS visible.
struct WindowChromeConfigurator: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async {
            guard let window = view.window else { return }
            window.titleVisibility = .hidden
            window.titlebarAppearsTransparent = true
            window.styleMask.insert(.fullSizeContentView)
            for button: NSWindow.ButtonType in [.closeButton, .miniaturizeButton, .zoomButton] {
                window.standardWindowButton(button)?.isHidden = false
            }
        }
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {}
}

/// Tactile press feedback: scale to 0.96 (never lower — reads exaggerated),
/// animating only transform, interruptible.
struct PressableButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.96 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

extension ButtonStyle where Self == PressableButtonStyle {
    static var pressable: PressableButtonStyle { PressableButtonStyle() }
}

/// Centered day separator between message groups.
struct DaySeparator: View {
    let date: Date

    var body: some View {
        HStack(spacing: 10) {
            Rectangle().fill(Tokens.hairline).frame(height: 1)
            Text(label)
                .font(Tokens.chip)
                .foregroundStyle(Tokens.textSecondary)
                .fixedSize()
            Rectangle().fill(Tokens.hairline).frame(height: 1)
        }
        .padding(.vertical, 6)
        .accessibilityLabel(label)
    }

    private var label: String {
        if Calendar.current.isDateInToday(date) { return "Today" }
        if Calendar.current.isDateInYesterday(date) { return "Yesterday" }
        return date.formatted(.dateTime.weekday(.wide).month(.abbreviated).day())
    }
}
