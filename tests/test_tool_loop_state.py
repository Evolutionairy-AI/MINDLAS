import json
from mindlas.vitals.events import Event
from mindlas.vitals.capture import tool_failure_event, tool_event
from mindlas.runtime import paths
from mindlas.runtime.tool_loop_state import (ToolFailureLoopSignals,
                                             build_tool_failure_loop_signals)


def _fail(turn, cmd="pytest -q", msg="Command timed out after 600s", tool="Bash"):
    return tool_failure_event("s", turn, f"t{turn}", tool, {"command": cmd},
                              {"type": "timeout", "message": msg})


def _ok(turn, cmd="ls", tool="Bash"):
    return tool_event("s", turn, f"t{turn}", tool, {"command": cmd})


def _prompt(turn, text="continue"):
    return Event("s", turn, f"t{turn}", "user_prompt", target=text)


def test_no_tool_calls_is_clean():
    sig = build_tool_failure_loop_signals([_prompt(1)], now_turn=1)
    assert sig.tool_call_count == 0 and sig.failed_tool_call_count == 0
    assert sig.consecutive_failure_count == 0 and sig.active_failure_signature == ""
    assert sig.stop_active is False and sig.success_since_last_failure is True


def test_calls_without_failures_are_clean():
    sig = build_tool_failure_loop_signals([_ok(1), _ok(2)], now_turn=2)
    assert sig.tool_call_count == 2 and sig.failed_tool_call_count == 0
    assert sig.failure_rate_pct == 0


def test_repeated_same_failure_counts_signature_and_command():
    events = [_prompt(1), _fail(2), _fail(3), _fail(4)]
    sig = build_tool_failure_loop_signals(events, now_turn=4)
    assert sig.failed_tool_call_count == 3
    assert sig.consecutive_failure_count == 3
    assert sig.same_signature_failure_count == 3          # identical timeout -> same signature
    assert sig.same_command_retry_count == 3
    assert sig.unique_failure_signature_count == 1
    assert sig.active_failure_category == "timeout" and sig.active_tool_name == "Bash"
    assert sig.timeout_count == 3 and sig.last_failure_turn == 4


def test_retry_without_new_evidence_counts_and_evidence_resets_it():
    # 3 identical failures in a row, no evidence between -> 2 retries-without-evidence
    sig = build_tool_failure_loop_signals([_fail(1), _fail(2), _fail(3)], now_turn=3)
    assert sig.retry_without_new_evidence_count == 2
    # a user prompt between the two failures IS new evidence -> 0
    sig2 = build_tool_failure_loop_signals([_fail(1), _prompt(2, "try a different flag"), _fail(3)],
                                           now_turn=3)
    assert sig2.retry_without_new_evidence_count == 0


def test_success_breaks_consecutive_and_sets_success_since_last_failure():
    sig = build_tool_failure_loop_signals([_fail(1), _fail(2), _ok(3)], now_turn=3)
    assert sig.consecutive_failure_count == 0             # trailing run ends at the success
    assert sig.success_since_last_failure is True


def test_successful_tool_call_is_new_evidence():
    # fail A, successful tool call, fail A -> the success is evidence -> 0 retries-without-evidence
    sig = build_tool_failure_loop_signals([_fail(1), _ok(2), _fail(3)], now_turn=3)
    assert sig.retry_without_new_evidence_count == 0


def test_window_excludes_old_turns():
    old = [_fail(1)]
    recent = [_ok(30)]
    sig = build_tool_failure_loop_signals(old + recent, now_turn=30, window_turns=20)
    assert sig.tool_call_count == 1 and sig.failed_tool_call_count == 0   # turn 1 is outside window


def test_active_stop_is_read(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    p = paths.active_stop_path("s", tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"stop_turn": 7, "active": True}), encoding="utf-8")
    sig = build_tool_failure_loop_signals([_fail(9)], now_turn=9, project_root=tmp_path)
    assert sig.stop_active is True and sig.last_stop_turn == 7


def test_active_stop_inactive_flag_is_not_active(tmp_path, monkeypatch):
    # An explicit {"active": false} record must read stop_active=False (deactivated stop).
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    p = paths.active_stop_path("s", tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"stop_turn": 7, "active": False}), encoding="utf-8")
    sig = build_tool_failure_loop_signals([_fail(9)], now_turn=9, project_root=tmp_path)
    assert sig.stop_active is False and sig.last_stop_turn is None


def test_malformed_events_do_not_crash():
    class Weird:                                          # missing every attribute
        pass
    sig = build_tool_failure_loop_signals([Weird(), _fail(2)], now_turn=2)
    assert isinstance(sig, ToolFailureLoopSignals)
    assert sig.failed_tool_call_count == 1
