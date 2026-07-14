"""Tests for the verification gate.

These run real ruff against real temp files, so they validate the gate end to end,
not a mock of it. The four properties that keep the gate on the right side of the
degradation result are each asserted directly: clean is silent, an introduced
regression surfaces located, pre-existing breakage is never surfaced, and a fix
returns to silence.
"""

import tempfile
from pathlib import Path

import pytest

from mindlas.vitals import verify
from mindlas.vitals.events import Event


def _win(path, turn=5):
    return [
        Event("s", turn, "t1", "user_prompt", target="work"),
        Event("s", turn, "t2", "tool_call", tool="Edit", target=path, cls="edit", args_key="e"),
        Event("s", turn, "t3", "assistant_msg"),
    ]


@pytest.fixture
def proj():
    d = Path(tempfile.mkdtemp())
    return d, d / "baseline.json"


def test_changed_files_finds_edits_only():
    events = _win("a.py") + [
        Event("s", 5, "t4", "tool_call", tool="Read", target="b.py", cls="read", args_key="r"),
        Event("s", 5, "t5", "tool_call", tool="Write", target="c.py", cls="write", args_key="w"),
    ]
    files = verify.changed_files(events)
    assert "a.py" in files and "c.py" in files and "b.py" not in files


def test_non_python_not_checkable():
    assert verify.changed_files(_win("notes.md")) == []


def test_clean_first_call_seeds_and_is_silent(proj):
    d, bl = proj
    (d / "m.py").write_text("def f(a, b):\n    return a + b\n")
    assert verify.gate(_win("m.py"), d, bl) is None
    assert bl.exists()  # baseline seeded


def test_introduced_regression_surfaces_located(proj):
    d, bl = proj
    (d / "m.py").write_text("def f(a, b):\n    return a + b\n")
    verify.gate(_win("m.py"), d, bl)  # seed clean
    (d / "m.py").write_text("def f(a, b):\n    return a + missing\n")
    out = verify.gate(_win("m.py"), d, bl)
    assert out is not None
    assert "m.py" in out and "line 2" in out and "F821" in out


def test_preexisting_breakage_not_blamed(proj):
    d, bl = proj
    (d / "m.py").write_text("def f(a, b):\n    return a + ghost\n")  # broken before agent
    assert verify.gate(_win("m.py"), d, bl) is None  # seed records it
    assert verify.gate(_win("m.py"), d, bl) is None  # still not surfaced


def test_only_new_defect_surfaces_not_preexisting(proj):
    d, bl = proj
    (d / "m.py").write_text("def f(a, b):\n    return a + ghost\n")
    verify.gate(_win("m.py"), d, bl)  # seed with ghost present
    (d / "m.py").write_text("def f(a, b):\n    return a + ghost + brandnew\n")
    out = verify.gate(_win("m.py"), d, bl)
    assert out is not None and "brandnew" in out and "ghost" not in out


def test_fix_returns_to_silence(proj):
    d, bl = proj
    (d / "m.py").write_text("def f(a, b):\n    return a + b\n")
    verify.gate(_win("m.py"), d, bl)
    (d / "m.py").write_text("def f(a, b):\n    return a + missing\n")
    assert verify.gate(_win("m.py"), d, bl) is not None
    (d / "m.py").write_text("def f(a, b):\n    return a + b\n")
    assert verify.gate(_win("m.py"), d, bl) is None


def test_early_defect_caught_by_late_distant_alert(proj):
    # The misalignment case: a cumulative detector fires several turns after the edit
    # that introduced a defect, while the agent has since worked on other files. A
    # recent-window scope would miss it; the cumulative scope must catch it.
    d, bl = proj

    def edit(turn, f):
        return [Event("s", turn, f"u{turn}", "user_prompt", target="w"),
                Event("s", turn, f"e{turn}", "tool_call", tool="Edit", target=f,
                      cls="edit", args_key="e"),
                Event("s", turn, f"a{turn}", "assistant_msg")]

    (d / "early.py").write_text("def a():\n    return 1\n")
    ev = edit(2, "early.py")
    assert verify.gate(ev, d, bl) is None            # seed early.py clean

    (d / "early.py").write_text("def a():\n    return broken_ref\n")
    ev += edit(3, "early.py")                          # defect introduced at turn 3

    for t in range(4, 10):                             # six turns on other files
        (d / f"other{t}.py").write_text("def f():\n    return 1\n")
        ev += edit(t, f"other{t}.py")

    out = verify.gate(ev, d, bl)                       # alert fires late, at turn 9
    assert out is not None
    assert "early.py" in out and "broken_ref" in out


