import shutil
import pytest

from mindlas import cli


def test_context_repair_preview_demo(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    rc = cli.main(["context", "repair", "--preview", "--demo", "context_rot_alert"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "# Mindlas Context Repair Pack" in out
    assert "Validation: pass" in out
    assert not (tmp_path / ".mindlas").exists()       # preview writes nothing


@pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not installed")
def test_context_repair_apply_verify_demo_passes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    rc = cli.main(["context", "repair", "--apply", "--demo", "context_rot_alert", "--verify"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Downstream check: pass" in out
    assert "demo sample" in out
    import json
    from mindlas.runtime import paths
    scj = json.loads(paths.scorecard_json_path().read_text(encoding="utf-8"))
    assert scj["corrections"][-1]["downstream"]["result"] == "pass"


def test_context_repair_apply_accept_records_decision(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    rc = cli.main(["context", "repair", "--apply", "--demo", "context_rot_alert", "--accept"])
    assert rc == 0
    import json
    from mindlas.runtime import paths
    rec = json.loads(
        paths.corrections_path().read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["human_decision"] == "accepted"


def test_context_repair_apply_reject_records_decision(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    rc = cli.main(["context", "repair", "--apply", "--demo", "context_rot_alert", "--reject"])
    assert rc == 0
    import json
    from mindlas.runtime import paths
    rec = json.loads(
        paths.corrections_path().read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["human_decision"] == "rejected"


def test_context_repair_accept_reject_conflict_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    with pytest.raises(SystemExit):
        cli.main(["context", "repair", "--apply", "--demo", "context_rot_alert",
                  "--accept", "--reject"])


def test_context_repair_apply_demo_then_scorecard(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    rc = cli.main(["context", "repair", "--apply", "--demo", "context_rot_alert"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Context Repair applied." in out
    assert "Rot (modeled post-repair):" in out and "→" in out
    # pack now lives under the session-keyed subtree (demo fixture SID = "demo")
    assert (tmp_path / ".mindlas" / "context" / "sessions" / "demo" / "latest.md").exists()

    rc = cli.main(["scorecard", "--latest", "--json"])
    j = capsys.readouterr().out
    # A Context-Repair-only session emits no RAILS label payload, so it must NOT read "ready"
    # (rails_export_ready keys off label presence, not off "a scorecard JSON was written").
    assert '"context_rot"' in j and '"rails_export_ready": false' in j


def test_resolve_session_prefers_explicit_then_pointer_over_mtime(tmp_path, monkeypatch):
    """Kills the mtime guess: an explicit --session, then the hook-written live-session pointer,
    each beat newest-by-mtime; with neither, it falls back to the old mtime heuristic."""
    import json
    import os
    from types import SimpleNamespace
    from mindlas.vitals.config import ledger_path
    from mindlas.vitals.ledger import Ledger
    from mindlas.vitals.events import Event
    from mindlas.runtime import paths
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path / "proj"))
    Ledger(ledger_path("old")).append(Event("old", 1, "t1", "user_prompt", target="old task"))
    Ledger(ledger_path("new")).append(Event("new", 1, "t2", "user_prompt", target="new task"))
    os.utime(ledger_path("old"), (1000, 1000))   # deterministic mtime order: "new" is newest
    os.utime(ledger_path("new"), (2000, 2000))

    # explicit --session wins even though "new" is newest by mtime
    assert cli._resolve_session_events(SimpleNamespace(session="old"))[0].session_id == "old"

    # the live-session pointer wins over mtime when there's no --session
    paths.current_session_path().parent.mkdir(parents=True, exist_ok=True)
    paths.current_session_path().write_text(json.dumps({"session_id": "old"}), encoding="utf-8")
    assert cli._resolve_session_events(SimpleNamespace(session=None))[0].session_id == "old"

    # neither present -> legacy newest-by-mtime fallback (no regression)
    paths.current_session_path().unlink()
    assert cli._resolve_session_events(SimpleNamespace(session=None))[0].session_id == "new"

    # an unresolved placeholder (no such ledger) degrades cleanly rather than erroring
    assert cli._resolve_session_events(
        SimpleNamespace(session="${CLAUDE_SESSION_ID}"))[0].session_id == "new"
