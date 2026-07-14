from pathlib import Path
from mindlas.runtime import paths


def test_paths_rooted_at_project_root_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    assert paths.mindlas_dir() == tmp_path / ".mindlas"
    assert paths.session_events_path() == tmp_path / ".mindlas" / "ledger" / "session_events.jsonl"
    # Pack artifacts are keyed by the session uuid (kills the newest-by-mtime guess).
    ctx = tmp_path / ".mindlas" / "context"
    assert paths.packs_dir("sess-abc") == ctx / "sessions" / "sess-abc" / "packs"
    assert paths.evidence_dir("sess-abc") == ctx / "sessions" / "sess-abc" / "evidence"
    assert paths.latest_pack_path("sess-abc") == ctx / "sessions" / "sess-abc" / "latest.md"
    assert paths.pack_snapshot_path("sess-abc", "20260628T120000") == \
        ctx / "sessions" / "sess-abc" / "packs" / "20260628T120000_context_repair.md"
    assert paths.evidence_snapshot_path("sess-abc", "20260628T120000") == \
        ctx / "sessions" / "sess-abc" / "evidence" / "20260628T120000_evidence.json"
    assert paths.draft_pack_path("sess-abc", "20260628T120000") == \
        ctx / "sessions" / "sess-abc" / "packs" / "20260628T120000_draft.md"
    # The reseed flag is session-keyed — pulled by the session's own id (same id across /clear).
    assert paths.pending_resume_path("sess-abc") == ctx / "sessions" / "sess-abc" / "pending_resume.json"
    assert paths.after_pending_path("sess-abc") == ctx / "sessions" / "sess-abc" / "after_pending.json"
    # Only the live-session pointer stays project-level (the who-am-I fallback for a caller with no id).
    assert paths.current_session_path() == tmp_path / ".mindlas" / "current_session.json"
    assert paths.scorecard_md_path() == tmp_path / ".mindlas" / "reports" / "latest_scorecard.md"


def test_session_id_is_sanitized_into_a_safe_path_segment(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    # unsafe characters collapse to underscores so a raw session id can't escape the subtree
    d = paths.context_session_dir("a/b:c*d")
    assert d.name == "a_b_c_d"
    assert d.parent == tmp_path / ".mindlas" / "context" / "sessions"


def test_project_root_defaults_to_cwd(monkeypatch):
    monkeypatch.delenv("MINDLAS_PROJECT_ROOT", raising=False)
    assert paths.project_root() == Path.cwd()


def test_verification_path_helpers_bare_and_explicit_root(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    assert paths.verification_dir() == tmp_path / ".mindlas" / "verification"
    # session-keyed: <verification>/sessions/<sid>/... (mirrors the context pack tree)
    vsess = tmp_path / ".mindlas" / "verification" / "sessions" / "s"
    assert paths.latest_verifier_result_path("s") == vsess / "latest_verifier_result.json"
    assert paths.verify_results_dir("s") == vsess / "results"
    assert paths.verifier_result_snapshot_path("s", "20260701T120000") == \
        vsess / "results" / "20260701T120000_verify_gate.json"

    other = tmp_path / "elsewhere"
    assert paths.verification_dir(other) == other / ".mindlas" / "verification"
    ovsess = other / ".mindlas" / "verification" / "sessions" / "s"
    assert paths.latest_verifier_result_path("s", other) == ovsess / "latest_verifier_result.json"
    assert paths.verify_results_dir("s", other) == ovsess / "results"


def test_split_path_helpers_bare_and_explicit_root(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    assert paths.splits_dir() == tmp_path / ".mindlas" / "splits"
    # session-keyed: <splits>/sessions/<sid>/... (mirrors the verification tree)
    ssess = tmp_path / ".mindlas" / "splits" / "sessions" / "s"
    assert paths.split_run_dir("s", "20260630T101500_ab12") == ssess / "20260630T101500_ab12"
    assert paths.latest_split_manifest_path("s") == ssess / "latest_split_manifest.json"

    other = tmp_path / "elsewhere"
    assert paths.splits_dir(other) == other / ".mindlas" / "splits"
    osess = other / ".mindlas" / "splits" / "sessions" / "s"
    assert paths.split_run_dir("s", "sid", other) == osess / "sid"
    assert paths.latest_split_manifest_path("s", other) == osess / "latest_split_manifest.json"


def test_stops_paths_use_explicit_root(tmp_path):
    root = Path(tmp_path)
    assert paths.stops_dir(root) == root / ".mindlas" / "stops"
    # session-keyed: <stops>/sessions/<sid>/... (a Stop guards only the session that looped)
    ssess = root / ".mindlas" / "stops" / "sessions" / "s"
    assert paths.stop_run_dir("s", "20260701T120000_8f12", root) == ssess / "20260701T120000_8f12"
    assert paths.latest_stop_path("s", root) == ssess / "latest_stop.json"
    assert paths.active_stop_path("s", root) == ssess / "active_stop.json"


def test_stops_paths_default_to_project_root(monkeypatch, tmp_path):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    assert paths.stops_dir() == Path(tmp_path) / ".mindlas" / "stops"
