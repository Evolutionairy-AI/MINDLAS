"""Tool Failure Loop — the 4-band LOOP gauge. Measures whether a coding agent is stuck
repeatedly failing/retrying the same tool/command without new evidence. Pure function of
ToolFailureLoopSignals; mirrors ChangeBlastRadiusScorer (builds its RiskReading directly with
band_4, thresholds 25/50/70). A high LOOP is a CONTROL risk (blind retry), not proof the tool is
broken — Stop records a controlled halt boundary, it does not repair the tool."""
from __future__ import annotations

from ..runtime.tool_loop_state import ToolFailureLoopSignals
from .base import RiskFact, RiskReading, RiskScorer, band_4

WATCH_MIN, WARNING_MIN, ALERT_MIN = 25, 50, 70


def score_tool_failure_loop(s: ToolFailureLoopSignals) -> int:
    """A capped weighted sum minus a stop-control credit, with repeated-failure
    floors and an active-stop cap. Round-then-clamp 0-100."""
    if s.failed_tool_call_count == 0:
        return 0
    consecutive_failure_term = min(30, max(0, s.consecutive_failure_count - 1) * 12)
    # Score the ACTIVE-segment repetition (blind retry since the last evidence), not the
    # whole-window counts — historical failures already interrupted by new evidence must not inflate.
    same_signature_term = min(25, max(0, s.same_signature_failure_count_active - 1) * 12)
    same_command_retry_term = min(20, max(0, s.same_command_retry_count_active - 1) * 10)
    retry_without_evidence_term = min(20, s.retry_without_new_evidence_count * 10)
    failure_rate_term = min(15, s.failure_rate_pct / 6)
    category_severity_term = min(15,
        s.timeout_count * 4 + s.permission_denied_count * 5 + s.not_found_count * 4
        + s.parse_error_count * 3 + s.unavailable_count * 4 + s.empty_result_count * 2)
    if s.failed_tool_call_count > 0 and not s.success_since_last_failure:
        no_success_term = min(10, s.turns_since_last_success)
    else:
        no_success_term = 0
    stop_control_credit = 60 if s.stop_active else 0
    score = (consecutive_failure_term + same_signature_term + same_command_retry_term
             + retry_without_evidence_term + failure_rate_term + category_severity_term
             + no_success_term - stop_control_credit)
    score = max(0, min(100, round(score)))
    # Minimum-risk floors for an active repeated failure (only while NOT already stopped).
    if s.consecutive_failure_count >= 3 and not s.stop_active:
        score = max(score, 70)
    elif s.same_signature_failure_count_active >= 3 and not s.stop_active:   # floor on active-segment count
        score = max(score, 70)
    elif s.retry_without_new_evidence_count >= 2 and not s.stop_active:
        score = max(score, 65)
    if s.stop_active:                                  # cap -> visible as controlled
        score = min(score, 24)
    return score


def tool_failure_loop_trigger(s: ToolFailureLoopSignals) -> bool:
    """Recommend Stop ONLY for an ACTIVE blind retry loop.
    - Never triggers while a stop is already active.
    - A success since the last failure means the loop is ALREADY broken -> suppress.
    - Repetition is measured over the ACTIVE evidence segment (evidence-aware
      `consecutive_failure_count` + the `_active` counts), so historical failures already interrupted
      by new evidence (a user prompt / a success) do NOT fire Stop. The raw whole-window category
      counts are intentionally NOT independent triggers here; they still feed the score, and
      `score >= 65` (now active-aware) is reachable only WITH active repetition — so a genuinely strong
      active pattern (e.g. two identical permission failures on the same command) still triggers via
      the score, while failures spread across new-evidence boundaries do not."""
    if s.stop_active:
        return False
    if s.success_since_last_failure:
        return False
    return (
        s.consecutive_failure_count >= 3
        or s.retry_without_new_evidence_count >= 2
        or s.same_signature_failure_count_active >= 3
        or s.same_command_retry_count_active >= 3
        or score_tool_failure_loop(s) >= 65
    )


