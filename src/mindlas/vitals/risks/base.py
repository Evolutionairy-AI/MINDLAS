"""Common interface every gauge implements. A scorer is a pure function of its
gauge's signals dataclass (ContextRotSignals, VerificationDebtSignals, ...) that yields
a 0-100 score and a fact-grounded RiskReading."""
from __future__ import annotations

from ..reading import RiskFact, RiskReading, state_for_score


class RiskScorer:
    risk_id: str = ""
    label: str = ""
    short_label: str = ""
    watch_min: int = 40
    alert_min: int = 65

    def score(self, state) -> int:
        raise NotImplementedError

    def read(self, state) -> RiskReading:
        raise NotImplementedError

    def _reading(self, state, *, score: int, direction: str,
                 confidence: str, facts: tuple[RiskFact, ...], summary: str,
                 correction: str, suggested_commands: tuple[str, ...],
                 can_verify: bool) -> RiskReading:
        return RiskReading(
            risk_id=self.risk_id, label=self.label, short_label=self.short_label,
            score=score, state=state_for_score(score, watch=self.watch_min, alert=self.alert_min),
            direction=direction, confidence=confidence, facts=facts, summary=summary,
            correction=correction, suggested_commands=suggested_commands, can_verify=can_verify,
        )
