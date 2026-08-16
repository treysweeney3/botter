# botterd — Backend Implementation Plan (handoff: GPT-5.6-Sol / Codex)

> **Audience:** an agent with NO other context. Everything needed is in this file plus the referenced local paths. Read `docs/SPEC.md` (same repo) for the product picture and the authoritative HTTP API contract (§4) and message schema; read `docs/PLAN_HERMES_SETUP.md` for the Hermes-side config this service depends on.

## What you are building

`botterd`: a local companion daemon on this Mac that sits between a SwiftUI app and a locally-installed Hermes agent. It owns bot metadata, drives Hermes profile lifecycle via CLI, proxies chat/routines/approvals to Hermes' HTTP api_server, and aggregates a sidebar feed. Single user, localhost only, but auth from day one (bearer token) because a cloud relay will front it later.

- Language/stack: **Python 3.11, FastAPI + uvicorn, httpx (async), aiosqlite**. Dependency manager: **uv** (`pyproject.toml` + `uv.lock`).
- Location: `/Users/treysweeney/projects/botter/backend/` (create it; repo root already has `docs/`).
- Runs as launchd agent `com.treysweeney.botterd` on `127.0.0.1:8674`.
- State: `~/.botter/` → `botter.db` (SQLite), `token` (app bearer token, 0600), `botterd.log`.

## The Hermes environment (facts, verified 2026-08-13)

