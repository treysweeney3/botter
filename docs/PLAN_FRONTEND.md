# Botter.app — Frontend Implementation Plan (owner: Fable 5)

Native SwiftUI app reproducing the Grok Bot look and interaction model (reference screenshots on file; design tokens and full UX spec in `docs/SPEC.md` §5). macOS first; the project is structured multiplatform so the iOS target (v3) reuses models, networking, and most views.

## Stack & project setup

- **Swift 6 / SwiftUI**, `@Observable` (Observation framework), structured concurrency. Min target: macOS 15+ (raise only if a needed API demands it).
- Project generated with **XcodeGen** (`app/project.yml`) so the whole app builds headlessly: `xcodegen generate && xcodebuild -scheme Botter build`. No storyboards/xibs.
- Two modules:
  - `BotterKit` (SPM package: models, `BotterClient`, stores, SSE) — platform-agnostic, unit-testable via `swift test`.
  - `Botter` app target (views, app lifecycle, macOS-specific chrome).
- Talks **only** to botterd (`http://127.0.0.1:8674`, bearer token from `~/.botter/token`). API contract + normalized message schema: `docs/SPEC.md` §4 — treat it as authoritative; if the backend deviates, flag it, don't silently adapt.
- Until botterd Phase 1 lands, develop against the contract-identical mock server (`backend/mockserver`, same port).

## Architecture

```
BotterKit
├── Models: Bot, Session, Message (kind: text|task_report|routine_created|approval_request|attachment),
│           TaskItem, Routine, RoutineExecution, Approval, FeedEntry  — Codable, snake_case strategy
├── BotterClient: async/await REST + two SSE consumers
│   ├── chatStream(sessionID:text:) -> AsyncThrowingStream<ChatEvent>   // delta/tool_event/approval_required/message_complete
│   └── eventsStream() -> AsyncStream<ServerEvent>                      // feed_updated/approval_* /routine_fired
│   └── SSE via URLSession.bytes(for:) line-parser; auto-reconnect w/ backoff + Last-Event-ID
├── Stores (@Observable, @MainActor):
│   ├── RosterStore     // sidebar: bots + previews + unread; refreshed on feed_updated
│   ├── ChatStore       // per-session message list; applies stream deltas; optimistic user bubble
│   ├── ApprovalStore   // global pending approvals; badge count
│   └── RoutineStore    // per-bot routines + executions
└── DesignSystem: Color/spacing/typography tokens from SPEC §5, avatar glyph assets
```

No local persistence in v1 beyond a small settings store — botterd/Hermes are the source of truth and are always on-machine; every launch fetches fresh. (Offline cache is an iOS-phase concern.)

## Phase 2 (v1 core) — build order

1. **Scaffold + design system.** XcodeGen project, dark-only appearance, token catalog, avatar view (colored circle + vector glyph; glyph set: ~10 simple abstract marks bundled as SF Symbol-style template PDFs/SVGs — draw them, don't use emoji, per repo rule). Verify: app builds and shows token gallery debug screen.
2. **Sidebar roster.** `NavigationSplitView`; search field filters locally; rows per SPEC §5 (avatar, bold name, relative timestamp, one-line preview, unread dot); user identity footer; archived section collapsed. Data from `GET /v1/bots` + live `feed_updated`. Verify against mock: visually compare to reference screenshot 1.
3. **Chat view.** Message list (LazyVStack in ScrollView, bottom-anchored, scroll-to-bottom on new): assistant bubbles (markdown via `AttributedString(markdown:)`), white right-aligned user bubbles, task-report cards (✓ rows `label → detail`), centered system chips ("Created routine …"), day separators. Composer pill (+, placeholder "Message {bot}", mic → stop button while streaming; Enter sends). Streaming: deltas append into the live bubble; `tool_event`s render as a transient activity line above the bubble. Verify: scripted mock conversation reproduces reference screenshot 1's thread, smooth 60fps scroll.
4. **Bot create/edit sheet.** Fields per SPEC §5 incl. suggested-role chips, color + glyph pickers, approval-boundary editor; archive & purge (purge behind a destructive confirm). Wire to POST/PATCH/DELETE. Verify: created bot appears in roster; edits round-trip.
5. **Wire to real botterd** once its Phase 1 lands: run the backend `scripts/e2e.sh` bot from the UI — create a real bot, hold a streamed conversation, confirm feed updates when a routine posts into the thread.

## Phase 3 (with backend) — routines & approvals UI

6. **Routines panel** (toolbar button in chat top bar → inspector/sheet): list w/ schedule in words + next-run, status dot from last execution, pause toggle, Run now; editor with preset schedule chips (hourly/daily/weekday mornings/weekly/custom cron with live natural-language preview). "Created routine" chips deep-link here.
7. **Approvals.** Inline `approval_request` bubble → Approve / Always allow / Deny buttons (decisions map to `once|always|deny`; use `session` for "Approve for this task" secondary action). Global: approvals pill at sidebar top when pending > 0, Dock badge via `NSApp.dockTile`, `UNUserNotificationCenter` notification with action buttons. Resolution from any surface reconciles via `approval_resolved` event.
8. **Chat top bar**: avatar + name → opens bot editor; monitor icon present but disabled with "Computer view — coming soon" tooltip (v2 slot).

## Phase 4 — polish

9. Memory viewer (bot editor tab rendering `GET /v1/bots/{id}/memory` markdown, read-only v1).
10. Search across sessions (`GET /v1/search`) from the sidebar field when server supports it.
11. Read-state sync (`POST /v1/sessions/{sid}/read` on view + scroll), launch-at-login toggle, settings window (token path, botterd health indicator), app icon.
12. Interaction polish pass: bubble entrance (subtle 8pt rise + fade, 180ms), streaming caret shimmer, sidebar unread dot transitions, hover states on rows/buttons — restrained; the reference design is calm.

## Verification bar (every phase)

- `swift test` for BotterKit (SSE parser against recorded fixtures from `backend/fixtures/`; store logic; model decoding of every `kind`).
- Build + launch via `xcodebuild`, screenshot, and **visually diff against the reference screenshots** — layout, spacing, and tone must read as the same product family. Fix before moving on.
- Streaming resilience: kill/restart mock mid-stream → UI shows a retriable error state, no crash, reconnect works.
- Accessibility sanity: VoiceOver labels on rows/buttons, full keyboard nav (↑↓ roster, ⌘N new bot, ⌘F search, Enter send).

## iOS (v3, later — keep in mind now)

- Keep all views free of AppKit except isolated `#if os(macOS)` chrome (dock badge, settings window).
- Navigation: the split view collapses to a stack on iOS (roster → chat) as in Grok Bot's phone UI (reference screenshot 3).
- Networking already token-based; the relay phase only changes `BotterClient.baseURL` + adds Cloudflare Access headers — design `ClientConfiguration` accordingly now.
