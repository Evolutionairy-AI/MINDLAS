import json
from mindlas.vitals import capture


def test_tool_event_output_chars_from_string_response():
    e = capture.tool_event("s", 1, "t1", "Read", {"file_path": "f.py"}, "x" * 5000)
    assert e.output_chars == 5000


def test_tool_event_output_chars_from_dict_response():
    resp = {"stdout": "hello", "stderr": ""}
    e = capture.tool_event("s", 1, "t1", "Bash", {"command": "echo hi"}, resp)
    assert e.output_chars == len(json.dumps(resp, ensure_ascii=False))


def test_tool_event_output_chars_none_when_no_response():
    e = capture.tool_event("s", 1, "t1", "Read", {"file_path": "f.py"})
    assert e.output_chars is None


from mindlas.vitals.events import Event
from mindlas.vitals.hooks import _on_post_tool
from mindlas.vitals.ledger import Ledger
from mindlas.vitals.config import ledger_path


def test_post_tool_hook_records_output_chars(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path))
    monkeypatch.setenv("MINDLAS_TESTTIER", "0")   # don't launch background tests
    sid = "sess1"
    led = Ledger(ledger_path(sid))
    led.append(Event(sid, 1, "t1", "user_prompt", target="do the work properly please"))
    payload = {"session_id": sid, "tool_name": "Read",
               "tool_input": {"file_path": "f.py"}, "tool_response": "y" * 3000, "cwd": str(tmp_path)}
    _on_post_tool(led, sid, payload)
    tool_calls = [e for e in Ledger(ledger_path(sid)).events() if e.kind == "tool_call"]
    assert tool_calls[-1].output_chars == 3000
