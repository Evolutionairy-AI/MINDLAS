"""Verify Gate action: the non-native correction for Verification Debt. preview()
plans deterministic Python checks and writes nothing; apply() runs them, writes a real verifier
result + a type-partitioned scorecard, and re-derives an EVIDENCE-BASED after-score (never
modeled). Honors actions->runtime layering (imports runtime; runtime never imports actions)."""
from __future__ import annotations

import json
from pathlib import Path

from .base import Action
from ..runtime import paths
from ..runtime.scorecard import write_scorecard
from ..runtime.verification_state import build_verification_debt_signals
from ..runtime.verify_exec import run_verify_commands, summarize_verify_status, coverage_of
from ..runtime.verify_plan import plan_verify_commands
from ..runtime.verify_types import VerifyContext, VerifyPreview, VerifyResult
from ..features.verification_debt import (
    score_verification_debt, verification_debt_trigger, verify_after_score)
from ..vitals.context import extract_context


class VerifyGate(Action):
    action_id = "verify_gate"

    def preview(self, state: VerifyContext) -> VerifyPreview:   # type: ignore[override]
        root = state.project_root or paths.project_root()
        signals = build_verification_debt_signals(
            state.events, session_id=state.session_id, now_turn=state.now_turn,
            project_root=root)
        before = score_verification_debt(signals)
        commands = plan_verify_commands(signals, project_root=root,
                                        allow_full_suite=state.allow_full_suite,
                                        max_seconds=state.max_seconds)
        trigger = verification_debt_trigger(signals, before)
        if commands:
            status = "planned"
            explanation = "Plan: " + "; ".join(c.label for c in commands) + f" — VERIFY {before}."
        else:
            status = "skipped"
            explanation = ("No safe deterministic check could be planned (no Python config or "
                           "tooling); Verification Debt is preserved.")
        return VerifyPreview(before=before, trigger=trigger, signals=signals,
                             commands=commands, status=status, explanation=explanation)

    def apply(self, state: VerifyContext, *, now: str) -> VerifyResult:   # type: ignore[override]
        root = state.project_root or paths.project_root()                   # step 0: resolve one root
        signals = build_verification_debt_signals(
            state.events, session_id=state.session_id, now_turn=state.now_turn, project_root=root)
        before = score_verification_debt(signals)                           # step 1
        commands = plan_verify_commands(signals, project_root=root,
                                        allow_full_suite=state.allow_full_suite,
                                        max_seconds=state.max_seconds)
        if commands:                                                        # step 3
            results = run_verify_commands(commands, project_root=root)
            status = summarize_verify_status(results)
            coverage = coverage_of(commands, results)
        else:                                                               # step 2 (skipped)
            results, status, coverage = (), "skipped", "none"

        covered = tuple(f for f in signals.changed_files if f.endswith(".py"))
        payload = self._result_payload(state, signals, status, coverage, results, now, covered)
        result_path = self._dump_result(state.session_id, root, payload, now)   # step 4 (no after yet)

        fresh = build_verification_debt_signals(                            # step 5 (rebuild)
            state.events, session_id=state.session_id, now_turn=state.now_turn, project_root=root)
        raw_after = score_verification_debt(fresh)
        after = verify_after_score(before, raw_after, status=status, coverage=coverage,
                                   evidence_is_fresh=fresh.evidence_is_fresh)   # step 6
        payload["before"], payload["after"] = before, after                 # step 6b (complete JSON)
        payload["state"] = "complete"                                        # finalize two-phase write
        self._dump_result(state.session_id, root, payload, now)

        rails = self._rails_labels(state, signals, fresh, before, after,    # observability labels
                                   status, coverage, commands, results, covered)
        vr = VerifyResult(applied=True, before=before, after=after, status=status,
                          coverage=coverage, commands=results, changed_files_covered=covered,
                          diff_hash=signals.current_diff_hash, result_path=str(result_path),
                          rails_labels=rails, human_decision=state.human_decision)
        self._append_correction(vr)                                         # step 7
        task = extract_context(list(state.events)).objective or "(unknown task)"  # step 8
        write_scorecard(session_id=state.session_id, task=task)
        return vr

    @staticmethod
    def _result_payload(state, signals, status, coverage, results, now, covered) -> dict:
        return {
            # written "incomplete" at step 4 (before before/after exist), rewritten
            # "complete" at step 6b — a crash between the two writes never leaves a silently-partial
            # result masquerading as final. The step-5 freshness rebuild reads status/coverage/
            # diff_hash/verifier_turn only, so an "incomplete" result is still a valid read.
            "type": "verify_gate", "state": "incomplete",
            "session_id": state.session_id, "created_at": now,
            "verifier_turn": state.now_turn, "status": status, "coverage": coverage,
            "diff_hash": signals.current_diff_hash,
            "changed_files": list(signals.changed_files), "changed_files_covered": list(covered),
            "commands": [{"label": r.label, "command": list(r.command), "exit_code": r.exit_code,
                          "status": r.status, "duration_ms": r.duration_ms,
                          "stdout_tail": r.stdout_tail, "stderr_tail": r.stderr_tail}
                         for r in results],
        }

    @staticmethod
    def _dump_result(session_id: str, root, payload: dict, now: str) -> Path:
        text = json.dumps(payload, indent=2)
        latest = paths.latest_verifier_result_path(session_id, root)
        latest.parent.mkdir(parents=True, exist_ok=True)
        latest.write_text(text, encoding="utf-8")
        snap = paths.verifier_result_snapshot_path(session_id, now, root)
        snap.parent.mkdir(parents=True, exist_ok=True)
        snap.write_text(text, encoding="utf-8")
        return latest

    @staticmethod
    def _append_correction(vr) -> None:
        cp = paths.corrections_path()           # env-keyed (== root/.mindlas/... with one resolved root)
        cp.parent.mkdir(parents=True, exist_ok=True)
        with cp.open("a", encoding="utf-8") as f:
            f.write(json.dumps(vr.to_dict()) + "\n")

    @staticmethod
    def _failure_signature(results) -> str:
        for r in results:
            if r.status in ("fail", "timeout"):
                detail = (r.stderr_tail or r.stdout_tail or "")[:120]
                return f"{r.label}:{r.status}:{detail}"
        return ""

    @staticmethod
    def _rails_labels(state, signals, fresh, before, after, status, coverage,
                      commands, results, covered) -> dict:
        return {
            "verification_debt_before": before, "verification_debt_after": after,
            "verify_gate_trigger_reason": "elevated verification debt; changed code without "
                                          "fresh passing evidence",
            "changed_file_count": signals.changed_file_count,
            "changed_lines": signals.changed_lines,
            "production_without_tests": signals.production_without_tests,
            "completion_claim_without_evidence": signals.completion_claim_without_evidence,
            "last_verifier_status": fresh.last_verifier_status,
            "diff_hash_before": signals.current_diff_hash,
            "diff_hash_after": fresh.current_diff_hash,
            "verify_gate_commands_planned": len(commands),
            "verify_gate_commands_run": len(results),
            "verify_gate_status": status, "verify_gate_coverage": coverage,
            "verify_gate_failure_signature": VerifyGate._failure_signature(results),
            "changed_files_covered": list(covered),
            "evidence_fresh_after": fresh.evidence_is_fresh,
            "human_override_if_any": state.human_decision,
        }
