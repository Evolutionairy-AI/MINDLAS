from mindlas.vitals.capture import tool_event
from mindlas.vitals.events import event_from_json, event_to_json


def test_edit_records_line_deltas():
    ev = tool_event(session_id="s", turn=1, ts="t", tool="Edit",
                    tool_input={"file_path": "a.py",
                                "old_string": "x = 1\n", "new_string": "x = 1\ny = 2\nz = 3\n"})
    assert ev.cls == "edit"
    assert ev.lines_added == 3 and ev.lines_deleted == 1


def test_write_records_added_only():
    ev = tool_event(session_id="s", turn=1, ts="t", tool="Write",
                    tool_input={"file_path": "a.py", "content": "a\nb\nc\n"})
    assert ev.lines_added == 3 and (ev.lines_deleted or 0) == 0


def test_read_has_no_line_deltas():
    ev = tool_event(session_id="s", turn=1, ts="t", tool="Read",
                    tool_input={"file_path": "a.py"})
    assert ev.lines_added is None and ev.lines_deleted is None


def test_line_deltas_survive_ledger_json_round_trip():
    ev = tool_event(session_id="s", turn=1, ts="t", tool="Edit",
                    tool_input={"old_string": "a\n", "new_string": "a\nb\n"})
    back = event_from_json(event_to_json(ev))
    assert back.lines_added == ev.lines_added and back.lines_deleted == ev.lines_deleted
