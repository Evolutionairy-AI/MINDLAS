from mindlas.cli import main
from mindlas.vitals.capture import tool_failure_event, tool_event
from mindlas.vitals.events import Event


def _fail(turn, cmd="pytest -q", msg="Command timed out after 600s", tool="Bash"):
    return tool_failure_event("s", turn, f"t{turn}", tool, {"command": cmd},
                              {"type": "timeout", "message": msg})


def _loop_events():
    return [Event("s", 1, "t1", "user_prompt", target="Fix the failing tests"),
            _fail(2), _fail(3), _fail(4)]


def _seed_loop(monkeypatch, tmp_path):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr("mindlas.cli._latest_events", lambda: _loop_events())


def test_loop_status_prints_gauge(tmp_path, monkeypatch, capsys):
    _seed_loop(monkeypatch, tmp_path)
    assert main(["loop", "status"]) == 0
    out = capsys.readouterr().out
    assert "LOOP" in out and "ALERT" in out


def test_loop_stop_preview_writes_nothing(tmp_path, monkeypatch, capsys):
    _seed_loop(monkeypatch, tmp_path)
    assert main(["loop", "stop", "--preview"]) == 0
    out = capsys.readouterr().out
    assert "preview" in out.lower() and "no source files" in out.lower()
    assert not (tmp_path / ".mindlas" / "stops").exists()


def test_loop_stop_preview_clean_shows_no_fake_win(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr("mindlas.cli._latest_events",
                        lambda: [tool_event("s", 1, "t1", "Bash", {"command": "ls"})])
    assert main(["loop", "stop", "--preview"]) == 0
    out = capsys.readouterr().out
    assert "no repeated tool-failure loop" in out.lower()   # honest reason, not a reduction line
    assert "controlled" not in out.lower()                  # no fabricated win
    assert not (tmp_path / ".mindlas" / "stops").exists()


def test_loop_stop_apply_then_latest(tmp_path, monkeypatch, capsys):
    _seed_loop(monkeypatch, tmp_path)
    assert main(["loop", "stop", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "controlled" in out.lower() and "do not retry" in out.lower()
    assert "stop_card.md" in out          # §11.4: the human-facing CARD path is surfaced, not the manifest
    assert (tmp_path / ".mindlas" / "stops" / "sessions" / "s" / "active_stop.json").exists()
    assert main(["loop", "latest"]) == 0
    assert "Stop ID" in capsys.readouterr().out


def test_loop_latest_without_stop_is_graceful(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr("mindlas.cli._latest_events", lambda: [])
    assert main(["loop", "latest"]) == 0
    assert "No Stop" in capsys.readouterr().out
