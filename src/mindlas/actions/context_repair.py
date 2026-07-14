"""Mindlas Context Repair: the non-native correction for Context Rot. Builds a
validated Smart Context Repair Pack + Evidence Index, writes it to the project-local
.mindlas/context/, marks a pending resume, and records before/after CTX.
It NEVER calls native /compact: the pack is assembled non-natively from our own
deterministic extractors — no native compaction, no LLM, no network."""
from __future__ import annotations

import json
from dataclasses import dataclass

from .base import Action
from ..features.context_rot import score_context_rot
from ..runtime import paths
from ..runtime.repair_pack import (Evidence, PackData, ValidationResult,
                                    build_pack_data, render_repair_pack, validate_pack)
from ..runtime.state import ContextRotSignals, build_context_rot_signals
from ..vitals.config import ledger_path as vitals_ledger_path, read_context_pct
from ..vitals.context import SessionContext
from ..vitals.events import Event, EventKind
from ..vitals.ledger import Ledger as VitalsLedger


def _read_inherited_contract(session_id: str) -> SessionContext | None:
    """The prior pack's contract, copied into this session by the /clear reseed. None when this
    isn't a reseeded session (no chaining) -> build_pack_data uses only the fresh extraction."""
    try:
        p = paths.inherited_contract_path(session_id)
        if not p.exists():
            return None
        d = json.loads(p.read_text(encoding="utf-8"))
        obj = d.get("objective")
        cons = tuple(d.get("constraints") or ())
        if not obj and not cons:
            return None
        return SessionContext(objective=obj, constraints=cons)
    except (OSError, ValueError):
        return None


@dataclass(frozen=True)
class RepairContext:
    events: tuple[Event, ...]
    session_id: str
    now_turn: int


@dataclass(frozen=True)
class PackPreview:
    pack_text: str
    pack_data: PackData
    evidence: tuple[Evidence, ...]
    validation: ValidationResult
    before: int


@dataclass(frozen=True)
class RepairResult:
    applied: bool
    validation: str
    before: int
    modeled_after_ctx: int
    evidence_preserved: int
    pack_path: str
    constraints_preserved: int = 0
    downstream: dict | None = None
    human_decision: str | None = None

    def to_dict(self) -> dict:
        return {"type": "context_repair", "applied": self.applied,
                "validation": self.validation, "before": self.before,
                "modeled_after_ctx": self.modeled_after_ctx,
                "evidence_preserved": self.evidence_preserved,
                "constraints_preserved": self.constraints_preserved,
                "downstream": self.downstream, "human_decision": self.human_decision,
                "pack_path": self.pack_path}


_PACK_BUDGET_CHARS = 8000   # modeled budget for the compact pack (a pack should fit comfortably)


def _before_score(rc: RepairContext) -> int:
    # Reuse the status line's cached MEASURED window % (when present) so the repair's before-score
    # matches `mindlas context status` instead of proxying mass to ~100%. None -> proxied (unchanged).
    pct = read_context_pct(rc.session_id)
    sig = build_context_rot_signals(list(rc.events), context_pct=pct, now_turn=rc.now_turn)
    return score_context_rot(sig)


def _after_score(pack_text: str, session_id: str) -> int:
    """MODELED CTX of the post-repair continuation: a fresh session whose only context is the
    compact pack (low turns, no stale contract, no accumulated outputs). A projection, not a
    live post-resume measurement — callers must label it "modeled". mass = pack size against
    the pack budget. (The divisor was previously `_PACK_BUDGET_CHARS * 100`,
    which made post-repair mass artificially tiny / near-zero — fixed to divide by the budget.)"""
    mass = min(100.0, 100.0 * len(pack_text) / _PACK_BUDGET_CHARS)
    sig = ContextRotSignals(session_id=session_id, turn_count=0, session_mass_pct=mass,
                            large_tool_outputs=0, task_contract_age_turns=0,
                            unresolved_assumptions=0, last_repair_age_turns=0,
                            mass_is_measured=False)   # the whole after-signal is a model -> estimated
    return score_context_rot(sig)


