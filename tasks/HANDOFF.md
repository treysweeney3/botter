# Handoff — Connections/Config split (2026-08-14)

## Completion update

Shipped and live-verified on 2026-08-14. See the **Connections / Config split** sections in `tasks/todo.md` and `backend/NOTES.md`. Final checks: backend 48/48, BotterKit 17/17, Xcode build succeeded, live catalog split 70 integrations / 63 config, reversible custom-config reset passed, and the freshly built app is running. The remaining human-only check is visual confirmation of the two tabs and edit/reset interactions on screen.

## Context
Botter (`~/projects/botter`) = SwiftUI macOS app + `botterd` FastAPI daemon (launchd `com.treysweeney.botterd`, 127.0.0.1:8674) managing the local Hermes agent (`~/.hermes`). Read `tasks/todo.md` (bottom sections), `backend/NOTES.md` (last two sections), and `docs/SPEC.md` §4 before touching anything. Hermes core is NEVER patched. Contract law lives in SPEC §4. Toolchain gotchas: use `/usr/bin/swift` (swiftly is broken); app builds via `cd app && xcodegen generate && xcodebuild -scheme Botter build`; backend tests via `cd backend && uv run pytest -q`.

## Shipped and verified earlier today (all deployed, botterd restarted, app relaunched)
1. **Connections v2**: botterd supervises a `hermes serve` child (`backend/botterd/hermes_serve.py`) — Hermes' dashboard management API, auth via self-minted `HERMES_DASHBOARD_SESSION_TOKEN`, ready sentinel `HERMES_BACKEND_READY port=<n>`, orphan protection via `HERMES_PARENT_PID`. `/v1/channels` GET/PUT (messaging platforms, slack excluded, gateway restart via launchctl kickstart + health wait). In-app Google OAuth (drives `~/.hermes/hermes-agent/skills/productivity/google-workspace/scripts/setup.py` — `--auth-url` / paste redirect URL / `--auth-code`). `Connection.fields` exposes vercel team_id / supabase project_ref.
2. **Integrations**: `/v1/integrations` GET/PUT/DELETE (`backend/botterd/integrations.py`) proxying the serve child's `/api/env` + Hermes credential lifecycle. 133 keys live. Excludes: `channel_managed`, category `messaging`, curated connection keys, protected infra keys (403). Custom UPPER_SNAKE_CASE keys allowed. Swift UI: Integrations section + searchable picker in `app/Botter/Connections/ConnectionsSheet.swift`.

## IN FLIGHT — resume here
User feedback: Integrations list is polluted with general Hermes config (e.g. "Browser Inactivity Timeout", "Image Tools Debug"). Wanted: profile-button popup with two tabs — **Connections** (credentials/integrations/channels) and **Config** (plain settings). Implementation is ~90% done but NOT tested/built/deployed:

Done (uncommitted working-tree changes, no git repo — files on disk):
- `backend/botterd/integrations.py`: added `_kind_for(row)` — `"integration"` if `is_password or url`, else `"config"`; `Integration.kind` field added in `backend/botterd/models.py`.
- `backend/mockserver/main.py`: mock rows compute `kind`.
- `backend/tests/test_integrations.py`: new test `test_integration_kind_splits_service_keys_from_plain_config` (7/7 passing at last run of that file).
- Swift `app/BotterKit/Sources/BotterKit/Models/Models.swift`: `Integration.kind: String` added.
- `app/Botter/Sidebar/SidebarView.swift`: footer label → "Connections & Config".
- `app/Botter/Connections/ConnectionsSheet.swift`: rewritten as two-tab sheet (segmented Picker "Connections"/"Config", title "Hermes"); `connectionsList` extracted; `configList` view with search + `ConfigRow` (appended at end of file); Integrations section + picker now filter `kind == "integration"`; config rows edit via existing `IntegrationValueSheet`, "Reset to default" calls `removeIntegration`.

## Remaining steps (in order)
1. `cd backend && uv run pytest -q` — expect ~48 passing; fix anything red.
2. `cd app/BotterKit && /usr/bin/swift test` — the `decodesChannelAndUpgradedConnectionShapes` test in `BotterKitTests.swift` does NOT yet include `kind` in its Integration JSON — Integration decode isn't in that test, so likely fine; add a decode case for Integration with `"kind"` if desired.
3. `cd app && xcodegen generate && xcodebuild -scheme Botter build` — fix any SwiftUI compile errors in ConnectionsSheet.swift (the two-tab restructure was the last edit and has NOT been compiled yet; watch indentation/brace issues around `connectionsList`/`configList`).
4. Update `docs/SPEC.md` §4 `/v1/integrations` row: add `kind` field ("integration"|"config") to the pinned shape.
5. Deploy: `launchctl kickstart -k gui/$(id -u)/com.treysweeney.botterd`, then smoke:
   - `TOKEN=$(cat ~/.botter/token); curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8674/v1/integrations` → verify rows carry `kind`, and things like BROWSER_INACTIVITY_TIMEOUT are `"config"` while BRAVE_SEARCH_API_KEY is `"integration"`.
   - Relaunch app: `killall Botter; open ~/Library/Developer/Xcode/DerivedData/Botter-*/Build/Products/Debug/Botter.app`. Check profile popup shows the two tabs, Integrations no longer shows debug/timeout entries, Config tab searches/edits/resets work.
6. Append results to `backend/NOTES.md` + close out the section in `tasks/todo.md`.
7. Answer the user's open question "what services can be configured here?" — enumerate the live integration-kind keys (`curl` above, group by category: ~37 tool, ~73 provider, 4 skill, plus custom). Give a readable list (Brave Search, Notion, ElevenLabs, Browserbase, Anthropic, Gemini, OpenAI, etc.).

## Safety rails
- Never touch main's Slack config; never restart the Hermes gateway during testing unless applying a real user-driven channel change (botterd restart is fine and does not touch the gateway).
- Secret values must never appear in responses/logs/tests.
- Live smoke writes only via a throwaway key (pattern used earlier: PUT+DELETE `BOTTER_SMOKE_TEST_KEY`).

## Memory notes (Claude memory dir, for continuity)
`~/.claude/projects/-Users-treysweeney-projects-botter/memory/` — `hermes-dashboard-api.md` has the verified serve-child contract; `botter-architecture.md` has the shipped-feature status lines. Update both when the Config tab ships.
