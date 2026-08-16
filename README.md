# Botter

A native macOS (later iOS) management app for the locally-installed **Hermes agent** (`~/.hermes`, Nous Research `hermes-agent`), modeled on xAI's **Grok Bot**: multiple named "Bots", each an isolated agent instance with its own role, persona, memory, sessions, routines, and approval boundary — all managed from a single chat-first UI.

## Documents

| Doc | Purpose | Owner |
|---|---|---|
| [`docs/SPEC.md`](docs/SPEC.md) | Product + system spec: concepts, architecture, API contract, design language, phasing | shared |
| [`docs/PLAN_HERMES_SETUP.md`](docs/PLAN_HERMES_SETUP.md) | Changes to the Hermes agent's own config and setup (no core code changes) | manual / scripted by backend |
| [`docs/PLAN_BACKEND.md`](docs/PLAN_BACKEND.md) | `botterd` companion service implementation plan — **self-contained handoff for GPT-5.6-Sol via Codex** | GPT-5.6-Sol |
| [`docs/PLAN_FRONTEND.md`](docs/PLAN_FRONTEND.md) | SwiftUI macOS app implementation plan | Fable 5 |

## System at a glance

```
┌─────────────────────────┐        ┌───────────────────────────────┐
│  Botter.app (SwiftUI)   │  HTTP  │  botterd (Python/FastAPI)     │
│  macOS now, iOS later   │ ─────► │  bot registry · aggregation   │
│                         │  SSE   │  profile lifecycle · proxy    │
└─────────────────────────┘        └──────────────┬────────────────┘
                                                  │ HTTP (127.0.0.1:8642)
                                                  ▼
                                   ┌───────────────────────────────┐
                                   │  Hermes gateway (existing)    │
                                   │  api_server + multiplexed     │
                                   │  profiles: one per Bot        │
                                   └───────────────────────────────┘
```

- **1 Bot = 1 Hermes profile** (`~/.hermes/profiles/<slug>`): own `SOUL.md`, memory, sessions, cron jobs.
- Hermes core is **never patched** — only config, profiles, and its already-built (currently dormant) HTTP API server are used, so `hermes update` stays safe.
- iOS access will go through a cloud relay (Cloudflare Tunnel recommended) in a later phase; `botterd` auth is designed to be relay-ready from day one.

## Repo layout (planned)

```
botter/
├── docs/          # the four planning docs
├── backend/       # botterd — Python 3.11 / FastAPI (GPT-5.6-Sol)
├── app/           # Botter.app — SwiftUI multiplatform (Fable 5)
└── scripts/       # Hermes setup / verification scripts
```
