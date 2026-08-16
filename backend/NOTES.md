# Phase 0 investigation notes

## Plan impact

The live run resolves the Phase 0 questions and identifies these contract or plan mismatches. The Hermes source and captured fixtures are authoritative. The planning documents (`SPEC.md` §4/§7, the internal planning documents) were updated 2026-08-13 to reflect every mismatch below.

The supervisor also reran setup and verification successfully: 22/22 checks passed, including loopback-only binding, repaired proxy allowlist, and Slack remaining connected on `main`.

1. **New-profile discovery.** The original open-question wording in `SPEC.md §7` and the pre-investigation create flow implied that a gateway restart might be needed before a new profile was visible. It is not: the first request after `hermes profile create` returned HTTP 200 without a restart ([investigation_a_no_restart.txt](fixtures/investigation_a_no_restart.txt)). A restart is still required during purge to clear in-memory state and prevent resurrection.
2. **Approval payload and endpoint.** `SPEC.md §4` describes botterd's public `POST /v1/approvals/{run_id}` payload as `{ "decision": ... }`; Hermes' upstream route is `POST /v1/runs/{id}/approval` and its body is `{ "choice": "once"|"session"|"always"|"deny" }`. Botterd must translate its public `decision` field to upstream `choice`. No live approval event was produced in this run, so this wire shape is source-verified rather than fixture-observed.
3. **Cron approvals.** `SPEC.md §7` and the backend plan left cron approvability open (or allowed wiring it later). The cron job ran asynchronously, returned `last_status: "ok"`, produced no `/v1/runs` record and no approval ([investigation_c_cron_approval.txt](fixtures/investigation_c_cron_approval.txt)). The v1 design must use the documented SOUL.md boundary plus `tool_loop_guardrails` fallback; app-side approval applies to app-initiated runs only.
4. **Session model.** The bot-create flow must not rely on the `/api/sessions` default. Hermes uses the literal `hermes-agent` when `model` is omitted, which can be rejected upstream. Always send an explicit valid model (the successful run sent `deepseek/deepseek-v4-flash-0731`; see [session_create.txt](fixtures/session_create.txt)).
5. **Clone is not production-ready by itself.** The setup plan's statement that `--clone` makes a bot work immediately is contradicted by the observed clone: it copied the user's full tool mounts, including unrelated private document directories from the host, and inherited `proxy.enabled: true` without profile-scoped iron-proxy config/tokens. Every terminal attempt was blocked with “proxy.enabled is true but iron-proxy is not configured” ([chat_stream_tool.sse](fixtures/chat_stream_tool.sse); clone provenance is recorded in [phase0_setup.txt](fixtures/phase0_setup.txt)). Bot-create must prune non-essential mounts and provision or explicitly handle per-profile egress before enabling terminal work.
6. **Platform inheritance.** The clone carries main's full platform configuration by virtue of `--clone`; no second Slack socket connected during the scratch run. Do not infer safety from the absence of a socket: bot-create must explicitly disable Slack (and any other platform the bot does not own), while keeping Slack on `main`.
7. **Purge/rollback is multi-step.** The simple “delete profile directory and restart” rollback wording in the plans is incomplete after a Docker sandbox has run. VirtioFS ACLs can make `hermes profile delete` crash (py3.11 `shutil.rmtree(onexc=...)` retry bug), leaving `.env`/config removed but a listed skeleton; a served profile can also be recreated by cron/log handles. The safe purge is: stop the profile's sandbox container; `hermes profile delete <slug> --yes`; `chmod -R -N` and remove any remainder; remove the `~/.local/bin/<slug>` wrapper; restart the gateway and wait for `/health`; then sweep any resurrected skeleton. The clean no-residue path is shown in [teardown.txt](fixtures/teardown.txt), but it does not cover the ACL/sandbox edge case. Do not reuse a slug until after the restart because deleted session/title state remains in memory.
8. **SSE is two protocols, not one.** Any plan language treating Hermes SSE as a single framing is incomplete: chat and run-events require separate parsers (details below). The downstream botterd contract may still normalize them, but the adapter must preserve tool/reasoning and terminal lifecycle information.

## (a) New-profile visibility

