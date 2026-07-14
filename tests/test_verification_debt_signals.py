from mindlas.runtime.verification_state import classify_path


def test_classify_path():
    assert classify_path("src/mindlas/cli.py") == "production"
    assert classify_path("tests/test_cli.py") == "test"
    assert classify_path("src/pkg/foo_test.py") == "test"
    assert classify_path("frontend/Button.tsx") == "production"
    assert classify_path("frontend/Button.test.tsx") == "test"
    assert classify_path("pyproject.toml") == "config"
    assert classify_path("setup.cfg") == "config"
    assert classify_path("README.md") == "neutral"
    assert classify_path("data/snapshot.json") == "neutral"
    # path containing a verb-like segment is still production, never misclassified
    assert classify_path("src/address.py") == "production"


from pathlib import Path
from mindlas.runtime.verification_state import _git_changes, _diff_hash
from _verify_helpers import init_git_repo, write   # tests/ is NOT a package (no
#   tests/__init__.py); pytest's default prepend import mode puts the test dir on sys.path,
#   so the helper imports as a top-level module. `from tests._verify_helpers` would ImportError.


def test_git_changes_counts_tracked_edits_and_untracked_files(tmp_path):
    init_git_repo(tmp_path)
    write(tmp_path, "baseline.txt", "seed\nmore\n")          # tracked edit: +1 line
    write(tmp_path, "src/new_mod.py", "a = 1\nb = 2\nc = 3\n")  # untracked: 3 lines
    files, lines, dh = _git_changes(tmp_path)
    assert "baseline.txt" in files
    assert "src/new_mod.py" in files                         # untracked file is captured
    assert lines >= 4                                        # 1 tracked + 3 untracked
    assert dh.startswith("sha256:")


def test_diff_hash_changes_when_untracked_content_changes(tmp_path):
    init_git_repo(tmp_path)
    write(tmp_path, "src/new_mod.py", "a = 1\n")
    h1 = _diff_hash(tmp_path, ["src/new_mod.py"])
    write(tmp_path, "src/new_mod.py", "a = 2\n")
    h2 = _diff_hash(tmp_path, ["src/new_mod.py"])
    assert h1 != h2                                          # untracked content is in the hash


def test_git_changes_returns_none_outside_a_repo(tmp_path):
    assert _git_changes(tmp_path) is None                    # not a git repo -> None (fallback path)


import json
from mindlas.runtime.verification_state import _resolve_result


from mindlas.runtime import paths as _paths


def _write_result(root: Path, session_id: str = "s", **kw):
    # session-keyed (mirrors the pack tree): a session reads only the evidence it produced.
    p = _paths.latest_verifier_result_path(session_id, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(kw), encoding="utf-8")


def test_resolve_result_missing_is_unknown_and_stale(tmp_path):
    r = _resolve_result(tmp_path, "s", current_diff_hash="sha256:abc", current_turn=10)
    assert r == {"last_verifier_status": "unknown", "last_verifier_turn": None,
                 "turns_since_last_pass": 10, "unresolved_failures": 0,
                 "verification_coverage": "none", "evidence_is_fresh": False}


def test_resolve_result_fresh_pass(tmp_path):
    _write_result(tmp_path, status="pass", coverage="targeted",
                  diff_hash="sha256:abc", verifier_turn=6)
    r = _resolve_result(tmp_path, "s", current_diff_hash="sha256:abc", current_turn=9)
    assert r["evidence_is_fresh"] is True
    assert r["last_verifier_status"] == "pass"
    assert r["turns_since_last_pass"] == 3           # 9 - 6
    assert r["verification_coverage"] == "targeted"
    assert r["unresolved_failures"] == 0


def test_resolve_result_stale_pass_does_not_clear_debt(tmp_path):
    _write_result(tmp_path, status="pass", coverage="full",
                  diff_hash="sha256:OLD", verifier_turn=6)
    r = _resolve_result(tmp_path, "s", current_diff_hash="sha256:NEW", current_turn=9)
    assert r["evidence_is_fresh"] is False           # hash mismatch -> stale
    assert r["verification_coverage"] == "none"      # coverage credit withheld when stale


def test_resolve_result_fail_is_unresolved(tmp_path):
    _write_result(tmp_path, status="fail", coverage="targeted",
                  diff_hash="sha256:abc", verifier_turn=6)
    r = _resolve_result(tmp_path, "s", current_diff_hash="sha256:abc", current_turn=9)
    assert r["last_verifier_status"] == "fail"
    assert r["unresolved_failures"] == 1             # fail -> unresolved
    assert r["evidence_is_fresh"] is False


from mindlas.vitals.events import Event
from mindlas.runtime.verification_state import (
    build_verification_debt_signals, demo_verification_debt_alert_signals,
)


def _edit(turn, target, added=10):
    return Event(session_id="s", turn=turn, ts="t", kind="tool_call", cls="edit",
                 target=target, lines_added=added, lines_deleted=0)