def test_late_entering_file_gets_own_clean_baseline(proj):
    # A file first touched mid-session must be judged against its own first-sight
    # state, not blamed for breakage that predates the agent reaching it.
    d, bl = proj

    def edit(turn, f):
        return [Event("s", turn, f"e{turn}", "tool_call", tool="Edit", target=f,
                      cls="edit", args_key="e"),
                Event("s", turn, f"a{turn}", "assistant_msg")]

    (d / "a.py").write_text("def a():\n    return 1\n")
    ev = edit(2, "a.py")
    verify.gate(ev, d, bl)                             # seed a.py

    # b.py is first edited at turn 5 and was already broken when the agent reached it
    (d / "b.py").write_text("def b():\n    return preexisting\n")
    ev += edit(5, "b.py")
    assert verify.gate(ev, d, bl) is None              # b.py seeded at first sight, silent

    # now the agent adds a NEW defect to b.py: only that surfaces
    (d / "b.py").write_text("def b():\n    return preexisting + alsonew\n")
    ev += edit(6, "b.py")
    out = verify.gate(ev, d, bl)
    assert out is not None and "alsonew" in out and "preexisting" not in out


def test_work_in_progress_lint_not_surfaced(proj):
    # An unused import is a normal mid-edit state, not breakage. Even newly introduced,
    # it must not surface, or the gate would confuse a working trajectory.
    d, bl = proj
    (d / "m.py").write_text("def f(a):\n    return a\n")
    verify.gate(_win("m.py"), d, bl)                  # seed clean
    (d / "m.py").write_text("import os\n\ndef f(a):\n    return a\n")  # unused import (F401)
    assert verify.gate(_win("m.py"), d, bl) is None   # silent, not a defect


def test_genuine_breakage_still_surfaces_after_narrowing(proj):
    # The narrowed selection must still catch real breakage.
    d, bl = proj
    (d / "m.py").write_text("def f(a):\n    return a\n")
    verify.gate(_win("m.py"), d, bl)
    (d / "m.py").write_text("def f(a):\n    return a + gone\n")   # F821
    out = verify.gate(_win("m.py"), d, bl)
    assert out is not None and "F821" in out


def test_render_defect_shape():
    diag = verify.Diagnostic(file="x.py", line=7, code="F821", message="Undefined name `z`")
    s = verify.render_defect(diag)
    assert s.startswith("[mindlas]")
    assert "x.py line 7" in s and "F821" in s
    assert "\u2014" not in s          # no em-dash
    assert "you" not in s.lower()     # no second person


def _gate_events(f, turn=5):
    return [Event("s", turn, "u", "user_prompt", target="w"),
            Event("s", turn, "e", "tool_call", tool="Edit", target=f, cls="edit", args_key="e"),
            Event("s", turn, "a", "assistant_msg")]


def test_boundary_detection():
    from mindlas.vitals import gate
    assert gate.boundary_of("git push origin main") == "git push"
    assert gate.boundary_of("git commit -m x") == "git commit"
    assert gate.boundary_of("vercel deploy --prod") == "deploy"
    assert gate.boundary_of("npm publish") == "publish"
    assert gate.boundary_of("ls -la") is None
    assert gate.boundary_of("python test.py") is None


