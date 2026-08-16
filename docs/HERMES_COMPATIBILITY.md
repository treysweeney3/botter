# Hermes compatibility

Botter drives the Hermes agent through surfaces that are **not a published,
versioned API**. They are configuration files, on-disk layouts, and an HTTP
server that Hermes ships but does not document as a contract. Upstream is free
to change any of them in a patch release without it being a bug on their side.

This file records what Botter has been verified against, and exactly which
behaviors it depends on, so that a break can be diagnosed in minutes instead of
an afternoon.

## Verified against

| Field | Value |
|---|---|
| Hermes Agent version | **v0.20.1 (2026.8.13)** |
| Commit | `d6a5cb9725df4b3d14a61aa7a2f717acb30c901b` |
| Commit date | 2026-08-15 |
| Python | 3.11.15 |
| Platform | macOS 15 (Apple Silicon) |

Check your own version:

```bash
hermes --version
git -C ~/.hermes/hermes-agent rev-parse --short HEAD
```

## Policy

Botter **warns but does not refuse** on an unrecognized Hermes version.

The rationale: pinning hard would make Botter unusable the day after any Hermes
release, and most Hermes changes do not touch the surfaces below. But silently
half-working against a changed Hermes is worse than either extreme — a failed
config write can leave a user's agent in a state they did not ask for.

So the contract is:

- **Read-only operations** proceed on any version.
- **Mutating operations** (credential writes, config edits, profile creation and
  deletion, gateway restarts) proceed, but the app surfaces a version banner
  when the running Hermes does not match the verified version above.
- `scripts/setup_hermes.sh` always backs up `config.yaml` and `proxy.yaml`
  before writing, on every version.

If you run a different version successfully, or find a break, please update the
table below and open a PR. That is one of the most useful contributions
available to this project.

## Known-compatible versions

| Hermes version | Status | Notes |
|---|---|---|
| v0.20.1 (`d6a5cb972`) | Verified | Reference version. All 102 backend tests plus a live `scripts/e2e.sh` pass. |

## Surfaces Botter depends on

If Botter breaks after a `hermes update`, one of these changed. They are listed
roughly in order of how likely they are to move.

### 1. The `hermes serve` management API

- **What:** `botterd` supervises a `hermes serve` child process and calls
  `GET/PUT/DELETE /api/env` for the credential catalog and every credential
  write.
- **Why:** this runs Hermes' own unified credential lifecycle, which reconciles
  stale `config.yaml` mirrors and clears env-seeded credential-pool entries. A
  raw `.env` edit does neither.
- **Breaks look like:** every credential shows as not-configured, or writes
  return 4xx/5xx.
- **Code:** `backend/botterd/hermes_serve.py`, `credentials.py`

### 2. The gateway HTTP API (`platforms.api_server`)

- **What:** `127.0.0.1:8642`, bearer `API_SERVER_KEY`, with
  `gateway.multiplex_profiles` enabled so one process serves every profile
  under `/p/<slug>/…`.
- **Breaks look like:** health check fails, or all bots appear offline.
- **Code:** `backend/botterd/hermes.py`

### 3. The chat SSE dialect

- **What:** Botter translates Hermes' SSE event stream into its own normalized
  contract (`docs/SPEC.md` §4). Hermes emits an assistant row per interim
  narration step; `botterd` folds those into one message per turn, pairing tool
  results to calls by `tool_call_id`.
- **Breaks look like:** duplicated bubbles, missing trace steps, or tool cards
  that never resolve.
- **Code:** `backend/botterd/normalize.py`, `feed.py`
- **Fixtures:** `backend/fixtures/*.sse` are captured real streams. If the
  dialect changes, recapture them with `scripts/phase0_investigate.sh`.

### 4. Profile layout and lifecycle

- **What:** one bot is one profile at `~/.hermes/profiles/<slug>` with its own
  `config.yaml`, `.env`, `SOUL.md`, memory, and `state.db`. Created by shelling
  `hermes profile create <slug> --clone`, described with `hermes profile
  describe`, removed with `hermes profile delete --yes` plus a container sweep.
- **Breaks look like:** bot creation fails, or deletion leaves orphans.
- **Code:** `backend/botterd/registry.py`

### 5. `mcp_servers` in profile `config.yaml`

- **What:** free-form `url` and `headers` map, with `${NAME}` expanded from that
  profile's `.env`. OAuth grants live at `~/.hermes/mcp-tokens/<name>.json`
  alongside `.client.json` and `.meta.json` — all three must travel together.
- **Note:** the gateway does **not** watch `config.yaml`. Its watcher lives in
  the interactive CLI, and `gateway/run.py` calls `discover_mcp_tools()` once at
  startup, so every MCP mutation requires a gateway restart.
- **Code:** `backend/botterd/mcp.py`, `global_auth.py`

### 6. The cron ledger

- **What:** routines are Hermes cron jobs; execution history is read from the
  profile's cron ledger.
- **Known limitation:** Hermes stores routine output without an execution ID, so
  a successful execution carries an empty summary. Botter does not invent a
  join.
- **Code:** `backend/botterd/routines.py`

### 7. The iron-proxy allowlist serializer

- **What:** `scripts/setup_hermes.sh` repairs `proxy.yaml`'s allowlist. In the
  verified version, `proxy.extra_allowed_hosts` is serialized one character per
  list item — the setup script discards those single-character fragments and
  re-adds the real host strings.
- **If upstream fixes this bug**, the repair becomes a no-op by design (it only
  drops length-1 entries), but the workaround should then be removed.
- **Code:** `scripts/setup_hermes.sh`

## Where runtime findings go

Verified observations about how Hermes actually behaves — as opposed to how it
appears to behave — belong in [`../backend/NOTES.md`](../backend/NOTES.md).
That file is the accumulated result of reading Hermes' source and capturing its
real output, and it is the highest-value document here for a new contributor.
