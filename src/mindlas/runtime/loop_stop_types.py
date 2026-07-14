"""Shared types for Stop: StopContext (input), StopPreview (dry-run), StopResult (apply
output + corrections record). They live in runtime so runtime helpers can produce them without
importing actions (preserves the actions->runtime layering). StopResult.to_dict is the `loop_stop`
correction record — its after-score key is `controlled_after_loop` (the CONTROLLED honesty
category, distinct from CTX's `modeled_after_ctx`, VERIFY's `after`, and BLAST's
`planned_after_blast`). Never `after`/`modeled_after_loop`/`planned_after_loop`."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..vitals.events import Event
from .tool_loop_state import ToolFailureLoopSignals


@dataclass(frozen=True)
class StopContext:
    events: tuple[Event, ...]
    session_id: str
    now_turn: int
    project_root: Path | None = None
    window_turns: int = 20
    human_decision: str | None = None      # optional human override (consistency with Verify Gate)


@dataclass(frozen=True)
class StopPreview:
    before: int
    trigger: bool
    signals: ToolFailureLoopSignals
    stop_id: str
    explanation: str
    recommended_next_actions: tuple[str, ...]


@dataclass(frozen=True)
class StopResult:
    applied: bool
    before: int
    controlled_after_loop: int
    status: str                  # controlled | no_stop | already_stopped | fail
    stop_id: str
    manifest_path: str
    active_stop_path: str
    failure_signature: str
    active_tool_name: str
    recommended_next_actions: tuple[str, ...]
    rails_labels: dict
    human_decision: str | None = None

    def to_dict(self) -> dict:
        # FIRST key is "type" so the scorecard readers can partition. The after-score
        # key is `controlled_after_loop` (never "after"/"modeled_after_ctx"/"planned_after_blast"),
        # so the CONTROLLED honesty category is inferable from the key alone. before /
        # controlled_after_loop / status / failure_signature are exactly what build_scorecard reads.
        return {
            "type": "loop_stop",
            "before": self.before,
            "controlled_after_loop": self.controlled_after_loop,
            "status": self.status,
            "stop_id": self.stop_id,
            "failure_signature": self.failure_signature,
            "active_tool_name": self.active_tool_name,
            "manifest_path": self.manifest_path,
            "active_stop_path": self.active_stop_path,     # full artifact traceability
            "recommended_next_actions": list(self.recommended_next_actions),
            "human_decision": self.human_decision,
            "rails_labels": self.rails_labels,
        }
