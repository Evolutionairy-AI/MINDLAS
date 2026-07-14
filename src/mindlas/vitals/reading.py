"""Typed result of a risk scorer. Pure data; no I/O.

A RiskReading is what every gauge produces: a 0-100 score, a coarse state, the
hard facts behind the score, and a concrete correction. Frozen + tuple-valued so
it is hashable and cheap to cache, matching the vitals/ idiom (Event.markers etc.).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RiskFact:
    key: str
    value: Any
    text: str            # human-readable, shown verbatim in vitals -v / alerts
    weight: float = 0.0   # optional: contribution to the score, for diagnostics


@dataclass(frozen=True)
class RiskReading:
    risk_id: str          # context_rot | verification_debt | change_blast_radius | tool_failure_loop
    label: str            # "Verification Debt"
    short_label: str      # "VERIFY"
    score: int            # 0..100
    state: str            # OK | WATCH | ALERT | SECURITY
    direction: str        # up | down | flat | unknown
    confidence: str       # low | medium | high (signal quality, not model confidence)
    facts: tuple[RiskFact, ...]
    summary: str
    correction: str
    suggested_commands: tuple[str, ...]
    can_verify: bool

    def to_dict(self) -> dict:
        return asdict(self)


def state_for_score(score: int, *, watch: int, alert: int) -> str:
    """Coarse state from a score and two thresholds. (Hysteresis/sustained logic is
    added in a later phase; v1 of Verification Debt alerts immediately by design.)"""
    if score >= alert:
        return "ALERT"
    if score >= watch:
        return "WATCH"
    return "OK"