`hermes profile create botter-scratch --clone` created the profile and the multiplexed gateway served `/p/botter-scratch/api/sessions` immediately. The first probe returned HTTP 200 with an empty session list; no gateway restart was performed ([investigation_a_no_restart.txt](fixtures/investigation_a_no_restart.txt)). The profile had its own `.env`, config, SOUL.md and wrapper ([phase0_setup.txt](fixtures/phase0_setup.txt)). Per-profile routes authenticate with that profile's `.env` key; a clone copies main's key, while a half-deleted profile without `.env` is rejected with “no profile-scoped API_SERVER_KEY is configured.”

Deletion is the exception. After a sandbox has run, stop the matching Docker container before CLI deletion, handle ACLs and leftovers, remove the wrapper, restart the gateway, and sweep any skeleton that reappears. The gateway's in-memory session/title cache also requires that restart; otherwise same-slug recreation can fail with “Title already in use.”

## (b) SSE wire formats

`POST /p/<slug>/api/sessions/{id}/chat/stream` emits conventional named SSE events: `event:` followed by `data:` JSON. Observed events include `run.started`, `message.started`, `assistant.delta`, `tool.progress` (including pseudo-tool `_thinking`), `tool.started`, `tool.completed`, `assistant.completed`, `run.completed`, and `done`; tool events include `tool_name`, previews/args, and run/session/message identifiers. Keepalive comment lines (`: keepalive`) can occur during long tool runs ([chat_stream_notool.sse](fixtures/chat_stream_notool.sse), [chat_stream_tool.sse](fixtures/chat_stream_tool.sse)).

`GET /p/<slug>/v1/runs/{id}/events` is a different dialect: data-only lines whose JSON contains the event name (`message.delta`, `reasoning.available`, `run.completed`), followed by `: stream closed` ([run_events.sse](fixtures/run_events.sse)). Botterd must support both parsers. A run is started with `{"input": ..., "session_id": ...}` and returns HTTP 202 plus `{"run_id":"run_<32hex>","status":"started"}` ([run_start.txt](fixtures/run_start.txt)). No live approval event was captured; use the source-verified `choice` approval payload described above.

The session-create body must include an explicit valid `model`; the successful fixture records `deepseek/deepseek-v4-flash-0731` ([session_create.txt](fixtures/session_create.txt)).

## (c) Cron approvability

The approval-shaped cron prompt was accepted, but `POST /api/jobs/<id>/run` only moved `next_run_at` to now. The scheduler fired roughly 90 seconds later, completed with `last_status: "ok"` and `repeat.completed: 1`, and never exposed a run id through the runs API or an approval event. The job was deleted afterward ([investigation_c_cron_approval.txt](fixtures/investigation_c_cron_approval.txt)). Treat cron execution as outside the app approval watcher in v1; enforce routine boundaries through SOUL.md and `tool_loop_guardrails`.

## Clone hygiene and egress

`--clone` copies main's full config, `.env`, SOUL.md and skills ([phase0_setup.txt](fixtures/phase0_setup.txt)). In addition to the company-vault mount and per-profile proxy failure above, the scratch clone did not establish a second Slack socket. Explicit per-profile config deltas remain mandatory: remove unnecessary mounts, turn Slack off, and provision/resolve egress before terminal use. The main profile remains the only live Slack agent.

## Egress provisioning

Phase 3 uses the existing main iron-proxy as the single egress boundary for bot profiles. It does **not** disable `proxy.enabled`, run a second daemon, mutate main's config, or mint new credentials.

Evidence and decision:

- `hermes -p <slug>` scopes `HERMES_HOME` to `~/.hermes/profiles/<slug>` (`hermes_cli/main.py`, profile override around lines 665–686). iron-proxy state is then hard-coded beneath that home as `proxy/`; Hermes has no config key for a shared proxy-state path (`agent/proxy_sources/iron_proxy.py`, `_proxy_state_dir_ro` around lines 365–375).
- Docker egress requires an enabled profile config plus a readable `proxy.yaml`, CA certificate, live PID/listener, and nonempty `mappings.json` (`tools/environments/docker.py`, `_egress_proxy_args_for_docker` around lines 397–555). The sandbox receives the shared proxy endpoint, CA path, and proxy tokens; real provider credentials remain in the daemon environment.
- iron-proxy mappings are provider-scoped, not profile-scoped. The config emits one required replacement rule per provider token (`iron_proxy.py`, mapping/config construction around lines 1155–1188 and 1545–1562). Distinct tokens for multiple profiles on the same provider are not supported by one daemon; the existing main mappings must be reused.
- A per-profile daemon is technically scriptable with `egress setup --no-restart`, but needs a unique three-port block, separate lifecycle supervision, and purge handling. The default port collides with main. The cloned raw `proxy.extra_allowed_hosts` is also a scalar on this install, while setup applies `list(...)` to it (`hermes_cli/proxy_cli.py` around line 386), which would render characters rather than host entries unless normalized.
- Main's read-only status showed a healthy listening daemon and the required artifacts. Its generated `proxy.yaml` contains the expected DNS/proxy/metrics/management/TLS/transforms/log sections and 22 rendered domains. No token, credential, CA key, management key, or allowlist value was recorded here.

`provision_profile_egress` creates a real mode-0700 `<profile>/proxy/` directory and individual read-through links for only `proxy.yaml`, `ca.crt`, `mappings.json`, and `iron-proxy.pid`. Individual links survive main token/config replacement and daemon PID changes but avoid exposing the whole main proxy directory through a directory symlink. Bot creation fails closed if main status is not listening, any required artifact is missing, or mappings are empty. The function is idempotent only when every existing link resolves to the expected main artifact; it refuses to replace unexpected profile state.

Egress lifecycle remains main-owned. Do not run `hermes -p <bot> egress stop`, `restart`, or `setup`: the linked PID can make `stop` signal the shared main daemon. Botterd never invokes those profile-scoped commands. Purging a bot removes only its links and profile directory; it does not follow them into main.

The installed `hermes egress status` build emitted through stdout during this investigation, while the Phase 0 script observed stderr. Callers must continue combining both streams and must never request `--show-tokens`.

## Routine execution history and delivery

Hermes has no full execution-history HTTP endpoint. `GET /api/jobs` adds only `latest_execution` (`cron/jobs.py`, `list_jobs` around lines 1842–1855), and `GET /api/jobs/{id}` returns the normalized stored job without history (`gateway/platforms/api_server.py` around lines 5648–5665; `cron/jobs.py` around lines 1795–1801).

The authoritative history is the profile-local `cron/executions.db` (`cron/executions.py`). Its `executions` table records `id`, `job_id`, source/process ownership, status (`claimed`, `running`, `completed`, `failed`, or `unknown`), claimed/started/finished timestamps, and error. Botterd opens it read-only, orders by `claimed_at DESC, id DESC`, and maps it into the pinned `{"executions": […]}` envelope. A successful execution has no summary in this ledger. Markdown output is stored separately under `cron/output/<job>/<second-resolution timestamp>.md` with no execution ID, so Botterd deliberately does not invent an unreliable join.

No supported `deliver` value targets an api_server session:

- `deliver: "local"` resolves no delivery target and only persists output (`cron/scheduler.py`, delivery resolution around lines 1279–1285 and 1605–1633).
- HTTP job creation overwrites origin with `{"platform":"api_server","chat_id":"api"}` and does not accept a caller session or `attach_to_session` (`gateway/platforms/api_server.py` around lines 1731–1748 and 5629–5642).
- Concrete delivery targets are messaging platforms. The api_server adapter cannot push a message because its response belongs to an HTTP request.
- Each cron fire runs in a new `cron_<job>_<timestamp>` session (`cron/scheduler.py` around lines 3497, 4112, and 4433–4491), never the bot's default session.
- Hermes' `attach_to_session`/`cron.mirror_delivery` feature mirrors only a successful non-local delivery to the exact originating gateway chat. It has no effect for a local/API-created job.

Botterd therefore keeps routine creation honest with `deliver: "local"`. `FeedWatcher` also watches `executions.db` and its WAL. New terminal ledger records emit the pinned `routine_fired` event plus `feed_updated`. For durable UI history, Botterd merges deterministic virtual `routine_created`-style status messages (`routine-execution:<execution-id>`) into only the bot's default session response and sidebar calculation. These messages state completed/failed/unknown from the ledger and are never written into Hermes' `state.db`.

## Supervisor script fixes

