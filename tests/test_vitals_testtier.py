"""Tests for the continuous test tier. These run real pytest in a temp project, so they
validate the tier end to end, including the property that matters most: it catches a
logic error the static tier is blind to."""

import tempfile
from pathlib import Path

import pytest

from mindlas.vitals import gate, testtier, verify
from mindlas.vitals.events import Event

# These run real pytest subprocesses: slow and environment-sensitive. They are
# integration tests, run in CI with MINDLAS_RUN_TESTTIER=1. The default unit suite
# skips them to stay fast and deterministic.
import os
pytestmark = pytest.mark.skipif(
    os.environ.get("MINDLAS_RUN_TESTTIER") != "1",
    reason="real-pytest integration tier; set MINDLAS_RUN_TESTTIER=1 to run",
)

_CMD = "pytest -q --tb=no -rf -p no:cacheprovider"


def _events():
    return [
        Event("s", 5, "u", "user_prompt", target="w"),
        Event("s", 5, "e", "tool_call", tool="Edit", target="mod.py", cls="edit", args_key="e"),
        Event("s", 5, "t", "tool_call", tool="Bash", target=_CMD, cls="test_run", args_key="t"),
    ]


@pytest.fixture
def proj():
    d = Path(tempfile.mkdtemp())
    (d / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    (d / "mod.py").write_text("def add(a, b):\n    return a + b\n")
    (d / "test_mod.py").write_text("from mod import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    return d


def test_discover_prefers_agent_command(proj):
    assert testtier.discover_command(_events(), proj) == _CMD


def test_discover_falls_back_to_markers(proj):
    events = [Event("s", 5, "e", "tool_call", tool="Edit", target="mod.py", cls="edit", args_key="e")]
    cmd = testtier.discover_command(events, proj)
    assert cmd and "pytest" in cmd


def test_baseline_run_seeds_and_is_silent(proj):
    tb = proj / "tb.json"
    ran, new = testtier.verify_tests(_events(), proj, tb)
    assert ran is True and new == [] and tb.exists()


def test_logic_error_caught_where_static_is_blind(proj):
    tb = proj / "tb.json"
    testtier.verify_tests(_events(), proj, tb)                 # seed: all pass
    (proj / "mod.py").write_text("def add(a, b):\n    return a - b\n")  # logic bug, static-clean
    ran, new = testtier.verify_tests(_events(), proj, tb)
    assert ran is True and "test_mod.py::test_add" in new
    assert verify.verify(["mod.py"], proj) == []              # static tier sees nothing


def test_preexisting_failure_not_blamed(proj):
    tb = proj / "tb.json"
    (proj / "mod.py").write_text("def add(a, b):\n    return a - b\n")  # broken before agent
    ran, new = testtier.verify_tests(_events(), proj, tb)     # seed with the failure present
    assert ran is True and new == []                          # not surfaced
    ran, new = testtier.verify_tests(_events(), proj, tb)
    assert new == []                                          # still not blamed


def test_state_roundtrip_and_staleness(proj):
    ts = proj / "ts.json"
    testtier.run_and_cache(_events(), proj, proj / "tb.json", ts, turn=5)
    st = testtier.read_state(ts)
    assert st is not None and st.ran is True
    assert testtier.read_state(ts, max_age_s=-1) is None      # stale -> None


def test_gate_blocks_on_verified_test_failure(proj, monkeypatch):
    tb = proj / "tb.json"; ts = proj / "ts.json"; vp = proj / "v.jsonl"; bl = proj / "vb.json"
    monkeypatch.setenv("MINDLAS_GATE", "live")
    testtier.verify_tests(_events(), proj, tb)                 # seed clean
    (proj / "mod.py").write_text("def add(a, b):\n    return a - b\n")
    testtier.run_and_cache(_events(), proj, tb, ts, turn=5)
    resp = gate.run_gate(_events(), proj, bl, vp, "git push", "git push origin main", ts)
    spec = resp.get("hookSpecificOutput", {})
    assert spec.get("permissionDecision") == "deny"
    assert "test_mod.py::test_add" in spec.get("permissionDecisionReason", "")


def test_gate_allows_when_tests_pass(proj, monkeypatch):
    tb = proj / "tb.json"; ts = proj / "ts.json"; vp = proj / "v.jsonl"; bl = proj / "vb.json"
    monkeypatch.setenv("MINDLAS_GATE", "live")
    testtier.verify_tests(_events(), proj, tb)
    testtier.run_and_cache(_events(), proj, tb, ts, turn=5)    # still passing
    resp = gate.run_gate(_events(), proj, bl, vp, "git push", "git push origin main", ts)
    assert resp == {}
