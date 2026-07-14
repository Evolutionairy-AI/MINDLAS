"""Shared types for the Patch Splitter: SplitContext (input), SplitBundle/SplitPlan
(the deterministic plan), SplitPreview (dry-run), SplitResult (apply output + corrections record).
They live in runtime so runtime helpers can produce them without importing actions (preserves the
actions->runtime layering). SplitResult.to_dict is the `patch_splitter` correction record — its
after-score key is `planned_after_blast` (the PLANNED honesty category, distinct from CTX's
`modeled_after_ctx` and VERIFY's `after`)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..vitals.events import Event
from .blast_state import ChangeBlastSignals


@dataclass(frozen=True)
class SplitContext:
    events: tuple[Event, ...]
    session_id: str
    now_turn: int
    project_root: Path | None = None
    max_bundle_files: int = 5
    max_bundle_lines: int = 250
    allow_single_file_large_bundle: bool = True
    human_decision: str | None = None      # optional human override (consistency with Verify Gate)


@dataclass(frozen=True)
class SplitBundle:
    bundle_id: str
    name: str
    reason: str
    files: tuple[str, ...]
    file_count: int
    changed_lines: int
    kinds: tuple[str, ...]
    concerns: tuple[str, ...]
    coverage: str               # "complete" | "untracked_only"
    risk_score: int
    has_tests: bool
    has_production: bool
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class SplitPlan:
    split_id: str
    session_id: str
    created_at: str
    before_blast: int
    planned_after_blast: int
    original_diff_hash: str
    bundles: tuple[SplitBundle, ...]
    validation_status: str      # "pass" | "warning" | "fail"
    validation_messages: tuple[str, ...]


@dataclass(frozen=True)
class SplitPreview:
    before: int
    trigger: bool
    signals: ChangeBlastSignals
    plan: SplitPlan
    explanation: str


@dataclass(frozen=True)
class SplitResult:
    applied: bool
    before: int
    planned_after: int
    status: str
    split_id: str
    manifest_path: str
    bundles: tuple[SplitBundle, ...]
    original_diff_hash: str
    rails_labels: dict
    human_decision: str | None = None        # optional human override, carried into the record

    def to_dict(self) -> dict:
        # FIRST key is "type" so the scorecard readers can partition. The
        # after-score key is `planned_after_blast` (never "after"/"modeled_after_ctx"), so the
        # PLANNED honesty category is inferable from the key alone. These are exactly the keys
        # build_scorecard already reads (before / planned_after_blast / status / bundle_count);
        # manifest_path + human_decision are extra audit fields the scorecard ignores.
        return {
            "type": "patch_splitter",
            "before": self.before,
            "planned_after_blast": self.planned_after,
            "status": self.status,
            "split_id": self.split_id,
            "bundle_count": len(self.bundles),
            "original_diff_hash": self.original_diff_hash,
            "manifest_path": self.manifest_path,
            "human_decision": self.human_decision,
            "rails_labels": self.rails_labels,
        }
