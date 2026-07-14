from mindlas.vitals.capture import (canonicalize_error, classify_error_category,
                                     tool_failure_event)


def test_canonicalize_masks_paths_numbers_and_temp_files_and_caps():
    raw = 'Timeout after 600s reading "C:\\Users\\me\\AppData\\Local\\Temp\\x.tmp" (line 42)'
    c = canonicalize_error(raw)
    assert c == c.lower()                       # lowercased
    assert "<n>" in c and "600" not in c        # numbers masked
    assert "<file>" in c or "<path>" in c       # temp/abs path masked
    assert len(c) <= 160


def test_canonicalize_none_is_empty():
    assert canonicalize_error("") == "" and canonicalize_error(None) == ""


def test_classify_categories():
    assert classify_error_category("Permission denied") == "permission"
    assert classify_error_category("No such file or directory") == "not_found"
    assert classify_error_category("command timed out") == "timeout"
    assert classify_error_category("SyntaxError: invalid syntax") == "parse"
    assert classify_error_category("0 results") == "empty"
    assert classify_error_category("connection refused") == "unavailable"
    assert classify_error_category("boom", exit_code=1) == "command_fail"
    assert classify_error_category("weird") == "unknown"
    assert classify_error_category("done", timed_out=True) == "timeout"   # hint wins


def test_tool_failure_event_shape():
    e = tool_failure_event("s", 4, "t4", "Bash", {"command": "pytest -q"},
                           {"type": "timeout", "message": "Command timed out after 600s",
                            "exit_code": 124})
    assert e.kind == "tool_call" and e.tool == "Bash"
    assert e.cls == "test_run"                              # pytest -> test_run
    assert e.target == "pytest -q"                          # command text in target
    assert e.markers[0] == "tool_error" and e.markers[1] == "timeout"
    assert e.exit_code == 124 and "<n>" in (e.error_text or "")


def test_tool_failure_event_is_defensive_on_empty_error():
    e = tool_failure_event("s", 1, "t1", "Bash", {}, {})
    assert e.kind == "tool_call" and e.markers[0] == "tool_error"
    assert e.markers[1] == "unknown" and e.exit_code is None and e.error_text == ""
