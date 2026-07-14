"""Shared types for the Verify Gate: VerifyContext (input), VerifyPreview
(dry-run), VerifyResult (apply output + corrections record). They live in runtime so runtime
helpers can produce them without importing actions (preserves actions->runtime layering)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..vitals.events import Event
from .verification_state import VerificationDebtSignals
from .verify_plan import VerifyCommand
from .verify_exec import VerifyCommandResult


@dataclass(frozen=True)
class VerifyContext:
    events: tuple[Event, ...]
    session_id: str
    now_turn: int
    project_root: Path | None = None
    max_seconds: int = 120
    allow_full_suite: bool = False
    human_decision: str | None = None


@dataclass(frozen=True)
class VerifyPreview:
    before: int
    trigger: bool
    signals: VerificationDebtSignals
    commands: tuple[VerifyCommand, ...]
    status: str                 # "planned" | "skipped"
    explanation: str


@dataclass(frozen=True)
class VerifyResult:
    applied: bool
    before: int
    after: int
    status: str
    coverage: str
    commands: tuple[VerifyCommandResult, ...]
    changed_files_covered: tuple[str, ...]
    diff_hash: str
    result_path: str
    rails_labels: dict
    human_decision: str | None = None

    def to_dict(self) -> dict:
        # FIRST key is "type" so both scorecard readers can partition. before/after
        # both present so the CTX-vs-VERIFY partition and the verify correction line read cleanly.
        return {
            "type": "verify_gate",
            "applied": self.applied,
            "before": self.before,
            "after": self.after,
            "status": self.status,
            "coverage": self.coverage,
            "diff_hash": self.diff_hash,
            "commands_run": len(self.commands),
            "rails_labels": self.rails_labels,
            "human_decision": self.human_decision,
        }