def test_seed_pre_edit_catches_first_edit_defect(proj, monkeypatch):
    # The reason for eager seeding: a defect introduced on the FIRST edit must still be
    # attributable. Seed the clean state before the edit, then the gate catches it.
    from mindlas.vitals import gate
    d, bl = proj
    (d / "auth.py").write_text("def f(a):\n    return a\n")     # clean, pre-agent
    gate.seed_pre_edit("auth.py", d, bl)                          # snapshot before edit
    (d / "auth.py").write_text("def f(a):\n    return a + bad\n")  # first edit breaks it
    findings = gate.decide(_gate_events("auth.py"), d, bl)
    assert findings and findings[0].file == "auth.py" and "bad" in findings[0].message


def test_gate_shadow_records_but_does_not_block(proj, monkeypatch):
    from mindlas.vitals import gate
    d, bl = proj
    vp = d / "verdicts.jsonl"
    monkeypatch.setenv("MINDLAS_GATE", "shadow")
    (d / "auth.py").write_text("def f(a):\n    return a\n")
    gate.seed_pre_edit("auth.py", d, bl)
    (d / "auth.py").write_text("def f(a):\n    return a + bad\n")
    resp = gate.run_gate(_gate_events("auth.py"), d, bl, vp, "git push", "git push origin main")
    assert resp == {}                                  # shadow never blocks
    verdicts = gate.read_verdicts(vp)
    assert len(verdicts) == 1 and verdicts[0]["decision"] == "would_block"


def test_gate_live_blocks_on_verified_defect(proj, monkeypatch):
    from mindlas.vitals import gate
    d, bl = proj
    vp = d / "verdicts.jsonl"
    monkeypatch.setenv("MINDLAS_GATE", "live")
    (d / "auth.py").write_text("def f(a):\n    return a\n")
    gate.seed_pre_edit("auth.py", d, bl)
    (d / "auth.py").write_text("def f(a):\n    return a + bad\n")
    resp = gate.run_gate(_gate_events("auth.py"), d, bl, vp, "git push", "git push origin main")
    spec = resp.get("hookSpecificOutput", {})
    assert spec.get("permissionDecision") == "deny"
    assert "Mindlas blocked" in spec.get("permissionDecisionReason", "")


def test_gate_allows_clean_change(proj, monkeypatch):
    from mindlas.vitals import gate
    d, bl = proj
    vp = d / "verdicts.jsonl"
    monkeypatch.setenv("MINDLAS_GATE", "live")
    (d / "auth.py").write_text("def f(a):\n    return a\n")
    gate.seed_pre_edit("auth.py", d, bl)
    (d / "auth.py").write_text("def f(a):\n    return a + a\n")   # still clean
    resp = gate.run_gate(_gate_events("auth.py"), d, bl, vp, "git push", "git push origin main")
    assert resp == {}                                  # clean -> allow, no block
    assert gate.read_verdicts(vp)[0]["decision"] == "allow"


def test_launch_background_noops_under_pytest(tmp_path):
    # Regression guard: a detached suite run must never spawn from a test or CI context,
    # or it leaks orphaned processes. PYTEST_CURRENT_TEST is always set during pytest, so
    # this asserts the launcher stays inert here and writes no state.
    from mindlas.vitals import testtier
    state = tmp_path / "state.json"
    testtier.launch_background(tmp_path / "ledger.jsonl", tmp_path,
                               tmp_path / "tb.json", state, 1)
    assert not state.exists()


def test_test_launch_debounce(tmp_path, monkeypatch):
    # Rapid edits must collapse to one background launch, not pile up. The debounce marker
    # is written on first launch and suppresses launches within the interval.
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path))
    from mindlas.vitals import hooks
    from mindlas.vitals.ledger import Ledger
    from mindlas.vitals.config import ledger_path, session_dir
    sid = "dbtest"
    p = {"cwd": str(tmp_path), "session_id": sid}
    led = Ledger(ledger_path(sid))
    marker = session_dir(sid) / "test_launch.ts"

    hooks._maybe_launch_tests(led, sid, p, 1)
    assert marker.exists()
    first = marker.read_text()
    hooks._maybe_launch_tests(led, sid, p, 1)          # within interval -> debounced
    assert marker.read_text() == first
    hooks._maybe_launch_tests(led, sid, p, 1, min_interval=0.0)  # interval elapsed -> relaunch
    assert float(marker.read_text()) >= float(first)