- BSD `awk` has no `strftime`; timestamping was moved to a Python stamper.
- A broad `rg 'iron|proxy'` matched Apple launchd services (including `com.apple.networkserviceproxy`); the restart logic now avoids dangerous kickstarts and uses the Hermes egress mechanism.
- `hermes egress status` writes its table to stderr, so capture logic reads both streams.
- `run_[0-9a-f]+` matched the literal `run_a` inside the JSON key `last_run_at`; run IDs are now extracted from parsed/nested JSON rather than loose grep.
- Teardown is ACL-aware and restarts the gateway before sweeping resurrected profile skeletons.

## Connections

### Hermes environment loading

The live main configuration forwards `GITHUB_TOKEN`, `VERCEL_TOKEN`, `VERCEL_TEAM_ID`, `SUPABASE_ACCESS_TOKEN`, and `SUPABASE_PROJECT_REF` into Docker terminals through `terminal.docker_forward_env`. `OPENROUTER_API_KEY`, `EXA_API_KEY`, and `XAI_API_KEY` are consumed by main-process provider/tool paths instead. `tools/env_passthrough.py` is not a substitute for this explicit forwarding: it rejects Hermes-managed provider credentials to preserve sandbox isolation.

Hermes loads the default profile `.env` at gateway startup and reloads credentials for turns (`gateway/run.py`, environment loading around lines 1880–1914). In multiplex mode, each routed turn builds a fresh isolated secret scope from that routed profile's `.env` (`gateway/run.py`, `_profile_runtime_scope` around lines 2005–2038; `agent/secret_scope.py`, `build_profile_secret_scope` around lines 272–280). Docker resolves explicitly forwarded values through that current scope for each command, including persistent containers (`tools/environments/docker.py`, passthrough resolution around lines 1548–1617). GitHub, Vercel, Supabase, OpenRouter, and xAI therefore need no gateway restart when their owning profile file changes.

Exa is the exception for the main profile: its provider retains a process-level SDK client after first use (`plugins/web/exa/provider.py`, client cache around lines 41–84). Botterd restarts the gateway after an actual Exa POST/DELETE mutation and waits for health with the same three-attempt kickstart pattern used by profile purge. Repeating an identical write does not rewrite the file or restart the gateway.

There is an important profile boundary. `hermes profile create --clone` copies `.env` once with `shutil.copy2`; it does not link it (`hermes_cli/profiles.py`, clone files around lines 60–65 and copy around lines 1117–1131). Existing named bots then read `profiles/<slug>/.env` as authoritative. Botter therefore treats the main file as canonical and transactionally reconciles authentication keys into every Botter-registered profile, including archived bots; unmanaged/orphan Hermes profiles are never scanned or changed. Fleet status is `error` when any registered profile differs. New profile creation is serialized with auth mutation so the clone and first session see one consistent credential state.

### Google OAuth

Google Workspace uses `google_token.json` plus `google_client_secret.json` under the active Hermes home. Its installed setup script uses a PKCE `Flow` with the deliberately non-listening redirect `http://localhost:1`; `--auth-url` writes `google_oauth_pending.json`, and a later `--auth-code` command exchanges the pasted redirect URL and writes the token (`skills/productivity/google-workspace/scripts/setup.py`, paths/redirect around lines 42–76 and flow around lines 310–449). There is no `InstalledAppFlow`, device flow, loopback callback server, or `hermes setup google` step.

Botterd therefore does not mint an authorization URL or token. When setup is needed it returns `authorization.url: null` and instructs the user to run `hermes`, ask it to set up Google Workspace, provide a Desktop OAuth client JSON, and paste the full redirect URL back to Hermes. This leaves pending-state creation and token exchange with Hermes' supported flow.

Status reads only local token JSON and then verifies every registered profile has byte-identical token/client files with mode `0600`. A missing or expired main token is `not_connected`; malformed JSON/expiry or fleet drift is `error`; a readable file with no expiry or a future RFC3339/ISO expiry is `connected`. Botterd never runs the setup script's `--check`, because that path may refresh over the network and rewrite the token. DELETE removes every managed `google_token.json` but retains all client-secret files.

