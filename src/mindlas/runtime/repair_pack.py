"""The Smart Context Repair Pack, Evidence Index, and validation.
Pure: builds and validates the pack; the Context Repair action writes it.

Non-native: the pack is assembled from our own deterministic extractors (extract_context,
changed_files) — no native compaction, no LLM, no network."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from ..vitals.context import SessionContext, extract_context
from ..vitals.events import Event, EventKind
from ..vitals.verify import changed_files

# A "resume/reseed" instruction that must NOT be mistaken for the task objective. Anchored on the
# RESUME verb (resume from / reseed / continue from ... pack), deliberately NOT on the bare phrase
# "context repair package" — in this very repo a legitimate objective can be ABOUT the repair pack,
# and only a resume/continue-from instruction should be rejected.
_META_OBJECTIVE = re.compile(
    r"\bresume from\b|\breseed\b|\bcontinue from the\b.{0,50}\bpack(age)?\b", re.I)


def _is_meta_resume(text: str | None) -> bool:
    return bool(text) and bool(_META_OBJECTIVE.search(text))


def _dedupe(items) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return tuple(out)


def _merge_contract(fresh: SessionContext,
                    inherited: SessionContext | None) -> SessionContext:
    """Fold an inherited (prior-pack) contract into the fresh extraction, so a SECOND repair after a
    /clear doesn't lose the task. Rules:
      - Objective: keep the freshly-extracted one only if it's a REAL task; if it's empty or a
        resume/meta prompt, inherit the prior objective. A genuinely new objective still replaces.
      - Constraints: union prior + fresh (deduped) so a compaction never drops a non-negotiable
        constraint the reset ledger no longer carries.
    With no inherited contract (not a reseeded session) this returns `fresh` unchanged."""
    if inherited is None:
        return fresh
    obj = fresh.objective
    if not obj or _is_meta_resume(obj):
        obj = inherited.objective or obj
    constraints = _dedupe(tuple(inherited.constraints) + tuple(fresh.constraints))
    return SessionContext(objective=obj, constraints=constraints)


def _sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Evidence:
    id: str          # E-0001
    type: str        # task | user_constraint
    source: str      # transcript
    session_id: str
    turn: int
    summary: str
    hash: str        # sha256:...

    def to_dict(self) -> dict:
        return {"id": self.id, "type": self.type, "source": self.source,
                "session_id": self.session_id, "location": {"turn": self.turn},
                "summary": self.summary, "hash": self.hash}


def _eid(n: int) -> str:
    return f"E-{n:04d}"


def build_evidence_index(events: list[Event], ctx: SessionContext,
                         session_id: str) -> tuple[Evidence, ...]:
    """One evidence record for the task objective and one per user constraint.
    Turns are looked up from the source prompt so each record points at its origin."""
    out: list[Evidence] = []
    n = 1
    if ctx.objective:
        turn = next((e.turn for e in events
                     if e.kind == EventKind.USER_PROMPT and e.target == ctx.objective), 0)
        out.append(Evidence(_eid(n), "task", "transcript", session_id, turn,
                            ctx.objective, _sha256(ctx.objective)))
        n += 1
    for c in ctx.constraints:
        turn = next((e.turn for e in events
                     if e.kind == EventKind.USER_PROMPT and e.target == c), 0)
        out.append(Evidence(_eid(n), "user_constraint", "transcript", session_id, turn,
                            c, _sha256(c)))
        n += 1
    return tuple(out)


@dataclass(frozen=True)
class PackData:
    session_id: str
    objective: str | None
    constraints: tuple[str, ...]
    changed_files: tuple[str, ...]
    verification_state: str
    decisions: tuple[str, ...]
    open_risks: tuple[str, ...]
    next_action: str
    evidence: tuple[Evidence, ...]


def build_pack_data(events: list[Event], *, session_id: str,
                    inherited: SessionContext | None = None) -> PackData:
    """Assemble the pack from deterministic extractors. No LLM, no network. When `inherited` is
    supplied (the prior pack's contract, carried across a /clear reseed), fold it in so a second
    repair keeps the objective/constraints instead of re-extracting from a reset ledger."""
    ctx = _merge_contract(extract_context(events), inherited)
    changed = tuple(changed_files(events))
    evidence = build_evidence_index(events, ctx, session_id)
    obj = ctx.objective
    return PackData(
        session_id=session_id,
        objective=obj,
        constraints=ctx.constraints,
        changed_files=changed,
        verification_state="unknown (no verifier run recorded)",
        decisions=(),
        open_risks=(),
        next_action=(f"Continue: {obj}" if obj else "Restate the task objective, then continue."),
        evidence=evidence,
    )


def _eid_for_constraint(pd: PackData, constraint: str) -> str:
    for e in pd.evidence:
        if e.type == "user_constraint" and e.summary == constraint:
            return e.id
    return "E-????"


def render_repair_pack(pd: PackData) -> str:
    """The nine-section Smart Context Repair Pack."""
    L: list[str] = ["# Mindlas Context Repair Pack", ""]
    L += ["## 1. Current Task Contract",
          (pd.objective or "Unknown — restate the objective."), ""]
    L += ["## 2. Non-Negotiable Constraints"]
    if pd.constraints:
        L += [f"- {c} [EID:{_eid_for_constraint(pd, c)}]" for c in pd.constraints]
    else:
        L += ["- none recorded"]
    L += [""]
    L += ["## 3. Active Repo State"]
    if pd.changed_files:
        L += [f"- changed: {f}" for f in pd.changed_files]
    else:
        L += ["- no file changes recorded"]
    L += [""]
    L += ["## 4. Current Diff State",
          (f"{len(pd.changed_files)} file(s) changed this session." if pd.changed_files
           else "No diff recorded."), ""]
    L += ["## 5. Verification State", pd.verification_state, ""]
    L += ["## 6. Decisions Made"]
    L += ([f"- {d}" for d in pd.decisions] if pd.decisions else ["- none recorded"])
    L += [""]
    L += ["## 7. Open Risks and Questions"]
    L += ([f"- {r}" for r in pd.open_risks] if pd.open_risks else ["- none"])
    L += [""]
    L += ["## 8. Next Best Action", pd.next_action, ""]
    L += ["## 9. Evidence Index"]
    if pd.evidence:
        L += ["| ID | Type | Turn | Summary |", "|---|---|---:|---|"]
        L += [f"| {e.id} | {e.type} | {e.turn} | {e.summary} |" for e in pd.evidence]
    else:
        L += ["- none"]
    return "\n".join(L) + "\n"


_REQUIRED_SECTIONS = (
    "## 1. Current Task Contract", "## 2. Non-Negotiable Constraints",
    "## 3. Active Repo State", "## 4. Current Diff State", "## 5. Verification State",
    "## 6. Decisions Made", "## 7. Open Risks and Questions", "## 8. Next Best Action",
    "## 9. Evidence Index")


@dataclass(frozen=True)
class ValidationResult:
    status: str                      # "pass" | "fail"
    checks: tuple[tuple[str, bool], ...]
    errors: tuple[str, ...]


def validate_pack(pd: PackData) -> ValidationResult:
    """V1-V8 — a real hard gate: no hardcoded passes. Renders the
    pack and inspects both PackData and the rendered text. open_risks/decisions of () render as
    an explicit 'none', which satisfies V6."""
    rendered = render_repair_pack(pd)
    section7 = rendered.split("## 7. Open Risks and Questions", 1)[-1].split("## 8.", 1)[0]
    task_eids = [e for e in pd.evidence if e.type == "task"]
    constraint_eids = {e.summary for e in pd.evidence if e.type == "user_constraint"}
    sha_ok = all(e.hash.startswith("sha256:") and len(e.hash) > len("sha256:")
                 for e in pd.evidence)
    checks = [
        ("V1 task contract + task evidence",
         bool(pd.objective) and len(task_eids) == 1),
        ("V2 constraints preserved (non-empty)",
         all(c.strip() for c in pd.constraints)),
        ("V3 changed files listed or explicit 'no file changes recorded'",
         ("- changed: " in rendered) or ("- no file changes recorded" in rendered)),
        ("V4 diff state summarized",
         ("file(s) changed this session." in rendered) or ("No diff recorded." in rendered)),
        ("V5 verification state explicit",
         bool(pd.verification_state.strip()) and "## 5. Verification State" in rendered),
        ("V6 open risks listed or explicit none",
         bool(pd.open_risks) or ("- none" in section7)),
        ("V7 next best action exists", bool(pd.next_action.strip())),
        ("V8 evidence for major claims (constraints + sha256 + nine sections)",
         all(c in constraint_eids for c in pd.constraints)
         and sha_ok and all(s in rendered for s in _REQUIRED_SECTIONS)),
    ]
    errors = tuple(name for name, ok in checks if not ok)
    return ValidationResult(status="pass" if not errors else "fail",
                            checks=tuple(checks), errors=errors)
