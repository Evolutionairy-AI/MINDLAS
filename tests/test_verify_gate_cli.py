from mindlas.cli import main
from mindlas.vitals.events import Event
from _verify_helpers import init_git_repo, write


def test_verify_status_runs(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    rc = main(["verify", "status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "VERIFY" in out


def test_verify_changed_back_compat_still_dispatches_legacy(capsys, tmp_path, monkeypatch):
    # bare `verify --changed` (no positional) must still reach the legacy reset path.
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    rc = main(["verify", "--changed"])
    capsys.readouterr()
    # No sessions in a clean temp home -> the legacy handler prints its no-sessions line, rc 0.
    assert rc == 0


def test_verify_demo_alert_reading():
    # The VERIFY demo signals must read ALERT via the canonical scorer.
    from mindlas.features.verification_debt import VerificationDebtScorer
    from mindlas.runtime.verification_state import demo_verification_debt_alert_signals
    r = VerificationDebtScorer().read(demo_verification_debt_alert_signals())
    assert r.short_label == "VERIFY" and r.state == "ALERT"


def test_mindlas_status_lights_verify(capsys, tmp_path, monkeypatch):
    # `mindlas status` must show a live VERIFY value, not the 'VERIFY --' placeholder.
    from mindlas.vitals import fixtures
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))   # non-git -> clean fallback
    monkeypatch.setattr("mindlas.cli._load_events", lambda args: fixtures.verification_debt())
    rc = main(["status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Verify" in out                 # the verify gauge column is present
    assert "--" not in out                 # lit live, not the placeholder marker



def test_plan1_commands_write_no_verification_artifacts(tmp_path, monkeypatch, capsys):
    # The read commands are read-only w.r.t. .mindlas/verification/. Writing latest_verifier_result.json
    # and results/*.json is the Verify Gate action's job alone — this pins that boundary.
    from mindlas.runtime.paths import verification_dir
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))   # non-git -> clean fallback
    for argv in (["verify", "status"],
                 ["status"]):
        assert main(argv) == 0
        capsys.readouterr()
    # Plan 1 (status/preview) is read-only w.r.t. .mindlas/verification/ — writing the session-keyed
    # result + results/ is Plan 2 (the Verify Gate action). Whole tree stays absent.
    assert not verification_dir(tmp_path).exists()


def _seed(monkeypatch, tmp_path):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    init_git_repo(tmp_path)
    write(tmp_path, "pyproject.toml", "[tool.x]\n")
    write(tmp_path, "src/app.py", "a = 1\n")
    ev = [Event(session_id="s", turn=1, ts="t", kind="tool_call", cls="edit", target="src/app.py")]
    monkeypatch.setattr("mindlas.cli._latest_events", lambda: ev)


def test_verify_gate_preview_writes_nothing(tmp_path, monkeypatch, capsys):
    _seed(monkeypatch, tmp_path)
    assert main(["verify", "gate", "--preview"]) == 0
    out = capsys.readouterr().out
    assert "plan" in out.lower()
    assert not (tmp_path / ".mindlas" / "verification").exists()


def test_verify_gate_apply_writes_artifacts_then_latest(tmp_path, monkeypatch, capsys):
    _seed(monkeypatch, tmp_path)
    assert main(["verify", "gate", "--apply"]) == 0
    assert (tmp_path / ".mindlas" / "verification" / "sessions" / "s" / "latest_verifier_result.json").exists()
    assert (tmp_path / ".mindlas" / "reports" / "latest_scorecard.json").exists()
    capsys.readouterr()
    assert main(["verify", "latest"]) == 0
    assert "VERIFY latest" in capsys.readouterr().out


def test_verify_latest_without_result_is_graceful(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    assert main(["verify", "latest"]) == 0
    assert "No verifier result" in capsys.readouterr().out
