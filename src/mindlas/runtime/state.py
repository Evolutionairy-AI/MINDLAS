"""Deterministic Context Rot input signals, derived from the event ledger.

Pure function of a list[Event]; no I/O, no LLM. These are the local, in-session
observables the research warrants (Liu 2023 lost-in-the-middle; multi-turn drift):
turns, accumulated context mass, large tool outputs, how long since the task was last
restated, unresolved corrections, and how long since the last MINDLAS context repair.

Product rules:
- Mass uses the real context-window percentage when the live hook supplies `context_pct`;
  otherwise it is proxied from ledger text + tool-output volume and `mass_is_measured`
  is False (the scorer then treats it as weaker evidence).
- Only `context_repair_end` (Mindlas's own non-native correction) resets the rot clock.
  Native `/compact` (`compact_boundary`) is intentionally NOT a Mindlas correction, so it
  never resets repair age and never clears unresolved corrections.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..vitals.context import _substantive   # reuse the same "real instruction?" test
from ..vitals.events import Event, EventKind, Marker

# Proxy budget: total characters of ledger text + tool output that maps to ~100% session
# mass when the real context percentage is unavailable. A starting value, tuned vs fixtures.
MASS_BUDGET_CHARS = 12000

# A tool result at/above this many characters counts as one "large tool output".
LARGE_TOOL_OUTPUT_CHARS = 2000


@dataclass(frozen=True)
class ContextRotSignals:
    session_id: str
    turn_count: int
    session_mass_pct: float
    large_tool_outputs: int
    task_contract_age_turns: int
    unresolved_assumptions: int
    last_repair_age_turns: int
    mass_is_measured: bool   # True if from real context_pct, False if proxied


def _last_restatement_turn(events: list[Event]) -> int:
    """Turn of the most recent substantive user prompt — when the task was last (re)stated."""
    last = 0
    for e in events:
        if e.kind == EventKind.USER_PROMPT and _substantive(e.target or ""):
            last = e.turn
    return last


def _last_repair_turn(events: list[Event]) -> int | None:
    """Turn of the most recent MINDLAS Context Repair. Native /compact (compact_boundary) is
    deliberately NOT counted — only Mindlas's own non-native correction resets the rot clock."""
    last = None
    for e in events:
        if e.kind == EventKind.CONTEXT_REPAIR_END:
            last = e.turn
    return last


def build_context_rot_signals(events: list[Event], *, context_pct: float | None = None,
                              now_turn: int | None = None) -> ContextRotSignals:
    sid = events[0].session_id if events else "session"
    turn = now_turn if now_turn is not None else max((e.turn for e in events), default=0)

    turn_count = sum(1 for e in events if e.kind == EventKind.USER_PROMPT)

    if context_pct is not None:
        mass_pct = max(0.0, min(100.0, float(context_pct)))
        measured = True
    else:
        chars = sum(len(e.target or "") + (e.output_chars or 0) for e in events)
        mass_pct = min(100.0, 100.0 * chars / MASS_BUDGET_CHARS)
        measured = False

    large_tool_outputs = sum(1 for e in events
                             if e.kind == EventKind.TOOL_CALL and (e.output_chars or 0) >= LARGE_TOOL_OUTPUT_CHARS)

    last_restate = _last_restatement_turn(events)
    contract_age = max(0, turn - last_restate) if last_restate else turn

    repair_turn = _last_repair_turn(events)
    unresolved = sum(1 for e in events
                     if e.kind == EventKind.USER_PROMPT and Marker.CORRECTION in e.markers
                     and e.turn > (repair_turn or 0))

    last_repair_age = (turn - repair_turn) if repair_turn is not None else turn

    return ContextRotSignals(
        session_id=sid, turn_count=turn_count, session_mass_pct=mass_pct,
        large_tool_outputs=large_tool_outputs, task_contract_age_turns=contract_age,
        unresolved_assumptions=unresolved, last_repair_age_turns=last_repair_age,
        mass_is_measured=measured,
    )
