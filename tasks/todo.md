# Botter — Build Progress

Authoritative docs: `docs/SPEC.md` (contract §4), `docs/PLAN_HERMES_SETUP.md`, `docs/PLAN_BACKEND.md` (codex/GPT-5.6-Sol), `docs/PLAN_FRONTEND.md` (Fable 5).
Rule: if Phase 0 investigations contradict a plan assumption, stop and update the affected doc before building on it.

## Phase 0 — Hermes setup + smoke-test investigations (codex) — GATE
- [x] Authoring run: inspect config schema, API/session/run/job routes, profile lifecycle, and proxy supervision without mutating Hermes
- [x] Authoring run: write Phase 0 scripts and `backend/NOTES.md` skeleton
- [x] Authoring run: run `bash -n` and static requirement review on all three scripts
- [x] `scripts/setup_hermes.sh` — enable api_server, multiplex_profiles, iron-proxy allowlist fix (idempotent, backups first)
- [x] `scripts/verify_hermes.sh` — non-mutating verification checks
- [x] `scripts/phase0_investigate.sh` — runtime investigations authored; supervisor execution pending
- [x] Fable review + fixes of codex scripts (see review log)
- [x] `setup_hermes.sh` executed: backups made, API_SERVER_KEY added, api_server on 127.0.0.1:8642, multiplex on, allowlist repaired (11 hosts), iron-proxy + gateway restarted healthy
- [x] `scripts/verify_hermes.sh` — all 22 checks PASS
- [x] Investigation (a): ANSWERED — new profile served immediately, no gateway restart needed (HTTP 200 on first probe); purge however needs container-stop → delete → ACL sweep → gateway restart → skeleton sweep
- [x] Investigation (b): ANSWERED — full transcripts in `backend/fixtures/*.sse`; chat SSE and run-events SSE use different framings; session create needs explicit `model`
- [x] Investigation (c): ANSWERED — NO, cron executions create no approvable runs; SOUL.md-boundary fallback confirmed for v1
- [x] Scratch profile deleted; no residue in `~/.hermes` (`profiles/` empty, served_profiles=[default], wrapper removed)
- [x] Slack on `main` still connected after all gateway restarts (verify check PASS)
- [x] Docs updated for contradictions: SPEC §4 (`choice` upstream) + §7 (all answered), PLAN_HERMES_SETUP Steps 1/3/4, PLAN_BACKEND facts + Phase 0/1
- [x] backend/NOTES.md final write (codex) — reviewed, accurate, evidence-cited
- [ ] **GATE: verification output shown to Trey — awaiting go-ahead for Phase 1+2**

## Phase 1 + 1.5 — botterd core + mock server (codex, parallel with frontend)
- [x] Scaffold `backend/` uv project and shared Pydantic HTTP models
- [x] Implement botterd config, constant-time bearer auth, error envelope, JSON logging, lifespan, and SQLite migrations
- [x] Implement Hermes client with distinct fixture-backed chat-stream and run-events SSE parsers plus explicit model resolution
- [x] Implement bot registry CRUD, clone hygiene, SOUL rendering, injectable subprocess runner, six-step purge, and rollback
- [x] Implement chat proxy with normalized SSE, 15 s heartbeat, and disconnect-safe upstream consumption
- [x] Implement feed aggregation/API-first fallback/read-only SQLite watcher and global events bus
- [x] Implement sessions, routines, approvals, memory, and search routes from `SPEC.md` §4
- [x] Implement contract-identical mock server with six-bot roster and scripted chat/approval/routine flows
- [x] Add unit, fixture, proxy, fallback, auth, and full mock contract smoke tests
- [x] Author curl-only `scripts/e2e.sh` without running it
- [x] Run `uv run pytest`; perform mock boot + health/bots curl check; review against plan

### Phase 1 + 1.5 intended outcome and constraints
- Build only backend Phase 1 and mock Phase 1.5; do not install launchd, build Phase 3 hardening, touch Hermes/Botter runtime state, or run live e2e.
- Treat `SPEC.md` §4 as the public contract; use snake_case and `{ "error": { "code", "message" } }` errors.
- Preserve `main`/`default`, translate public approval `decision` to Hermes `choice`, and always create Hermes sessions with an explicit model.
- Keep all test state inside temporary directories and all subprocess/Hermes interactions mocked.

### Phase 1 + 1.5 review
- Implemented all 25 SPEC §4 method/path operations in both the real and mock apps using shared contract models.
- Fixture-backed parsers cover both Hermes SSE dialects, including timestamp-column and harness-marker removal.
- Review fixed chronological history inversion, canonical persisted completion IDs, reloadable task reports, terminal-frame queue safety, exact purge mount matching, and ordinary-vs-SSE timeouts.
- Final local verification: 17 tests passed; mock booted on 127.0.0.1:8674 and returned the expected health and six-bot roster.
- Live Hermes e2e and launchd installation were intentionally not run.

