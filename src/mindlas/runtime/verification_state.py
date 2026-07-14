"""Deterministic Verification Debt signals. Git diff (+ untracked) is the primary
source; the ledger supplies the completion-claim marker (`done_claim`) by direct event scan,
anchored to last_verifier_turn (NOT build_session_state, whose count is floored at the
legacy `allow` verdict), and the session id. Reads — never writes —
.mindlas/verification/latest_verifier_result.json for freshness; the Verify Gate action is
the writer."""
from __future__ import annotations

import fnmatch
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import git_helpers, paths
from .git_helpers import diff_hash as _diff_hash   # back-compat: tests import _diff_hash here
from ..vitals.events import EventKind, Marker

# Canonical marker sets live in runtime.project_markers (single source of truth); aliased to the
# historical private names so classify_path and any tests referencing them stay unchanged.
from .project_markers import (  # noqa: E402
    TEST_FILE_GLOBS as _TEST_GLOBS,
    TEST_DIR_NAMES as _TEST_DIRS,
    PRODUCTION_EXTENSIONS as _PROD_EXT,
    CONFIG_FILE_NAMES as _CONFIG_NAMES,
    CONFIG_FILE_GLOBS as _CONFIG_GLOBS,
)


def classify_path(path: str) -> str:
    """production | test | config | neutral. Deterministic; multi-language by design."""
    p = PurePosixPath(path.replace("\\", "/"))
    name = p.name
    parts = set(p.parts)
    if any(d in parts for d in _TEST_DIRS) or any(fnmatch.fnmatch(name, g) for g in _TEST_GLOBS):
        return "test"
    if name in _CONFIG_NAMES or any(fnmatch.fnmatch(name, g) for g in _CONFIG_GLOBS):
        return "config"
    if p.suffix in _PROD_EXT:
        return "production"
    return "neutral"


def _numstat(args: list[str], root: Path) -> tuple[list[str], int]:
    per_file, total = git_helpers.numstat(args, root)
    return [p for (p, _a, _d) in per_file], total


def _untracked(root: Path) -> list[str]:
    return git_helpers.untracked(root)


def _git_changes(root: Path) -> tuple[list[str], int, str] | None:
    """(changed_files, changed_lines, diff_hash) or None if git is unavailable / not a repo."""
    if git_helpers._run_git(["rev-parse", "--is-inside-work-tree"], root) is None:
        return None
    files: dict[str, bool] = {}
    total = 0
    for extra in ([], ["--cached"]):
        fs, ln = _numstat(extra, root)
        for f in fs:
            files[f] = True
        total += ln
    untracked = _untracked(root)
    for u in untracked:
        files[u] = True
        try:
            total += len((root / u).read_text(encoding="utf-8", errors="replace").splitlines())
        except Exception:
            pass
    # Return a deterministic, sorted file list so changed_files / production_files_changed
    # / diff-hash inputs / scorecards / RAILS labels never vary with diff/untracked discovery order.
    return sorted(files.keys()), total, _diff_hash(root, untracked)


def _load_latest_result(root: Path, session_id: str) -> dict | None:
    try:
        return json.loads(
            paths.latest_verifier_result_path(session_id, root).read_text(encoding="utf-8"))
    except Exception:
        return None


def _resolve_result(root: Path, session_id: str, *, current_diff_hash: str,
                    current_turn: int) -> dict:
    """Derive the result-based signal fields from this session's stored latest result.
    Session-keyed: a session reads only the evidence it produced (mirrors the pack tree)."""
    stored = _load_latest_result(root, session_id)
    if not stored:
        return {"last_verifier_status": "unknown", "last_verifier_turn": None,
                "turns_since_last_pass": current_turn, "unresolved_failures": 0,
                "verification_coverage": "none", "evidence_is_fresh": False}
    status = stored.get("status", "unknown")
    vturn = stored.get("verifier_turn")
    fresh = (stored.get("diff_hash") == current_diff_hash) and (status == "pass")
    turns_since = (current_turn - vturn) if (status == "pass" and vturn is not None) else current_turn
    return {
        "last_verifier_status": status,
        "last_verifier_turn": vturn,
        "turns_since_last_pass": max(0, turns_since),
        "unresolved_failures": 1 if status == "fail" else 0,
        "verification_coverage": stored.get("coverage", "none") if fresh else "none",
        "evidence_is_fresh": fresh,
    }