def test_build_signals_over_temp_repo_classifies_and_flags(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    init_git_repo(tmp_path)
    write(tmp_path, "src/app.py", "x = 1\n")                # untracked production, no test
    events = [_edit(1, "src/app.py")]
    sig = build_verification_debt_signals(events, session_id="s", now_turn=3,
                                          project_root=tmp_path)
    assert "src/app.py" in sig.changed_files
    assert sig.production_files_changed == ("src/app.py",)
    assert sig.test_files_changed == ()
    assert sig.production_without_tests is True
    assert sig.last_verifier_status == "unknown"
    assert sig.evidence_is_fresh is False
    assert sig.current_diff_hash.startswith("sha256:")


def test_build_signals_completion_claim_from_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    init_git_repo(tmp_path)
    write(tmp_path, "src/app.py", "x = 1\n")
    events = [_edit(1, "src/app.py"),
              Event(session_id="s", turn=2, ts="t", kind="assistant_msg",
                    markers=("done_claim",))]
    sig = build_verification_debt_signals(events, session_id="s", now_turn=2,
                                          project_root=tmp_path)
    assert sig.completion_claim_without_evidence is True   # claim present, no fresh evidence


def test_claim_before_last_verifier_turn_is_not_flagged(tmp_path, monkeypatch):
    # A done_claim that PREDATES the stored verifier turn was covered by that run, so it
    # is not "without evidence" — even though the working tree has since drifted (stale hash).
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    init_git_repo(tmp_path)
    write(tmp_path, "src/app.py", "x = 1\n")
    _write_result(tmp_path, status="pass", coverage="full",
                  diff_hash="sha256:OLD", verifier_turn=5)        # verifier ran at turn 5
    events = [_edit(1, "src/app.py"),
              Event(session_id="s", turn=3, ts="t", kind="assistant_msg", markers=("done_claim",))]
    sig = build_verification_debt_signals(events, session_id="s", now_turn=6,
                                          project_root=tmp_path)
    assert sig.completion_claim_without_evidence is False   # claim at turn 3 <= last_verifier_turn 5


def test_claim_after_last_verifier_turn_with_stale_evidence_is_flagged(tmp_path, monkeypatch):
    # A done_claim AFTER the stored verifier turn, with no fresh evidence, IS flagged.
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    init_git_repo(tmp_path)
    write(tmp_path, "src/app.py", "x = 1\n")
    _write_result(tmp_path, status="pass", coverage="full",
                  diff_hash="sha256:OLD", verifier_turn=5)        # stale (hash mismatch)
    events = [_edit(6, "src/app.py"),
              Event(session_id="s", turn=7, ts="t", kind="assistant_msg", markers=("done_claim",))]
    sig = build_verification_debt_signals(events, session_id="s", now_turn=7,
                                          project_root=tmp_path)
    assert sig.completion_claim_without_evidence is True    # claim at turn 7 > last_verifier_turn 5


def test_verifier_result_is_session_keyed_not_shared(tmp_path, monkeypatch):
    # Session-keying: a verify result written for session A is evidence for A ONLY. A concurrent
    # session B on the same repo/diff does not inherit it — B's own subtree is empty, so B reads
    # 'unknown'/stale and must prove its own work. This is the collision fix (mirrors the pack tree).
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    init_git_repo(tmp_path)
    write(tmp_path, "src/app.py", "a = 1\n")
    _files, _lines, dh = _git_changes(tmp_path)          # the live tree's diff hash
    _write_result(tmp_path, session_id="A", status="pass", coverage="full",
                  diff_hash=dh, verifier_turn=1)
    a = build_verification_debt_signals([], session_id="A", now_turn=2, project_root=tmp_path)
    b = build_verification_debt_signals([], session_id="B", now_turn=2, project_root=tmp_path)
    assert a.evidence_is_fresh is True and a.last_verifier_status == "pass"       # A sees its pass
    assert b.evidence_is_fresh is False and b.last_verifier_status == "unknown"   # B does NOT inherit


def test_build_signals_clean_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    init_git_repo(tmp_path)
    sig = build_verification_debt_signals([], session_id="s", now_turn=0, project_root=tmp_path)
    assert sig.changed_file_count == 0 and sig.changed_lines == 0


def test_demo_signals_are_alert_grade():
    sig = demo_verification_debt_alert_signals()
    assert sig.changed_file_count >= 1
    assert sig.production_without_tests is True
    assert sig.last_verifier_status in ("unknown", "fail")


def test_mindlas_state_excluded_from_change_detection(tmp_path):
    from mindlas.runtime.verification_state import _git_changes
    init_git_repo(tmp_path)
    write(tmp_path, "src/app.py", "a = 1\n")
    files1, _lines1, h1 = _git_changes(tmp_path)
    # Mindlas writes its own state under .mindlas/ — it must never count as an agent change.
    write(tmp_path, ".mindlas/verification/latest_verifier_result.json", '{"x": 1}')
    write(tmp_path, ".mindlas/reports/corrections.jsonl", '{"type": "verify_gate"}\n')
    files2, _lines2, h2 = _git_changes(tmp_path)
    assert not any(f.replace("\\", "/").startswith(".mindlas/") for f in files2)
    assert h1 == h2          # .mindlas writes do not move the diff hash (freshness stays intact)