def controlled_after_loop_score(before: int, signals: ToolFailureLoopSignals) -> int:
    """The CONTROLLED after-score. Stop does not repair the tool — it caps LOOP to a
    controlled band once the halt boundary is written. Below WATCH there is nothing to control."""
    if before < 25:
        return before
    return min(15, before)


_SUMMARY_ELEVATED = ("Tool Failure Loop is elevated — the agent is repeatedly failing or retrying "
                     "the same tool/command without new evidence.")
_SUMMARY_OK = "No repeated tool-failure pattern detected."
_SUMMARY_CONTROLLED = ("A stop boundary is active — the repeated failure loop has been interrupted "
                       "and is being held controlled.")
_CORRECTION = ("The same tool failure is repeating without new evidence. Stop the loop and require "
               "a changed plan before retrying — do not retry the same command unchanged.")


class ToolFailureLoopScorer(RiskScorer):
    risk_id = "tool_failure_loop"
    label = "Tool Failure Loop"
    short_label = "LOOP"
    watch_min = WATCH_MIN
    alert_min = ALERT_MIN

    def score(self, s: ToolFailureLoopSignals) -> int:   # type: ignore[override]
        return score_tool_failure_loop(s)

    def read(self, s: ToolFailureLoopSignals) -> RiskReading:   # type: ignore[override]
        score = self.score(s)
        band = band_4(score, watch=WATCH_MIN, warning=WARNING_MIN, alert=ALERT_MIN)
        elevated = band != "STABLE"
        facts: list[RiskFact] = []
        if s.failed_tool_call_count:
            facts.append(RiskFact("failed_tool_calls", s.failed_tool_call_count,
                                  f"{s.failed_tool_call_count} failed tool calls"))
        if s.consecutive_failure_count:
            facts.append(RiskFact("consecutive_failures", s.consecutive_failure_count,
                                  f"{s.consecutive_failure_count} consecutive failures"))
        if s.same_signature_failure_count > 1:
            facts.append(RiskFact("same_signature_failures", s.same_signature_failure_count,
                                  f"{s.same_signature_failure_count} same-signature failures"))
        if s.same_command_retry_count > 1:
            facts.append(RiskFact("same_command_retries", s.same_command_retry_count,
                                  f"{s.same_command_retry_count} same-command retries"))
        if s.retry_without_new_evidence_count:
            facts.append(RiskFact("retry_without_new_evidence", s.retry_without_new_evidence_count,
                                  f"{s.retry_without_new_evidence_count} retries without new evidence"))
        if s.active_tool_name:
            facts.append(RiskFact("active_tool", s.active_tool_name,
                                  f"active tool: {s.active_tool_name}"))
        if s.active_failure_signature:
            facts.append(RiskFact("failure_signature", s.active_failure_signature,
                                  f"failure signature: {s.active_failure_signature}"))
        if s.failed_tool_call_count and s.active_failure_category:
            # Failure category is a distinct fact (not just the signature prefix). Guarded by
            # failure presence so a clean session never surfaces "failure category: unknown".
            facts.append(RiskFact("failure_category", s.active_failure_category,
                                  f"failure category: {s.active_failure_category}"))
        if s.stop_active:
            facts.append(RiskFact("stop_active", True, "a stop boundary is active"))
        summary = (_SUMMARY_CONTROLLED if s.stop_active
                   else (_SUMMARY_ELEVATED if elevated else _SUMMARY_OK))
        show_action = elevated and not s.stop_active
        return RiskReading(
            risk_id=self.risk_id, label=self.label, short_label=self.short_label,
            score=score, state=band,
            direction="up" if band in ("WARNING", "ALERT") else "flat",
            confidence="high" if s.failed_tool_call_count else "medium",
            facts=tuple(facts), summary=summary,
            correction=_CORRECTION if show_action else "",
            suggested_commands=("/mindlas-loop-stop",) if show_action else (),
            can_verify=False,
        )
