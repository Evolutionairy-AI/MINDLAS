"""The trust gate. Inline enforcement at the propagation boundary.

This is the spine of the product, not the report. When an autonomous agent is about to
let its work propagate, commit, push, deploy, the gate verifies the agent-introduced
change against ground truth and makes a real allow-or-block decision. No human in the
loop. The decision is written to the verdict ledger the moment it is made, and the
report renders those verdicts. The report never recomputes; the gate is the source of
truth.

Three modes (MINDLAS_GATE):
  shadow  default. The gate makes the real decision and records what it WOULD have
          blocked, but does not stop the run. This is dry-run enforcement, the standard
          way a fraud system or a WAF earns the right to act: it measures its own
          false-positive rate before it is allowed to halt anything.
  live    the gate blocks at the boundary. Enforcement for real. Opt-in until the
          shadow false-positive rate is measured low, then default in the enforcement tier.
  off     disabled.

Gating is not correcting. The gate never touches the agent's reasoning. It enforces a
trust boundary on the output, the same thing RAILS does to a transaction, done to a
commit. So it carries none of the self-correction degradation risk.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from . import verify
from .events import Event

# Propagation boundaries: the points where an autonomous agent's output leaves the
# workspace and becomes something the world acts on. Matched against shell commands.
_BOUNDARIES = (
    ("git push", re.compile(r"\bgit\s+push\b")),
    ("git commit", re.compile(r"\bgit\s+commit\b")),
    ("deploy", re.compile(r"\b(deploy|vercel|netlify|fly\s+deploy|kubectl\s+apply|"
                          r"docker\s+push|terraform\s+apply|serverless\s+deploy)\b")),
    ("publish", re.compile(r"\b(npm\s+publish|pip\s+upload|twine\s+upload|cargo\s+publish)\b")),
)

_VALID = ("shadow", "live", "off")


def gate_mode() -> str:
    m = os.environ.get("MINDLAS_GATE", "shadow").strip().lower()
    return m if m in _VALID else "shadow"


def boundary_of(command: str | None) -> str | None:
    """The propagation boundary a shell command crosses, or None."""
    if not command:
        return None
    for label, pat in _BOUNDARIES:
        if pat.search(command):
            return label
    return None


@dataclass
class Verdict:
    ts: float
    boundary: str          # "git push", "deploy", ...
    decision: str          # "allow" | "would_block" | "block"
    mode: str              # "shadow" | "live"
    findings: list         # list of {file, line, code, message}
    command: str
    turn: int = 0          # max event turn at gate time (anchors Verification Debt reset)

    def to_json(self) -> str:
        return json.dumps(asdict(self))


def seed_pre_edit(target: str | None, cwd: Path, baseline_path: Path) -> None:
    """Snapshot a file's clean state BEFORE the agent's first edit to it, so a defect
    the very first edit introduces is still attributable as agent-introduced. No-op if
    the file is already baselined or not checkable. This is what keeps the gate's verdict
    honest: it diffs against the true pre-agent state, not a post-edit guess."""
    if not target or not target.endswith(".py"):
        return
    try:
        baseline = verify.load_baseline(baseline_path)
        if target in baseline:
            return
        current = verify.verify([target], cwd)
        baseline[target] = {d.signature() for d in current}
        verify.save_baseline(baseline_path, baseline)
    except Exception:
        pass


def decide(events: list[Event], cwd: Path, baseline_path: Path,
           test_state_path: Path | None = None):
    """Verify the agent's changes against ground truth. Returns the located findings that
    would justify a block, combining the static tier (breakage in changed files) and the
    test tier (newly failing tests from the freshest cached run). Empty means allow."""
    files = verify.changed_files(events)
    findings = []
    baseline = verify.load_baseline(baseline_path)
    for f in files:
        current = verify.verify([f], cwd)
        findings.extend(d for d in current if d.signature() not in baseline.get(f, set()))
    findings.sort(key=lambda d: (files.index(d.file) if d.file in files else 99, d.line))

    if test_state_path is not None:
        from . import testtier
        st = testtier.read_state(test_state_path)
        if st and st.ran and st.new_failures:
            for tid in st.new_failures:
                findings.append(verify.Diagnostic(
                    file=tid, line=0, code="TEST", message="introduced test failure"))
    return findings


def run_gate(events: list[Event], cwd: Path, baseline_path: Path,
             verdict_path: Path, boundary: str, command: str,
             test_state_path: Path | None = None) -> dict:
    """Execute the gate at a propagation boundary. Record the verdict, then either block
    (live) or let it through (shadow). Returns the Claude Code hook response."""
    mode = gate_mode()
    if mode == "off":
        return {}
    findings = decide(events, cwd, baseline_path, test_state_path)
    blocked = bool(findings)
    decision = ("block" if mode == "live" else "would_block") if blocked else "allow"

    verdict = Verdict(
        ts=time.time(), boundary=boundary, decision=decision, mode=mode,
        findings=[{"file": d.file, "line": d.line, "code": d.code, "message": d.message}
                  for d in findings],
        command=command[:200], turn=max((e.turn for e in events), default=0),
    )
    _record(verdict_path, verdict)

    if mode == "live" and blocked:
        first = findings[0]
        where = first.file if first.line == 0 else f"{first.file} line {first.line}"
        reason = (f"Mindlas blocked this {boundary}: {where}, "
                  f"{first.message} ({first.code}). The change introduces a verified "
                  f"defect. Fix it before letting this propagate.")
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}
    # shadow, or allow: never stop the run
    return {}


def _record(verdict_path: Path, verdict: Verdict) -> None:
    try:
        verdict_path.parent.mkdir(parents=True, exist_ok=True)
        with open(verdict_path, "a", encoding="utf-8") as f:
            f.write(verdict.to_json() + "\n")
    except Exception:
        pass


def read_verdicts(verdict_path: Path) -> list[dict]:
    try:
        return [json.loads(line) for line in
                verdict_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except Exception:
        return []