- `HERMES_HOME=/Users/treysweeney/.hermes`. Source checkout: `/Users/treysweeney/.hermes/hermes-agent` (Python 3.11, aiohttp, SQLite). CLI: `/Users/treysweeney/.local/bin/hermes`.
- Gateway runs via launchd `ai.hermes.gateway` (KeepAlive). After the setup plan is applied it exposes an **OpenAI-compatible HTTP api_server on `http://127.0.0.1:8642`**, bearer key in env `API_SERVER_KEY` (read it from `~/.hermes/.env`).
- **Profiles = bot instances.** Each profile lives at `~/.hermes/profiles/<slug>/` (own `config.yaml`, `SOUL.md`, `memories/MEMORY.md`, `memories/USER.md`, `state.db`, cron). Created via `hermes profile create <slug> --clone --description "<role>"`. Default profile is `main` — **never manage `main` as a bot; it belongs to the user's existing Slack deployment.**
- With `gateway.multiplex_profiles: true`, per-profile routes are prefixed: `http://127.0.0.1:8642/p/<slug>/…`.
- Hermes api_server routes (from `~/.hermes/hermes-agent/gateway/platforms/api_server.py`, route table near line 2056 — **read that file first; treat it, not this table, as ground truth**):
  - `POST /v1/chat/completions` (headers `X-Hermes-Session-Id`, `X-Hermes-Session-Key` for continuity) · `POST /v1/responses` (+ GET/DELETE `/v1/responses/{id}`)
  - `GET /v1/models`, `/v1/capabilities`, `/v1/skills`, `/v1/toolsets`
  - `GET|POST /api/sessions` · `GET|PATCH|DELETE /api/sessions/{id}` · `GET /api/sessions/{id}/messages` · `POST /api/sessions/{id}/fork` · `POST /api/sessions/{id}/chat` and `POST /api/sessions/{id}/chat/stream` (SSE) · `PATCH /api/sessions/{id}/model`
  - `POST /v1/runs` (`{"input", "session_id"}` → 202 `{"run_id": "run_<32hex>", "status": "started"}`) · `GET /v1/runs/{id}` · `GET /v1/runs/{id}/events` (SSE lifecycle incl. approval events) · `POST /v1/runs/{id}/approval` — payload key is **`choice`**, not `decision`: `{"choice": "once"|"session"|"always"|"deny"}` (verified in source 2026-08-13; botterd's own API keeps `decision` per SPEC §4 and translates) · `POST /v1/runs/{id}/stop`
  - Cron: CRUD `GET|POST /api/jobs`, `/api/jobs/{id}`, `POST /api/jobs/{id}/pause|resume|run`
  - `GET /health`, `/health/detailed`
- Per-profile SQLite (`~/.hermes/profiles/<slug>/state.db`, WAL): tables `sessions` (cols incl. `id, source, session_key, model, system_prompt, parent_session_id`, token/cost), `messages` (`role, content, tool_calls, tool_name, active, compacted, platform_message_id`), FTS5 `messages_fts`. You MAY open these **read-only** (`file:…?mode=ro`, WAL-safe) for feed aggregation and search; never write.
- Developer docs: `~/.hermes/hermes-agent/AGENTS.md` (§Profiles line ~1201), `~/.hermes/hermes-agent/website/docs/user-guide/multi-profile-gateways.md`, `docs/session-lifecycle.md`. Two Hermes invariants you must not violate through the API: don't mutate a session's past context, and don't churn system prompts mid-conversation (SOUL.md edits take effect for **new** sessions).

## Phase 0 — Hermes setup automation + smoke tests — ✅ COMPLETE 2026-08-13

Done: `scripts/setup_hermes.sh`, `scripts/verify_hermes.sh` (22/22 checks pass), `scripts/phase0_investigate.sh`; findings in `backend/NOTES.md`, raw SSE transcripts in `backend/fixtures/`. Key outcomes Phase 1 MUST honor:
   a. New profiles are served live (no restart). **Purge is multi-step** (sandbox container stop → CLI delete → ACL strip + rm → wrapper removal → gateway restart → skeleton sweep) — see `PLAN_HERMES_SETUP.md` Step 3 and `NOTES.md`.
   b. Chat SSE and run-events SSE use **different framings** (named `event:` lines vs data-only with `event` inside JSON) — `hermes.py` needs both parsers; fixtures are the parser test vectors. `POST /api/sessions` must pass an explicit `model` (defaults to invalid literal `hermes-agent`).
   c. Cron-fired executions do NOT create approvable runs — routines rely on the SOUL.md boundary fallback; app-initiated chats are the only guaranteed-approvable surface in v1. `POST /api/jobs/{id}/run` returns immediately and the scheduler fires ~1.5–3 min later (surface "queued" state in the API).
   d. Bot-create config deltas required: prune inherited mounts (clone mounted the user's company vault), resolve per-profile egress (inherited `proxy.enabled: true` with no profile iron-proxy config **blocks the terminal entirely**), confirm Slack stays off the profile.

## Phase 1 — botterd core

Implement the API exactly as specified in `docs/SPEC.md` §4. Structure:

```
backend/
├── pyproject.toml
├── botterd/
│   ├── main.py            # FastAPI app, auth middleware, lifespan (holds httpx client, watchers)
│   ├── config.py          # ~/.botter paths, reads API_SERVER_KEY from ~/.hermes/.env
│   ├── db.py              # aiosqlite, migrations (schema below)
│   ├── registry.py        # Bot CRUD, profile lifecycle (subprocess → hermes CLI), SOUL.md templating
│   ├── hermes.py          # typed async client for the Hermes api_server (incl. SSE re-streaming)
│   ├── feed.py            # sidebar aggregation + unread; read-only sqlite fallback
│   ├── chat.py            # /v1/sessions/*: proxy + translate stream → SPEC §4 SSE events
│   ├── routines.py        # /api/jobs proxy annotated with bot_id
│   ├── approvals.py       # run watchers, pending-approval store, decision forwarding
│   ├── events.py          # /v1/events firehose (in-process pub/sub)
│   └── normalize.py       # Hermes message rows/stream → SPEC §4 normalized schema (incl. task_report derivation)
├── fixtures/              # captured Hermes SSE transcripts (Phase 0)
├── mockserver/            # see Phase 1.5
└── tests/
```

`botter.db` schema: `bots(id TEXT PK, slug TEXT UNIQUE, display_name, title, description, avatar_color, avatar_glyph, approval_boundary, default_session_id, archived INTEGER DEFAULT 0, created_at, updated_at)` · `read_state(bot_id, session_id, last_read_message_id, PRIMARY KEY(bot_id, session_id))` · `pending_approvals(run_id TEXT PK, bot_id, session_id, summary, requested_at, resolved_at, decision)` · `schema_version`.

Key behaviors:
- **Bot create**: validate slug → `hermes profile create <slug> --clone --description …` (subprocess, generous timeout) → write templated `SOUL.md` (template in `PLAN_HERMES_SETUP.md` Step 3) → apply per-profile config deltas (Slack off, prune inherited mounts, resolve per-profile egress — see Phase 0 outcome d) → no gateway refresh needed (Phase 0a: live discovery) → create default session via `/p/<slug>/api/sessions` **with an explicit `model`** (Phase 0b) → store row. Roll back with the full purge sequence on partial failure. **Bot purge** uses the multi-step sequence from `PLAN_HERMES_SETUP.md` Step 3 (container stop → delete → ACL sweep → gateway restart → skeleton sweep); do not reuse a purged slug before the restart completes.
- **Chat stream translation**: upstream Hermes SSE → downstream events `delta | tool_event | approval_required | message_complete | error` (SPEC §4). Heartbeat comments every 15 s. Client disconnect must NOT cancel the Hermes run (bot keeps working; result lands in history / feed).
- **Feed**: `GET /v1/bots` returns roster + latest message preview + unread count per bot. Primary source: Hermes API per profile; fallback (and for previews/search): read-only `state.db`. Cache briefly; push `feed_updated` on `/v1/events` when a watcher notices new messages (poll mtimes of profile `state.db`-wal files ~2 s, cheap).
- **Approvals**: every run botterd initiates gets a watcher on `/v1/runs/{id}/events`; `approval_required` → insert pending row + `approval_pending` event + macOS notification hook point. `POST /v1/approvals/{run_id}` forwards the decision and resolves.
- **Auth**: constant-time bearer check against `~/.botter/token` (generate on first boot, 0600). `/v1/health` unauthenticated.

## Phase 1.5 — Mock server for the frontend

`mockserver/` — a tiny FastAPI app (same contract, same port) serving canned data: 6 bots matching the Grok Bot reference roster, scripted SSE chat replies (with `task_report` and `approval_required` examples from fixtures), fake routines/approvals. `uv run mock` starts it. This unblocks the SwiftUI workstream before real Hermes integration is proven — **keep it contract-identical** (share Pydantic models between botterd and mockserver).

## Phase 3 (with frontend Phase 3) — Routines & approvals hardening

- Routine CRUD mapped to `/p/<slug>/api/jobs`; execution history via `/api/jobs` detail + `~/.hermes/profiles/<slug>/cron/executions.db` read-only if the API lacks history.
- Per Phase 0(c) findings, either wire cron-run approvals or document the SOUL.md-boundary fallback in `NOTES.md`.

## Deployment & ops

- `scripts/install_botterd.sh`: writes `~/Library/LaunchAgents/com.treysweeney.botterd.plist` (`uv run --project /Users/treysweeney/projects/botter/backend uvicorn botterd.main:app --host 127.0.0.1 --port 8674`, KeepAlive, log to `~/.botter/botterd.log`), `launchctl bootstrap`.
- Structured logs (one line JSON); `GET /v1/health` reports botterd version, Hermes `/health` reachability, gateway PID freshness.

## Definition of done / verification (run all of it, show output)

1. `uv run pytest` green — unit tests for registry (subprocess mocked), normalize (against captured fixtures), auth, and an httpx-mock suite for the proxy layer.
2. Live end-to-end script `scripts/e2e.sh`: create bot `test-bot` → chat "list your tools" and stream to completion → create a routine (`*/5 * * * *`, then pause) → trigger an approval (chat request that requires one per Phase 0 findings) → approve via API → archive bot → purge bot. Each step curls botterd only, asserts on JSON, and must leave `~/.hermes` clean (no `test-bot` residue).
3. `verify_hermes.sh` still passes and Slack on `main` still responds afterwards.

## Rules

- Never patch files under `~/.hermes/hermes-agent/` (the Hermes source). Config/profile changes only, through the documented mechanisms.
- Never write to any Hermes `state.db`.
- `main` profile is not a bot: exclude it from every roster, watcher, and lifecycle path.
- Secrets (`API_SERVER_KEY`, botterd token) never in logs, never in the repo.