## Phase 2 — SwiftUI app (Fable 5, against mock server)
- [x] 1. Scaffold: XcodeGen project (`app/project.yml`), BotterKit SPM (models, client, 2 SSE consumers, 4 stores, tokens, 10 vector glyphs), token gallery (⌘⇧D window). `swift test` 9/9 green; `xcodebuild` clean.
- [x] 2. Sidebar roster built (search pill, avatar rows w/ timestamp+preview+unread dot, approvals pill, archived section, user footer) — code-complete; visual pass against reference pending mock data
- [x] 3. Chat view built (streaming bubbles w/ typing indicator + tool activity line, task-report cards, system chips, approval bubble w/ inline actions, pill composer w/ stop button) — code-complete; scripted-conversation verification pending mock
- [x] 4. Bot create/edit sheet built (suggested-role chips, color+glyph pickers, boundary editor, archive/purge w/ destructive confirm)
- [x] Contract reconciliation (see review log): adopted backend envelopes, adapted Swift client; SPEC §4 envelope pin + §5 glyph vocabulary added; `swift test` 9/9, backend `uv run pytest` 17/17
- [x] Mock verified end-to-end from CLI: roster (correct glyphs + timestamps), streamed chat (delta→message_complete), approval round-trip (request → list → decide → cleared)
- [x] App rebuilt and launched against mock (visual pass on-screen; screenshots unavailable from background session)
- [x] 5. Real botterd wired and live e2e PASSED: create bot → streamed chat → routine create/pause → approval probe (WARN: none pending, expected on sandboxed installs) → archive → purge; zero Hermes residue, gateway healthy, Slack connected. UI-driven pass (create a bot with ⌘N, chat) is Trey's to confirm on-screen.