@dataclass(frozen=True)
class VerificationDebtSignals:
    session_id: str
    changed_files: tuple[str, ...]
    changed_file_count: int
    changed_lines: int
    production_files_changed: tuple[str, ...]
    test_files_changed: tuple[str, ...]
    config_files_changed: tuple[str, ...]
    production_without_tests: bool
    config_mixed_with_source: bool
    turns_since_last_pass: int
    last_verifier_status: str
    last_verifier_turn: int | None
    unresolved_failures: int
    completion_claim_without_evidence: bool
    verification_coverage: str
    evidence_is_fresh: bool
    current_diff_hash: str


def _fallback_changes(events) -> tuple[list[str], int]:
    """Git unavailable: changed set from Mindlas edit/write events (fallback path)."""
    files, lines = {}, 0
    for e in events:
        if e.kind == EventKind.TOOL_CALL and e.cls in ("edit", "write") and e.target:
            files[e.target] = True
            lines += (e.lines_added or 0) + (e.lines_deleted or 0)
    return sorted(files.keys()), lines   # deterministic order on the fallback path too


def build_verification_debt_signals(events, *, session_id=None, now_turn=None,
                                    project_root=None) -> VerificationDebtSignals:
    events = list(events or [])
    sid = session_id or (events[0].session_id if events else "session")
    turn = now_turn if now_turn is not None else max((e.turn for e in events), default=0)
    root = project_root if project_root is not None else paths.project_root()

    git = _git_changes(Path(root))
    if git is not None:
        changed_files, changed_lines, diff_hash = git
    else:
        changed_files, changed_lines = _fallback_changes(events)
        diff_hash = "sha256:" + hashlib.sha256(
            ("\0".join(sorted(changed_files))).encode("utf-8", "replace")).hexdigest()

    prod = tuple(f for f in changed_files if classify_path(f) == "production")
    tests = tuple(f for f in changed_files if classify_path(f) == "test")
    cfg = tuple(f for f in changed_files if classify_path(f) == "config")

    res = _resolve_result(Path(root), sid, current_diff_hash=diff_hash, current_turn=turn)
    last_verifier_turn = res["last_verifier_turn"]
    evidence_is_fresh = res["evidence_is_fresh"]
    # Reuse the upstream "done_claim" marker (no text scan), but count only claims on
    # turns AFTER the stored verifier's turn — NOT build_session_state, whose count is floored at
    # the legacy `allow` verdict and so is decoupled from latest_verifier_result.json. Check kind
    # first so .markers is only read on assistant_msg events (mirrors vitals/state.py).
    claim_without_evidence = any(
        e.kind == EventKind.ASSISTANT_MSG and Marker.DONE_CLAIM in e.markers
        and e.turn > (last_verifier_turn or 0)
        for e in events
    ) and not evidence_is_fresh

    return VerificationDebtSignals(
        session_id=sid,
        changed_files=tuple(changed_files),
        changed_file_count=len(changed_files),
        changed_lines=changed_lines,
        production_files_changed=prod,
        test_files_changed=tests,
        config_files_changed=cfg,
        production_without_tests=bool(prod) and not tests,
        config_mixed_with_source=bool(cfg) and bool(prod),
        turns_since_last_pass=res["turns_since_last_pass"],
        last_verifier_status=res["last_verifier_status"],
        last_verifier_turn=res["last_verifier_turn"],
        unresolved_failures=res["unresolved_failures"],
        completion_claim_without_evidence=claim_without_evidence,
        verification_coverage=res["verification_coverage"],
        evidence_is_fresh=evidence_is_fresh,
        current_diff_hash=diff_hash,
    )


def demo_verification_debt_alert_signals() -> VerificationDebtSignals:
    """A deterministic ALERT-grade reading for `vitals --demo verification_debt_alert`
    (git-independent, so the demo is reproducible anywhere)."""
    prod = ("src/app/payments.py", "src/app/orders.py", "src/app/users.py",
            "src/app/api.py", "src/app/db.py")
    return VerificationDebtSignals(
        session_id="demo", changed_files=prod, changed_file_count=len(prod), changed_lines=312,
        production_files_changed=prod, test_files_changed=(), config_files_changed=(),
        production_without_tests=True, config_mixed_with_source=False,
        turns_since_last_pass=7, last_verifier_status="unknown", last_verifier_turn=None,
        unresolved_failures=0, completion_claim_without_evidence=True,
        verification_coverage="none", evidence_is_fresh=False, current_diff_hash="sha256:demo",
    )
