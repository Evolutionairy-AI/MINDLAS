"""Shared gauge primitives. Re-exports the scorer/reading contract from
the vitals layer so gauges import from `features`, and adds the four-band
classifier the reliability bars use."""
from __future__ import annotations

from ..vitals.reading import RiskFact, RiskReading   # re-export
from ..vitals.risks.base import RiskScorer            # re-export

__all__ = ["RiskFact", "RiskReading", "RiskScorer", "band_4"]


def band_4(score: int, *, watch: int, warning: int, alert: int) -> str:
    """Four-band state:
    STABLE < watch <= WATCH < warning <= WARNING < alert <= ALERT."""
    if score >= alert:
        return "ALERT"
    if score >= warning:
        return "WARNING"
    if score >= watch:
        return "WATCH"
    return "STABLE"
