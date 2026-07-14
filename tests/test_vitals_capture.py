from mindlas.vitals.capture import (
    classify_tool, make_args_key, detect_markers, tool_event, prompt_event,
    assistant_event,
)


def test_read_edit_write_search_classes():
    assert classify_tool("Read", {"file_path": "a.py"}) == "read"
    assert classify_tool("Edit", {"file_path": "a.py"}) == "edit"
    assert classify_tool("Write", {"file_path": "a.py"}) == "write"
    assert classify_tool("Grep", {"pattern": "x"}) == "search"


def test_bash_test_runner_vs_command():
    assert classify_tool("Bash", {"command": "pytest tests/ -q"}) == "test_run"
    assert classify_tool("Bash", {"command": "npm test"}) == "test_run"
    assert classify_tool("Bash", {"command": "ls -la"}) == "command"


def test_unknown_tool_is_other():
    assert classify_tool("WebFetch", {"url": "http://x"}) == "other"


def test_args_key_is_stable_and_target_sensitive():
    assert make_args_key("Edit", {"file_path": "a.py"}) == make_args_key("Edit", {"file_path": "a.py"})
    assert make_args_key("Edit", {"file_path": "a.py"}) != make_args_key("Edit", {"file_path": "b.py"})


def test_correction_and_done_markers():
    assert "correction" in detect_markers("user_prompt", "No, I said don't touch the parser")
    assert "correction" not in detect_markers("user_prompt", "please add a test")
    assert "done_claim" in detect_markers("assistant_msg", "That should do it, the fix works")


def test_make_args_key_bash_uses_command_text():
    assert make_args_key("Bash", {"command": "npm test"}) == "Bash:npm test"


def test_tool_event_builds_tool_call_with_empty_markers():
    e = tool_event("s1", 3, "ts", "Edit", {"file_path": "a.py"})
    assert e.kind == "tool_call"
    assert e.cls == "edit"
    assert e.target == "a.py"
    assert e.args_key == "Edit:a.py"
    assert e.markers == ()


def test_prompt_event_maps_prompt_to_target_and_detects_markers():
    e = prompt_event("s1", 1, "ts", "No, don't touch the parser")
    assert e.kind == "user_prompt"
    assert e.target == "No, don't touch the parser"   # verbatim prompt text -> target
    assert e.cls is None
    assert "correction" in e.markers
    # a plain prompt yields no markers
    assert prompt_event("s1", 2, "ts", "please add a test").markers == ()


def test_assistant_event_captures_done_claim_marker_only():
    e = assistant_event("s1", 3, "ts", "Great — the fix works now.")
    assert e.kind == "assistant_msg"
    assert "done_claim" in e.markers
    assert e.target is None                            # raw assistant text is not stored
    # a neutral closing message yields no marker, and None text is safe
    assert assistant_event("s1", 4, "ts", "Here is the next step.").markers == ()
    assert assistant_event("s1", 5, "ts", None).markers == ()


def test_args_key_is_content_sensitive_for_edits():
    a = make_args_key("Edit", {"file_path": "x.py", "new_string": "AAA"})
    b = make_args_key("Edit", {"file_path": "x.py", "new_string": "BBB"})
    same = make_args_key("Edit", {"file_path": "x.py", "new_string": "AAA"})
    assert a != b       # different edit content -> different key
    assert a == same    # identical edit content -> identical key
