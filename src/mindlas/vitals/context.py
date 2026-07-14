"""Cheap extraction: events -> stated objective + flagged constraints.

No work-graph, no LLM. Objective = first substantive user_prompt, replaced if a
later prompt explicitly reframes the goal. Constraints = correction-marked prompts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .events import Event, EventKind, Marker

_TRIVIAL = {"ok", "okay", "yes", "no", "y", "n", "go", "continue", "next",
            "thanks", "thank you", "sure", "yep", "yeah", "proceed"}
_REFRAME = re.compile(r"\b(actually|instead|scrap that|forget that|new goal|let'?s switch|change of plan|never mind)\b", re.I)


@dataclass(frozen=True)
class SessionContext:
    objective: str | None
    constraints: tuple[str, ...]


def _substantive(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 12:
        return False
    if t.lower() in _TRIVIAL:
        return False
    if t.startswith("/"):  # slash command
        return False
    return True


def extract_context(events: list[Event]) -> SessionContext:
    objective: str | None = None
    constraints: list[str] = []
    for e in events:
        if e.kind != EventKind.USER_PROMPT:
            continue
        text = e.target or ""
        if Marker.CORRECTION in e.markers:
            constraints.append(text)
        if not _substantive(text):
            continue
        if objective is None:
            objective = text
        elif _REFRAME.search(text):
            objective = text
    return SessionContext(objective=objective, constraints=tuple(constraints))
