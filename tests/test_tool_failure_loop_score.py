from mindlas.vitals.capture import tool_failure_event, tool_event
from mindlas.vitals.events import Event
from mindlas.runtime import paths
from mindlas.runtime.tool_loop_state import build_tool_failure_loop_signals
from mindlas.features.tool_failure_loop import (
    WARNING_MIN, ALERT_MIN, score_tool_failure_loop, tool_failure_loop_trigger,
    controlled_after_loop_score, ToolFailureLoopScorer)


def _fail(turn, cmd="pytest -q", msg="Command timed out after 600s", tool="Bash"):
    return tool_failure_event("s", turn, f"t{turn}", tool, {"command": cmd},
                              {"type": "timeout", "message": msg})


def _prompt(turn, text="continue"):
    return Event("s", turn, f"t{turn}", "user_prompt", target=text)


def _sig(events, now_turn, **kw):
    return build_tool_failure_loop_signals(events, now_turn=now_turn, **kw)


def test_no_failures_scores_zero():
    s = _sig([tool_event("s", 1, "t1", "Bash", {"command": "ls"})], 1)
    assert score_tool_failure_loop(s) == 0
    assert tool_failure_loop_trigger(s) is False


def test_one_isolated_failure_is_watch_at_most():
    s = _sig([_fail(1)], 1)
    assert score_tool_failure_loop(s) < WARNING_MIN          # STABLE or WATCH, never WARNING/ALERT


def test_three_consecutive_failures_alert():
    s = _sig([_fail(1), _fail(2), _fail(3)], 3)
    assert score_tool_failure_loop(s) >= ALERT_MIN
    assert tool_failure_loop_trigger(s) is True


def test_retry_without_evidence_triggers():
    s = _sig([_fail(1), _fail(2), _fail(3)], 3)
    assert s.retry_without_new_evidence_count >= 2 and tool_failure_loop_trigger(s) is True


def test_success_after_failures_suppresses_trigger():
    # Three same-signature failures THEN a success -> the blind loop is already broken,
    # so Stop must NOT be recommended even though the window still counts 3 same-signature failures.
    s = _sig([_fail(1), _fail(2), _fail(3), tool_event("s", 4, "t4", "Bash", {"command": "ls"})], 4)
    assert s.same_signature_failure_count == 3            # whole-window diagnostic count unchanged
    assert s.success_since_last_failure is True
    assert tool_failure_loop_trigger(s) is False          # success broke the active loop


def test_failures_interrupted_by_evidence_do_not_trigger():
    # failA / prompt / failA / prompt / failA is NOT a blind retry loop — the user gave
    # new evidence each time. Whole-window same-signature is 3, but the ACTIVE segment holds only the
    # last failure, and consecutive is evidence-aware, so nothing fires.
    s = _sig([_fail(1), _prompt(2, "try a different flag"), _fail(3),
              _prompt(4, "check the config"), _fail(5)], 5)
    assert s.same_signature_failure_count == 3            # window (diagnostic) still 3
    assert s.same_signature_failure_count_active == 1     # active segment: only the last failure
    assert s.consecutive_failure_count == 1              # evidence-aware: a prompt breaks the run
    assert s.retry_without_new_evidence_count == 0        # evidence between every retry
    assert score_tool_failure_loop(s) < 65               # score cannot reach the trigger threshold
    assert tool_failure_loop_trigger(s) is False          # not a blind loop -> no Stop


def test_category_penalties_present():
    def _perm(t):
        return tool_failure_event("s", t, f"t{t}", "Bash", {"command": "cat /etc/x"},
                                  {"type": "error", "message": "permission denied"})
    s = _sig([_perm(1), _perm(2)], 2)
    assert s.permission_denied_count == 2 and tool_failure_loop_trigger(s) is True


def test_stop_active_caps_score_and_blocks_trigger(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    ap = paths.active_stop_path("s", tmp_path)
    ap.parent.mkdir(parents=True, exist_ok=True)
    ap.write_text(json.dumps({"stop_turn": 2, "active": True}), encoding="utf-8")
    s = _sig([_fail(1), _fail(2), _fail(3)], 3, project_root=tmp_path)
    assert s.stop_active is True
    assert score_tool_failure_loop(s) <= 24                  # active-stop cap -> controlled/STABLE-WATCH
    assert tool_failure_loop_trigger(s) is False             # active stop blocks the trigger


def test_controlled_after_and_clamp():
    assert controlled_after_loop_score(86, _sig([_fail(1)], 1)) == 15
    assert controlled_after_loop_score(10, _sig([_fail(1)], 1)) == 10   # below WATCH -> unchanged
    s = _sig([_fail(i) for i in range(1, 8)], 7)
    assert 0 <= score_tool_failure_loop(s) <= 100            # clamp


def test_scorer_reading_band_and_suggested_command():
    s = _sig([_fail(1), _fail(2), _fail(3)], 3)
    r = ToolFailureLoopScorer().read(s)
    assert r.short_label == "LOOP" and r.state == "ALERT" and r.can_verify is False
    assert r.suggested_commands == ("/mindlas-loop-stop",)
    assert any("failure signature" in f.text for f in r.facts)
    assert any("failure category" in f.text for f in r.facts)   # category is a DISTINCT fact
