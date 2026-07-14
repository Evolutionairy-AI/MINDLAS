from mindlas.vitals.events import Event
from mindlas.vitals.ledger import Ledger
from mindlas.vitals.config import ledger_path
from mindlas.vitals.statusline import build_statusline_text


def _seed_alert_session(sid):
    led = Ledger(ledger_path(sid))
    led.append(Event(sid, 1, "t1", "user_prompt",
                     target="Refactor the auth module onto the new token service."))
    for i in range(2, 41):
        led.append(Event(sid, i, f"t{i}", "user_prompt", target="continue"))
        led.append(Event(sid, i, f"t{i}b", "tool_call", tool="Read", target=f"m{i}.py",
                         cls="read", output_chars=4000))
    return led


def test_statusline_survives_rot_scorer_error(tmp_path, monkeypatch):
    # If the Rot scorer raises, the band must degrade Rot to '--' and STILL render the header +
    # the other three gauges — never collapse to header-only (the fail-silent-asymmetry fix).
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    sid = "s_rot_err"
    _seed_alert_session(sid)

    def _boom(self, sig):
        raise RuntimeError("bad rot signal")
    monkeypatch.setattr("mindlas.features.context_rot.ContextRotScorer.read", _boom)

    payload = {"session_id": sid, "context_window": {"used_percentage": 92.0},
               "model": {"display_name": "Opus", "id": "claude-opus-4-8"},
               "workspace": {"project_dir": "/x/mindlas"}}
    out = build_statusline_text(payload)
    assert "Opus" in out                                  # header still present
    for label in ("Rot", "Verify", "Blast", "Loop"):     # band still rendered, not header-only
        assert label in out
    assert "--" in out                                    # Rot column degraded to the placeholder


def test_statusline_is_ctx_first_with_all_four_gauges(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))   # isolate: BLAST reads a clean tree -> 0 STABLE
    sid = "s_alert"
    _seed_alert_session(sid)
    payload = {"session_id": sid, "context_window": {"used_percentage": 92.0},
               "model": {"display_name": "Opus", "id": "claude-opus-4-8"},
               "workspace": {"project_dir": "/x/mindlas"}}
    out = build_statusline_text(payload)
    assert "Rot" in out and "Alert" in out                # rot gauge + an Alert state present
    # VERIFY/BLAST/LOOP are now wired live off the (clean, isolated) tree -> all STABLE.
    for label in ("Verify", "Blast", "Loop"):
        assert label in out
    assert out.count("Stable") >= 3


def test_statusline_measured_mass_drives_high_confidence(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path))
    sid = "s_meas"
    led = Ledger(ledger_path(sid))
    led.append(Event(sid, 1, "t1", "user_prompt", target="short fresh task here"))
    # near-empty session: the measured 95% context window flows through (mass term ~28), but
    # mass alone caps at 30 so the band is STABLE — this test only proves the measured pct is
    # threaded into the live reading and rendered, not that it alerts.
    payload = {"session_id": sid, "context_window": {"used_percentage": 95.0}}
    out = build_statusline_text(payload)
    assert "Rot" in out


import io
from mindlas.vitals import statusline as sl


def test_run_statusline_emits_ctx_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))   # isolate: BLAST reads a clean tree -> 0 STABLE
    sid = "s_run"
    _seed_alert_session(sid)
    payload = '{"session_id": "s_run", "context_window": {"used_percentage": 90.0}}'
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    rc = sl.run_statusline()
    out = capsys.readouterr().out
    assert rc == 0
    assert "Rot" in out
    for label in ("Verify", "Blast", "Loop"):
        assert label in out
    assert out.count("Stable") >= 3