Google files are credential mounts declared by the skill and captured when a persistent Docker sandbox is created. Replacing the host file cannot update a running container's mount, and Hermes can recreate a missing container from gateway-cached run arguments. After a Google add/rotate/delete, Botterd therefore removes containers selected only by Hermes' ownership labels (`hermes-agent=1`, exact `hermes-profile=<slug>`), restarts the gateway, and performs the same label-scoped sweep again. A pending repair marker survives partial failure; retrying Connect/Disconnect must finish the rebuild before success. Google mutation is refused while Botter knows a chat run is active because sandbox removal terminates in-container work.

### POST behavior

- `api_key`: requires `secret`; accepts optional `fields` aliases (`team_id` for Vercel and `project_ref` for Supabase); transactionally updates main plus every registered bot profile and returns `{"connection": {...}}`. Presence means configured, not externally verified.
- `oauth` (`google`): returns `{"connection": {...}}` when the local token is present and unexpired; otherwise returns `{"authorization": {"url": null, "instructions": "..."}}`.
- `platform` (`slack`): returns a `403 connection_not_managed` error. GET derives display-only status from main config plus credential presence; POST/DELETE never modify Slack.

Every successful POST/DELETE publishes `connection_updated` with only `{"id", "status"}`. `.env` writes are atomic, mode `0600`, collapse duplicate assignments for managed keys, reject line-breaking input, and preserve every non-managed byte. No secret value is returned, logged, or included in errors.

## Connections v2 — channels + in-app Google OAuth (2026-08-14)

### hermes serve supervision

The Hermes dashboard backend (`hermes serve`, `hermes_cli/web_server.py`) is the sanctioned management API — the same headless FastAPI process the official desktop app spawns (`apps/desktop/electron/main.ts` ~8295). Botterd's `HermesServe` spawns `hermes serve --host 127.0.0.1 --port 0` lazily on the first `/v1/channels` request with two injected env vars: `HERMES_DASHBOARD_SESSION_TOKEN` (self-minted; echoed back as `X-Hermes-Session-Token` — verified live that requests without it get 401) and `HERMES_PARENT_PID` (Hermes' serve-parent watchdog polls it ~2s and `os._exit(0)`s when botterd dies, so a SIGKILLed botterd never leaks the child). Readiness is the stdout sentinel `HERMES_BACKEND_READY port=<n>`; observed live as the first stdout line, well under the 120s budget. The child is kept for the daemon's lifetime, terminated in lifespan shutdown, and respawned once on a failed request. stdout/stderr are drained to debug logs to avoid pipe stalls.

### Channels

