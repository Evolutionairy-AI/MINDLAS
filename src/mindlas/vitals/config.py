"""Per-machine path resolution and event-classification constants.

Nothing here does I/O — it only computes paths.
"""
from __future__ import annotations

import os
from pathlib import Path

# Bash/command tool_calls whose command contains one of these substrings are
# classed `test_run`. Users in other stacks can extend via MINDLAS_TEST_PATTERNS
# (comma-separated).
DEFAULT_TEST_PATTERNS: tuple[str, ...] = (
    "pytest", "npm test", "yarn test", "jest", "go test", "cargo test",
    "mvn test", "unittest", "rspec", "phpunit", "dotnet test",
)


def test_patterns() -> tuple[str, ...]:
    extra = os.environ.get("MINDLAS_TEST_PATTERNS", "")
    parsed = tuple(p.strip() for p in extra.split(",") if p.strip())
    return DEFAULT_TEST_PATTERNS + parsed


def home_dir() -> Path:
    """Per-machine state root. Override with MINDLAS_HOME (used by tests)."""
    root = os.environ.get("MINDLAS_HOME")
    return Path(root) if root else Path.home() / ".mindlas"


def session_dir(session_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id) or "unknown"
    return home_dir() / "sessions" / safe


def ledger_path(session_id: str) -> Path:
    return session_dir(session_id) / "ledger.jsonl"


def loop_alert_path(session_id: str) -> Path:
    """Per-session one-shot dedupe cache of the last alerted loop hash. Cleared by
    `mindlas loop release` so a byte-identical loop recurring after a release re-fires the
    in-session nudge instead of being silently deduped."""
    return session_dir(session_id) / "loop_alert.json"


def statusline_backup_path() -> Path:
    return home_dir() / "statusline_backup.json"


def latest_ledger() -> Path | None:
    """Newest session ledger by mtime — the heuristic for 'the current session'."""
    sessions = home_dir() / "sessions"
    if not sessions.exists():
        return None
    candidates = list(sessions.glob("*/ledger.jsonl"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def latest_session_id() -> str | None:
    """The most-recent session's id (its on-disk dir name), or None if no sessions."""
    led = latest_ledger()
    return led.parent.name if led is not None else None


def claude_config_dir() -> Path:
    """Claude Code's config dir. Respects CLAUDE_CONFIG_DIR (used by tests and real installs)."""
    root = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(root) if root else Path.home() / ".claude"


def claude_settings_path() -> Path:
    return claude_config_dir() / "settings.json"


def context_pct_path(session_id: str) -> Path:
    """Per-session cache of the last MEASURED context-window %. The status line (the only surface
    that sees the live window %) writes it; the CLI status surfaces read it so their CTX mass
    matches the live line instead of proxying to ~100%."""
    return session_dir(session_id) / "context_pct.json"


def read_context_pct(session_id: str):
    """The last MEASURED context-window % the status line cached for this session, or None if never
    measured. The single reader shared by the CLI status surfaces AND the Context Repair action, so
    every CTX before-score reuses the live line's mass instead of proxying — and they all agree."""
    if not session_id:
        return None
    try:
        import json
        p = context_pct_path(session_id)
        if not p.exists():
            return None
        v = json.loads(p.read_text(encoding="utf-8")).get("pct")
        return float(v) if isinstance(v, (int, float)) else None
    except (OSError, ValueError):
        return None


def verify_baseline_path(session_id: str) -> Path:
    return session_dir(session_id) / "verify_baseline.json"


def verdict_ledger_path(session_id: str) -> Path:
    return session_dir(session_id) / "verdicts.jsonl"


def test_baseline_path(session_id: str) -> Path:
    return session_dir(session_id) / "test_baseline.json"


def test_state_path(session_id: str) -> Path:
    return session_dir(session_id) / "test_state.json"
