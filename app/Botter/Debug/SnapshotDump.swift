import AppKit
import SwiftUI
import BotterKit

/// Renders chat views to PNG at launch, headless.
///
/// Exists because verifying this UI from an automated shell is otherwise
/// impossible: `screencapture` needs Screen Recording permission the shell
/// does not have, and `cacheDisplay`/`CALayer.render(in:)` return blank pixels
/// for SwiftUI content. `ImageRenderer` draws the real view tree.
///
/// Run with `BOTTER_SNAPSHOT_DIR=/some/dir open -a Botter` — the app writes the
/// frames and quits without touching the backend.
@MainActor
enum SnapshotDump {
    private static let script = """
    Pistachio is your **fastest-growing** flavor — sales are up 23% this \
    month and margins beat vanilla by 8 points. Stone-fruit flavors are \
    trending in the same range, and [the index](https://example.com) backs it up.

    - Restock `pistachio-base` before Thursday
    - Hold the vanilla order at 60%
    """

    /// Writes one frame per prefix of a canned reply, then exits. Returns
    /// false when no dump was requested, so the app starts normally.
    static func runIfRequested() -> Bool {
        guard let directory = ProcessInfo.processInfo.environment["BOTTER_SNAPSHOT_DIR"] else {
            return false
        }
        let words = script.split(separator: " ", omittingEmptySubsequences: false)
        let stops = [4, 11, 19, 27, words.count]
        for (index, stop) in stops.enumerated() {
            let text = words.prefix(stop).joined(separator: " ")
            write(
                StreamingBubble(text: text),
                to: URL(fileURLWithPath: directory).appendingPathComponent("stream-\(index).png")
            )
        }
        write(
            // Same trailing gutter the streaming bubble sits in, so the two
            // frames are comparable.
            HStack {
                MessageBlocksView(text: script)
                Spacer(minLength: 48)
            },
            to: URL(fileURLWithPath: directory).appendingPathComponent("settled.png")
        )
        write(
            RevealRamp(),
            to: URL(fileURLWithPath: directory).appendingPathComponent("reveal-ramp.png")
        )
        // The turn as the eye sees it: one steady "Thinking" for the whole run,
        // then the step count above the settled reply.
        write(
            VStack(alignment: .leading, spacing: 14) {
                StreamingBubble(text: "")
                StreamingBubble(text: words.prefix(11).joined(separator: " "))
                VStack(alignment: .leading, spacing: 6) {
                    ThinkingTraceView(trace: Self.trace)
                    HStack {
                        MessageBlocksView(text: script)
                        Spacer(minLength: 48)
                    }
                }
            },
            to: URL(fileURLWithPath: directory).appendingPathComponent("turn.png")
        )
        // Each user bubble should hug its own text and stop at the cap.
        write(
            VStack(alignment: .trailing, spacing: 10) {
                UserBubble(text: "Hi", attachments: [])
                UserBubble(text: "What are the communications charges?", attachments: [])
                UserBubble(
                    text: "Pull the transaction-level detail on apps and software, then "
                        + "cross-check the sales line against last quarter.",
                    attachments: []
                )
            },
            to: URL(fileURLWithPath: directory).appendingPathComponent("user-bubbles.png")
        )
        return true
    }

    private static let trace = ExchangeTrace(duration: 7.4, steps: [
        ToolStep(id: 0, name: "salesforce", status: "ok", summary: "Pulled 52 qualified accounts"),
        ToolStep(id: 1, name: "sheets", status: "ok", summary: "Wrote the flavor margins tab"),
        ToolStep(id: 2, name: "gmail", status: "error", summary: "Draft rejected — no recipient"),
    ])

    /// A frozen frame of the reveal: the trailing words at the progress values
    /// they pass through, which is what the eye actually sees mid-stream.
    private struct RevealRamp: View {
        private static let line = "Stone-fruit flavors are trending in the same range"
        private static let progress: [Double] = [1, 1, 1, 1, 0.85, 0.6, 0.35, 0.15, 0.02]

        var body: some View {
            let words = Self.line.split(separator: " ").map(String.init)
            WordFlowLayout(spaceWidth: StreamingProse.spaceWidth) {
                ForEach(Array(words.enumerated()), id: \.offset) { index, word in
                    StreamWord(token: StreamToken(text: word))
                        .modifier(StreamReveal(progress: Self.progress[index]))
                }
            }
            .padding(.horizontal, 15)
            .padding(.vertical, 11)
            .background(
                RoundedRectangle(cornerRadius: Tokens.bubbleRadius, style: .continuous)
                    .fill(Tokens.cardBackground)
            )
        }
    }

    private static func write(_ view: some View, to url: URL) {
        let renderer = ImageRenderer(
            content: view
                .frame(width: 600, alignment: .leading)
                .padding(20)
                .background(Tokens.windowBackground)
                .environment(\.colorScheme, .dark)
        )
        renderer.scale = 2
        guard let image = renderer.nsImage,
              let tiff = image.tiffRepresentation,
              let bitmap = NSBitmapImageRep(data: tiff),
              let data = bitmap.representation(using: .png, properties: [:])
        else { return }
        try? data.write(to: url)
    }
}
