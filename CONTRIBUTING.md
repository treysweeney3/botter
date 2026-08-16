# Contributing to Botter

Thanks for your interest. Botter accepts bug fixes, documentation, and feature
contributions.

Please read the **Invariants** section before writing code. Botter has two
architectural rules that are not negotiable, and a PR that breaks either one
cannot be merged no matter how good it is.

---

## Invariants

**1. Hermes core is never patched.**

Botter interacts with the Hermes agent through exactly three surfaces: its
configuration files, its profile directories, and its HTTP API. Botter does not
patch, vendor, monkey-patch, or fork any Hermes source. This is what keeps
`hermes update` safe for every user, forever.

If a feature seems to require changing Hermes, the answer is either to work
within the existing surfaces or to contribute the change upstream to
[hermes-agent](https://github.com/NousResearch/hermes-agent) — not to carry a
patch here.

**2. One bot is one Hermes profile.**

A bot is a real, isolated agent instance at `~/.hermes/profiles/<slug>` with
its own `config.yaml`, `SOUL.md`, memory, `state.db`, and cron jobs. Nothing
about a bot is simulated, faked, or stored only in Botter's database. Botter's
SQLite database holds presentation metadata (display name, avatar color and
glyph, archived state, read state) and nothing that constitutes agent state.

**3. The API contract is pinned.**

The contract in [`docs/SPEC.md`](docs/SPEC.md) §4 — envelopes, the normalized
message schema, and both SSE payload shapes — is shared by three
implementations: `botterd`, the mock server, and the Swift client. A change to
any one of them is a change to all three plus the spec, in the same PR.

---

## Scope

**In scope:** bug fixes, macOS app features, `botterd` features, Hermes
integration depth, accessibility, performance, documentation, and tests.

**Currently out of scope** — please open a discussion before writing code:

| Area | Why |
|---|---|
| iOS app target | `BotterKit` builds for iOS 18, but the app target and the cloud relay it needs are a planned phase with unresolved design questions. |
| Windows / Linux support | Botter depends on launchd, `SMAppService`, AppKit, and macOS notification APIs throughout. |
| Alternative agent backends | Botter is a Hermes management app. Abstracting the backend would dissolve the product. |
| Bundling or vendoring Hermes | See invariant 1. |
| Telemetry, analytics, crash reporting | Botter collects nothing. This is a permanent commitment, not a default. |
| Emoji in the UI | The design language uses vector glyphs and the bundled otter poses. See [`docs/SPEC.md`](docs/SPEC.md). |

---

## Developer Certificate of Origin

Botter uses the [DCO](https://developercertificate.org/) rather than a CLA. You
keep copyright in your contribution; you simply certify that you have the right
to submit it under the project's Apache 2.0 license.

Sign off every commit:

```bash
git commit -s -m "your message"
```

This appends a `Signed-off-by: Your Name <your@email.com>` trailer. Commits
without it cannot be merged. To fix the last commit: `git commit --amend -s`.

---

## Development setup

**Prerequisites**

| Tool | Purpose | Required for |
|---|---|---|
| macOS 15+ | AppKit and `SMAppService` APIs | app |
| Xcode 16+ (Swift 6) | building the app | app |
| [`xcodegen`](https://github.com/yonaskolb/XcodeGen) | generates `Botter.xcodeproj` from `project.yml` | app |
| [`uv`](https://docs.astral.sh/uv/) | Python environment and runner | backend |
| A Hermes install | live integration only | end-to-end work |
| Docker | agent sandboxes and egress enforcement | optional, recommended |

**You do not need Hermes to work on the app or the API contract.** The mock
server is a contract-identical, in-memory implementation of every `botterd`
route, including both SSE streams. It is a development tool, not a shipped
product surface.

```bash
cd backend && uv run mock         # 127.0.0.1:8674, bearer token "mock-token"
```

Then build and run the app against it as normal. This is the fastest loop for
UI work and the only one that runs in CI.

**Full local setup** — see [`docs/SETUP.md`](docs/SETUP.md).

---

## Running the checks

Everything below must pass before you open a PR. CI runs the same commands.

```bash
# Backend — 102 tests, no Hermes required
cd backend && uv run pytest -q

# Swift package — models, SSE decoding, stores
cd app/BotterKit && /usr/bin/swift test

# App build
cd app && xcodegen generate && xcodebuild -scheme Botter -configuration Debug build

# Shell scripts
shellcheck scripts/*.sh
```

Note: `Botter.xcodeproj` is **generated** and is not tracked in git.
`project.yml` is the source of truth. Run `xcodegen generate` after adding,
renaming, or deleting a source file, or the file will not compile.

`scripts/e2e.sh` runs a live end-to-end pass against an installed daemon and a
real Hermes. It is not run in CI and it creates and deletes a real profile.

---

## Pull requests

- **One concern per PR.** A bug fix and a refactor are two PRs.
- **Describe the behavior change**, not just the code change. If it is a bug
  fix, say how you reproduced the bug.
- **New behavior needs a test.** For a bug fix, the test should fail without
  your fix — please confirm you checked that.
- **Match the surrounding style.** The codebase uses full sentences in
  comments, explains *why* rather than *what*, and avoids abbreviations. Python
  is typed and uses `from __future__ import annotations`. There is no
  autoformatter; follow the file you are editing.
- **No new runtime dependencies** without discussion. `BotterKit` has zero
  third-party dependencies and that is a deliberate property. The backend
  dependency list is small on purpose.
- **UI changes need a screenshot** in the PR description, in both the default
  window size and a narrow one.

## Hermes version compatibility

Botter depends on Hermes behavior that is not part of any published API — the
`hermes serve` `/api/env` surface, the `mcp_servers` config key, the cron
ledger layout, the SSE dialect, and the profile purge sequence.

The version Botter is tested against is recorded in
[`docs/HERMES_COMPATIBILITY.md`](docs/HERMES_COMPATIBILITY.md). If you discover
a behavior difference on a different Hermes version, a report with the version
and the observed difference is a genuinely valuable contribution — that file is
where the knowledge should land.

Verified runtime findings about how Hermes actually behaves belong in
[`backend/NOTES.md`](backend/NOTES.md), which is the most useful document in
the repository for a new contributor.

---

## Code of conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
