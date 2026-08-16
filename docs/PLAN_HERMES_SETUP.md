# Hermes Agent — Config & Setup Plan for Botter

Goal: make the existing Hermes install serve Botter **without touching Hermes core code**. Everything here is config, environment, and CLI-driven profile management. All paths are absolute for this machine.

Hermes facts (verified 2026-08-13):
- `HERMES_HOME=/Users/treysweeney/.hermes`; source checkout at `~/.hermes/hermes-agent` (git, branch `main`); launcher `~/.local/bin/hermes`.
- Gateway runs as launchd service `ai.hermes.gateway` (`~/Library/LaunchAgents/ai.hermes.gateway.plist`), `KeepAlive true`, logs at `~/.hermes/logs/gateway*.log`. Slack platform connected.
- Config: `~/.hermes/config.yaml` (`_config_version: 34`); secrets in `~/.hermes/.env`; persona in `~/.hermes/SOUL.md`; sessions in `~/.hermes/state.db` (SQLite/WAL).
- The OpenAI-compatible HTTP api_server (`gateway/platforms/api_server.py`, default port **8642**) exists but is **not enabled** — no `API_SERVER_KEY` set.
- Profiles are documented in `~/.hermes/hermes-agent/AGENTS.md` §"Profiles: Multi-Instance Support" and `website/docs/user-guide/profiles.md`; **`~/.hermes/profiles/` does not exist yet** (only default `main`).
- Exhaustive option reference: `~/.hermes/hermes-agent/cli-config.yaml.example` (92 KB). **Consult it for exact key names before every config edit below** — key spellings here are from the api_server module and docs, and must be verified against the example file for config version 34.

---

## Step 0 — Backups & hygiene (before anything)

```bash
cp ~/.hermes/config.yaml ~/.hermes/config.yaml.bak.botter-$(date +%Y%m%d)
cp ~/.hermes/proxy/proxy.yaml ~/.hermes/proxy/proxy.yaml.bak.botter-$(date +%Y%m%d)
```

Note: there is a live interactive `hermes` CLI session and the gateway running. Config edits are picked up on gateway restart; restart deliberately (Step 5), not mid-task.

## Step 1 — Enable the HTTP api_server (loopback only)

1. Generate a key and add to `~/.hermes/.env`:
   ```bash
   echo "API_SERVER_KEY=$(openssl rand -hex 32)" >> ~/.hermes/.env
   ```
2. In `~/.hermes/config.yaml`, enable the platform. **Verified 2026-08-13 against `api_server.py` (lines 1377–1382): `host`/`port` live under `extra`, not directly under `api_server`:**
   ```yaml
   platforms:
     api_server:
       enabled: true
       extra:
         host: 127.0.0.1    # never 0.0.0.0 — relay comes later via botterd only
         port: 8642
   ```
3. The server auths with `Authorization: Bearer $API_SERVER_KEY`. **Per-profile routes (`/p/<slug>/…`) authenticate against the profile's own `.env` key** — a profile without `.env` is rejected outright ("no profile-scoped API_SERVER_KEY is configured"). `--clone` copies main's `.env`, so cloned bots share main's key value.

## Step 2 — Enable profile multiplexing

One gateway process serving every profile at `/p/<profile>/…` (instead of one launchd service per bot):

```yaml
gateway:
  multiplex_profiles: true
```

