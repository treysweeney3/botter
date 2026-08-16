# Plan: the Computer panel

**Status:** Design. Not implemented. Not committed to.
**Target:** v2
**Depends on:** an unresolved spike (see [Spike zero](#spike-zero-do-this-before-anything-else)).

---

## The feature

Each bot gets a computer it can work on, and that you can watch and take over.

> Open the Computer panel and the bot's desktop spins up on its own — live
> screen preview while it works, **Open desktop** to take over in your browser,
> or point the bot at this Mac instead.

The header button already exists in the app, disabled. This document plans what
sits behind it.

Three modes, in descending order of confidence:

| Mode | What it is | Phase |
|---|---|---|
| **Cloud box** | A per-bot Linux VM from [box](https://box.ascii.dev/) (ASCII). Provisioned on demand, billed per second. | 2 |
| **Local Docker** | The Hermes sandbox container the bot already runs in, surfaced instead of hidden. | 2 |
| **This Mac** | The bot drives the host machine directly. | Deferred — see [Mode 3](#mode-3-this-mac--deferred) |

---

## Spike zero: do this before anything else

**Question: can an external agent perform computer-use (screenshot, click,
type) against a box, or only shell commands and file I/O?**

This is not a detail. It determines what the feature *is*.

- **If computer-use is available:** the bot genuinely operates a desktop. Live
  preview shows the agent working. This is the advertised feature.
- **If it is shell and files only:** the bot works in a terminal, and the
  desktop exists for *you* to take over. Still useful — arguably more useful for
  real work — but it is a different product, and the copy above is a lie.

The published SDK overview lists "creating Boxes, prompting them, reading
events, listing API key metadata, managing secrets, and opening desktop or SSH
access." It does not confirm screenshot/click/type primitives. The
`POST /boxes/{boxId}/desktop` endpoint takes `vnc=1`, which suggests the desktop
is reached over VNC — a human-facing channel, not necessarily an agent one.

**Spike:** buy one month ($20 minimum), create a box, and determine empirically
whether the REST API exposes screen capture and input injection. Timebox to
half a day. Do not write a line of Botter code first. Record findings in
`backend/NOTES.md` alongside the Hermes findings.

Secondary unknowns worth resolving in the same spike:

- What does `POST /boxes/{boxId}/prompt` actually run? The marketing copy says
  "run Claude or Codex inside." If the box runs its *own* agent, that competes
  with Hermes rather than serving it — see [Architecture](#architecture).
- Cold-start latency for `POST /boxes` and for `resume` from a stopped box.
- Whether the desktop URL is a browser-native page (noVNC) or requires a native
  VNC client. This decides the entire preview implementation.
- Whether `publicAccess` desktop URLs are unguessable and expirable.

---

## Architecture

The hard question is not *how does Botter talk to box* — it is **how does a
Hermes bot get tools to drive one**, without violating the invariant that
Hermes core is never patched.

box publishes Python and TypeScript SDKs and a REST API. **It does not publish
an MCP server.** So Hermes has no native way to reach it.

### Options considered

**A. Botter ships a thin MCP server wrapping the box REST API — recommended.**

Botter already manages `mcp_servers` in every profile's `config.yaml`
(`backend/botterd/mcp.py`), already handles MCP OAuth and grant fan-out, and
already restarts the gateway on MCP mutation. A box MCP server slots into
machinery that exists and is tested.

```
Botter.app  ──HTTP/SSE──>  botterd  ──REST──>  ascii.dev/api/box/v1
    │                         │
    │  Computer panel         │  serves an MCP endpoint on loopback
    │  preview + controls     ▼
    │                    Hermes gateway ──MCP──> box tools
    └── WKWebView ────────────────────────────> desktop URL (browser)
```

Tools exposed to the bot: `box_status`, `box_exec`, `box_read_file`,
`box_write_file`, `box_desktop_url`, and — **if spike zero confirms it** —
`box_screenshot`, `box_click`, `box_type`.

Serving the MCP endpoint from `botterd` itself means the box API key never
reaches the profile `.env`, and one implementation serves every bot.

**B. Bot delegates to the agent running inside the box (`/prompt`).**

Hermes hands a task to Claude or Codex running in the box and polls
`/boxes/{id}/events`. Much less code. But now two agents with two memories and
two personas are involved in one task, the bot's `SOUL.md` and approval boundary
do not apply inside the box, and the trace the user sees in chat is secondhand.
This breaks the product's central promise that a bot is one coherent agent.
**Rejected**, though it may be worth offering as an explicit "delegate to a
coding agent" action later.

**C. SSH from a Hermes skill.**

Works, needs no MCP server, but puts credentials and connection management into
a skill Botter would have to install into each profile — closer to owning
Hermes' internals than Botter should get, and harder to surface in the UI.
**Rejected.**

### Where box state lives

- **`BOX_API_KEY`** is an ordinary env credential. It flows through the existing
  credential surface (`credentials.py`) with no new machinery — add it to
  `CURATED` with a `box` group so it gets a first-class card.
- **Box identity per bot** (`box_id`, region, size, status, last-used) is
  presentation and lifecycle metadata, so it belongs in Botter's SQLite
  database next to avatar color and archived state — **not** in the Hermes
  profile. A bot with no box is the normal state.

---

## Cost control is a feature requirement, not a nicety

box bills **per second**, with a **$20/month minimum**, across three VM sizes.
"The desktop spins up on its own" is a phrase that, implemented naively, spends
the user's money without asking.

Non-negotiable requirements:

1. **No implicit provisioning.** Opening the Computer panel shows an offer, not
   a running VM. The first box for a bot requires an explicit action.
2. **Visible cost.** The panel shows uptime and estimated spend for the current
   session, always, not behind a disclosure.
3. **Idle auto-stop**, on by default, user-configurable, default 15 minutes.
   `POST /boxes/{id}/stop` pauses billing; `resume` brings it back.
4. **Stop on quit.** If the app quits or `botterd` stops with a box running,
   stop the box. A VM left running overnight because a laptop lid closed is the
   failure mode that loses trust permanently.
5. **A hard monthly ceiling** the user sets, after which Botter refuses to
   provision and says why.
6. **Onboarding states the pricing** before the first box is ever created.

## Other constraints to design around

- **Region.** box is EU-only (Germany, Finland, France). For a US user that is
  meaningful latency on an interactive desktop, and it is a data-residency fact
  that belongs in the UI, not a footnote — work products and any credentials the
  bot uses in the box leave the user's country.
- **Egress.** Hermes enforces egress rules through iron-proxy on local Docker
  sandboxes. A cloud box is **outside that boundary entirely.** A bot that
  cannot reach a host locally may be able to reach it from a box. This must be
  stated plainly in the UI and in `SECURITY.md` before the feature ships.
- **Approvals.** The approval boundary is enforced by Hermes on tool calls. If
  box tools arrive as MCP tools, they are ordinary tool calls and the existing
  approval machinery applies — which is the strongest argument for option A.
  Verify this holds rather than assuming it.
- **Zero dependencies.** `BotterKit` has no third-party dependencies and that is
  deliberate. If the desktop is browser-native, preview is a `WKWebView` and
  takeover is `NSWorkspace.shared.open(url)` — no dependency. If it needs a
  native VNC client, that is a dependency and a design discussion, not a
  quiet `Package.swift` edit.

---

## Mode 2: local Docker

Every bot already runs in a Hermes-managed Docker sandbox. Surfacing that
container — a file browser, a command log, a terminal view — delivers a real
part of the Computer panel with **no new vendor, no new cost, and no data
leaving the machine.**

It is also strictly less risky than the cloud path and does not depend on spike
zero. **Consider shipping this first.** It makes the panel real, validates the
UI, and gives the cloud box something to slot into rather than being the whole
feature.

## Mode 3: "this Mac" — deferred

"Point the bot at this Mac instead" means granting an LLM-driven agent control
of the user's actual computer — Accessibility and Screen Recording permissions,
input injection, and access to every logged-in session on the machine.

This is a categorically different threat model from a disposable VM, and Hermes'
entire sandboxing design exists to avoid it. It is **not** a mode toggle beside
the others.

If it is ever built it needs its own design document covering permission
prompts, a hard allowlist of what can be driven, mandatory per-action approval,
a visible always-on indicator, and a panic stop. **Out of scope for this plan.**
The UI should not offer it until that document exists.

---

## Phasing

| Phase | Deliverable | Gate |
|---|---|---|
| 0 | Spike zero — computer-use support, prompt semantics, desktop URL shape, cold-start latency | Half a day. Findings in `NOTES.md`. |
| 1 | Local Docker sandbox view — file browser, command log | None. Ship independently. |
| 2 | `BOX_API_KEY` credential card + box lifecycle in `botterd` (create/status/stop/resume/delete) + cost ceiling | Spike zero |
| 3 | Box MCP server in `botterd`; bot gets tools; approvals verified to apply | Phase 2 |
| 4 | Computer panel UI — live preview, uptime and spend, Open desktop | Phase 3 |
| 5 | Snapshot and fork (box supports both) — "branch this bot's work" | Phase 4 |

Phase 1 is worth doing regardless of whether the cloud path is ever built.

## Open questions

- Does an approval raised by a box MCP tool render correctly in chat, and can it
  be denied before the box acts? Must be verified in phase 3, not assumed.
- One box per bot, or one box per session? Per-bot matches the mental model;
  per-session matches how people actually work on parallel tasks. Snapshots and
  `fork` make per-session cheap and may be the better answer.
- What happens to a bot's box when the bot is deleted or archived? Deleting the
  bot must delete the box, or the user pays for orphans. Archiving should stop
  it, not delete it.
- Is `box` the right dependency at all? It is a young, single-vendor, EU-only
  service with a $20/month floor, and this feature would make it a hard
  requirement for a headline capability. Worth a deliberate second look — and
  worth designing the `botterd` box interface as a provider abstraction thin
  enough that a second backend is possible.
