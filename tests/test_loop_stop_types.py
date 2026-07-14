from mindlas.runtime.loop_stop_types import StopContext, StopResult


def test_stop_context_defaults():
    ctx = StopContext(events=(), session_id="s", now_turn=4)
    assert ctx.project_root is None and ctx.window_turns == 20
    assert ctx.human_decision is None


def test_stop_result_to_dict_is_partitionable_loop_stop_record():
    r = StopResult(applied=True, before=86, controlled_after_loop=15, status="controlled",
                   stop_id="20260701T120000_8f12", manifest_path="/x/m.json",
                   active_stop_path="/x/active.json", failure_signature="timeout:Bash:8f12abcd",
                   active_tool_name="Bash", recommended_next_actions=("a", "b"),
                   rails_labels={"tool_failure_loop_before": 86}, human_decision="accepted")
    d = r.to_dict()
    assert list(d)[0] == "type" and d["type"] == "loop_stop"          # first key -> partitionable
    assert d["before"] == 86 and d["controlled_after_loop"] == 15     # canonical key name
    assert d["status"] == "controlled" and d["stop_id"] == "20260701T120000_8f12"
    assert d["failure_signature"] == "timeout:Bash:8f12abcd" and d["active_tool_name"] == "Bash"
    assert d["manifest_path"] == "/x/m.json" and d["active_stop_path"] == "/x/active.json"  # paths carried
    assert d["human_decision"] == "accepted"                          # carried
    assert d["rails_labels"] == {"tool_failure_loop_before": 86}
    assert "after" not in d and "modeled_after_loop" not in d         # never these keys
    assert "planned_after_loop" not in d and "applied" not in d
