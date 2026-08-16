# Security Policy

Botter manages API credentials, shells out to a local CLI, and supervises a
long-running local daemon. Please read this before filing a report — several
properties below are deliberate design choices rather than defects.

## Reporting a vulnerability

**Do not open a public issue for a security vulnerability.**

Report privately through GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository: **Security → Report a vulnerability**.

Include the affected component (`Botter.app`, `botterd`, or a `scripts/`
entry), the version or commit, reproduction steps, and the impact you believe
it has.

This is a volunteer-maintained project with no paid on-call. Expect an
acknowledgement within 7 days and an initial assessment within 30 days. There
is no bug bounty.

## Supported versions

Only the latest commit on `main` receives security fixes. Botter is pre-1.0
and there are no maintained release branches.

## Security model

Understanding these boundaries will tell you whether a finding is a bug.

### Network exposure

- `botterd` binds `127.0.0.1:8674` only. It is never bound to `0.0.0.0`.
- The Hermes gateway binds `127.0.0.1:8642` only.
- **No inbound ports are opened.** Botter does not configure a tunnel, relay,
  or port forward. A future iOS phase will introduce an opt-in relay; it does
  not exist today.

### Authentication

- `botterd` requires `Authorization: Bearer <token>` on every route except
  `GET /v1/health`.
- The token is minted with `secrets.token_urlsafe(32)` on first run and stored
  at `~/.botter/token` with mode `0600` inside a `0700` directory. `botterd`
  refuses to read it if the path is a symlink or not a regular file.
- Botter calls the Hermes gateway with Hermes' own `API_SERVER_KEY`, read from
  `~/.hermes/.env`.

Because auth is a loopback bearer token on a single-user machine, **any local
process running as your user can read the token and drive `botterd`.** This is
the same trust boundary as the Hermes agent itself. Botter does not defend
against a local attacker who already has your user account.

### Credential storage — known and accepted

**Credentials are stored as plaintext in Hermes' `.env` files** (mode `0600`),
because that is the format the Hermes agent reads. Botter writes them through
Hermes' own credential lifecycle rather than inventing a parallel store.

Botter does **not** currently place credentials in the macOS Keychain. Doing so
would not remove the plaintext copy, since Hermes must still read `.env` at
startup — it would only add a second location holding the same secret.

Consequences you should understand before using Botter:

- Any process running as your user can read these files.
- Unencrypted Time Machine or cloud backups of your home directory will
  contain them.
- Credentials are written to `~/.hermes/.env` **and** to each Botter-managed
  profile under `~/.hermes/profiles/<slug>/.env`. See
  [`docs/DESIGN_CREDENTIAL_SCOPE.md`](docs/DESIGN_CREDENTIAL_SCOPE.md) for the
  current blast radius and the plan to narrow it.

Reports that Botter "stores API keys in plaintext" will be closed as working
as designed, with a pointer here. A concrete proposal that removes the
plaintext copy without breaking Hermes compatibility is very welcome.

### What Botter redacts

- Credential values are never returned by the API; `GET /v1/integrations`
  returns `redacted_value` only.
- Secrets are not written to `botterd` logs.
- `scripts/phase0_investigate.sh` scrubs `API_SERVER_KEY` values from the
  transcripts it captures.

**If you are attaching logs to an issue, re-read them first.** Log redaction is
best-effort, not a guarantee.

### Subprocess execution

Botter shells out to the `hermes` CLI and to `docker`. All subprocess calls use
`asyncio.create_subprocess_exec` with argument arrays — never a shell string —
so argument values cannot be interpreted as shell syntax. Profile slugs are
validated against `^[a-z0-9-]{1,32}$` and resolved paths are checked to stay
inside the Hermes profiles root.

A finding that reaches command injection or path traversal through these paths
is a genuine vulnerability. Please report it.

### Botter modifies your Hermes installation

`scripts/setup_hermes.sh` edits `~/.hermes/config.yaml` and
`~/.hermes/proxy/proxy.yaml`, and restarts your Hermes gateway. It backs up
both files before writing. This is the documented purpose of the script, not a
vulnerability — but review it before running it, as you would any script that
edits your configuration.

### Out of scope

- Anything requiring an attacker to already have local code execution as your
  user.
- Vulnerabilities in the Hermes agent itself — report those to
  [Nous Research](https://github.com/NousResearch/hermes-agent).
- Vulnerabilities in third-party services (Composio, Google, MCP servers) —
  report those to the service.
- Missing hardening on a build you produced yourself without code signing.
- Social-engineering a user into pasting a malicious credential or MCP URL.

## Telemetry

Botter collects and transmits **no telemetry, analytics, or crash reports**.
There is no phone-home of any kind. Network traffic originates only from the
Hermes agent, going to the LLM provider and to the services you configure.
