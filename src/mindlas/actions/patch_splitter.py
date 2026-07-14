"""Patch Splitter action: the source-read-only correction for Change Blast Radius.
preview() plans a deterministic split and writes nothing; apply() writes a validated split queue
under .mindlas/splits/ + a PLANNED scorecard win — it NEVER modifies, stages, commits, or rewrites
source. The after-score key is `planned_after_blast` (not modeled, not evidence-based) and
can_verify stays False. Honors actions->runtime layering (imports runtime + features; runtime never
imports actions). The per-bundle scorer is passed down so the runtime planner stays feature-free."""
from __future__ import annotations

import json

from .base import Action
from ..runtime import git_helpers, paths
from ..runtime.blast_state import build_file_changes, build_signals_from_changes
from ..runtime.patch_split_artifacts import write_split_artifacts
from ..runtime.patch_split_plan import plan_patch_split
from ..runtime.scorecard import write_scorecard
from ..runtime.split_types import SplitContext, SplitPreview, SplitResult
from ..features.blast_radius import change_blast_trigger, score_change_blast_radius
from ..vitals.context import extract_context

class PatchSplitter(Action):
    action_id = "patch_splitter"

    def _plan(self, state: SplitContext, *, now):
        root = state.project_root or paths.project_root()
        changes, dh = build_file_changes(state.events, session_id=state.session_id,
                                         project_root=root)
        signals = build_signals_from_changes(changes, session_id=state.session_id, diff_hash=dh)
        untracked = git_helpers.untracked(root)
        plan = plan_patch_split(signals, changes, session_id=state.session_id,
                                score_fn=score_change_blast_radius, untracked=untracked,
                                max_bundle_files=state.max_bundle_files,
                                max_bundle_lines=state.max_bundle_lines,
                                allow_single_large=state.allow_single_file_large_bundle, now=now)
        return root, signals, plan

    @staticmethod
    def _no_reduction(plan, before: int) -> bool:
        # 1 bundle, or the dominant bundle's own score >= before, so planned == before.
        return len(plan.bundles) <= 1 or plan.planned_after_blast >= before

    def preview(self, state: SplitContext) -> SplitPreview:   # type: ignore[override]
        _root, signals, plan = self._plan(state, now=None)
        before = score_change_blast_radius(signals)
        trigger = change_blast_trigger(signals)
        if self._no_reduction(plan, before):
            expl = "Diff is already coherent; no split produced."
        else:
            expl = (f"Plan {len(plan.bundles)} bundle(s); BLAST {before} → "
                    f"{plan.planned_after_blast} planned (validation {plan.validation_status}). "
                    f"No source files will be modified.")
        return SplitPreview(before=before, trigger=trigger, signals=signals, plan=plan,
                            explanation=expl)

    def apply(self, state: SplitContext, *, now: str) -> SplitResult:   # type: ignore[override]
        root, signals, plan = self._plan(state, now=now)
        before = score_change_blast_radius(signals)
        if self._no_reduction(plan, before):                 # no artifacts, no correction, no win
            return SplitResult(applied=False, before=before, planned_after=before,
                               status="no_split", split_id=plan.split_id, manifest_path="",
                               bundles=(), original_diff_hash=signals.current_diff_hash,
                               rails_labels={}, human_decision=state.human_decision)
        manifest_path, _manifest = write_split_artifacts(plan, project_root=root)
        result = SplitResult(applied=True, before=before, planned_after=plan.planned_after_blast,
                             status=plan.validation_status, split_id=plan.split_id,
                             manifest_path=manifest_path, bundles=plan.bundles,
                             original_diff_hash=signals.current_diff_hash,
                             rails_labels=self._rails_labels(signals, plan, before,
                                                             state.human_decision),
                             human_decision=state.human_decision)
        self._append_correction(result, root)                # same root as artifacts
        task = extract_context(list(state.events)).objective or "(unknown task)"
        write_scorecard(session_id=state.session_id, task=task, root=root)   # same root
        return result

    @staticmethod
    def _append_correction(result: SplitResult, root) -> None:
        cp = paths.corrections_path(root)                    # corrections land under `root`
        cp.parent.mkdir(parents=True, exist_ok=True)
        with cp.open("a", encoding="utf-8") as f:
            f.write(json.dumps(result.to_dict()) + "\n")

    @staticmethod
    def _rails_labels(signals, plan, before, human_decision=None) -> dict:
        covered = [p for b in plan.bundles for p in b.files]
        overlap_count = len(covered) - len(set(covered))
        union_complete = set(covered) == set(signals.changed_files) and overlap_count == 0
        return {
            "change_blast_before": before,
            "change_blast_planned_after": plan.planned_after_blast,
            "patch_splitter_status": plan.validation_status,
            "patch_splitter_split_id": plan.split_id,
            "patch_splitter_bundle_count": len(plan.bundles),
            "patch_splitter_validation_status": plan.validation_status,
            "patch_splitter_original_diff_hash": plan.original_diff_hash,
            "changed_file_count": signals.changed_file_count,
            "changed_lines": signals.changed_lines,
            "directory_count": signals.directory_count,
            "concern_count": signals.concern_count,
            "file_kind_count": signals.file_kind_count,
            "production_without_tests": signals.production_without_tests,
            "config_mixed_with_source": signals.config_mixed_with_source,
            "docs_mixed_with_source": signals.docs_mixed_with_source,
            "high_centrality_file_count": len(signals.high_centrality_files),
            "bundle_file_counts": [b.file_count for b in plan.bundles],
            "bundle_line_counts": [b.changed_lines for b in plan.bundles],
            "bundle_concern_counts": [len(b.concerns) for b in plan.bundles],
            "bundle_scores": [b.risk_score for b in plan.bundles],
            "bundle_warnings": [list(b.warnings) for b in plan.bundles],
            "split_union_complete": union_complete,
            "split_overlap_count": overlap_count,
            "source_files_modified": False,
            "verify_gate_recommended_after_split": True,
            "human_override_if_any": human_decision,       # consistency with Verify Gate
        }
