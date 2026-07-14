from mindlas.vitals.hooks import dispatch, _ROUTES
from mindlas.vitals.ledger import Ledger
from mindlas.vitals.config import ledger_path
from mindlas.vitals.events import Event
from mindlas.vitals import install as inst


def test_post_tool_failure_records_a_tool_error_event(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path))
    assert "PostToolUseFailure" in _ROUTES
    dispatch("PostToolUseFailure", {"session_id": "f1", "tool_name": "Bash",
                                    "tool_input": {"command": "pytest -q"},
                                    "tool_error": {"type": "timeout",
                                                   "message": "Command timed out after 600s"}})
    events = Ledger(ledger_path("f1")).events()
    fails = [e for e in events if "tool_error" in (e.markers or ())]
    assert len(fails) == 1
    assert fails[0].kind == "tool_call" and fails[0].tool == "Bash"
    assert fails[0].markers[1] == "timeout" and fails[0].target == "pytest -q"


def test_post_tool_failure_no_tool_is_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path))
    assert dispatch("PostToolUseFailure", {"session_id": "f2"}) == {}   # no tool_name -> no-op


def test_post_tool_failure_turn_is_monotonic_and_matches_success_recording(tmp_path, monkeypatch):
    # Anti-corruption guard: `turn` in the ledger is the
    # CONVERSATION turn (user-prompt index), NOT a per-tool-call counter — _on_post_tool records a
    # SUCCESS with turn = max(_current_turn(led), 1) (the current turn), and many tool calls share one
    # turn. A failure MUST use the same rule, else it desyncs from the successes it interleaves with
    # and poisons window/stop timing. So with the current turn == 3, the appended failure is turn 3
    # (never a fabricated "4"), and it is never LESS than any prior turn (monotonic non-decreasing).
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path))
    led = Ledger(ledger_path("f3"))
    led.append(Event("f3", 1, "t1", "user_prompt", target="go"))
    led.append(Event("f3", 2, "t2", "user_prompt", target="again"))
    led.append(Event("f3", 3, "t3", "user_prompt", target="third"))     # current conversation turn = 3
    dispatch("PostToolUseFailure", {"session_id": "f3", "tool_name": "Bash",
                                    "tool_input": {"command": "pytest -q"},
                                    "tool_error": {"type": "timeout", "message": "x"}})
    events = Ledger(ledger_path("f3")).events()
    fail = [e for e in events if "tool_error" in (e.markers or ())][-1]
    assert fail.turn == 3                                       # current turn, exactly like a success
    assert fail.turn >= max(e.turn for e in events if e is not fail)   # monotonic: never backwards


def test_install_registers_post_tool_failure_matcher():
    groups = inst._hook_groups("C:/My Tools/mindlas.exe")
    assert "PostToolUseFailure" in groups
    assert groups["PostToolUseFailure"]["matcher"] == "*"
    assert groups["PostToolUseFailure"]["hooks"][0]["command"] == \
        '"C:/My Tools/mindlas.exe" hook PostToolUseFailure'
