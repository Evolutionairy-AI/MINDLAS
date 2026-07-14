"""Project-local .mindlas/ path resolution.

The runtime stores per-project state under <project_root>/.mindlas/, distinct from the
per-machine ~/.mindlas/ session ledgers (vitals/config.py). Override the root with
MINDLAS_PROJECT_ROOT (used by tests)."""
from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    root = os.environ.get("MINDLAS_PROJECT_ROOT")
    return Path(root) if root else Path.cwd()


def mindlas_dir(root: Path | None = None) -> Path:
    return _root(root) / ".mindlas"


def cold_store_dir(root: Path | None = None) -> Path:
    """Cold store root for lossless transcript snapshots.
    Prefers the plugin's persistent data dir (${CLAUDE_PLUGIN_DATA}/.mindlas) so snapshots
    survive outside the project tree; falls back to the project-local .mindlas/ when the
    plugin env is absent (e.g. the legacy `install-hooks` path)."""
    data = os.environ.get("CLAUDE_PLUGIN_DATA")
    return (Path(data) / ".mindlas") if data else mindlas_dir(root)


def ledger_dir(root: Path | None = None) -> Path:
    return mindlas_dir(root) / "ledger"


def session_events_path(root: Path | None = None) -> Path:
    return ledger_dir(root) / "session_events.jsonl"


def context_dir(root: Path | None = None) -> Path:
    return mindlas_dir(root) / "context"


def _safe_session(session_id: str) -> str:
    """Filesystem-safe session-id segment (mirrors vitals.config.session_dir sanitization),
    so the pack tree can key off the real Claude Code session uuid without unsafe path chars."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id) or "session"


def context_sessions_dir(root: Path | None = None) -> Path:
    return context_dir(root) / "sessions"


def context_session_dir(session_id: str, root: Path | None = None) -> Path:
    """Per-session pack subtree: .mindlas/context/sessions/<uuid>/. Keying the generated pack
    artifacts by the real session id kills the newest-by-mtime guess and makes a repair's
    output attributable to the exact session that produced it."""
    return context_sessions_dir(root) / _safe_session(session_id)


def packs_dir(session_id: str) -> Path:
    return context_session_dir(session_id) / "packs"


def evidence_dir(session_id: str) -> Path:
    return context_session_dir(session_id) / "evidence"


def reports_dir(root: Path | None = None) -> Path:
    return _root(root) / ".mindlas" / "reports"


def latest_pack_path(session_id: str, root: Path | None = None) -> Path:
    return context_session_dir(session_id, root) / "latest.md"


def pack_contract_path(session_id: str, root: Path | None = None) -> Path:
    """Durable structured task contract (objective + constraints) for this session, so a LATER
    reseeded session can inherit it when its own reset ledger yields a hollow/meta pack (the b2b
    repair case). Written by the Context Repair action on apply."""
    return context_session_dir(session_id, root) / "contract.json"


def inherited_contract_path(session_id: str, root: Path | None = None) -> Path:
    """The PRIOR session's contract, copied here by the /clear reseed so build_pack_data can inherit
    the objective/constraints instead of re-extracting from this session's reset ledger."""
    return context_session_dir(session_id, root) / "inherited_contract.json"


# The session subtree already says WHOSE pack this is; the timestamp only orders the history
# WITHIN it. These helpers keep that naming convention here (owned by the module that owns the
# directory layout) instead of hand-built `f"{now}_..."` strings at each call site.
def pack_snapshot_path(session_id: str, now: str) -> Path:
    """Immutable timestamped copy of an applied repair pack, kept as history alongside latest.md."""
    return packs_dir(session_id) / f"{now}_context_repair.md"


def draft_pack_path(session_id: str, now: str) -> Path:
    """A validation-failed pack — written to the packs subtree but never made live."""
    return packs_dir(session_id) / f"{now}_draft.md"


def evidence_snapshot_path(session_id: str, now: str) -> Path:
    return evidence_dir(session_id) / f"{now}_evidence.json"


def pending_resume_path(session_id: str, root: Path | None = None) -> Path:
    """Per-session one-shot reseed flag: .mindlas/context/sessions/<uuid>/pending_resume.json.
    Repair writes it under its own session id; the SessionStart reseed reads it under the starting
    session's id first, then falls back to the id recorded in current_session.json (a /clear may
    keep or mint the session uuid — both are covered). Session-keyed so simultaneous sessions never
    overwrite each other's pending reseed — the reseed pulls the pack by session id, not via a
    shared marker."""
    return context_session_dir(session_id, root) / "pending_resume.json"


