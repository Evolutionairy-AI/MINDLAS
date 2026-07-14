from mindlas.vitals.events import Event, event_from_json, event_to_json


def test_new_fields_default_none_and_old_construction_still_valid():
    e = Event("s", 1, "t1", "tool_call", tool="Bash", target="pytest", cls="test_run")
    assert e.exit_code is None and e.error_text is None       # backward-compatible defaults


def test_round_trip_with_error_fields():
    e = Event("s", 3, "t3", "tool_call", tool="Bash", target="pytest -q", cls="test_run",
              markers=("tool_error", "timeout"), exit_code=124, error_text="timed out")
    d = event_from_json(event_to_json(e))
    assert d.exit_code == 124 and d.error_text == "timed out"
    assert d.markers == ("tool_error", "timeout")


def test_success_event_json_omits_error_keys():
    # a normal (non-failure) event's JSON must NOT carry exit_code/error_text
    # keys, keeping old ledger lines byte-stable. The keys appear only when the fields are set.
    import json
    ok = json.loads(event_to_json(Event("s", 1, "t1", "tool_call", tool="Read", target="a.py",
                                         cls="read")))
    assert "exit_code" not in ok and "error_text" not in ok
    fail = json.loads(event_to_json(Event("s", 2, "t2", "tool_call", tool="Bash", target="pytest",
                                          cls="test_run", markers=("tool_error", "timeout"),
                                          exit_code=124, error_text="timed out")))
    assert fail["exit_code"] == 124 and fail["error_text"] == "timed out"


def test_old_ledger_line_without_error_fields_parses():
    # an old ledger line has no exit_code/error_text keys -> must read as None, not raise
    old = ('{"session_id": "s", "turn": 2, "ts": "t2", "kind": "tool_call", "tool": "Read",'
           ' "target": "a.py", "cls": "read", "markers": [], "args_key": "Read:a.py",'
           ' "lines_added": null, "lines_deleted": null, "output_chars": 40}')
    e = event_from_json(old)
    assert e.exit_code is None and e.error_text is None and e.tool == "Read"