### 2026-08-13 live-wiring fixes
- e2e.sh: `rg` was Claude Code's bundled binary, not installed on the system — installed ripgrep via brew (scripts depend on it).
- botterd purge: `launchctl kickstart -k` can race the dying gateway still holding 8642 → new instance boots WITHOUT api_server (observed live; took Slack's gateway down until manual restart). Fixed in `registry.py`: purge now retries the kickstart up to 3× until `/health` answers.
- e2e.sh approval step made tolerant (WARN not FAIL) per Phase 0 finding; `avatar_glyph` fixed to pinned vocabulary.
- Running services: real botterd on 127.0.0.1:8674 (foreground uvicorn, launchd install still pending — backend "Deployment & ops" section); Botter.app connected to it.

## Phase 3 — Routines + approvals (both sides) — ✅ COMPLETE 2026-08-14
- [x] Routines panel UI (frontend step 6): list w/ status dot + schedule-in-words (CronText) + pause toggle + Run now (shows "Queued"), editor w/ preset chips + custom cron + live natural-language preview + delete
- [x] Approvals global surfaces (frontend step 7): sidebar pill → popover list w/ Approve/Always/Deny, dock badge, UNUserNotificationCenter notifications on approval_pending; event routing moved to AppModel feeding roster + approvals stores
- [x] Backend: egress provisioning for bot profiles (shared main iron-proxy, fail-closed artifact links)
- [x] Backend: routine execution history wiring (cron/executions.db + default-thread virtual messages)
- [x] UI fix: `routine_created`-kind chips now render the message's own text ("Routine completed: X"), not hardcoded "Created routine"
- [x] LIVE egress validation: created `botter-egress-test` via API → proxy symlinks correct → sandbox ran `echo` (exit 0, returned as live task_report) → external `curl https://api.github.com` → HTTP 200 through shared iron-proxy → purge clean, iron-proxy intact, verify_hermes 22/22
- [x] Profile deltas confirmed live: `platforms.slack.enabled: false`, `proxy.enabled: true`, `docker_volumes: []` (note: `docker_forward_env` still forwards GITHUB/VERCEL/SUPABASE tokens to bot sandboxes — deliberate for now, bots need them for real work; revisit for least-privilege per-bot)
- [x] launchd install: `com.treysweeney.botterd` running via `scripts/install_botterd.sh`, KeepAlive, healthy

### Phase 3 backend intended outcome and constraints (2026-08-14)

- Preserve the pinned `SPEC.md` §4 envelopes and normalize Hermes cron data behind `{"executions": […]}`.
- Provision cloned-profile egress through supported Hermes behavior, preferring a shared main proxy only if profile credentials and isolation are source-supported; never mutate main or Hermes source.
- Deliver routine output into the bot's default session only if Hermes has an explicit supported delivery path; otherwise surface the source-supported fallback honestly.
- Add idempotent launchd install/uninstall scripts, but do not run either script or create any real Hermes profile.

### Phase 3 backend plan

- [x] Inspect Hermes egress CLI help, source resolution path, and main proxy structure without exposing secrets.
- [x] Inspect Hermes jobs API, cron persistence schema, and delivery modes/session targeting.
- [x] Confirm the egress and cron delivery decisions from evidence before implementation.
- [x] Implement profile egress provisioning and wire it into bot creation with mocked subprocess/file tests.
- [x] Implement execution-history normalization and supported cron-to-session delivery behavior with fixtures/mocks.
- [x] Add idempotent `install_botterd.sh` and `uninstall_botterd.sh` with occupied-port protection.
- [x] Run focused tests, full `uv run pytest`, and `bash -n` on both scripts.
- [x] Review the diff for contract preservation, main-profile isolation, secret leakage, and retained purge restart retries.
- [x] Record evidence, decisions, verification output, and review notes in `backend/NOTES.md` and this file.

### Phase 3 backend review

- Egress source review selected one main-owned daemon with profile-local links to only the four Docker-required artifacts. Profile config explicitly keeps `proxy.enabled` and `enforce_on_docker` true; provisioning fails closed rather than granting direct network access.
- Cron source review confirmed the API has no full history and no api_server-session delivery target. History comes from the read-only profile ledger; terminal executions are virtualized into the default thread and broadcast through the pinned events.
- Independent review caught and fixed the canonical Hermes envelope shapes for both `mappings.json` (`{"version", "tokens"}`) and `jobs.json` (`{"jobs", "updated_at"}`), plus removed the duplicate queue-time `routine_fired` event.
- The public `SPEC.md` §4 models/envelopes were not changed. The purge gateway kickstart loop remains at three attempts.
- Final verification: 21 tests passed in 0.83s; both launchd scripts passed `bash -n`; the embedded plist passed `plutil -lint`.

## Phase 4 — Polish — ✅ COMPLETE 2026-08-14 (v1 scope)
- [x] 9. Memory viewer: Details/Memory tab in bot editor, renders MEMORY.md + USER.md read-only
- [x] 10. Search: sidebar field Enter → `GET /v1/search` message results section (bot name + snippet + timestamp, click selects bot); clearing resets
- [x] 11. Read-state sync (`POST /v1/sessions/{sid}/read` on history load + message_complete), Settings window (botterd health dot, Hermes status, endpoint, token path, launch-at-login via SMAppService)
- [x] 12. Interaction polish: message entrance (8pt rise + fade, 180ms), streaming caret blink (TimelineView), unread dot scale/fade transition, sidebar row hover state
- [x] App icon: Icon Composer `Botter/AppIcon.icon` (otter artwork, dark gradient fill, translucency + specular), compiled by actool into AppIcon.icns/Assets.car — replaced the old programmatic AppIcon.appiconset
- [x] Final state: `swift test` 10/10, backend pytest 21/21, app rebuilt + relaunched against launchd botterd
- Deferred (v1.5+): general document/PDF attachments, model override picker, "Created routine" chip deep-link to routines panel

## Review log
(append findings/decisions here)

## Composer + Hermes settings UI fixes (2026-08-14)

### Confirmed outcome

- Balance the composer's outer vertical inset at 12pt while retaining its 44pt internal pill geometry.
- Make the leading plus button attach one real image with preview/removal; Hermes supports inline images on this transport but explicitly rejects generic file parts.
- Keep supported inputs to PNG/JPEG/GIF/WebP, validate the actual signature, and cap raw image data at 5 MB.
- Keep an exact 8pt gap between the Config search field and results outside the scroll content.

### Plan

- [x] Confirm the intended composer spacing and inspect Hermes' supported attachment surfaces.
- [x] Normalize the composer inset using the existing design tokens/layout conventions.
- [x] Implement image selection, preview/removal, validation, structured transport, history normalization, and rendering end to end.
- [x] Add deliberate spacing between the Config search field and its result list.
- [x] Add focused tests for image validation, Hermes run payloads, history normalization, and Swift decoding.
- [x] Run BotterKit tests, the backend suite, and an Xcode build.
- [x] Launch the freshly built app and verify live botterd health; automated UI clicking was blocked by macOS' Accessibility permission gate, so the click-to-picker path was additionally verified through its compiled state/presentation wiring.

### Review

- Composer spacing is now symmetric without changing the pill's internal 44pt geometry. Config search/results use an explicit 8pt sibling gap that cannot scroll away.
- The plus button opens a native image importer, renders a removable preview, supports image-only sends, and disables only during streaming. Invalid/oversized images surface a user-facing alert.
- Botter sends Hermes' native multimodal content shape through the existing `/v1/runs` approval-capable flow; user-image data survives history reloads and renders in the conversation.
- Generic documents were not faked or silently converted: Hermes' current session HTTP API rejects them, and the UI honestly filters to its supported image formats.
- Verification: backend `69 passed` (one pre-existing asyncio subprocess cleanup warning); BotterKit `17 passed`; Xcode `BUILD SUCCEEDED`; restarted launchd botterd returned healthy with Hermes reachable.

### Composer internal-spacing correction

- [x] Replace the asymmetric 14pt leading / 8pt trailing inset with optically balanced 16pt leading / 14pt trailing insets (the 24pt plus and 28pt mic centers now both sit 28pt from their edge).
- [x] Center the controls vertically, increase icon-to-field spacing from 10pt to 12pt and vertical inset from 8pt to 10pt; increase the radius from 22pt to 24pt to preserve true pill geometry.
- [x] Rebuild the app and verify the exact internal spacing values changed in source and compiled output (`xcodebuild`: `BUILD SUCCEEDED`).

### Composer deployed-app refresh correction

- [x] Resolve the executable/app-bundle path used when Botter is opened normally: the user-facing copy was `/Applications/Botter.app`, still timestamped 10:10, while the corrected build existed only elsewhere.
- [x] Build the spacing change into the normal Xcode DerivedData product and sync it to `/Applications/Botter.app`.
- [x] Relaunch the deployed bundle and verify PID 73005 runs `/Applications/Botter.app/Contents/MacOS/Botter`; installed and build-product dylib SHA-256 hashes match.
- [x] Document the shortest reliable refresh workflow for future UI edits: build, `ditto` the DerivedData app into `/Applications/Botter.app`, then open that exact path. Quit/reopen alone never recompiles source.

#### Review

- Root cause was two app bundles with the same bundle identifier: a stale 10:10 `/Applications` copy and a newer development product. The prior `/tmp` build proved compilation but could not update the Dock/Spotlight-launched app.
- Normal DerivedData rebuild completed successfully at 12:29. The `/Applications` compiled Swift dylib matches it byte-for-byte (`ad49bb19…bd96`) and the running process path is the deployed bundle.

## botterd v1.5 — Connections API intended outcome and constraints (2026-08-14)

- Implement the three pinned `/v1/connections` operations and `connection_updated` firehose event in both the real and mock servers.
- Manage only the six named Hermes `.env` integrations plus the main-profile Google token; keep Slack display-only and never mutate main Slack configuration.
- Preserve `.env` bytes outside exact managed-key lines, use mode `0600`, never expose secret values in responses/logs/errors/tests/docs, and perform no live-system mutations in this session.
- Define `connected` as locally configured, not externally verified; keep status checks to local file/env presence and Google expiry parsing.

### Connections implementation plan

- [x] Confirm the pinned response/request/event shapes and existing error/status conventions.
- [x] Establish source-backed Hermes `.env` load timing, sandbox forwarding keys, and Google OAuth behavior.
- [x] Add strict shared connection, request, envelope, and authorization models.
- [x] Implement the connection registry, byte-preserving atomic env edits, cheap status detection, managed-kind connect/disconnect, gateway refresh/restart behavior, and event publication.
- [x] Wire real routes and add six mixed canned mock connections with Google authorization behavior and immutable Slack.
- [x] Add focused unit tests for registry/env/token behavior, redaction, events, real ASGI routes, mock contract, and Slack immutability.
- [x] Run focused tests and full `uv run pytest`; review the diff for contract fidelity, secret leakage, file scope, idempotency, and restart safety.
- [x] Add `backend/NOTES.md` Connections evidence and append the final review/verification record here.

### Connections review

- The real registry exposes six API-key integrations, Google OAuth, and display-only Slack. Multi-field POST updates only supplied aliases; DELETE removes every mapped assignment.
- The `.env` editor atomically replaces exact assignments, collapses managed duplicates, preserves all other bytes and newline style, rejects line injection/symlink escape, and enforces mode `0600`. Responses, errors, logs, docs, and source contain no live credential values.
- Source review corrected the product scope honestly: main `.env` changes are hot for main and future clones, while existing bot profiles retain independent copied files. A gateway restart cannot synchronize them. Exa alone restarts after a real mutation because its SDK client is process-cached.
- Google status is local-only and never refreshes. The supported PKCE flow cannot be completed by the pinned API under the runtime-write constraints, so authorization returns `url: null` plus Hermes-guided instructions.
- Both real and mock ASGI contracts cover named envelopes, all connection kinds/statuses, Google authorization, strict requests, immutable Slack, and exact `connection_updated` payloads.
- Final verification: `uv run pytest` collected 30 tests and passed 30/30 in 1.12s; `compileall` passed; the repository credential-pattern audit found no key/token material.

### 2026-08-13 authoring review
- Source inspection confirmed `platforms.api_server.extra.host/port`, `gateway.multiplex_profiles`, profile deletion with `hermes profile delete <name> --yes`, and `/v1/runs` approval requests using `choice`.
- Scripts are authored but not executed in this run; supervisor runtime evidence is required before filling `backend/NOTES.md`.

### 2026-08-13 Fable review of codex scripts (fixes applied before execution)
- setup: launchd detection `rg 'iron|proxy'` matched Apple system services (`com.apple.networkserviceproxy`) and would have kickstarted one — replaced with `hermes egress status/restart` (status table prints to stderr, handled).
- investigate: cron approval-probe prompt told the bot to write to the real `~/.hermes/config.yaml` — replaced with harmless `/tmp/botter-approval-probe` target.
- investigate: profile API key fallback to main key (cloned profiles may share); same in verify's per-profile check.
- investigate: `json_field` regex couldn't parse nested JSON (session id extraction failed) — replaced with recursive JSON search.
- verify: allowlist containment check was grep-for-quoted-strings; ruamel writes unquoted entries → false FAILs. Now compares parsed YAML.
- verify: traceback check flagged benign BrokenPipe/APIConnectionError artifacts of our own deliberate gateway restart — now filtered.

### 2026-08-13 contract reconciliation (Phase 1 ↔ Phase 2 convergence)
- SPEC §4 hadn't pinned REST envelopes; codex implemented named envelopes (`{"bots": […]}` etc.) with flattened roster fields and flagged it honestly. Decision: backend shapes win — they're strict (pydantic `extra="forbid"`), tested, and sensible. Swift client adapted; envelopes now pinned in SPEC §4.
- Divergences fixed on the Swift side: envelope decoding, chat body `message` (was `text`), read marker `message_id`, memory `{bot_id, memory, user}`, task state `failed` (was `error`), Session/Routine/Approval field additions, `approval_boundary` required on create.
- Fixed on the backend side: mock roster glyphs were SF Symbol names → switched to the app's pinned 10-glyph vocabulary (now also pinned in SPEC §5); e2e.sh glyph likewise.
- e2e.sh approval step made tolerant: sandboxed-terminal installs may legitimately raise no approval (Phase 0 finding); warn instead of fail, still require message_complete.
- Codex contract concerns logged: `before` = exclusive message-id cursor (pinned); session-id ambiguity across profiles → `ambiguous_session` error; Hermes can restrict approval choices/issue multiple approvals per run — v1 accepts the fixed enum + run_id keying.

### 2026-08-13 runtime findings (evidence in backend/fixtures/)
- (a) Multiplexed gateway discovers new profiles LIVE — `/p/<slug>/api/sessions` returned 200 on the first attempt after `hermes profile create`. No restart needed in botterd bot-create flow.
- NEW finding: after `hermes profile delete` + recreate under the same slug WITHOUT gateway restart, the gateway retains in-memory session/title state ("Title already in use by session <old-id>", HTTP 400). botterd's purge flow must restart (or otherwise reset) the gateway if a slug may be reused. Investigate script now restarts the gateway when it must clean up a leftover scratch profile.
- NEW finding (profile purge is NOT clean by CLI alone): once a bot's docker sandbox has run, `hermes profile delete` CRASHES — the VirtioFS-mounted cache dirs carry a macOS ACL (`user:… deny delete`) and Hermes' rmtree retry hits an upstream py3.11 bug (`shutil.rmtree(onexc=)` TypeError). Delete then leaves a HALF-DELETED profile (`.env`/config gone, sandbox dirs remain) that 401s on API and still shows in `hermes profile list`. Correct purge sequence for botterd: stop the profile's sandbox container (match docker mounts on `profiles/<slug>`) → `hermes profile delete --yes` → if dir remains: `chmod -R -N` + `chmod -R u+rwX` + `rm -rf` → remove `~/.local/bin/<slug>` wrapper → restart gateway.
- NEW finding: `--clone` copies main's full config including tool mounts — the scratch sandbox container mounted `~/Documents/Deep South Software` (company vault). Per-profile config deltas in bot-create must prune mounts/platform access the bot shouldn't have.
- Investigation (c) API facts: `POST /p/<slug>/api/jobs` returns full job JSON (id, `schedule.expr`, `state`, `last_run_at`, `next_run_at`, `deliver`); `POST …/jobs/{id}/run` responds 200 immediately and just moves `next_run_at` to now — execution is async via the cron scheduler.
- `/v1/runs` on a profile: 202 `{"run_id": "run_<32hex>", "status": "started"}`.

## Connections v2 — add connections via UI (2026-08-14)

Goal: the Connections sheet becomes a real "add a connection" surface — dynamic
channel catalog + credential forms + an in-app Google OAuth flow — using Hermes'
sanctioned dashboard API (`hermes serve`), never patching Hermes core.

Architecture: botterd spawns/supervises one `hermes serve --host 127.0.0.1 --port 0`
child with a self-minted `HERMES_DASHBOARD_SESSION_TOKEN`, parses the
`HERMES_BACKEND_READY port=N` sentinel, and proxies a curated subset of its API.
Gateway restarts reuse botterd's proven launchctl kickstart + health-wait path.
Main-profile scope only (same as Connections v1); Slack stays untouchable.

### Plan
- [x] 1. botterd `hermes_serve.py`: HermesServe supervisor (lazy spawn, ready-sentinel parse, stdout drain, token auth client, restart-on-death, clean shutdown, HERMES_PARENT_PID watchdog)
- [x] 2. botterd `channels.py` + models: `GET /v1/channels`, `PUT /v1/channels/{id}` (env/clear_env/enabled → dashboard PUT, then kickstart restart + health wait + settle poll), `channel_updated` event; deny-list slack/api_server/webhook/relay/msgraph_webhook/wecom_callback
- [x] 3. Google in-app OAuth: drives the skill setup script via hermes venv python; POST /v1/connections/google accepts `code` + `client_secret_json`; authorization payload gains `code_entry` + `needs_client_secret`
- [x] 4. Connection exposes `fields` — Vercel team_id / Supabase project_ref now reachable end to end
- [x] 5. Mock server: channels endpoints, google code flow, exa/xai rows added
- [x] 6. SPEC.md §4 rows + SSE payloads pinned; NOTES.md evidence appended
- [x] 7. Swift models/client/store + app-level ConnectionsStore in the SSE loop (dead `apply(_:)` fixed)
- [x] 8. Swift UI: Connections sheet v2 (Credentials + Channels, ChannelConfigSheet, Google paste-URL callout, extra-fields inputs)
- [x] 9. Tests: pytest 42/42 (6 new channel/supervisor + 6 new google tests), swift test 17/17, xcodebuild succeeded
- [x] 10. Verification: live read-only smoke (33-platform catalog, 401 without token, sentinel-first-line), end-to-end via launchd botterd (27 channels, 1.35s), serve child reaped on botterd restart; no gateway restart performed

### Connections v2 review
- Live platform state for a healthy adapter is `connected` (not `running`) — caught in live smoke, aligned in UI/mock/SPEC before ship.
- The dashboard PUT does not restart the gateway; botterd owns the restart with the proven kickstart+health-wait, then polls the /test probe up to 10s so the response isn't a stale `pending_restart`.
- Hermes auto-enables a platform once its token env var exists (`_enable_from_env`), so "save credentials" is the connect action; disable writes `enabled: false` which is honored explicitly.
- Slack stays untouchable at three layers: excluded from /v1/channels, 403 on PUT, display-only row in /v1/connections.
- Orphan safety: HERMES_PARENT_PID watchdog (~2s poll, os._exit) covers SIGKILLed botterd; lifespan close covers clean shutdown — verified live across a kickstart restart.
- Still main-profile scope; per-bot channels later via the dashboard API's `?profile=` param.

## Integrations (generic keys) — ✅ COMPLETE 2026-08-14
- [x] botterd `integrations.py` + `/v1/integrations` (list/put/delete via dashboard /api/env + credential lifecycle), `integration_updated` event
- [x] Exclusions: channel_managed + messaging category + curated connection keys + protected infra keys (403) — live-verified
- [x] Mock server rows/routes; SPEC §4 rows + SSE payload
- [x] Swift: Integration model/client/store, Integrations section, searchable catalog picker (category groups, docs links, custom keys), replace/remove flows
- [x] Tests: pytest 47/47, swift 17/17, build OK; live smoke: 133-key catalog, custom-key PUT→.env→DELETE→clean, protected-key 403s

## Otter avatar set — 2026-08-14
- [x] App icon: Icon Composer `app/Botter/AppIcon.icon` wired via `project.yml` (`wrapper.icon` resource); `.icns` fallback for macOS 15, layered stack in `Assets.car` for macOS 26
- [x] Avatar glyphs replaced: 10 ASCII-mosaic otters (`float swim dive stand sprawl peek groom shell wave raft`) generated with codex imagegen, exported as template PNGs in `BotterKit/Resources/Otters.xcassets`
- [x] `Glyph.resolve` maps the legacy vector vocabulary onto the otter set so existing bots keep a stable avatar; SPEC §5 re-pinned, mock roster + e2e.sh updated
- Art direction notes: generated white-on-black then converted to alpha (imagegen handles transparency poorly); coarse ~20-char mosaic + a solidify pass, because fine ASCII stipple averages to an illegible wash below 30pt. Dropped a "curled ball" pose — a circular silhouette inside a circular disc reads as a blob.
- [ ] Trey to confirm on-screen: ⌘N sheet header preview + avatar picker row, sidebar rows at 18/36pt

## Connector credentials unavailable in bot chats — 2026-08-14

### Intended outcome and constraints

- A connection completed from Botter must be usable by the bot chat runtime whose UI reports that connection as connected.
- Fix the shared credential lifecycle, including Google file credentials and env-backed connectors, without exposing secret values or weakening Hermes sandbox isolation.
- Preserve per-profile runtime boundaries, Slack immutability, and existing gateway/egress behavior.

### Plan

- [x] Reproduce the profile-path mismatch from connection write through bot runtime/sandbox resolution.
- [x] Compare Google file credentials with env-backed connections and identify the smallest shared propagation abstraction.
- [x] Confirm the current ownership semantics from the existing UI/API contract and implementation evidence; product ownership change awaits Trey’s decision.
- [x] **DECISION GATE:** Trey confirmed authentication is global across Botter bots.
- [x] Add regression tests that fail for an existing bot profile after a connection update.
- [x] Implement the root-cause fix for connect, status, and disconnect across affected managed connectors.
- [x] Run focused tests, the full backend suite, Swift tests/build if the contract or UI changes, and secret-pattern audits.
- [x] Review the diff for sandbox isolation, symlink/path safety, partial-failure behavior, and document results here.

### Review

- `GlobalAuth` now treats main credentials as canonical and reconciles only DB-registered profiles (active and archived), never filesystem orphans. Curated env keys use byte-preserving transactional edits; integration-kind rows use Hermes' profile-scoped lifecycle with a durable retry marker; config and messaging channels retain main-profile scope.
- Google token/client files are regular mode-0600 copies. OAuth status understands refresh grants and tolerates per-profile access-token refresh while rejecting malformed/empty grants. Connect/disconnect block known chats and running routine ledgers, serialize with bot creation/manual routine starts, and refresh fixed credential mounts with exact-label Docker sweep → gateway restart → sweep.
- Independent review found and drove fixes for client-secret symlink overwrite, malformed-token 500s, pending-disconnect retry, lifecycle cancellation, unset DELETE semantics, mixed-generation status reads, routine overlap, profile-root containment, and launchd's missing Docker PATH. Remaining upstream limitation: Hermes exposes no transaction or scheduler-pause primitive; Botter retains durable repair markers and rechecks quiescence around sandbox sweeps.
- Final automated verification: backend `72 passed` (one pre-existing asyncio subprocess-finalizer warning), BotterKit `17/17`, and macOS `xcodebuild` succeeded. Credential-pattern audit found no repository credential material (only package URL hash false positives in `uv.lock`).
- Live repair: botterd first reported Google fleet drift, then copied the existing main token/client into `inbox-manager`, normalized all four files to regular `0600`, cleared the pending marker, removed stale exact-profile containers, restarted Hermes successfully (fresh gateway PID), and now reports Google plus all curated credentials connected for every Botter bot.

## Connections / Config split — 2026-08-14

Intended outcome: the profile popup has separate **Connections** and **Config** tabs. Connections contains credentials, service integrations, and messaging channels; Config contains non-service Hermes settings. The API pins an explicit `kind: "integration" | "config"` discriminator so the client does not infer product meaning.

Constraints: keep all existing credential lifecycle and ownership protections; never expose secret values; never mutate Slack or restart the Hermes gateway; keep changes limited to this presentation/contract split.

### Plan

- [x] Run the complete backend suite and independently review classification/contract behavior.
- [x] Run BotterKit tests and add explicit Integration decoding coverage if missing.
- [x] Regenerate and build the macOS app; fix any SwiftUI compile issues.
- [x] Pin `kind` in `docs/SPEC.md` and record implementation evidence in `backend/NOTES.md`.
- [x] Restart only botterd, smoke the live integrations payload without printing values, and relaunch Botter.
- [x] Review the final result for simplicity, scope, and secret safety; document verification here.

### Review

- Live metadata disproved the initial password/URL-only rule because Hermes masks unknown settings as passwords. The final server-owned classifier uses catalog metadata, explicit service-identifier suffixes, and setting-oriented custom suffixes; Swift never guesses.
- Independent review caught and fixed mock custom-PUT drift and custom-delete response reclassification before final deployment.
- Final verification: backend 48/48, BotterKit 17/17, app build succeeded, live catalog 70 integrations / 63 config, and reversible custom-config PUT/DELETE clean. One existing asyncio subprocess-finalizer warning remains unrelated to this feature.
- `botterd` was restarted without touching the Hermes gateway or Slack. The newly built Botter app is running from DerivedData (PID confirmed after a one-time LaunchServices quit/open race).

## One reply per turn — 2026-08-14

Problem: one user message produced 7 assistant bubbles. Hermes writes an assistant row for
each interim narration sentence. Each row carries `tool_calls` plus text. `normalize_rows`
made a bubble from every row. Only the last row of a turn is the true answer.

Evidence: `normalize_rows` over the live `inbox-manager` `state.db` gives 7 bubbles for the
turn "Google has been reconnected". This matches the screenshot.

Intended outcome: Botter shows one assistant bubble for each turn. The narration and the tool
steps move into a collapsed trace above the answer. The agent also narrates less.

Constraints: do not patch Hermes core. Keep the SSE contract in SPEC §4. Keep the live stream
and the reloaded history consistent.

### Plan

- [x] Rewrite `normalize_rows`: fold interim rows into a trace, pair tools by `tool_call_id`, emit one message per turn
- [x] Add `note` to `TaskItem.state` in `models.py` and `docs/SPEC.md`
- [x] Select `tool_call_id` in the `feed.py` SQLite fallback queries
- [x] Emit narration notes on the live stream path (`chat.py`) so live and reloaded traces match
- [x] Extend backend tests with real narration + `tool_call` wrapper pairing cases
- [x] Make `TaskReportCard` a collapsed trace above the answer; render note lines apart from tool lines
- [x] Add a brevity rule to `render_soul` and regenerate SOUL.md for the existing bots
- [x] Verify: backend suite, live `/v1/sessions/<id>/messages`, live run, rebuilt app relaunched
- [ ] Trey confirms the collapsed trace on screen (this shell has no Screen Recording permission)

### Review

- Name-based tool pairing was the hidden second defect. A Hermes `tool_call` wrapper reports the
  function name `tool_call`, while the result row carries the real tool name. The mismatch failed
  the old all-or-nothing guard and dropped the whole trace for that turn. Pairing now uses
  `tool_call_id`, which both the Hermes API and the SQLite fallback provide.
- Live evidence: the `inbox-manager` session went from 22 bubbles to 10 (5 turns), and the
  "Google has been reconnected" turn went from 7 bubbles to 1.
- A live probe run returned one `message_complete` with `kind=task_report` and the exact answer
  `hello`. The narration buffer did not swallow the answer, because it only flushes at a tool start.
- The three bot SOUL.md files matched the generated template byte for byte before the rewrite, so
  the brevity rule cost no hand-written content.
- Verification: backend 108/108, BotterKit 18/18, `xcodebuild` succeeded, botterd restarted healthy,
  and the app runs from the rebuilt bundle (PID confirmed). The Hermes gateway was not restarted.

## 2026-08-16 — Word-level streaming text

Goal: replace the chunk-fade streaming bubble with the reference behaviour Trey supplied (React):
words resolving out of blur at a steady cadence, an inline caret riding behind the last word.

### Plan

- [x] `StreamTokenizer`: split live markdown into styled words (bold, italic, inline code, links,
      bullets, headings) with line/paragraph breaks
- [x] `WordFlowLayout`: baseline-aligned paragraph flow, one view per word (per-word blur is
      impossible inside a single `Text`)
- [x] `StreamingProse`: paced release decoupled from delta arrival, blur/opacity/rise transition
- [x] `StreamingBlocksView`: settled blocks render as their final selves; only trailing prose animates
- [x] Close the streamed-vs-settled gap: bullets, inline-code tint, link colour, paragraph gap, width
- [x] Hover-revealed copy action under finished replies
- [x] `SnapshotDump`: headless PNG rendering of chat views (`BOTTER_SNAPSHOT_DIR=...`)
- [x] Verify: build, snapshot frames at five stream prefixes + settled + a frozen reveal ramp,
      12 s live run of the looping gallery demo, BotterKit 18/18
- [ ] Trey confirms the cadence on screen (⌘⇧D → Streaming); a static frame cannot show timing

### Review

- The old `StreamedText` faded each arriving delta as one lump, so a burst of ten words popped in
  together. Release is now paced on its own clock (55 ms per word, accelerating to drain any
  backlog within ~0.3 s), which is what makes the reveal read as reading rather than as chunks.
- The last word is held back until the next delta arrives: it may still be half-transmitted, and
  revealing it would flicker as it grows.
- Streaming and settled renders were compared frame to frame and now wrap at the same points with
  the same bullets, code tint and link colour. Two changes were needed on the settled side —
  `ProseBubble` normalises list markers to "•", and `Text(markdown:)` styles inline-code runs —
  because inline-only `AttributedString` renders neither.
- Text selection is unavailable during the stream (words are separate views) and returns the
  moment the message settles.
- `SnapshotDump` exists because `screencapture` needs Screen Recording permission this shell does
  not have. `cacheDisplay` and `CALayer.render(in:)` both return blank pixels for SwiftUI; only
  `ImageRenderer` draws the real tree.

## 2026-08-16 — Steady stream status and non-jittery scrolling

Goal: two grievances from a screen recording — the transcript stutters while text streams, and the
status line flips between "Thinking", streamed narration and tool names within a single turn.
Wanted: one steady "Thinking" during the turn, steps behind a "Worked through N steps" disclosure.

### Plan

- [x] Trace the flip to its source (`ChatStore.apply` rewrote `toolActivity` on every tool event and
      wiped the live text, so the bubble swapped between prose and the indicator mid-turn)
- [x] Drop `toolActivity` from `ChatStore.Streaming` — steps already live in `currentSteps`
- [x] `AgentWorkingIndicator` says "Thinking", nothing else
- [x] `StreamingBubble` keeps the indicator pinned below the prose for the whole turn
- [x] `ThinkingTraceView` label becomes "Worked through N steps" when there are steps
- [x] Show the disclosure whenever a trace has steps, not only when the turn took >= 2 s
- [x] Replace the per-token `proxy.scrollTo` with `.defaultScrollAnchor(.bottom, for: .sizeChanges)`
- [x] Verify: build, `SnapshotDump` turn frame, BotterKit 18/18
- [ ] Trey confirms the scroll is smooth on screen (a static frame cannot show it)

### Review

- The jitter had two causes stacked on each other. `onChange(of: chat.streaming)` fired on every
  delta and issued an unanimated `scrollTo`, which fought the word-reveal animation growing the
  content between those jumps. And a tool call reset the live text to "", collapsing the bubble
  back to the small indicator and then re-growing it — a hard height change per step.
- `.defaultScrollAnchor(.bottom, for: .sizeChanges)` (macOS 15) hands the follow-the-bottom job to
  the scroll view, which tracks the animated height instead of chasing it. The explicit `scrollTo`
  is kept only for `messages.count`, so sending still snaps to the bottom from anywhere.
- Keeping the indicator on screen for the whole turn is what removes the remaining swap: when a
  tool call clears the narration, only the prose above it goes away — the "Thinking" row does not
  move, appear or change its label.
- `toolActivity` was removed from the streaming state rather than merely ignored, so there is one
  source for what the agent did (`currentSteps` -> `ExchangeTrace`) and the live state no longer
  churns on tool events at all.
- Not verified here: the scroll itself. `screencapture` needs Screen Recording permission this
  shell does not have, and the recording on the Desktop was unreadable for the same reason.