def after_pending_path(session_id: str, root: Path | None = None) -> Path:
    """Measured-after per-session flag: the reseeded session records that its next measured CTX is
    the true after-repair score. Keyed by the reseeding session's id (the status-line reconcile
    reads it under that same id) so concurrent sessions never clobber each other's after-capture."""
    return context_session_dir(session_id, root) / "after_pending.json"


def current_session_path(root: Path | None = None) -> Path:
    """Project-local pointer to the live session id, stamped by the hooks (which always
    receive session_id in their payload). The repair CLI — a `!`-shell subprocess with no
    session_id of its own — reads it to address THIS session's ledger + pack tree by its real
    uuid instead of guessing newest-by-mtime."""
    return mindlas_dir(root) / "current_session.json"


def corrections_path(root: Path | None = None) -> Path:
    return reports_dir(root) / "corrections.jsonl"


def scorecard_md_path(root: Path | None = None) -> Path:
    return reports_dir(root) / "latest_scorecard.md"


def scorecard_json_path(root: Path | None = None) -> Path:
    return reports_dir(root) / "latest_scorecard.json"


def scorecard_meta_path(root: Path | None = None) -> Path:
    """Non-derivable scorecard inputs (session_id + task) persisted so the status-line
    measured-after reconcile can re-render the scorecard after a /clear whose lean ledger
    dropped the task."""
    return reports_dir(root) / "scorecard_meta.json"


def _root(root: Path | None) -> Path:
    return root if root is not None else project_root()


def verification_dir(root: Path | None = None) -> Path:
    return _root(root) / ".mindlas" / "verification"


def verification_sessions_dir(root: Path | None = None) -> Path:
    return verification_dir(root) / "sessions"


def verification_session_dir(session_id: str, root: Path | None = None) -> Path:
    """Per-session verifier subtree: .mindlas/verification/sessions/<uuid>/ (mirrors
    context_session_dir). Keys the verifier result + history by the real session id, so a verify
    run is attributable to the exact session that produced it — killing the newest-by-mtime guess
    and the cross-session result collision when two sessions share a project tree."""
    return verification_sessions_dir(root) / _safe_session(session_id)


def latest_verifier_result_path(session_id: str, root: Path | None = None) -> Path:
    return verification_session_dir(session_id, root) / "latest_verifier_result.json"


def verify_results_dir(session_id: str, root: Path | None = None) -> Path:
    return verification_session_dir(session_id, root) / "results"


def verifier_result_snapshot_path(session_id: str, now: str, root: Path | None = None) -> Path:
    """Immutable timestamped copy of an applied verifier result, kept as history alongside
    latest_verifier_result.json (mirrors pack_snapshot_path)."""
    return verify_results_dir(session_id, root) / f"{now}_verify_gate.json"


def splits_dir(root: Path | None = None) -> Path:
    return _root(root) / ".mindlas" / "splits"


def splits_session_dir(session_id: str, root: Path | None = None) -> Path:
    """Per-session split subtree: .mindlas/splits/sessions/<uuid>/ (mirrors verification_session_dir).
    Keys the split queue by the real session id so simultaneous sessions sharing a project tree never
    collide on latest_split_manifest.json."""
    return splits_dir(root) / "sessions" / _safe_session(session_id)


def split_run_dir(session_id: str, split_id: str, root: Path | None = None) -> Path:
    return splits_session_dir(session_id, root) / split_id


def latest_split_manifest_path(session_id: str, root: Path | None = None) -> Path:
    return splits_session_dir(session_id, root) / "latest_split_manifest.json"


def stops_dir(root: Path | None = None) -> Path:
    return _root(root) / ".mindlas" / "stops"


def stops_session_dir(session_id: str, root: Path | None = None) -> Path:
    """Per-session stop subtree: .mindlas/stops/sessions/<uuid>/ (mirrors verification_session_dir).
    Keys the stop boundary by the real session id so a Stop guards only the session that looped, never
    another session sharing the project tree."""
    return stops_dir(root) / "sessions" / _safe_session(session_id)


def stop_run_dir(session_id: str, stop_id: str, root: Path | None = None) -> Path:
    return stops_session_dir(session_id, root) / stop_id


def latest_stop_path(session_id: str, root: Path | None = None) -> Path:
    return stops_session_dir(session_id, root) / "latest_stop.json"


def active_stop_path(session_id: str, root: Path | None = None) -> Path:
    return stops_session_dir(session_id, root) / "active_stop.json"