`GET /v1/channels` proxies `GET /api/messaging/platforms` (main profile; observed 33 platforms live, 27 after exclusions). Excluded: `slack` (main's own agent — stays display-only in /v1/connections, PUT returns 403 `channel_not_managed`) plus `api_server`, `webhook`, `msgraph_webhook`, `wecom_callback`, `relay`, `local`. `PUT /v1/channels/{id}` forwards `{env, clear_env, enabled}` to Hermes' allowlist-validated `PUT /api/messaging/platforms/{id}` (400 unknown key, 404 unknown platform, 409 multiplex port-binding conflict — all mapped through), then restarts the gateway with the proven three-attempt `launchctl kickstart` + health-wait (the dashboard's own restart endpoint is fire-and-forget, so it is not used), then re-probes via `POST .../test` and polls up to 10s while the adapter state is still `pending_restart`/`gateway_stopped`. Live platform state for a healthy adapter is `connected` (not `running`). Every successful update publishes `channel_updated {"id", "state"}`. Note Hermes auto-enables a platform when its token env var is present (`gateway/config.py _enable_from_env`) unless `platforms.<id>.enabled: false` is explicit, so writing credentials effectively connects.

### Google in-app OAuth

POST `/v1/connections/google` now drives the skill script (`skills/productivity/google-workspace/scripts/setup.py`) under the Hermes venv python (`~/.hermes/hermes-agent/venv/bin/python`, overridable via `HERMES_PYTHON`), pinned to the right home with `/usr/bin/env HERMES_HOME=...`. Empty body: token valid → connection; no `google_client_secret.json` → `authorization {needs_client_secret: true}`; else `--auth-url` → `authorization {url, code_entry: true}`. Body `{client_secret_json}` → validated (`installed`/`web` key), written to a 0600 tempfile, stored via `--client-secret`, tempfile removed. Body `{code}` (full pasted redirect URL) → `--auth-code` → status re-read → connection. Script `ERROR:` lines surface (truncated to 300 chars); the pasted code/URL is never echoed into errors. DELETE best-effort `--revoke` before removing the token file. Deps and client secret verified present on this machine; `--check` run 2026-08-14 returned AUTHENTICATED.

### Verification (2026-08-14)

pytest 42/42 (new: test_channels.py — supervisor sentinel/auth/error mapping with a scripted /bin/sh child + httpx MockTransport, service mapping/guards/restart-failure; google flow tests with a scripted runner). Swift 17/17, xcodebuild BUILD SUCCEEDED. Live smoke against the running install: standalone spawn script (catalog + 401 check + clean SIGTERM), then end-to-end through launchd botterd — `/v1/channels` 27 channels in 1.35s cold, `/v1/connections` shows `fields`, POST google (already connected) returns the connection with no side effects. After `launchctl kickstart -k` of botterd the previous serve child was reaped and exactly one fresh child remained.

Channels remain main-profile-only. Authentication does not: curated credentials, Google, and integration-kind rows are reconciled to every Botter-managed profile through exact file updates or the dashboard API's supported `?profile=` scope.

## Integrations — generic credential keys (2026-08-14)

`GET/PUT/DELETE /v1/integrations` exposes every non-channel Hermes env credential through the supervised serve child's `/api/env` surface (133 live after filtering: 37 tool, 73 provider, 4 skill, 3 setting, 16 custom). Integration-kind writes run Hermes' credential lifecycle once per registered profile through the supported `?profile=` parameter, with profiles first and main as the canonical commit point; failures roll back already-applied targets and withhold the event. Config-kind rows remain main-profile settings. Excluded: `channel_managed` vars, the whole `messaging` category (live smoke caught 69 leaking per-platform extras like SLACK_HOME_CHANNEL — platform config belongs to Channels), the curated /v1/connections keys, and protected infrastructure keys (API_SERVER_KEY, Slack tokens). Custom UPPER_SNAKE_CASE keys are allowed; Hermes' name denylist (PATH, LD_PRELOAD, …) surfaces as 422. No gateway restart (Exa stays curated with its restart). Labels derive from provider_label or prettified key; values never round-trip. Event: `integration_updated {"key", "is_set"}` only after fleet success.

## Connections / Config split (2026-08-14)

`Integration.kind` now pins `"integration" | "config"` in the public model and mock contract. The profile popup is a two-tab Hermes sheet: Connections contains curated credentials, generic service integrations, and channels; Config contains searchable plain settings with Set/Change and Reset to default actions. Both surfaces still use the same credential-lifecycle PUT/DELETE routes, so ownership guards and redaction behavior are unchanged.

Live inspection invalidated the first `is_password || url` classifier: Hermes reports unknown `.env` keys as custom password fields, including `BROWSER_INACTIVITY_TIMEOUT`, `IMAGE_TOOLS_DEBUG`, and path/debug/terminal settings. The final classifier treats recognized custom setting suffixes (`_DEBUG`, `_TIMEOUT`, `_PATH`, `_BASE_URL`, etc.) as config and leaves other custom keys as integrations. Known catalog password fields and service identifiers (`_CREDENTIALS_PATH`, `_PROJECT_ID`, `_PUBLIC_KEY`) remain integrations; advanced browser/provider overrides remain config. Delete preserves the pre-delete custom metadata so its response does not transiently reclassify a reset config, and mock PUT/DELETE uses the same classifier.

Verification: backend `pytest` 48/48 (one pre-existing asyncio subprocess finalizer warning after loop close), BotterKit 17/17 with explicit `kind: "config"` decoding coverage, and `xcodebuild -scheme Botter build` succeeded. Final live metadata-only smoke after launchd restart: 133 rows = 70 integrations + 63 config; the reported timeout/debug rows, Obsidian path, and agent-browser engine are config, while Brave Search, Browserbase project ID, and Vertex credentials path are integrations. A reversible `BOTTER_SMOKE_TEST_CONFIG_FLAG` PUT/DELETE returned `kind=config` both times and disappeared from the catalog afterward. No secret values were printed, and the Hermes gateway was not restarted.

## Global authentication reconciliation (2026-08-14)

Botter's Connections surface is machine-global authentication for Botter-managed bots. `GlobalAuth` enumerates active and archived profiles from Botter's SQLite registry and validates the profile root/slug/final path before mutation; unmanaged Hermes profiles remain untouched. Curated env keys are edited transactionally in main and every registered profile. Integration-kind rows call Hermes `/api/env?profile=<slug>` profiles-first and main-last; a mode-0600 per-key marker survives failure/cancellation and forces a full lifecycle replay on retry, because Hermes has no cross-profile transaction covering `.env`, auth pool, cache, suppression state, and config mirrors. Status comparison runs under the same lock and surfaces pending/drift state.

Google uses stable refresh-grant identity for fleet comparison, so independently refreshed access tokens/expiry do not create false drift. Client-secret destinations are preflighted against symlinks, stored under the auth lock, normalized to 0600, and restored if profile propagation fails. Token/client files are copied atomically as regular 0600 files. Since Docker credential mounts and gateway run arguments are fixed at container construction, Google changes remove containers by exact Hermes ownership/profile labels, restart the gateway, then repeat the sweep. Botter blocks known active chats/routines and rechecks quiescence around both sweeps; manual routine starts share the lock. The scheduler has no upstream pause primitive, so a durable Google repair marker prevents false success if a late overlap or refresh failure occurs.

The launchd agent's default PATH omits Docker Desktop's CLI location. `Settings.from_env()` therefore pins `DOCKER_BIN` to `/usr/local/bin/docker` by default (overridable), just as Botter pins Hermes executables. The first live retry exposed this and safely left the Google repair marker; after the path fix, retry completed.

Final verification: backend 72/72 tests passed (the pre-existing asyncio subprocess-finalizer warning remains), BotterKit 17/17 passed, and the macOS app build succeeded. Live reconciliation changed only Botter-registered `inbox-manager`: main/profile Google token and client files are regular 0600, the repair marker is absent, no stale labeled container remains, Hermes restarted healthy with a fresh PID, and `/v1/connections` reports Google plus every curated credential globally connected. Credential contents were never printed.

## One assistant message per turn (2026-08-14)

Hermes persists an assistant row for every step of a turn. Each interim row carries `tool_calls` plus a sentence of narration; only the closing row holds the answer. `normalize_rows` made a bubble from every row, so one user message produced seven replies. Evidence from the live `inbox-manager` `state.db`: the turn "Google has been reconnected" gave 7 bubbles before the change and 1 after; the whole session went from 22 bubbles to 10.

`normalize_rows` now folds interim rows into a trace and emits one message for each turn. The narration becomes a `note` item and the tool calls become `done`/`failed` items on the closing message, in order. Tool results pair with their call by `tool_call_id`, because a Hermes `tool_call` wrapper hides the tool it runs — the result row carries the real `tool_name`, so name-based pairing dropped whole traces (observed on the Composio turn). `feed.py` therefore selects `tool_call_id` in both read-only fallback queries. Search no longer runs turn logic: one hit is one row, so it uses `normalize_row`.

A call with no result row keeps `running`. A turn that never closes still yields one `task_report` with empty `text`, so a stopped run does not lose its work. The live path keeps parity: `ChatManager._consume` buffers `message.delta` text and files it as a synthetic `assistant.note` event when the next tool starts, so `derive_task_items` builds the same trace during a run as a reload does.

`TaskItem.state` gains `note` (SPEC §4). The app renders a task report as a collapsed "Worked through N steps" line above the reply; the expanded card shows note sentences and tool steps apart. `ChatStore` clears the streaming bubble at each tool start for the same reason.

`render_soul` also gained a brevity rule ("Answer in one message… Do the work, then report once."), and the three existing bot SOUL.md files were regenerated. Each matched the previous generated template exactly before the rewrite, so nothing hand-written was lost.

Verification: backend 108/108, BotterKit 18/18, `xcodebuild -scheme Botter build` succeeded. Live `/v1/sessions/api_1786713615_c1406d2a/messages` returns 10 bubbles (5 turns). A live run through `POST /v1/sessions/{sid}/chat` produced one `message_complete` with `kind=task_report`, text `hello`, and one `done` item; the reloaded history for that turn matched.
