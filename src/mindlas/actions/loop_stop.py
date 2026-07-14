"""Stop action: the source-read-only + environment-read-only correction for Tool Failure
Loop. preview() computes signals + trigger and writes nothing; apply() writes a controlled stop
boundary under .mindlas/stops/, appends a `type="loop_stop"` correction, and updates the scorecard.
It runs NO commands, mutates NO source, stages/commits nothing, calls no LLM. The after-score
key is `controlled_after_loop` (not modeled/planned/evidence-based) and can_verify stays False.
Honors actions->runtime layering (imports runtime + features; runtime never imports actions). One
resolved root threads to artifacts + corrections + scorecard."""
from __future__ import annotations

import hashlib
import json

from .base import Action
from ..runtime import paths
from ..runtime.loop_stop_artifacts import (DEFAULT_NEXT_ACTIONS, StopArtifactPayload,
                                           write_stop_artifacts)
from ..runtime.loop_stop_types import StopContext, StopPreview, StopResult
from ..runtime.scorecard import write_scorecard
from ..runtime.tool_loop_state import build_tool_failure_loop_signals
from ..features.tool_failure_loop import (
    controlled_after_loop_score, score_tool_failure_loop, tool_failure_loop_trigger)
from ..vitals.context import extract_context


class LoopStop(Action):
    action_id = "loop_stop"

    @staticmethod
    def _stop_id(stamp: str, signals) -> str:
        # Stop id format {now}_{hash4}. Prefer the active failure signature; fall back to the loop hash.
        basis = signals.active_failure_signature or signals.current_loop_hash
        return f"{stamp}_{hashlib.sha256((basis or '').encode('utf-8')).hexdigest()[:4]}"

    def _signals(self, state: StopContext, root):
        return build_tool_failure_loop_signals(
            state.events, session_id=state.session_id, now_turn=state.now_turn,
            project_root=root, window_turns=state.window_turns)

    def preview(self, state: StopContext) -> StopPreview:   # type: ignore[override]
        root = state.project_root or paths.project_root()
        signals = self._signals(state, root)
        before = score_tool_failure_loop(signals)
        trigger = tool_failure_loop_trigger(signals)
        stop_id = self._stop_id("preview", signals)
        if signals.stop_active:
            expl = "A stop boundary is already active; no new Stop is recommended."
        elif not trigger:
            expl = "No repeated tool-failure loop detected; Stop is not recommended."
        else:
            expl = (f"Repeated tool failure detected — signature {signals.active_failure_signature}, "
                    f"tool {signals.active_tool_name}. Stop the loop and require a changed plan. "
                    f"No source files will be modified.")
        return StopPreview(before=before, trigger=trigger, signals=signals, stop_id=stop_id,
                           explanation=expl, recommended_next_actions=DEFAULT_NEXT_ACTIONS)

    def apply(self, state: StopContext, *, now: str) -> StopResult:   # type: ignore[override]
        root = state.project_root or paths.project_root()                    # step 0: resolve one root
        signals = self._signals(state, root)                                 # step 1
        before = score_tool_failure_loop(signals)                            # step 2
        stop_id = self._stop_id(now, signals)
        if signals.stop_active:                                              # step 3: already active
            return self._suppressed(before, "already_stopped", stop_id, signals, state)
        if not tool_failure_loop_trigger(signals):                          # step 4: no trigger
            return self._suppressed(before, "no_stop", stop_id, signals, state)
        controlled_after = controlled_after_loop_score(before, signals)      # step 5
        payload = StopArtifactPayload(
            stop_id=stop_id, session_id=state.session_id, created_at=now,
            stop_turn=state.now_turn, before=before, controlled_after_loop=controlled_after,
            status="controlled", failure_signature=signals.active_failure_signature,
            active_tool_name=signals.active_tool_name,
            active_command_fingerprint=signals.active_command_fingerprint,
            active_failure_category=signals.active_failure_category,
            consecutive_failure_count=signals.consecutive_failure_count,
            same_signature_failure_count=signals.same_signature_failure_count,
            same_command_retry_count=signals.same_command_retry_count,
            retry_without_new_evidence_count=signals.retry_without_new_evidence_count,
            recommended_next_actions=DEFAULT_NEXT_ACTIONS)
        manifest_path, active_path, _m = write_stop_artifacts(payload, project_root=root)  # step 6
        result = StopResult(
            applied=True, before=before, controlled_after_loop=controlled_after,
            status="controlled", stop_id=stop_id, manifest_path=manifest_path,
            active_stop_path=active_path, failure_signature=signals.active_failure_signature,
            active_tool_name=signals.active_tool_name,
            recommended_next_actions=DEFAULT_NEXT_ACTIONS,
            rails_labels=self._rails_labels(signals, before, controlled_after, stop_id,
                                            state.human_decision),
            human_decision=state.human_decision)
        self._append_correction(result, root)                                # step 7: same root
        task = extract_context(list(state.events)).objective or "(unknown task)"
        write_scorecard(session_id=state.session_id, task=task, root=root)    # step 8: same root
        return result

    @staticmethod
    def _suppressed(before, status, stop_id, signals, state) -> StopResult:
        # a suppressed apply writes NO artifacts and NO correction — never a fake win.
        return StopResult(applied=False, before=before, controlled_after_loop=before, status=status,
                          stop_id=stop_id, manifest_path="", active_stop_path="",
                          failure_signature=signals.active_failure_signature,
                          active_tool_name=signals.active_tool_name,
                          recommended_next_actions=DEFAULT_NEXT_ACTIONS, rails_labels={},
                          human_decision=state.human_decision)

    @staticmethod
    def _append_correction(result: StopResult, root) -> None:
        cp = paths.corrections_path(root)                    # corrections land under `root`
        cp.parent.mkdir(parents=True, exist_ok=True)
        with cp.open("a", encoding="utf-8") as f:
            f.write(json.dumps(result.to_dict()) + "\n")

    @staticmethod
    def _rails_labels(signals, before, controlled_after, stop_id, human_decision=None) -> dict:
        return {
            "tool_failure_loop_before": before,
            "tool_failure_loop_controlled_after": controlled_after,
            "loop_stop_status": "controlled",
            "loop_stop_stop_id": stop_id,
            "loop_stop_failure_signature": signals.active_failure_signature,
            "loop_stop_active_tool_name": signals.active_tool_name,
            "loop_stop_active_command_fingerprint": signals.active_command_fingerprint,
            "loop_stop_failure_category": signals.active_failure_category,
            "consecutive_failure_count": signals.consecutive_failure_count,
            "same_signature_failure_count": signals.same_signature_failure_count,
            "same_command_retry_count": signals.same_command_retry_count,
            "retry_without_new_evidence_count": signals.retry_without_new_evidence_count,
            "failure_rate_pct": signals.failure_rate_pct,
            "timeout_count": signals.timeout_count,
            "permission_denied_count": signals.permission_denied_count,
            "not_found_count": signals.not_found_count,
            "parse_error_count": signals.parse_error_count,
            "empty_result_count": signals.empty_result_count,
            "unavailable_count": signals.unavailable_count,
            "stop_active": True,
            "source_files_modified": False,
            "commands_run_by_stop": 0,
            # Honesty: declare live-capture scope so downstream never overclaims.
            "loop_capture_scope": "PostToolUseFailure only; built-in tool_use_error not live-captured",
            "built_in_tool_failures_live_captured": False,
            "recommended_next_actions": list(DEFAULT_NEXT_ACTIONS),
            "human_override_if_any": human_decision,
        }
