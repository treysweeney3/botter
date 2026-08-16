<!--
One concern per PR. A bug fix and a refactor are two PRs.
-->

## What this changes

<!-- Describe the behavior change, not just the code change. -->

## Why

<!-- Link the issue if there is one. -->

## How it was verified

<!--
"It compiles" is not verification. What did you actually run and observe?
For a bug fix: how did you reproduce the bug before fixing it, and did you
confirm the new test fails without your fix?
-->

## Checklist

- [ ] Commits are signed off (`git commit -s`) — see [CONTRIBUTING.md](../blob/main/CONTRIBUTING.md)
- [ ] `cd backend && uv run pytest -q` passes
- [ ] `cd app/BotterKit && swift test` passes
- [ ] `cd app && xcodegen generate && xcodebuild -scheme Botter build` succeeds
- [ ] `shellcheck scripts/*.sh` is clean (if scripts changed)
- [ ] New behavior has a test
- [ ] No hardcoded absolute paths (`/Users/...`) — derive from `$HOME` or script location
- [ ] Screenshot attached (if this changes the UI)

## Invariants

- [ ] Does not patch, fork, or vendor Hermes core
- [ ] Preserves "one bot is one real Hermes profile"
- [ ] If the API contract changed: `botterd`, the mock server, the Swift client, and `docs/SPEC.md` §4 were all updated together
