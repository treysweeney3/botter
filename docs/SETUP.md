# Setting up Botter

Botter is a management app for a **Hermes agent running on your own Mac**.
It does not run in the cloud, it has no hosted component, and it does not ship
its own agent. You bring the agent and the API keys; Botter gives you a roster
of bots on top of them.

There is no installer or `.dmg` yet. You build from source. The whole process
is four commands once the prerequisites are in place.

---

## 1. Prerequisites

| Requirement | Why | Required? |
|---|---|---|
| macOS 15 (Sequoia) or later | AppKit and `SMAppService` APIs | Yes |
| Xcode 16+ with Swift 6 | building the app | Yes |
| [XcodeGen](https://github.com/yonaskolb/XcodeGen) | generates the Xcode project from `project.yml` | Yes |
| [uv](https://docs.astral.sh/uv/) | Python 3.11 environment for `botterd` | Yes |
| A **Hermes agent** at `~/.hermes` | the thing Botter manages | Yes |
| An **LLM provider API key** | Hermes needs a model to run | Yes |
| Docker | agent sandboxes and egress enforcement | Optional, recommended |

Install the build tools:

```bash
brew install xcodegen uv
```

### About Docker

Docker is **optional but recommended**. Hermes uses containers to sandbox
agent tool execution and to enforce egress rules through iron-proxy. Without
Docker:

- Botter still runs, and bots still work.
- Tools execute with less isolation from the rest of your machine.
- The Google Workspace sandbox-refresh path and the container sweep during
  profile deletion become no-ops.

Botter locates the Docker CLI automatically across Docker Desktop, Homebrew
(both architectures), Colima, and OrbStack. Override with `DOCKER_BIN` if
yours lives somewhere unusual.

---

## 2. Get a Hermes agent

**Botter manages an existing Hermes install — it will not install one for
you.** This is deliberate: Hermes is your agent, with your keys, your memory,
and your data. Botter attaches to it rather than owning it.

### If you already have Hermes

Confirm it works before continuing:

```bash
hermes --version
```

You are ready. Skip to step 3.

> **Heads up:** Botter attaches to your existing agent and **modifies it**.
> Step 3 edits `~/.hermes/config.yaml` and `~/.hermes/proxy/proxy.yaml`, and
> restarts your gateway. Both files are backed up first. Botter also writes
> credentials into your main profile — read
> [Blast radius](#5-what-botter-changes-on-your-machine) below before running
> it against an agent you depend on.

### If you do not have Hermes yet

Install it from Nous Research, then run its own setup wizard:

```bash
# 1. Install (installs to ~/.hermes)
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash

# 2. Reload your shell so the `hermes` command resolves
source ~/.zshrc          # or ~/.bashrc

# 3. Run Hermes' setup wizard — this is where your API key goes
hermes setup
```

`hermes setup` is where you **bring your own LLM API key**. Botter never asks
for it and never stores it; Hermes owns that credential. If you want to change
provider or model later, use `hermes model`.

Verify Hermes works on its own before adding Botter:

```bash
hermes chat
```

If that does not work, fix it first — Botter cannot make a broken agent work.
Hermes issues belong at
[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent).

> Botter is developed against a specific Hermes version. See
> [`HERMES_COMPATIBILITY.md`](HERMES_COMPATIBILITY.md) for the verified version
> and the exact behaviors Botter depends on. Other versions generally work;
> Botter warns rather than refusing.

---

## 3. Prepare Hermes for Botter

```bash
git clone https://github.com/treysweeney3/botter.git
cd botter

scripts/setup_hermes.sh
scripts/verify_hermes.sh
```

`setup_hermes.sh` makes three changes, after backing up both files it touches:

1. **Enables the HTTP API server** — sets `platforms.api_server.enabled: true`
   on `127.0.0.1:8642` and generates an `API_SERVER_KEY` if one is absent. This
   server already ships with Hermes; it is dormant by default.
2. **Enables profile multiplexing** — `gateway.multiplex_profiles: true`, so one
   gateway process serves every bot profile.
3. **Repairs the iron-proxy allowlist** — works around a serializer bug that
   writes `extra_allowed_hosts` one character per list entry.

It then restarts the gateway and waits for `/health`.

`verify_hermes.sh` runs 22 checks. **All of them must pass** before continuing.
If any fail, stop and fix them — `botterd` will not work correctly against a
half-configured Hermes.

**Hermes installed somewhere else?**

```bash
HERMES_HOME=/path/to/.hermes scripts/setup_hermes.sh
```

Every script and `botterd` itself honor `HERMES_HOME`, `HERMES_BIN`, and
`HERMES_PYTHON`.

---

## 4. Install and run Botter

```bash
# Install botterd as a launchd agent on 127.0.0.1:8674
scripts/install_botterd.sh

# Build and launch the app
scripts/run_app.sh
```

`install_botterd.sh` resolves your `uv` path and this repository's location
automatically, writes a launchd plist, and polls `/v1/health` for up to 30
seconds. It refuses to install if something else already holds port 8674.

`run_app.sh` regenerates the Xcode project when `project.yml` has changed,
builds, and relaunches.

> **Note:** quitting and reopening Botter from the Dock does *not* pick up
> source changes — it relaunches the binary already on disk. Re-run
> `scripts/run_app.sh` after any edit. Use `--clean` to wipe DerivedData first,
> or `--no-launch` to build without launching.

### Connecting your integrations

Once the app is running, open the **Hermes** sheet:

- **Credentials** — the full env-credential catalog (~130 keys), covering
  GitHub, Vercel, Supabase, OpenRouter, Exa, xAI, and the rest. Values are
  written through Hermes' own credential lifecycle.
- **Apps** — Google Workspace signs in with OAuth from inside the app.
  **Composio** is available as an MCP preset: one connection reaches roughly a
  thousand apps (Gmail, Notion, Linear, Jira, Slack). Composio Connect is an
  **OAuth resource, not an API key** — you authorize it in the browser, and
  bots prompt you the first time they need a specific app. There is no Composio
  API key to paste.

---

## 5. What Botter changes on your machine

Full transparency, since Botter attaches to an agent you may already rely on:

| Path | Change | Reversible |
|---|---|---|
| `~/.hermes/config.yaml` | enables `api_server` and `multiplex_profiles` | Backed up to `.bak.botter-<date>` |
| `~/.hermes/proxy/proxy.yaml` | repairs the allowlist | Backed up to `.bak.botter-<date>` |
| `~/.hermes/.env` | adds `API_SERVER_KEY`; **credentials you save in Botter are written here** | Manual |
| `~/.hermes/profiles/<slug>/` | one new profile per bot you create | `hermes profile delete`, or delete the bot in-app |
| `~/.botter/` | Botter's own state: `botter.db`, `token`, `botterd.log` | Delete the directory |
| `~/Library/LaunchAgents/io.github.treysweeney3.botterd.plist` | the launchd agent | `scripts/uninstall_botterd.sh` |

**Credential blast radius:** a credential saved in Botter is written to your
main Hermes profile *and* to every Botter-managed bot profile. Your `main`
profile is currently the canonical commit point for credentials and OAuth
grants. If that is more sharing than you want, see
[`DESIGN_CREDENTIAL_SCOPE.md`](DESIGN_CREDENTIAL_SCOPE.md) — narrowing this is a
planned change.

**Uninstall:**

```bash
scripts/uninstall_botterd.sh   # removes the launchd agent, leaves ~/.botter intact
rm -rf ~/.botter               # remove Botter's state too
```

Restore the Hermes config backups if you want to fully undo step 3. Bot
profiles are ordinary Hermes profiles and survive uninstalling Botter.

---

## 6. Troubleshooting

**`scripts/setup_hermes.sh` says no Hermes install found**

It looked in `~/.hermes`. Pass `HERMES_HOME=/actual/path` if yours is
elsewhere, or install Hermes first (step 2).

**`install_botterd.sh` cannot find `uv`**

launchd runs with a minimal `PATH`, so the script resolves `uv` to an absolute
path. If it cannot find one, install uv or set `UV_BIN=/path/to/uv`.

**`install_botterd.sh` says port 8674 is already held**

You have a development server running. Stop it and re-run. The guard exists to
prevent two `botterd` instances fighting over the port.

**botterd starts but is unhealthy**

```bash
tail -50 ~/.botter/botterd.log
curl -s http://127.0.0.1:8674/v1/health
```

The most common causes are Hermes' `model.default` being unset (run
`hermes model`) and a missing `API_SERVER_KEY` (re-run `setup_hermes.sh`).

**The app builds but shows nothing / cannot connect**

Confirm `botterd` is healthy, then check that `~/.botter/token` exists and is
mode `0600`. The app reads its bearer token from there.

**Something broke after `hermes update`**

Likely a Hermes surface changed. See
[`HERMES_COMPATIBILITY.md`](HERMES_COMPATIBILITY.md), which lists every
behavior Botter depends on and what a break in each one looks like. Reporting
the version and symptom is a genuinely useful contribution.

---

## Developing without Hermes

You do not need a Hermes agent to work on the app or the API contract. The
mock server is a contract-identical, in-memory implementation of every
`botterd` route including both SSE streams:

```bash
cd backend && uv run mock     # 127.0.0.1:8674, bearer token "mock-token"
```

This is a **development tool**, not a demo mode — it is not a supported way to
"try" Botter. See [`../CONTRIBUTING.md`](../CONTRIBUTING.md).
