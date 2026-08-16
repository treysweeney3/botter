# Botter — Product & System Specification

Status: v1 draft · 2026-08-13
Decisions locked: bots = **Hermes profiles** · iOS via **cloud relay** (later phase) · v1 scope = **roster + chat + bot CRUD + routines + approvals** (group threads and live computer view are v2).

---

## 1. What we're building

A personal, local-first clone of xAI's **Grok Bot** for the user's existing **Hermes agent** (Nous Research `hermes-agent`, installed at `~/.hermes`). The product idea, verbatim from Grok Bot: instead of one catch-all assistant, you run a roster of named **Bots**, each with a job ("Sales Outbound", "Inbox Manager", "Expense Manager"), its own memory and working style, scheduled **routines**, and an **approval boundary** — all managed from a chat-first native app.

Grok Bot feature model (for reference — what we're cloning, and what maps where):

| Grok Bot concept | Botter equivalent | Phase |
|---|---|---|
| Bot (name, title, description, avatar) | Hermes **profile** + Botter metadata (avatar color/glyph) | v1 |
| Per-bot memory | Profile-scoped `memories/MEMORY.md` + `USER.md` | v1 (view), v1.5 (edit) |
| Chat with a bot, streaming | Hermes api_server session chat (SSE) | v1 |
| Routines (scheduled workflows) | Hermes **cron jobs** per profile (`/api/jobs`) | v1 |
| Approval boundaries / "You're in control" | Hermes run approvals (`/v1/runs/{id}/approval`) + SOUL.md boundary text | v1 |
| Group chats / bots passing work | Cross-profile threads (Hermes `a2a` toolset / delegation) | **v2** |
| Shared computer (watch the bot work) | Read-only sandbox/terminal/browser viewer | **v2** |
| Learn a routine by demonstration | Out of scope (Hermes has no screen-watching trainer) — routines are created conversationally or in the editor | — |
| Mobile app | iOS via SwiftUI multiplatform + cloud relay | v3 |

## 2. Architecture

Three components. Hermes core is **never modified** — its own developer guide (`~/.hermes/hermes-agent/AGENTS.md`) mandates that capability lives at the edges, and this install is a git checkout updated by `hermes update`; patches would create merge pain.

```
Botter.app (SwiftUI, macOS→iOS)
   │   HTTPS/HTTP + SSE, bearer token          ← only surface the app knows
   ▼
botterd (Python 3.11 / FastAPI, launchd, 127.0.0.1:8674)
   │   • bot registry (bot ↔ profile, avatar, title, archived)
   │   • profile lifecycle via `hermes profile …` CLI + SOUL.md templating
   │   • sidebar feed aggregation + unread state across profiles
   │   • transparent proxy to Hermes api_server (per-profile prefix)
   │   • approvals + routines fan-in, single SSE event firehose
   ▼
Hermes gateway (existing launchd service, ai.hermes.gateway)
   • api_server platform enabled → OpenAI-compatible HTTP on 127.0.0.1:8642
   • gateway.multiplex_profiles → one process serves all profiles at /p/<profile>/…
   • one profile per bot under ~/.hermes/profiles/<slug>
```

Why a companion service instead of the app talking to Hermes directly:

1. Profile lifecycle (create/clone/describe) is **CLI-only** in Hermes; something on the Mac must shell out.
2. The sidebar needs an aggregate ("latest message + unread per bot across N profiles") that no single Hermes endpoint provides.
3. Bot presentation metadata (avatar color/glyph, display name vs profile slug, archived state) has no home in Hermes.
4. iOS/relay later: the app keeps one origin + one auth scheme; only botterd is ever exposed through the tunnel — Hermes stays loopback-only.

### Trust & security model

- Hermes api_server binds `127.0.0.1:8642`, bearer `API_SERVER_KEY` (generated at setup). Never exposed beyond loopback.
- botterd binds `127.0.0.1:8674`, bearer `BOTTERD_TOKEN`. The macOS app reads the token from `~/.botter/token` (v1) — same user account, no network exposure.
- v3 relay: Cloudflare Tunnel (`cloudflared` as launchd service) + Cloudflare Access service token in the iOS app. botterd's bearer auth is unchanged; the tunnel adds transport + edge auth. No inbound ports ever open on the Mac.

## 3. Data model

`botterd` owns a small SQLite DB at `~/.botter/botter.db`; everything conversational stays in Hermes (per-profile `state.db`).

**Bot** — `id` (uuid) · `slug` (Hermes profile name, `[a-z0-9-]`, immutable) · `display_name` · `title` (job title, e.g. "Sales Outbound") · `description` (role in operational terms; also passed to `hermes profile create --description`, which Hermes kanban uses for work routing) · `avatar_color` (hex, from an 8-color palette) · `avatar_glyph` (name of a bundled otter avatar — **never an emoji**) · `approval_boundary` (free text, injected into SOUL.md) · `default_session_id` (the bot's "main thread" Hermes session) · `archived` (hide-not-delete, mirrors Grok Bot) · timestamps.

**Session / Message** — owned by Hermes (`sessions`, `messages` tables per profile `state.db`; FTS5 available for search). Botter always addresses them through the api_server, keyed by `(bot → profile, session_id)`.

**Routine** — owned by Hermes cron (`/api/jobs` per profile): id, name, schedule (cron expr), prompt/payload, paused. botterd adds no storage — it proxies and annotates with `bot_id`.

**Approval** — a pending Hermes run approval: `(bot_id, run_id, summary, requested_at)`. botterd tracks runs it started and watches their SSE event streams; unresolved approvals surface in the app and (v1.5) as macOS notifications.

**Unread state** — per `(bot_id, session_id)` last-read message id, stored in botter.db (server-side so iOS inherits it later).

## 4. botterd HTTP API (contract between the two workstreams)

Both plans build against this. Base URL `http://127.0.0.1:8674`, all routes under `/v1`, `Authorization: Bearer <token>`, JSON bodies, snake_case. Errors: `{ "error": { "code", "message" } }` with proper status codes.

| Method & path | Purpose |
|---|---|
| `GET /v1/health` | botterd + Hermes gateway/api_server reachability, versions |
| `GET /v1/bots` | roster incl. per-bot latest-message preview, timestamp, unread count (this is the sidebar payload) |
| `POST /v1/bots` | create bot → creates Hermes profile, writes SOUL.md, registers metadata, creates default session |
| `GET /v1/bots/{id}` | full bot detail incl. memory summary + routine count |
| `PATCH /v1/bots/{id}` | edit metadata; `display_name/title/description/avatar/approval_boundary/archived` (SOUL.md re-rendered on persona-affecting changes) |
| `DELETE /v1/bots/{id}` | archive (default) or `?purge=true` to delete the profile after confirmation |
| `GET /v1/bots/{id}/sessions` · `POST …/sessions` | list / start conversations with a bot |
| `GET /v1/sessions/{sid}/messages?before=&limit=` | paged history (normalized message schema below) |
| `POST /v1/sessions/{sid}/chat` | send user message → **SSE stream**: `delta` (text tokens), `tool_event` (tool name + status + human summary), `approval_required` (run_id, summary), `message_complete`, `error` |
| `POST /v1/sessions/{sid}/read` | set last-read marker |
| `POST /v1/sessions/{sid}/stop` | interrupt the active run |
| `GET /v1/bots/{id}/routines` · `POST …/routines` | list / create cron jobs for the bot |
| `PATCH /v1/routines/{rid}` · `DELETE` | edit schedule/prompt/name; delete |
| `POST /v1/routines/{rid}/run` · `/pause` · `/resume` | manual fire; toggle |
| `GET /v1/routines/{rid}/executions?limit=` | recent execution history + outcome |
| `GET /v1/approvals` | all pending approvals across bots |
| `POST /v1/approvals/{run_id}` | `{ "decision": "once" \| "session" \| "always" \| "deny" }` (botterd translates to Hermes' upstream payload, which uses the key **`choice`**, verified in api_server.py 2026-08-13) |
| `GET /v1/bots/{id}/memory` | render of the profile's `MEMORY.md` + `USER.md` |
| `GET /v1/events` | global **SSE firehose**: `bot_updated`, `feed_updated` (sidebar refresh hints), `approval_pending`, `approval_resolved`, `routine_fired`, `integration_updated`, `mcp_updated` |
| `GET /v1/integrations` | **v3 — the single credential surface.** Replaces `/v1/connections`, which is gone. Every non-channel Hermes env credential/config entry (~140 tools/providers/skills/settings + custom keys) from the dashboard `/api/env` catalog, **plus** the curated apps that used to be connections, plus the two rows that are not env values: `SLACK` and `GOOGLE_WORKSPACE`. `{"integrations": [{"key", "label", "description", "url", "category", "kind": "integration"\|"config", "is_set", "redacted_value", "is_password", "advanced", "custom", "sync_status": "synced"\|"out_of_sync"\|null, "sync_detail", "status": "connected"\|"not_connected"\|"error", "detail", "group", "group_label", "required", "restart_after_write", "auth": "value"\|"oauth"\|"external"}]}`. Curated rows come first in a fixed order, then configured keys, then the browseable catalog. `group` makes several keys render as one card (`VERCEL_TOKEN` + `VERCEL_TEAM_ID`); `required: false` marks the optional field. `status` is `error` when any Botter-registered profile is out of sync. `auth`: `value` = pasted secret, `oauth` = Google (see `/v1/auth/google`), `external` = Hermes-owned and read-only. Channel-managed vars and infrastructure keys (`API_SERVER_KEY`, `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `HERMES_DASHBOARD_SESSION_TOKEN`) are excluded |
| `PUT /v1/integrations/{key}` | v3: body `{"value": "…"}` — writes through Hermes' credential lifecycle for main **and every registered bot profile** (also reconciles config.yaml mirrors on rotation). This is now the only credential write path; the curated apps no longer take a raw `.env` edit. Custom UPPER_SNAKE_CASE keys allowed; Hermes' denylist (PATH, LD_PRELOAD, …) → 422; `SLACK`, `GOOGLE_WORKSPACE`, channel-managed and infrastructure keys → `403 integration_not_managed` → `{"integration": {…}}`. Restarts the gateway only when the row carries `restart_after_write` (Exa alone, whose key is process-cached) |
| `DELETE /v1/integrations/{key}` | v3: full lifecycle removal (env + credential-pool + config mirrors) across every profile → `{"integration": {…}}`; 404 when not set. A curated key keeps its card; a custom key drops out of the catalog |
| `POST /v1/auth/google` | Google OAuth for every Botter-managed profile. Empty body starts or repairs the fleet → `{"authorization": {"url", "instructions", "code_entry", "needs_client_secret"}}`; `code_entry: true` means finish by re-POSTing `{"code": "<pasted redirect URL>"}` → `{"integration": …}`; `needs_client_secret: true` means first POST `{"client_secret_json": "<Google OAuth Desktop client JSON>"}`. Refreshes persistent bot sandboxes before returning success and may return `409 connection_busy` while a bot chat is active |
| `DELETE /v1/auth/google` | best-effort server-side revoke, removes every managed profile token, refreshes persistent sandboxes → `{"integration": {…}}` |
| `GET /v1/mcp` | v3: MCP servers in `mcp_servers` of every Botter-managed `config.yaml`. `{"servers": [{"name", "label", "description", "url", "command", "auth": "none"\|"oauth"\|"header", "enabled", "preset", "docs_url", "authorized", "status", "detail", "sync_status", "sync_detail"}], "presets": […]}`. One MCP server reaches a whole tool catalog, so it needs none of the per-app env plumbing above. `authorized` reflects Hermes' per-profile grant at `HERMES_HOME/mcp-tokens/<name>.json`. `presets` offers known servers not yet installed (currently Composio Connect, an MCP **OAuth** resource — it holds no API key) |
| `PUT /v1/mcp/{name}` | v3: body `{"url": "https://…", "auth": "none"\|"oauth"\|"header", "headers": {…}}` — writes the entry to main and every registered profile, then **restarts the gateway**. Unlike `.env`, botterd edits `config.yaml` directly and comment-preservingly: Hermes' own config endpoints run the document through `yaml.dump` and would strip the user's comments to set one nested key. Header values may reference `${ENV_KEY}`, which Hermes expands per profile — botterd stores the reference verbatim. Non-https URL, bad header name, or header auth with no headers → 422 → `{"server": {…}, "restarted": bool}` |
| `DELETE /v1/mcp/{name}` | v3: removes the entry from every profile, leaves no empty `mcp_servers` mapping behind, restarts the gateway → `{"server": {…}, "restarted": bool}`; 404 when unknown |
| `GET /v1/search?q=&bot_id=` | cross-session message search (Hermes FTS5) — v1.5 |

**REST envelopes (pinned 2026-08-13, as implemented — both workstreams conform).** Every response wraps its resource in a named envelope; requests use the field names shown:

- `GET /v1/bots` → `{"bots": [ <bot + "latest_message_preview", "latest_message_at", "unread_count"> ]}` (roster fields are flattened onto each bot object)
- `GET/POST/PATCH /v1/bots/{id}` → `{"bot": {…}}` (detail adds `memory_summary`, `routine_count`) · `DELETE` → `{"id", "archived", "purged"}`
- Sessions: `{"sessions": […]}` / `{"session": {…}}`; session objects carry `model`, `message_count`, timestamps
- `GET …/messages` → `{"messages": […], "has_more": bool}`; `before` is an exclusive message-id cursor
- Chat send body: `{"message": "…"}` for text, or `{"message": [{"type":"text","text":"…"}, {"type":"image_url","image_url":{"url":"data:image/…;base64,…","detail":"auto"}}]}` for one inline PNG/JPEG/GIF/WebP image up to 5 MB · read marker body: `{"message_id": "…"}` · stop → `{"session_id", "stopped"}`
- Routines: `{"routines": […]}` / `{"routine": {…}}`; routine objects carry `state`; `POST …/run` → `{"routine": {…}, "state": "queued"}` (cron fires asynchronously ~1.5–3 min later) · executions → `{"executions": […]}`
- Approvals: `{"approvals": […]}` (items include `session_id`); decision POST → `{"run_id", "decision", "resolved"}`
- Memory: `{"bot_id", "memory", "user"}` (two markdown documents)
- Bot create requires `approval_boundary` (send `""` if empty); `avatar_color` must be one of the 8 palette values; `task_items[].state` ∈ `done | failed | running | note`.

**Normalized message schema** (botterd translates Hermes' `messages` rows / stream into this; the app never parses Hermes internals):

```json
{
  "id": "…", "session_id": "…", "bot_id": "…",
  "role": "user" | "assistant" | "system",
  "kind": "text" | "task_report" | "routine_created" | "approval_request" | "attachment",
  "text": "markdown",
  "attachments": [ { "type": "image", "url": "data:image/…;base64,…", "media_type": "image/png", "filename": "optional.png" } ],
  "task_items": [ { "label": "Salesforce → list pulled", "detail": "52 accounts", "state": "done" },
                   { "label": "Let me pull the account list first.", "detail": "", "state": "note" } ],
  "routine": { "id": "…", "name": "Overnight outbound" },
  "created_at": "ISO-8601"
}
```

`task_report` powers the trace above a reply; botterd derives items from Hermes tool-call records where feasible, else the message stays `text`.

**One turn is one assistant message (pinned 2026-08-14).** Hermes persists an assistant row for every step of a turn: each interim row carries `tool_calls` plus a sentence of narration, and only the closing row holds the answer. botterd never makes a bubble from an interim row. The narration becomes a `note` item and the tool calls become `done`/`failed` items on the closing message, in the order they happened. Tool results pair with their call by `tool_call_id`; the result row also names the tool that actually ran, which a `tool_call` wrapper does not. A call with no result row keeps `running`. A turn that never closes (a stopped run) still yields one `task_report` with empty `text` so the work stays visible.

**SSE payload schemas (pinned 2026-08-13 — both workstreams build to exactly these).** Chat stream (`POST /v1/sessions/{sid}/chat`), SSE with named events (`event:` + `data:` lines):

```
event: delta              data: {"text": "…"}                                  ← append to live bubble
event: tool_event         data: {"name": "terminal", "status": "started"|"ok"|"error", "summary": "Runs `echo hello`"}
event: approval_required  data: {"run_id": "…", "summary": "…"}
event: message_complete   data: {"message": { …normalized message schema above… }}
event: error              data: {"code": "…", "message": "…"}
```

`message_complete` is terminal for the exchange (the full persisted message replaces optimistic streamed state). Heartbeats are SSE comment lines (`: …`) and must be ignored. Events firehose (`GET /v1/events`), same framing:

```
event: bot_updated        data: {"bot_id": "…"}
event: feed_updated       data: {"bot_id": "…" | null}          ← null = refresh whole roster
event: approval_pending   data: {"approval": {"run_id": "…", "bot_id": "…", "summary": "…", "requested_at": "ISO-8601"}}
event: approval_resolved  data: {"run_id": "…", "decision": "once"|"session"|"always"|"deny"}
event: routine_fired      data: {"bot_id": "…", "routine_id": "…", "name": "…"}
event: integration_updated data: {"key": "…", "is_set": true|false}
event: mcp_updated        data: {"name": "…", "status": "connected"|"not_connected"|"error"}
```

**v3 contract change (2026-08-14).** `/v1/connections` and `/v1/channels` are both deleted.
Channels configured messaging platforms on the **main** profile, which Botter never treats as
a bot; Hermes' own dashboard owns that (`hermes serve` → `/api/messaging/platforms`). See
`docs/removed/channels-2026-08-14/`. Also: Its 8 curated
apps are ordinary `/v1/integrations` rows carrying `group`, `required`,
`restart_after_write`, and `auth`; Google moved to `/v1/auth/google`; Slack is a
read-only row (`auth: "external"`). `connection_updated` is gone — listen for
`integration_updated`. Two consequences worth knowing: credential writes now
depend on the botterd-supervised `hermes serve` child being up (previously the
curated 8 worked without it), and every credential now takes Hermes' credential
lifecycle rather than a raw `.env` edit, which also reconciles stale config.yaml
mirrors and clears credential-pool entries.

## 5. UX specification (from the Grok Bot reference screenshots)

**Layout** — `NavigationSplitView`, fixed dark theme (the reference product is dark-only; match it).

**Sidebar (~285 pt)**: app-level `+` (new bot) top-right; pill search field; bot rows — 36 pt circular avatar (bot color, otter glyph), bold name, right-aligned relative timestamp, one-line secondary preview of the latest message (lowercase-casual, truncated). Selected row: subtle elevated background. Bottom: user identity row (initials avatar + name). Archived bots hidden behind a disclosure.

**Chat view**: top bar with bot avatar + name (click → bot editor sheet) and a trailing monitor icon (v2 computer view — hidden or disabled in v1). Message list:
- Assistant messages: dark elevated bubbles (left), markdown.
- Task report cards: bordered card, rows of `✓ label → detail` in monospaced-adjacent alignment.
- User messages: **white bubbles, right-aligned, black text**.
- Approval flow: an `approval_request` message renders Approve / Always allow / Deny inline actions; a resolved approval collapses to a small 👍-style acknowledgment badge rendered as a vector asset, not an emoji character.
- System chips, centered and small: "Created routine ⏱ Overnight outbound", "Updated memory".
- (v2) attribution rows "Messages from ⬤ X and ⬤ Y" above cross-bot content.

**Composer**: pill field, leading `+` opens a native image picker (one PNG/JPEG/GIF/WebP up to 5 MB, with preview/removal before send), placeholder "Message {bot name}", trailing mic (dictation via system input; no custom STT in v1). Enter sends; Shift+Enter newline. While streaming: stop button replaces mic. General document/PDF uploads remain deferred because Hermes' session HTTP API supports inline images but rejects file content parts.

**Bot creation/editing** (sheet): name, job title, description ("describe the role in operational terms" helper text), avatar color + glyph picker, approval boundary text area, model override (optional, from `GET /v1/models` passthrough — v1.5). Creation shows suggested-role chips like the reference ("Sales Outbound", "Talent Scout", "Expense Manager", "Chief of Staff", …).

**Routines** (per-bot panel, reachable from the chat top bar): list with name, human-readable schedule, last run status dot, paused toggle; editor with cron helper presets (hourly/daily/weekly/custom); "Run now". Routine executions append into the bot's main thread as normal messages (Hermes cron already delivers into sessions).

**Approvals**: badge on the app icon + an approvals section in the sidebar header when non-empty; approving from either the inline bubble or the list resolves everywhere (SSE-driven).

**Design language** (tokens; refine visually during build): backgrounds `#0D0D0D` window / `#161618` sidebar / `#1E1E20` cards-bubbles; text `#ECECEC` primary / `#8A8A8E` secondary; user bubble `#FFFFFF` on black text; hairlines `#2A2A2C`; bot palette 8 colors ≈ teal `#2EC7A6`, orange `#E8833A`, purple `#8B5CF6`, blue `#3B82F6`, red `#EF4444`, green `#22C55E`, yellow `#EAB308`, pink `#EC4899`; radii — bubbles 16, cards 12, composer/search full-pill; type — system SF, 13 pt body sidebar / 14 pt chat, semibold names. **No emoji anywhere in the UI**; avatars and badges are colored circles + bundled otter artwork. **Glyph vocabulary (re-pinned 2026-08-14)** — `avatar_glyph` is one of the app's ten bundled ASCII-mosaic otters: `float swim dive stand sprawl peek groom shell wave raft` (unknown names render as `float`). The pre-2026-08-14 vector vocabulary (`bolt orbit prism wave spark arc stack target branch grid`) is still accepted on the wire and maps onto the otter set in `Glyph.resolve`, so existing bots keep a stable avatar.

## 6. Phasing

| Phase | Deliverable | Owner |
|---|---|---|
| 0 | Hermes setup: enable api_server, multiplex profiles, proxy-allowlist fix, verification script (`docs/PLAN_HERMES_SETUP.md`) | manual + scripts |
| 1 | botterd core: registry, bot CRUD → profile lifecycle, chat proxy w/ SSE, feed aggregation, events firehose | GPT-5.6-Sol |
| 2 | macOS app v1: sidebar, chat with streaming + task cards, bot create/edit | Fable 5 |
| 3 | Routines + approvals end-to-end (both sides) | both |
| 4 | Polish: memory viewer, search, unread sync, notifications, attachments | both |
| 5 (v2) | Group threads, computer view; then iOS + Cloudflare relay (v3) | both |

Phases 1 and 2 overlap: the frontend starts against a **mock botterd** (tiny fixture server included in the backend plan) so neither blocks the other.

## 7. Risks & open questions — RESOLVED 2026-08-13 (Phase 0; evidence in `backend/fixtures/`, details in `backend/NOTES.md`)

1. **New-profile visibility** — ✅ ANSWERED: the running multiplexed gateway serves a freshly created profile immediately (HTTP 200 on first probe, no restart). **However, profile DELETION requires a restart + sweep**: the gateway resurrects served-profile dirs and retains deleted profiles' in-memory session/title state; `hermes profile delete` also crashes on sandbox-created ACL dirs. Purge sequence documented in `PLAN_HERMES_SETUP.md` Step 3.
2. **api_server coverage under `/p/<profile>/`** — ✅ ANSWERED: sessions, chat/stream SSE, jobs, and `/v1/runs` all work under the prefix. Two caveats: per-profile routes auth against the *profile's own* `.env` key, and `POST /api/sessions` defaults the model to the literal `hermes-agent` (upstream 400) — always pass an explicit `model`. Chat SSE (`event:` + `data:` framing; events `run.started`/`message.started`/`assistant.delta`/`tool.progress`/`tool.started`/`tool.completed`/`assistant.completed`/`run.completed`/`done`) and run-events SSE (data-only framing, event name inside JSON) use **different dialects** — captured in `backend/fixtures/*.sse`.
3. **Approvals for cron-initiated work** — ✅ ANSWERED: **cron executions do NOT create approvable runs** (job fires asynchronously ~1.5–3 min after `run`, completes with no runs-API record, no approval raised). The fallback stands as design: per-bot approval boundaries via SOUL.md + `tool_loop_guardrails`; app-side approvals guaranteed only for app-initiated chats in v1.
4. **Task-report fidelity** — unchanged (heuristic mapping v1). Good news: `tool.started` events carry `tool_name`, `preview`, and `args`, which is enough for the conservative ✓-card mapping.
5. **Docker terminal backend under N profiles** — unchanged guidance (≤6 active bots initially). NEW related risk: cloned profiles inherit `proxy.enabled: true` without per-profile iron-proxy config, which **blocks their terminal entirely**; bot-create must provision or resolve egress per profile (backend Phase 1).
