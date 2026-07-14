from mindlas.vitals.events import Event
from mindlas.vitals.ledger import Ledger


def _ev(turn, kind="tool_call", **kw):
    return Event(session_id="s", turn=turn, ts="t", kind=kind, **kw)


def test_append_then_read_preserves_order(tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    led.append(_ev(1))
    led.append(_ev(2))
    evs = led.events()
    assert [e.turn for e in evs] == [1, 2]


def test_read_missing_file_is_empty(tmp_path):
    assert Ledger(tmp_path / "nope.jsonl").events() == []


def test_tolerates_partial_last_line(tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    led.append(_ev(1))
    # simulate a crash mid-write of a second line
    with open(led.path, "a", encoding="utf-8") as f:
        f.write('{"session_id": "s", "turn": 2, "kind": "too')  # truncated
    evs = led.events()
    assert [e.turn for e in evs] == [1]


def test_count_kind(tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    led.append(_ev(1, kind="user_prompt", target="a"))
    led.append(_ev(1, kind="tool_call"))
    led.append(_ev(2, kind="user_prompt", target="b"))
    assert led.count_kind("user_prompt") == 2
