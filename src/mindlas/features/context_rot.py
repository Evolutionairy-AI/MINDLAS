"""Context Rot gauge: a deterministic 0-100 score from ledger signals, the four
bands STABLE/WATCH/WARNING/ALERT, and the Mindlas Context Repair recommendation.

Estimated (proxied) context mass is treated as weaker evidence: it cannot, on its own,
escalate to a strong ALERT — see ContextRotScorer.read."""
from __future__ import annotations

from .base import RiskFact, RiskReading, RiskScorer, band_4
from ..runtime.state import ContextRotSignals

WATCH_MIN, WARNING_MIN, ALERT_MIN = 40, 65, 80


def _mass_term(s: ContextRotSignals) -> float:
    return min(30.0, s.session_mass_pct * 0.30)


def _non_mass_score(s: ContextRotSignals) -> int:
    """The score from the hard (non-mass) signals only — the corroboration available when
    mass is merely estimated."""
    raw = (
        min(20.0, s.turn_count / 2)
        + min(15.0, s.large_tool_outputs * 3)
        + min(15.0, s.task_contract_age_turns * 0.75)
        + min(10.0, s.unresolved_assumptions * 2)
        + min(10.0, s.last_repair_age_turns * 0.5)
    )
    return max(0, min(100, round(raw)))


def score_context_rot(s: ContextRotSignals) -> int:
    """A capped weighted sum, clamped to 0..100."""
    return max(0, min(100, round(_mass_term(s) + _non_mass_score(s))))


def context_rot_trigger(s: ContextRotSignals, score: int) -> bool:
    """Recommend Context Repair. Reads only CTX signals — deliberately no
    cross-gauge rule (each gauge triggers independently)."""
    return score >= WARNING_MIN or s.task_contract_age_turns > 25


_SUMMARY_ELEVATED = "Context Rot is elevated."
_SUMMARY_OK = "Context is healthy."
_CORRECTION = ("The session has accumulated stale context, buried constraints, or "
               "tool-output mass. Run Mindlas Context Repair to compact and restate "
               "the task contract.")


class ContextRotScorer(RiskScorer):
    risk_id = "context_rot"
    label = "Context Rot"
    short_label = "ROT"
    watch_min = WATCH_MIN
    alert_min = ALERT_MIN

    def score(self, state: ContextRotSignals) -> int:   # type: ignore[override]
        return score_context_rot(state)

    def read(self, state: ContextRotSignals) -> RiskReading:   # type: ignore[override]
        s = state
        score = self.score(s)
        band = band_4(score, watch=WATCH_MIN, warning=WARNING_MIN, alert=ALERT_MIN)
        # Estimated mass must not, on its own, escalate to a strong ALERT.
        # ALERT on proxied mass requires the hard (non-mass) signals to clear the WARNING line.
        if band == "ALERT" and not s.mass_is_measured and _non_mass_score(s) < WARNING_MIN:
            band = "WARNING"
        elevated = band != "STABLE"
        mass_txt = (f"context/session mass ~{round(s.session_mass_pct)}%"
                    + ("" if s.mass_is_measured else " (estimated)"))
        facts = (
            RiskFact("turn_count", s.turn_count, f"{s.turn_count} turns in the session"),
            RiskFact("task_contract_age_turns", s.task_contract_age_turns,
                     f"{s.task_contract_age_turns} turns since the task was last restated"),
            RiskFact("large_tool_outputs", s.large_tool_outputs,
                     f"{s.large_tool_outputs} large tool outputs accumulated"),
            RiskFact("session_mass_pct", round(s.session_mass_pct), mass_txt),
        )
        return RiskReading(
            risk_id=self.risk_id, label=self.label, short_label=self.short_label,
            score=score, state=band,
            direction="up" if band in ("WARNING", "ALERT") else "flat",
            confidence="high" if s.mass_is_measured else "medium",
            facts=facts,
            summary=_SUMMARY_ELEVATED if elevated else _SUMMARY_OK,
            correction=_CORRECTION if elevated else "",
            suggested_commands=("/mindlas-repair",) if elevated else (),
            can_verify=False,
        )
