# Design: credential scope and the role of the `main` profile

**Status:** Scoping. No implementation yet.
**Decision needed before:** the first public release that people run against an
agent they already depend on.

---

## Problem

Botter is designed to **adopt an existing Hermes agent** rather than install a
private one. That is the premise of the product: you already have an agent, and
Botter gives you a roster of bots on top of it.

The consequence is that Botter operates on a `main` profile that belongs to the
user and predates Botter. Today Botter treats that profile as its own
infrastructure. A credential saved in Botter's UI is written to the user's main
Hermes agent, and an OAuth grant is authorized against it.

On the original author's machine this is invisible — `main` *is* Botter's. On
someone else's machine, "I added a GitHub token to a bot" silently rewriting
their primary agent's `.env` is a surprise, and arguably a data-integrity
issue.

## How it works today

`main` is not incidentally involved. It has **four distinct load-bearing
roles**, all in `backend/botterd/global_auth.py` unless noted.

### 1. Write target for every credential

```python
# global_auth.py:210-211  (mutate_dashboard_env)
# Profiles first; main is the canonical commit point.
targets: list[str | None] = [*(await self.registered_slugs()), None]
```

`None` is the sentinel for main. The same pattern governs config edits
(`apply_config_edit`, line 301). Every credential write and every config edit
lands on main **and** on every registered bot profile, transactionally, with
rollback.

### 2. Baseline for drift detection

```python
# global_auth.py:181-183  (_env_mismatches_locked)
main_path = self.settings.hermes_home / ".env"
main_values = {key: read_env_value(main_path, key) for key in keys}
```

Every bot profile's value is compared **against main's**. Main is the source of
truth that defines what "in sync" means. This is why simply removing main from
the write list does not work — drift detection would lose its reference point
and every credential would report as out-of-sync.

### 3. Canonical anchor for OAuth grants

```python
# global_auth.py:468-469
# `HERMES_HOME/mcp-tokens/<name>.*` … The user authorizes once against main,
# so the grant is copied outward from there.
```

MCP grants (`<name>.json`, `.client.json`, `.meta.json` — all three must travel
together) and Google Workspace tokens are obtained once against main and then
copied into each bot profile. `google_client_secret.json` also lives at
`HERMES_HOME/` (`config.py: google_client_secret_path`), i.e. main's home.

### 4. Implicit template for every new bot

```python
# registry.py:300
[hermes_bin, "profile", "create", slug, "--clone", "--description", ...]
```

Per `hermes profile create --help`, bare `--clone` copies `config.yaml`, `.env`,
`SOUL.md`, and skills **from the active profile**. So every new bot begins life
holding a copy of that profile's entire `.env`.

> **Latent bug, independent of this design question.** `--clone` clones the
> *active* profile, not `main` specifically. If a user's active Hermes profile
> is something other than main, new bots silently inherit from an unexpected
> source, and drift detection — which compares against main — will immediately
> report them as out of sync. Botter should pass `--clone-from` explicitly
> regardless of which option below is chosen. This is worth fixing on its own.

### Blast radius, stated plainly

Saving one credential in Botter writes it to `~/.hermes/.env` **and** to
`~/.hermes/profiles/<slug>/.env` for every bot. Creating a bot copies the
active profile's entire `.env` into the new profile. Nothing is encrypted;
everything is mode `0600` plaintext (see [`../SECURITY.md`](../SECURITY.md)).

Note that Botter already models the "not ours to write" case correctly for
Slack: `credentials.py` marks `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN` as
`PROTECTED_KEYS` and renders Slack read-only with the note *"main's own Slack
agent … managed by Hermes."* That pattern is the seed of the fix.

---

## Options

### A. Status quo, with disclosure

Keep main canonical. Document the blast radius in `SECURITY.md` and `SETUP.md`
(both now do this) and show it during onboarding.

- **Cost:** none.
- **Risk:** users adopt Botter onto a working agent and discover afterwards that
  their primary `.env` was modified. Reasonable people will consider that a bug
  regardless of documentation.

### B. Dedicated anchor profile — **recommended**

Botter creates and owns a profile (`botter-anchor`) at first run. That profile
replaces main in all four roles. Main becomes read-only, displayed the way
Slack already is.

Feasibility is confirmed: `hermes profile create --clone-from SOURCE` exists,
so bots can be cloned from the anchor rather than from the active profile.

**Work required:**

| Role | Change | Where |
|---|---|---|
| 1. Write target | Replace the `None` sentinel with the anchor slug in the two `targets` lists | `global_auth.py:211, 301` |
| 2. Drift baseline | Read the baseline from the anchor's `.env` instead of `hermes_home/.env` | `global_auth.py:181-183` |
| 3. OAuth anchor | Point the MCP grant source and Google canonical paths at the anchor | `global_auth.py:468-560`, `config.py`, `mcp.py:109` |
| 4. Clone template | Pass `--clone-from <anchor>` explicitly | `registry.py:300` |
| — | Create the anchor on first run; surface it in health/preflight | `main.py`, `registry.py` |
| — | Migration for existing installs | new |

The `None`-sentinel abstraction means roles 1 and 2 are close to a substitution
rather than a rewrite. Role 3 is the genuinely fiddly part: the Google OAuth
flow drives a Hermes skill script whose paths assume `HERMES_HOME`.

**Migration.** Existing users already have credentials on main and bots cloned
from it. The migration must: create the anchor, copy main's Botter-managed
credentials and grants into it, and then *stop* writing to main — without
deleting anything from main, since the user may still want those values there.
Removing them is the user's call, not Botter's.

**Cost:** meaningful but bounded. Roughly a day, plus tests and migration.
**Benefit:** Botter stops mutating an agent it does not own. Uninstalling
becomes clean. The security story becomes explainable in one sentence.

### C. User-selectable sharing scope

Ask at onboarding: *"Share credentials with your main Hermes agent?"* — off by
default for an adopted install, on for a Botter-created one.

This is not an alternative to B so much as **the UI on top of B**: the machinery
that lets main be excluded is exactly the anchor work. Worth building as a
setting once B exists, not before.

---

## Recommendation

1. **Now, independently:** fix the `--clone` → `--clone-from` bug. It is a real
   defect with a one-line fix, and it is not coupled to this decision.
2. **Now:** ship option A's disclosure — done, in `SECURITY.md` and
   `SETUP.md` §5.
3. **Before the release that targets adopted agents:** implement B.
4. **Later:** expose C as a setting.

The forcing question: *would a user be upset to learn Botter did this?* For
writing to `~/.hermes/.env` on an agent they already relied on, the honest
answer is yes. That is what makes B worth the day.

## Open questions

- Should the anchor be a hidden implementation detail, or a visible profile the
  user can inspect? Hidden is cleaner UX; visible is more honest and easier to
  debug. Leaning visible-but-unlisted.
- Does the Google Workspace skill script tolerate a `HERMES_HOME`-relative path
  that points at a profile rather than the root? **Needs verification** —
  `config.py: google_setup_script` and `google_client_secret_path` both assume
  the root. This is the main technical risk in option B and should be spiked
  before committing to the work.
- Should archived bots keep receiving credential updates? Today
  `registered_slugs()` passes `include_archived=True`, so they do. Probably
  correct — unarchiving should not produce a broken bot — but it deserves to be
  a deliberate choice rather than a default.
