---
disable-model-invocation: true
description: Run the Mindlas Verify Gate — run deterministic checks on this session's changed files and reset Verification Debt on verified-passing evidence
allowed-tools: Bash(mindlas verify gate --apply:*)
---

!`mindlas verify gate --apply --session ${CLAUDE_SESSION_ID}`

The Verify Gate planned deterministic checks for the files changed this session, ran
them, and re-derived the VERIFY gauge from the **evidence** (never modeled) — the
`before → after` move above reflects only what actually passed.

**If a check failed:** the failing command is the next thing to fix — do not claim the
task is done. Address it, then re-run `/mindlas-verify` to earn the reset on fresh
passing evidence.

**If it was skipped:** no safe deterministic check could be planned (no Python config or
tooling on the changed files), so Verification Debt is deliberately *preserved* rather
than falsely cleared.
