from mindlas.vitals.events import Event, event_to_json, event_from_json


def _ev(**kw):
    base = dict(session_id="s1", turn=1, ts="t", kind="tool_call",
               tool="Edit", target="a.py", cls="edit",
               markers=(), args_key="Edit:a.py")
    base.update(kw)
    return Event(**base)


def test_roundtrip_preserves_all_fields():
    e = _ev(markers=("correction",))
    back = event_from_json(event_to_json(e))
    assert back == e


def test_markers_roundtrip_as_tuple():
    e = _ev(kind="user_prompt", tool=None, target="fix the bug", cls=None,
            markers=("correction",), args_key=None)
    back = event_from_json(event_to_json(e))
    assert back.markers == ("correction",)
    assert isinstance(back.markers, tuple)


def test_json_is_single_line():
    assert "\n" not in event_to_json(_ev())
