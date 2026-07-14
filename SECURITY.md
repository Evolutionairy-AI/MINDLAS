# Security

## What Mindlas touches

Mindlas runs as Claude Code hooks on your machine. By design it makes **no network calls and no
LLM calls**: every signal is computed locally from the per-session event ledger. It writes only
to `~/.mindlas/` (per-machine session ledgers), the project-local `.mindlas/` directory
(packs, reports, stop boundaries), and — when installing — your Claude Code `settings.json`
(reversible via `--uninstall`). The trust gate and loop guard can block a tool call only in
their explicit opt-in modes (`MINDLAS_GATE=live`, `MINDLAS_LOOP_GUARD=block`); the default
posture is observe/warn.

## Reporting a vulnerability

Open a private security advisory on GitHub
(https://github.com/Evolutionairy-AI/MINDLAS/security/advisories) or contact the maintainers
through the repository. Please do not file public issues for exploitable problems.