class ContextRepair(Action):
    action_id = "context_repair"

    def preview(self, state: RepairContext) -> PackPreview:   # type: ignore[override]
        rc = state
        inherited = _read_inherited_contract(rc.session_id)   # prior pack, if this is a reseed
        pd = build_pack_data(list(rc.events), session_id=rc.session_id, inherited=inherited)
        pack_text = render_repair_pack(pd)
        return PackPreview(pack_text=pack_text, pack_data=pd, evidence=pd.evidence,
                           validation=validate_pack(pd), before=_before_score(rc))

    def apply(self, state: RepairContext, *, now: str,
              downstream: dict | None = None,
              human_decision: str | None = None) -> RepairResult:   # type: ignore[override]
        rc = state
        pv = self.preview(rc)
        if pv.validation.status != "pass":
            paths.packs_dir(rc.session_id).mkdir(parents=True, exist_ok=True)
            paths.draft_pack_path(rc.session_id, now).write_text(pv.pack_text, encoding="utf-8")
            return RepairResult(applied=False, validation="fail", before=pv.before,
                                modeled_after_ctx=pv.before, evidence_preserved=len(pv.evidence),
                                pack_path=str(paths.draft_pack_path(rc.session_id, now)),
                                constraints_preserved=len(pv.pack_data.constraints),
                                downstream=None, human_decision=human_decision)

        # Loop-critical writes FIRST, and deliberately NOT swallowed: the SessionStart reseed needs
        # exactly two artifacts — the pack (latest.md) and the one-shot marker (pending_resume.json).
        # Writing them before any archival snapshot means an archival failure can never leave the
        # silent half-state "pack written, marker missing" that arms a dead reseed (the exact failure
        # a snapshot-write crash produced before this ordering). Both live in context_session_dir.
        paths.context_session_dir(rc.session_id).mkdir(parents=True, exist_ok=True)
        paths.latest_pack_path(rc.session_id).write_text(pv.pack_text, encoding="utf-8")
        # One-shot reseed flag under THIS session's subtree. The SessionStart reseed pulls the pack
        # by session id (latest_pack_path), so the flag carries no pack_path and can never collide
        # with a concurrent session's reseed.
        paths.pending_resume_path(rc.session_id).write_text(json.dumps(
            {"state": "RESUME_PENDING", "created_at": now, "session_id": rc.session_id}),
            encoding="utf-8")

        # The keystone of the closed loop: record that the repair happened so the rot clock
        # resets. Emitted ONLY here, on the applied branch (a failed/draft repair must NOT
        # reset the clock). TWO-LEDGER TRAP: this method writes its pack/marker/scorecard to the
        # per-PROJECT tree (runtime.paths), but the live CTX gauge (vitals/statusline) and the
        # CLI (`mindlas context status` -> cli._latest_events -> vitals.config.latest_ledger) read
        # their EVENT STREAM from the per-MACHINE VITALS ledger (~/.mindlas/sessions/<sid>/
        # ledger.jsonl). context_repair_end is read in exactly one place — state._last_repair_turn
        # — which runs over THAT vitals stream, so the event MUST land there, or the loop only
        # closes in a test and the live gauge stays stuck. Appending here makes
        # last_repair_age_turns -> 0 and unresolved_assumptions -> 0 on the very gauge that
        # triggered the repair. Event field order is positional: Event(session_id, turn, ts, kind).
        VitalsLedger(vitals_ledger_path(rc.session_id)).append(
            Event(rc.session_id, rc.now_turn, now, EventKind.CONTEXT_REPAIR_END))

        # Archival writes are BEST-EFFORT: the timestamped history snapshot + evidence package are
        # for later retrieval, not the live loop. A failure here (disk full, path length, file lock)
        # must NOT sever the reseed that the pack + marker + clock reset above already armed.
        try:
            # Durable structured contract (the possibly-INHERITED objective + constraints) so a
            # later /clear reseed can chain it forward — this is what lets repair #3 inherit from
            # #2's merged contract, not just #1. Best-effort: the live loop above doesn't need it.
            paths.pack_contract_path(rc.session_id).write_text(json.dumps(
                {"objective": pv.pack_data.objective,
                 "constraints": list(pv.pack_data.constraints)}), encoding="utf-8")
            paths.packs_dir(rc.session_id).mkdir(parents=True, exist_ok=True)
            paths.evidence_dir(rc.session_id).mkdir(parents=True, exist_ok=True)
            paths.pack_snapshot_path(rc.session_id, now).write_text(pv.pack_text, encoding="utf-8")
            # The evidence package self-identifies its session + repair time, so a session with
            # multiple context compactions produces distinct, self-describing packages that can be
            # ordered/attributed from content alone (not just the `{now}` in the filename).
            paths.evidence_snapshot_path(rc.session_id, now).write_text(
                json.dumps({"session_id": rc.session_id, "created_at": now,
                            "evidence": [e.to_dict() for e in pv.evidence]}, indent=2),
                encoding="utf-8")
        except OSError:
            pass  # best-effort history; the live reseed (pack + marker + clock reset) is already done

        paths.reports_dir().mkdir(parents=True, exist_ok=True)
        modeled_after = _after_score(pv.pack_text, rc.session_id)
        result = RepairResult(applied=True, validation="pass", before=pv.before,
                              modeled_after_ctx=modeled_after,
                              evidence_preserved=len(pv.evidence),
                              pack_path=str(paths.latest_pack_path(rc.session_id)),
                              constraints_preserved=len(pv.pack_data.constraints),
                              downstream=downstream, human_decision=human_decision)
        with open(paths.corrections_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(result.to_dict()) + "\n")

        # The scorecard is produced BY the repair (product promise: "no session
        # without a scorecard"), so `mindlas scorecard --latest` never has to re-discover the
        # session from a possibly-unpersisted ledger.
        self._write_scorecard(rc)
        return result

    @staticmethod
    def _write_scorecard(rc: RepairContext) -> None:
        """Build + persist latest_scorecard.{md,json} from the accumulated corrections.jsonl.
        Lazy imports avoid a runtime import cycle (scorecard/context import nothing from here)."""
        from ..runtime.scorecard import (scorecard_from_corrections, render_scorecard_md,
                                          scorecard_to_json)
        from ..vitals.context import extract_context

        corrections = tuple(
            json.loads(ln) for ln in
            paths.corrections_path().read_text(encoding="utf-8").splitlines() if ln.strip())
        task = extract_context(list(rc.events)).objective or "(unknown task)"
        sc = scorecard_from_corrections(rc.session_id, task, corrections)
        paths.reports_dir().mkdir(parents=True, exist_ok=True)
        paths.scorecard_md_path().write_text(render_scorecard_md(sc), encoding="utf-8")
        paths.scorecard_json_path().write_text(scorecard_to_json(sc), encoding="utf-8")
        # Persist the non-derivable inputs (session_id + task) so the status-line
        # measured-after reconcile can re-render this scorecard with the correct task after a
        # /clear — whose lean reseeded ledger no longer carries the objective.
        paths.scorecard_meta_path().write_text(
            json.dumps({"session_id": rc.session_id, "task": task}), encoding="utf-8")
