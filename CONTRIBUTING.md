# Contributing to Mindlas

Thanks for looking under the hood. Mindlas is early and moving fast, and issues plus small focused PRs are the most useful things you can send.

## Ground rules

- **One change per PR.** Small is fast to review.
- **Every gauge or action change needs a test.** The suite is the spec.
- **No new runtime dependencies** without an issue discussing it first.
- **Determinism is the product.** Nothing in the scoring or correction path may call an LLM or touch the network — no LLM calls, no network calls anywhere in `src/mindlas`. Any networked feature ships opt-in and off by default, and the local instrument must work fully without it.
- **The honesty invariants are deliberate.** A suppressed action writes no artifacts, an estimated signal must not escalate on its own, and an after-score never borrows a stronger label than it earned. The test suite enforces these — do not "fix" them.
- Keep `pyproject.toml`, `src/mindlas/__init__.py`, and `.claude-plugin/plugin.json` versions in lockstep.

## Setup

```bash
git clone https://github.com/Evolutionairy-AI/MINDLAS.git
cd MINDLAS
python -m venv .venv
# activate it (Windows: .\.venv\Scripts\Activate.ps1 · POSIX: source .venv/bin/activate)
pip install -e ".[dev]"
```

## Before you open a PR

1. `pytest` — the full suite must pass (offline, deterministic, a few minutes). **Activate the venv first:** several tests shell out to `ruff` and `pytest` as subprocesses; without the venv's scripts dir on PATH you get real-looking failures that are environment artifacts, not code failures.
2. `ruff check src/ tests/` — must be clean.
3. If your change touches the hooks, statusline, or plugin surface, validate it in a **live Claude Code session** (see "Proven offline → Live-CLI validation" in the README) — this codebase's history shows pytest alone does not prove the live wiring.

## Good first contributions

- Reproducible bug reports with a session ledger excerpt (strip anything private first).
- Host coverage: reports of how the hooks behave on your Claude Code version and OS.
- Docs fixes where the manual and the behavior disagree.

## Reporting a catch that went wrong

False positives matter as much as catches. If a gauge alerted on a healthy session or stayed quiet on a bad one, open an issue with the scorecard JSON (`mindlas scorecard --json`) and what you expected — your scorecard is exactly the data the thresholds need.

## License

Licensed under Apache 2.0. By contributing you agree your work ships under the same license.