Read `~/.hermes/hermes-agent/website/docs/user-guide/multi-profile-gateways.md` first — it documents this mode, including whether new profiles are discovered live or need a gateway restart (open question #1 in SPEC.md).

Also add to `~/.hermes/config.yaml` (main profile): `gateway.write_sessions_json: false` is **optional** — leave the legacy `sessions/sessions.json` mirror as-is for now; botterd doesn't read it.

## Step 3 — Profile-per-bot conventions

Botter (via botterd) creates a profile per bot:

```bash
hermes profile create <slug> --clone --description "<one-line role>"
```

- `--clone` copies the current main config (model provider, terminal backend, proxy settings, keys context) so new bots work immediately; memories/sessions are NOT cloned (desired — fresh memory per bot).
- `<slug>`: `[a-z0-9-]`, e.g. `sales-outbound`. Immutable; display name lives in botterd.
- `--description` matters: Hermes kanban routes work to profiles by description.
- Each profile home: `~/.hermes/profiles/<slug>/` with its own `config.yaml`, `SOUL.md`, `memories/`, `state.db`, `cron/`.
- **Never** run two agent processes against one profile (documented hard warning — they corrupt each other's memory writes). The multiplexed gateway is the single writer per profile; the `hermes -p <slug>` CLI should not be used interactively on a bot profile while the gateway serves it.

**SOUL.md template** written by botterd on create/edit (per-profile persona = the bot's role):

```markdown
# {display_name} — {title}

You are {display_name}, {title} for Trey. {description}

## Working style
- Report finished work as a short summary followed by a checklist of steps taken
  in the form "✓ <system> → <action> · <result>".
- Keep long-term notes about this role in memory; cite live data for decisions.

## Approval boundary
{approval_boundary}
Never take actions beyond this boundary without asking for approval first.
```

**Per-profile config deltas** botterd applies after create (via `hermes -p <slug> config set …` or direct YAML edit while gateway is stopped):
- Disable platforms the bot shouldn't own: Slack stays **only** on `main` (bot profiles must not connect a second Slack socket). Verify cloned profiles don't inherit `platforms.slack.enabled: true`; if they do, unset it. (Observed 2026-08-13: the scratch clone did **not** open a second Slack socket.)
- Terminal backend: keep `docker`, but consider lowering `container_memory` (main is 5120 MB) if the roster grows; guidance: ≤6 active bots initially.
- **Prune inherited mounts**: `--clone` copies main's full tool mounts — the scratch clone's sandbox container mounted `~/Documents/Deep South Software` (company vault). Bots must not inherit mounts their role doesn't need.
- **Egress**: resolve the per-profile iron-proxy gap (see Step 4) or the bot's terminal is dead on arrival.

**Profile deletion (verified 2026-08-13 — `hermes profile delete` alone is NOT sufficient):** once a profile's docker sandbox has run, its VirtioFS-mounted cache dirs carry macOS ACLs (`deny delete`) that crash the CLI delete (upstream py3.11 `shutil.rmtree(onexc=)` bug), leaving a half-deleted profile. The running gateway also resurrects served-profile dirs (cron ticker/log handles) and keeps deleted profiles' session/title state in memory. Correct purge sequence: stop the profile's sandbox container (match docker mounts on `profiles/<slug>`) → `hermes profile delete <slug> --yes` → `chmod -R -N` + `rm -rf` any remainder → remove `~/.local/bin/<slug>` wrapper → `launchctl kickstart -k gui/$UID/ai.hermes.gateway` and wait for `/health` → sweep any resurrected skeleton dir.

## Step 4 — Fix the iron-proxy allowlist bug (pre-existing)

`~/.hermes/proxy/proxy.yaml` has a config-serialization bug: the JSON string from `proxy.extra_allowed_hosts` was splatted character-by-character into the tail of the `transforms[allowlist].domains` list. Effect: those extra hosts (googleapis, github, vercel, supabase, intuit) are probably NOT actually allowlisted for sandbox egress.

Fix: regenerate or hand-edit `proxy.yaml` so `domains:` contains the real hostnames from `extra_allowed_hosts` in `config.yaml`, then restart iron-proxy. **Verified 2026-08-13: iron-proxy is a Hermes-managed subprocess (no launchd entry of its own) — restart it with `hermes egress restart`** (note: `hermes egress status` prints its table to stderr). Worth doing regardless of Botter — bots doing GitHub/Vercel work will hit `CONNECT tunnel failed: 403` otherwise. Done and verified 2026-08-13 via `scripts/setup_hermes.sh` + `scripts/verify_hermes.sh`.

**Egress is per-profile**: cloned profiles inherit `proxy.enabled: true` but have no profile-scoped iron-proxy config/tokens, so their sandbox terminal is **completely blocked** ("proxy.enabled is true but iron-proxy is not configured"). botterd's bot-create must run per-profile egress provisioning or explicitly resolve this (backend Phase 1 decision; see `backend/NOTES.md`).

## Step 5 — Apply & verify

```bash
launchctl kickstart -k gui/$UID/ai.hermes.gateway    # restart gateway with new config
sleep 5
source ~/.hermes/.env
curl -s http://127.0.0.1:8642/health                                   # expect ok
curl -s -H "Authorization: Bearer $API_SERVER_KEY" \
     http://127.0.0.1:8642/v1/models | head -c 400                      # model list
curl -s -H "Authorization: Bearer $API_SERVER_KEY" \
     http://127.0.0.1:8642/v1/capabilities | head -c 400                # capability discovery
# after first profile exists:
curl -s -H "Authorization: Bearer $API_SERVER_KEY" \
     http://127.0.0.1:8642/p/<slug>/api/sessions                        # per-profile sessions
tail -50 ~/.hermes/logs/gateway.error.log                               # no tracebacks
```

Also confirm Slack still works (message the bot in the home channel) — the setup must not regress the existing main-profile Slack deployment.

## Step 6 — Ongoing operational rules

- `hermes update` remains safe: nothing above patches `~/.hermes/hermes-agent`. After updates, re-run the Step 5 verification (config `_config_version` migrations can move keys).
- botterd is the only writer of bot-profile `SOUL.md` and botterd-managed config keys; manual edits to `~/.hermes/config.yaml` (main) remain the user's.
- Rollback: restore the two `.bak.botter-*` files, delete `~/.hermes/profiles/<slug>` dirs, restart gateway.

## Automation note

Steps 1–2 and 4–5 should be codified as `scripts/setup_hermes.sh` + `scripts/verify_hermes.sh` in this repo (idempotent, check-before-write) — assigned to the backend workstream (Phase 0 of `PLAN_BACKEND.md`). Step 3 is runtime behavior inside botterd itself.
