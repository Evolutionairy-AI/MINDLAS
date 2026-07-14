"""Verification Debt — the canonical 4-band VERIFY gauge. Code changed without
fresh evidence it still works. Pure function of VerificationDebtSignals; mirrors
ContextRotScorer (builds its RiskReading directly with band_4, thresholds 25/50/70)."""
from __future__ import annotations

from .base import RiskFact, RiskReading, RiskScorer, band_4
from ..runtime.verification_state import VerificationDebtSignals

WATCH_MIN, WARNING_MIN, ALERT_MIN = 25, 50, 70

_STATUS_TERM = {"pass_fresh": 0, "pass_stale": 10, "unknown": 12,
                "skipped": 15, "timeout": 20, "fail": 25}
_COVERAGE_CREDIT = {"full": 25, "targeted": 18, "partial": 8, "none": 0}


def score_verification_debt(s: VerificationDebtSignals) -> int:
    # A truly clean tree is STABLE even with no prior verifier result.
    if s.changed_file_count == 0 and s.changed_lines == 0 and not s.unresolved_failures:
        return 0

    if s.last_verifier_status == "pass":
        status_term = _STATUS_TERM["pass_fresh"] if s.evidence_is_fresh else _STATUS_TERM["pass_stale"]
    else:
        status_term = _STATUS_TERM.get(s.last_verifier_status, _STATUS_TERM["unknown"])

    coverage_credit = _COVERAGE_CREDIT.get(s.verification_coverage, 0) if s.evidence_is_fresh else 0

    score = (
        min(20, s.changed_file_count * 4)
        + min(15, s.changed_lines / 20)
        + min(15, s.turns_since_last_pass * 1.5)
        + status_term
        + (15 if s.production_without_tests else 0)
        + (10 if s.completion_claim_without_evidence else 0)
        + min(15, s.unresolved_failures * 5)
        + (10 if s.config_mixed_with_source else 0)
        - coverage_credit
    )
    # round() is banker's rounding (half-to-even): the demo/heavy fixtures land on x.5 and
    # resolve to 82/90 (not 83/91). Tests assert bands (>= ALERT_MIN), never an exact half value.
    return max(0, min(100, round(score)))


def verification_debt_trigger(s: VerificationDebtSignals, score: int) -> bool:
    """Recommend the Verify Gate (five signal clauses)."""
    return (
        score >= 65
        or (s.changed_file_count >= 5 and not s.evidence_is_fresh)
        or (bool(s.production_files_changed)
            and s.last_verifier_status in ("fail", "unknown", "skipped", "timeout"))
        or (s.production_without_tests and s.changed_file_count >= 2)
        or s.completion_claim_without_evidence
    )


_SUMMARY_ELEVATED = "Verification Debt is elevated — changed code lacks fresh evidence it works."
_SUMMARY_OK = "Changed code has fresh verification evidence."
_CORRECTION = ("Code changed without a fresh passing verifier. Run the Mindlas Verify Gate to "
               "obtain evidence-based confirmation that the changes still work.")


class VerificationDebtScorer(RiskScorer):
    risk_id = "verification_debt"
    label = "Verification Debt"
    short_label = "VERIFY"
    watch_min = WATCH_MIN
    alert_min = ALERT_MIN

    def score(self, s: VerificationDebtSignals) -> int:   # type: ignore[override]
        return score_verification_debt(s)

    def read(self, s: VerificationDebtSignals) -> RiskReading:   # type: ignore[override]
        score = self.score(s)
        band = band_4(score, watch=WATCH_MIN, warning=WARNING_MIN, alert=ALERT_MIN)
        elevated = band != "STABLE"
        facts: list[RiskFact] = []
        if s.changed_file_count:
            facts.append(RiskFact("files", s.changed_file_count,
                                  f"{s.changed_file_count} files changed"))
        if s.changed_lines:
            facts.append(RiskFact("lines", s.changed_lines, f"{s.changed_lines} lines changed"))
        if s.production_without_tests:
            facts.append(RiskFact("production_without_tests", True,
                                  "production code changed with no test changes"))
        facts.append(RiskFact("last_verifier_status", s.last_verifier_status,
                              f"last verifier: {s.last_verifier_status}"
                              + (" (fresh)" if s.evidence_is_fresh else "")))
        if s.completion_claim_without_evidence:
            facts.append(RiskFact("completion_claim", True,
                                  "completion claimed without fresh evidence"))
        return RiskReading(
            risk_id=self.risk_id, label=self.label, short_label=self.short_label,
            score=score, state=band,
            direction="up" if band in ("WARNING", "ALERT") else "flat",
            confidence="high" if s.changed_file_count else "medium",
            facts=tuple(facts),
            summary=_SUMMARY_ELEVATED if elevated else _SUMMARY_OK,
            correction=_CORRECTION if elevated else "",
            suggested_commands=("/mindlas-verify",) if elevated else (),
            can_verify=True,
        )


_AFTER_CAP = {"full": 10, "targeted": 18, "partial": 35}


def verify_after_score(before: int, after: int, *, status: str, coverage: str,
                       evidence_is_fresh: bool) -> int:
    """Evidence-based after-score caps. `after` is already the re-derived score
    (score_verification_debt over rebuilt signals); these clamp it. Caps for a clean pass apply
    ONLY when evidence_is_fresh; fail/timeout/skipped raise a floor regardless of freshness."""
    if status == "fail":
        return max(before, 95)
    if status == "timeout":
        return max(before, 80)
    if status == "skipped":
        return max(before, 70)
    # status == "pass"
    if evidence_is_fresh and coverage in _AFTER_CAP:
        return min(after, _AFTER_CAP[coverage])
    return after
