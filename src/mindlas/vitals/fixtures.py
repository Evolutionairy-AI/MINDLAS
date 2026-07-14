"""Deterministic synthetic event streams for tests and the `--demo` CLI flags.

Convention: a *turn* is a user→assistant exchange. Each turn here is built as
  user_prompt -> [tool_call ...] -> assistant_msg
"""
from __future__ import annotations

from .events import Event, EventKind, Marker

SID = "demo"


def _b():
    return _Builder()


class _Builder:
    def __init__(self):
        self.events: list[Event] = []
        self.turn = 0
        self._n = 0

    def _ts(self) -> str:
        self._n += 1
        return f"t{self._n:04d}"

    def user(self, text: str, *, correction: bool = False):
        self.turn += 1
        self.events.append(Event(SID, self.turn, self._ts(), EventKind.USER_PROMPT,
                                 target=text, markers=(Marker.CORRECTION,) if correction else ()))
        return self

    def tool(self, tool: str, target: str, cls: str, output_chars: int | None = None):
        self.events.append(Event(SID, self.turn, self._ts(), EventKind.TOOL_CALL,
                                 tool=tool, target=target, cls=cls,
                                 args_key=f"{tool}:{target}", output_chars=output_chars))
        return self

    def stop(self):
        self.events.append(Event(SID, self.turn, self._ts(), EventKind.ASSISTANT_MSG))
        return self

def verification_debt(files: int = 5, total_lines: int = 312, turns: int = 7) -> list[Event]:
    """Verification-Debt demo: the agent edits many files/lines across
    several turns and claims completion, with NO verifier pass — drives VERIFY to ALERT.
    No allow verdict exists for this session, so every change counts as unverified."""
    per = max(1, total_lines // files)
    evs: list[Event] = []
    for i in range(files):
        evs.append(Event(SID, i + 1, f"t{i+1:04d}", EventKind.TOOL_CALL,
                         tool="Edit", target=f"src/mod_{i}.py", cls="edit",
                         lines_added=per, lines_deleted=0))
    last = files
    while last < turns:                         # pad to `turns` without adding new files
        last += 1
        evs.append(Event(SID, last, f"t{last:04d}", EventKind.TOOL_CALL,
                         tool="Edit", target=f"src/mod_{last % files}.py", cls="edit",
                         lines_added=1, lines_deleted=0))
    evs.append(Event(SID, last, f"t{last:04d}b", EventKind.ASSISTANT_MSG, markers=(Marker.DONE_CLAIM,)))
    return evs


def verification_debt_alert() -> list[Event]:
    """Events for the canonical VERIFY demo loader. The 4-band gauge renders from synthetic
    signals (demo_verification_debt_alert_signals), so these events only satisfy _load_events."""
    return verification_debt()


def context_rot_low(n: int = 4) -> list[Event]:
    """Context Rot demo: a short, freshly-stated, low-output session — CTX stays STABLE."""
    b = _b()
    for i in range(1, n + 1):
        (b.user(f"Implement helper function number {i} with a short docstring.")
          .tool("Read", f"mod{i}.py", "read", output_chars=150)
          .tool("Edit", f"mod{i}.py", "edit")
          .stop())
    return b.events


def context_rot_alert(turns: int = 40) -> list[Event]:
    """Context Rot demo: a long session that states the task once, never
    restates it, accumulates large tool outputs, and is never repaired — drives CTX to ALERT."""
    b = _b()
    b.user("Refactor the authentication module onto the new token service "
           "without changing the public API.")               # the one objective, turn 1
    b.tool("Read", "auth/main.py", "read", output_chars=4000).stop()
    for i in range(2, turns + 1):
        b.user("continue", correction=(i % 7 == 0))           # never restates; periodic corrections
        b.tool("Read", f"auth/mod_{i}.py", "read", output_chars=4000)
        b.tool("Grep", f"token_{i}", "search", output_chars=4000)
        b.stop()
    return b.events
