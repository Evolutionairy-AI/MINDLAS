import json
from mindlas.actions.verify_gate import VerifyGate
from mindlas.runtime.verify_types import VerifyContext
from mindlas.vitals.events import Event
from _verify_helpers import init_git_repo, write


def _edit(turn, target):
    return Event(session_id="s", turn=turn, ts="t", kind="tool_call", cls="edit",
                 target=target, lines_added=8, lines_deleted=0)


def test_preview_plans_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    init_git_repo(tmp_path)
    write(tmp_path, "pyproject.toml", "[tool.x]\n")
    write(tmp_path, "src/app.py", "x = 1\n")
    ctx = VerifyContext(events=(_edit(1, "src/app.py"),), session_id="s", now_turn=2,
                        project_root=tmp_path)
    pv = VerifyGate().preview(ctx)
    assert pv.status == "planned"
    assert any(c.kind == "in_process" for c in pv.commands)        # L1 AST planned
    assert pv.before >= 0 and isinstance(pv.trigger, bool)
    assert not (tmp_path / ".mindlas" / "verification").exists()   # preview writes nothing


def test_preview_skipped_when_nothing_plannable(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    init_git_repo(tmp_path)                       # no pyproject + non-.py change -> nothing to plan
    write(tmp_path, "notes.txt", "hello\n")
    ctx = VerifyContext(events=(), session_id="s", now_turn=0, project_root=tmp_path)
    pv = VerifyGate().preview(ctx)
    assert pv.status == "skipped" and pv.commands == ()


def test_apply_pass_reduces_verify_and_writes_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))      # A9: ctx.root == env root
    init_git_repo(tmp_path)
    write(tmp_path, "pyproject.toml", "[tool.x]\n")
    write(tmp_path, "src/app.py", "a = 1\nb = 2\n")               # clean -> AST passes
    ctx = VerifyContext(events=(_edit(1, "src/app.py"),), session_id="s", now_turn=4,
                        project_root=tmp_path)
    vr = VerifyGate().apply(ctx, now="20260630T120000")
    assert vr.status == "pass" and vr.applied is True
    assert vr.after < vr.before and vr.after <= 35                 # capped (partial, evidence-based)
    latest = json.loads((tmp_path / ".mindlas" / "verification" / "sessions" / "s"
                         / "latest_verifier_result.json").read_text())
    assert latest["verifier_turn"] == 4 and latest["diff_hash"].startswith("sha256:")
    assert latest["status"] == "pass"
    assert latest["state"] == "complete"                          # amendment 3: finalized result
    assert "before" in latest and "after" in latest              # step 6b rewrote the full payload
    assert list((tmp_path / ".mindlas" / "verification" / "sessions" / "s" / "results").glob("*_verify_gate.json"))
    sc = (tmp_path / ".mindlas" / "reports" / "latest_scorecard.json")
    assert json.loads(sc.read_text())["features"]["verification_debt"]["last_result"] == "pass"


def test_apply_fail_drives_alert_and_records_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    init_git_repo(tmp_path)
    write(tmp_path, "pyproject.toml", "[tool.x]\n")
    write(tmp_path, "src/broken.py", "def f(:\n")                 # syntax error -> AST fails
    ctx = VerifyContext(events=(_edit(1, "src/broken.py"),), session_id="s", now_turn=3,
                        project_root=tmp_path)
    vr = VerifyGate().apply(ctx, now="20260630T120100")
    assert vr.status == "fail" and vr.after >= 95
    corrections = (tmp_path / ".mindlas" / "reports" / "corrections.jsonl").read_text().splitlines()
    rec = json.loads(corrections[-1])
    assert rec["type"] == "verify_gate" and rec["status"] == "fail"
    assert rec["rails_labels"]["verify_gate_status"] == "fail"


def test_apply_leaves_no_bytecode_byproducts(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    init_git_repo(tmp_path)
    write(tmp_path, "pyproject.toml", "[tool.x]\n")
    write(tmp_path, "src/app.py", "a = 1\n")
    ctx = VerifyContext(events=(_edit(1, "src/app.py"),), session_id="s", now_turn=2,
                        project_root=tmp_path)
    VerifyGate().apply(ctx, now="20260630T120200")
    assert list((tmp_path / "src").rglob("__pycache__")) == []
    assert list(tmp_path.rglob("*.pyc")) == []
    assert not (tmp_path / ".pytest_cache").exists()


def test_apply_skipped_keeps_debt_high(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    # No pyproject AND no changed .py -> nothing is plannable (under amendment 5 a changed .py would
    # plan the config-free AST tier, so a genuine skip needs a non-.py change).
    init_git_repo(tmp_path)
    write(tmp_path, "notes.txt", "hello\n")       # non-.py change -> not even the L1 AST tier applies
    ctx = VerifyContext(events=(_edit(1, "notes.txt"),), session_id="s", now_turn=2,
                        project_root=tmp_path)
    vr = VerifyGate().apply(ctx, now="20260630T120300")
    assert vr.status == "skipped" and vr.after >= 70
