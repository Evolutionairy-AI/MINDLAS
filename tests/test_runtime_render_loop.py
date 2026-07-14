from pathlib import Path
from mindlas.vitals.capture import tool_failure_event
from mindlas.runtime.tool_loop_state import build_tool_failure_loop_signals
from mindlas.features.tool_failure_loop import ToolFailureLoopScorer
from mindlas.features.context_rot import ContextRotScorer
from mindlas.runtime.state import build_context_rot_signals
from mindlas.runtime.render import render_loop_gauge, render_ctx_statusline, _BAND_COLOR


def _loop_reading(n):
    events = [tool_failure_event("s", i, f"t{i}", "Bash", {"command": "pytest -q"},
                                 {"type": "timeout", "message": "Command timed out after 600s"})
              for i in range(1, n + 1)]
    return ToolFailureLoopScorer().read(build_tool_failure_loop_signals(events, now_turn=n))


def _ctx_reading():
    return ContextRotScorer().read(build_context_rot_signals([], now_turn=0))


def test_statusline_lights_loop_when_reading_supplied():
    import re
    ctx = _ctx_reading()
    line = render_ctx_statusline(ctx, None, None, _loop_reading(3))
    # 3 identical consecutive timeout failures SATURATE the weighted sum (score == 100, not the 70
    # ALERT floor), so assert on the BAND via regex — never a brittle numeric substring. A future
    # scoring-weight tweak must not silently break this live-reachability check. The LOOP segment is
    # also color-wrapped by band exactly like ROT — the label and score+band are each red-wrapped.
    red = re.escape(_BAND_COLOR["ALERT"])
    assert "Loop" in line                            # white label present
    assert re.search(red + r"Alert", line)           # Title-case state wrapped in the ALERT (red) color
    assert re.search(red + r"\d+", line)             # score wrapped red on row B


def test_statusline_loop_placeholder_when_none():
    ctx = _ctx_reading()
    # back-compat: fewer args -> LOOP is the last gauge on line 2 and shows a dim placeholder
    # (label + empty square track + the "--" marker) instead of a live score.
    line0 = render_ctx_statusline(ctx, plain=True).split("\n")[0]
    assert "Loop" in line0 and line0.rstrip().endswith("--")


def test_loop_gauge_matches_golden():
    out = render_loop_gauge(_loop_reading(4), plain=True)
    golden = Path(__file__).parent / "vitals_golden" / "tool_failure_loop_alert.txt"
    if not golden.exists():
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(out, encoding="utf-8")            # self-seed on first run
    assert out == golden.read_text(encoding="utf-8")
    assert "LOOP" in out and "Suggested: /mindlas-loop-stop" in out
