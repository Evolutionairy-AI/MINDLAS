import json
import pytest
from mindlas.vitals import config
from mindlas.vitals.events import Event
from mindlas.vitals.ledger import Ledger
from mindlas import cli


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path))
    sid = "sess1"
    led = Ledger(config.ledger_path(sid))
    for i in range(5):
        led.append(Event(session_id=sid, turn=i + 1, ts=f"t{i+1}", kind="tool_call",
                         tool="Edit", target=f"m{i}.py", cls="edit", lines_added=70))
    return sid


def test_verify_changed_clean_records_allow_verdict(session, monkeypatch):
    sid = session
    # make verify see a CLEAN tree (lazy import in _cmd_verify picks up these patches)
    monkeypatch.setattr("mindlas.vitals.verify.verify", lambda paths, cwd: [])
    monkeypatch.setattr("mindlas.vitals.verify.changed_files", lambda events, **k: ["m0.py"])

    rc = cli.main(["verify", "--changed", "--session", sid])
    assert rc == 0

    verdicts = [json.loads(l) for l in
                config.verdict_ledger_path(sid).read_text(encoding="utf-8").splitlines() if l.strip()]
    assert any(v["decision"] == "allow" for v in verdicts)


def test_verify_changed_dirty_reports_defect(session, monkeypatch):
    sid = session
    from mindlas.vitals.verify import Diagnostic
    monkeypatch.setattr("mindlas.vitals.verify.changed_files", lambda events, **k: ["m0.py"])
    monkeypatch.setattr("mindlas.vitals.verify.verify",
                        lambda paths, cwd: [Diagnostic(file="m0.py", line=3, code="F821",
                                                       message="undefined name 'x'")])
    rc = cli.main(["verify", "--changed", "--session", sid])
    assert rc == 1   # non-zero when an introduced defect is found
