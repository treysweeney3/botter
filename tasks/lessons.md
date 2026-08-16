# Lessons

## 2026-08-14 — Verified a throwaway build, not the app the user launches
- **What happened:** I built Botter into a temporary DerivedData directory and reported success, but never replaced or relaunched the user's normal app bundle. Quitting and reopening therefore loaded the same stale executable.
- **Rule:** For visible app changes, compilation is only an intermediate check. Resolve the running executable path, rebuild or install that exact app bundle, relaunch it, and verify the changed UI from that bundle before reporting completion.

## 2026-08-14 — Changed the container gap instead of the control spacing
- **What happened:** The user reported spacing on the composer bar. I changed only its outer top/bottom inset, leaving the bar's own asymmetric horizontal padding and control spacing untouched, then claimed the issue was fixed.
- **Rule:** For a spacing bug, name and measure the exact relationship in the screenshot before editing: viewport-to-control, control-to-content, or sibling-to-sibling. Verification must compare that same measurement after the change; a nearby spacing change is not evidence that the reported spacing changed.

## 2026-08-14 — Wired the action, never wired the presentation
- **What happened:** The sidebar `+` button and ⌘N both set `AppModel.isCreatingBot = true`, but no view had a `.sheet(isPresented:)` bound to it — clicking did nothing. Shipped through two build-verified phases because compilation can't catch a state flag nobody observes, and headless verification never clicked the button.
- **Rule:** When adding any user-triggered state flag, grep for its consumers before calling the feature done — every `isX = true` needs a matching presentation/observer in the same commit. For UI built without on-screen verification, walk each interactive control and name the exact view that reacts to it.

## 2026-08-14 — Global auth cannot stop at the default profile
- **What happened:** The global Connections screen completed Google OAuth and reported success after writing only `~/.hermes`, while Botter chats execute in isolated `profiles/<slug>` homes. The active bot could not see the token; later API-key and integration changes had the same ownership flaw.
- **Rule:** A globally presented authentication action is complete only when every Botter-managed runtime can consume it. Test the full path from credential persistence through profile scope and persistent sandbox mounts; aggregate status must fail visibly when any managed profile is out of sync.

## 2026-08-16 — Headless UI verification is possible after all
- **What happened:** UI work here has repeatedly shipped unverified because an automated shell has no Screen Recording permission, so `screencapture` fails. `NSView.cacheDisplay` and `CALayer.render(in:)` were tried next and both write blank pixels for SwiftUI content.
- **Rule:** Render SwiftUI views to PNG in-process with `ImageRenderer` (`SnapshotDump`, `BOTTER_SNAPSHOT_DIR=dir`). It needs no permission. Design views so their resolved state is the default render — animation driven by `onAppear`-set `@State` renders blank, whereas an insertion `.transition` renders at identity — then a snapshot is real evidence, not a guess.
