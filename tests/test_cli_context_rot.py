
from mindlas import cli


def test_status_demo_low_prints_ctx_first_line(capsys, tmp_path, monkeypatch):
    # BLAST reads the project_root git/working tree, so isolate it to a clean tmp dir — otherwise
    # the test would read (and flake on) the developer's dirty repo now that `status` wires BLAST live.
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    rc = cli.main(["status", "--demo", "context_rot_low"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Rot" in out                           # the reliability band leads with the Rot gauge
    assert "Blast" in out and "Loop" in out       # all four gauges are wired live now — no placeholders
    assert "Stable" in out                        # the low fixture has no tool failures -> Loop Stable


def test_status_shows_four_gauge_overview(capsys, tmp_path, monkeypatch):
    """`mindlas status` renders the one-line four-gauge overview (VERIFY/BLAST/LOOP live off a
    clean isolated tree = 0 STABLE), not the old `--` placeholders."""
    from mindlas.vitals.events import Event
    from mindlas.vitals.ledger import Ledger
    from mindlas.vitals.config import ledger_path
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))   # clean tree -> BLAST/VERIFY/LOOP 0 STABLE
    sid = "s_cli_live"
    led = Ledger(ledger_path(sid))
    led.append(Event(sid, 1, "t1", "user_prompt", target="short fresh task here"))
    led.append(Event(sid, 1, "t1b", "tool_call", tool="Read", target="m.py",
                     cls="read", output_chars=150))

    assert cli.main(["status", "--plain"]) == 0
    out = capsys.readouterr().out
    for label in ("Verify", "Blast", "Loop"):
        assert label in out
    assert out.count("Stable") >= 3               # Verify/Blast/Loop all read Stable


def test_context_status_shows_ctx_gauge_block(capsys, tmp_path, monkeypatch):
    """`mindlas context status` shows the DETAILED CTX gauge block (facts + pending resume),
    parallel to `verify/blast/loop status` — NOT the four-gauge overview (that's `mindlas status`)."""
    from mindlas.vitals.events import Event
    from mindlas.vitals.ledger import Ledger
    from mindlas.vitals.config import ledger_path
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    sid = "s_ctx_block"
    led = Ledger(ledger_path(sid))
    led.append(Event(sid, 1, "t1", "user_prompt", target="short fresh task here"))
    led.append(Event(sid, 1, "t1b", "tool_call", tool="Read", target="m.py",
                     cls="read", output_chars=150))

    assert cli.main(["context", "status", "--plain"]) == 0
    out = capsys.readouterr().out
    assert "ROT" in out                              # the Rot gauge block (short label)
    assert "turns in the session" in out             # a contributing fact, like the sibling gauges
    assert "Pending resume:" in out
    assert "VERIFY" not in out and "BLAST" not in out  # the four-gauge overview is `mindlas status`


def test_status_demo_alert_is_alert(capsys):
    cli.main(["status", "--demo", "context_rot_alert"])
    assert "Alert" in capsys.readouterr().out


def test_cli_ctx_reuses_measured_pct_from_statusline(tmp_path, monkeypatch):
    """The status line caches the measured window %; the CLI CTX reading must reuse it so the two
    surfaces AGREE, instead of the CLI proxying mass to ~100% and reading higher than the live line."""
    from mindlas.vitals.events import Event
    from mindlas.vitals.ledger import Ledger
    from mindlas.vitals.config import ledger_path, context_pct_path
    from mindlas.vitals.statusline import build_statusline_text
    from mindlas.runtime.state import build_context_rot_signals
    from mindlas.features.context_rot import ContextRotScorer
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    sid = "s_conv"
    led = Ledger(ledger_path(sid))
    led.append(Event(sid, 1, "t1", "user_prompt", target="Refactor the auth module today"))
    for i in range(2, 6):
        led.append(Event(sid, i, f"t{i}", "user_prompt", target="continue"))
        led.append(Event(sid, i, f"t{i}b", "tool_call", tool="Read", target=f"m{i}.py",
                         cls="read", output_chars=4000))
    ev = Ledger(ledger_path(sid)).events()

    proxied = cli._ctx_reading_from_events(ev).score          # no cache yet -> mass proxied to ceiling
    build_statusline_text({"session_id": sid, "context_window": {"used_percentage": 3.0}})
    assert context_pct_path(sid).exists()                     # status line cached the measured %

    measured = cli._ctx_reading_from_events(ev).score         # CLI now reuses that measured %
    assert measured < proxied                                 # low measured mass lowers the score
    now = max(e.turn for e in ev)
    expected = ContextRotScorer().read(
        build_context_rot_signals(ev, context_pct=3.0, now_turn=now)).score
    assert measured == expected                               # exactly the live line's CTX score


def test_repair_before_score_reuses_measured_pct(tmp_path, monkeypatch):
    """The Context Repair before-score must reuse the cached measured window % (like the status
    surfaces), so `mindlas context repair` reports a before that MATCHES `mindlas context status`
    instead of a proxied (~100% mass) one."""
    from mindlas.vitals.events import Event
    from mindlas.vitals.config import context_pct_path
    from mindlas.actions.context_repair import ContextRepair, RepairContext
    from mindlas.runtime.state import build_context_rot_signals
    from mindlas.features.context_rot import score_context_rot
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    sid = "s_before"
    events = [Event(sid, 1, "t1", "user_prompt", target="Refactor the auth module today")]
    for i in range(2, 6):
        events.append(Event(sid, i, f"t{i}", "user_prompt", target="continue"))
        events.append(Event(sid, i, f"t{i}b", "tool_call", tool="Read", target=f"m{i}.py",
                            cls="read", output_chars=4000))
    rc = RepairContext(events=tuple(events), session_id=sid, now_turn=5)

    proxied = ContextRepair().preview(rc).before          # no cache yet -> mass proxied to ceiling
    p = context_pct_path(sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"pct": 3.0}', encoding="utf-8")        # status line cached a measured 3%
    measured = ContextRepair().preview(rc).before         # repair now reuses it
    assert measured < proxied
    expected = score_context_rot(
        build_context_rot_signals(list(events), context_pct=3.0, now_turn=5))
    assert measured == expected                           # exactly the status line's CTX score


def test_ctx_gauge_block_shows_correction_for_alert_fixture():
    # The CTX gauge block (renderer) carries the correction + /mindlas-repair suggestion
    # for the alert fixture.
    from mindlas.vitals import fixtures
    from mindlas.runtime.render import render_ctx_gauge
    from mindlas.runtime.state import build_context_rot_signals
    from mindlas.features.context_rot import ContextRotScorer
    events = fixtures.context_rot_alert()
    now = max(e.turn for e in events)
    reading = ContextRotScorer().read(build_context_rot_signals(events, now_turn=now))
    out = render_ctx_gauge(reading, plain=True)
    assert "ROT" in out and "ALERT" in out
    assert "Correction:" in out
    assert "/mindlas-repair" in out
