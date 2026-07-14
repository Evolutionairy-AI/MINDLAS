"""Change Blast Radius — the 4-band BLAST gauge. Measures how broadly a coding-agent
patch has spread across files, directories, and concerns. Pure function of ChangeBlastSignals;
mirrors VerificationDebtScorer (builds its RiskReading directly with band_4, thresholds
25/50/70). A broad patch is higher REVIEW/COORDINATION risk, not proof the code is wrong."""
from __future__ import annotations

from ..runtime.blast_state import ChangeBlastSignals
from .base import RiskFact, RiskReading, RiskScorer, band_4

WATCH_MIN, WARNING_MIN, ALERT_MIN = 25, 50, 70


def score_change_blast_radius(s: ChangeBlastSignals) -> int:
    """A capped weighted sum minus a cohesion credit, round-then-clamp."""
    if s.changed_file_count == 0 and s.changed_lines == 0:
        return 0
    if s.changed_file_count <= 3 and s.concern_count <= 1 and s.directory_count <= 1:
        cohesion_credit = 15
    elif s.changed_file_count <= 5 and s.concern_count <= 2:
        cohesion_credit = 8
    else:
        cohesion_credit = 0
    score = (
        min(20, s.changed_file_count * 3)
        + min(15, s.changed_lines / 30)
        + min(15, max(0, s.directory_count - 1) * 4)
        + min(20, max(0, s.concern_count - 1) * 5)
        + min(10, max(0, s.file_kind_count - 1) * 4)
        + min(10, len(s.high_centrality_files) * 5)
        + min(10, (len(s.deleted_files) + len(s.renamed_files)) * 5)
        + (10 if s.production_without_tests else 0)
        + (10 if s.config_mixed_with_source else 0)
        + (5 if s.docs_mixed_with_source else 0)
        - cohesion_credit
    )
    return max(0, min(100, round(score)))


def change_blast_trigger(s: ChangeBlastSignals) -> bool:
    """Recommend Patch Splitter. Does NOT fire for small coherent patches even when
    Verification Debt is high — that is Verify Gate's job."""
    score = score_change_blast_radius(s)
    return (
        score >= 65
        or s.changed_file_count >= 8
        or s.concern_count >= 4
        or (s.directory_count >= 4 and s.changed_lines >= 120)
        or (s.config_mixed_with_source and s.changed_file_count >= 3)
        or (s.production_without_tests and s.changed_file_count >= 5)
        or s.max_lines_in_one_file >= 400
        or (len(s.high_centrality_files) >= 2 and s.changed_file_count >= 4)
    )


_SUMMARY_ELEVATED = ("Change Blast Radius is elevated — the patch has spread across many "
                     "files, directories, or concerns.")
_SUMMARY_OK = "The change set is small and coherent."
_CORRECTION = ("The patch is broad enough to raise review and coordination risk. Run the "
               "Mindlas Patch Splitter to partition it into smaller, coherent, reviewable "
               "bundles.")


class ChangeBlastRadiusScorer(RiskScorer):
    risk_id = "change_blast_radius"
    label = "Change Blast Radius"
    short_label = "BLAST"
    watch_min = WATCH_MIN
    alert_min = ALERT_MIN

    def score(self, s: ChangeBlastSignals) -> int:   # type: ignore[override]
        return score_change_blast_radius(s)

    def read(self, s: ChangeBlastSignals) -> RiskReading:   # type: ignore[override]
        score = self.score(s)
        band = band_4(score, watch=WATCH_MIN, warning=WARNING_MIN, alert=ALERT_MIN)
        elevated = band != "STABLE"
        facts: list[RiskFact] = []
        if s.changed_file_count:
            facts.append(RiskFact("files", s.changed_file_count,
                                  f"{s.changed_file_count} files changed"))
        if s.changed_lines:
            facts.append(RiskFact("lines", s.changed_lines, f"{s.changed_lines} lines changed"))
        if s.directory_count:
            facts.append(RiskFact("directories", s.directory_count,
                                  f"{s.directory_count} directories touched"))
        if s.concern_count:
            facts.append(RiskFact("concerns", s.concern_count,
                                  f"{s.concern_count} concerns detected"))
        if s.file_kind_count > 1:
            facts.append(RiskFact("kinds", s.file_kind_count,
                                  f"{s.file_kind_count} file kinds mixed"))
        if s.high_centrality_files:
            facts.append(RiskFact("high_centrality", len(s.high_centrality_files),
                                  f"{len(s.high_centrality_files)} high-centrality files touched"))
        if s.production_without_tests:
            facts.append(RiskFact("production_without_tests", True,
                                  "production code changed with no test changes"))
        if s.config_mixed_with_source:
            facts.append(RiskFact("config_mixed_with_source", True,
                                  "config changed alongside source"))
        return RiskReading(
            risk_id=self.risk_id, label=self.label, short_label=self.short_label,
            score=score, state=band,
            direction="up" if band in ("WARNING", "ALERT") else "flat",
            confidence="high" if s.changed_file_count else "medium",
            facts=tuple(facts),
            summary=_SUMMARY_ELEVATED if elevated else _SUMMARY_OK,
            correction=_CORRECTION if elevated else "",
            suggested_commands=("/mindlas-blast-split",) if elevated else (),
            can_verify=False,
        )
