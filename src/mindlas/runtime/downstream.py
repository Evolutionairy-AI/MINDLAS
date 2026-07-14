"""Independent downstream health check for Context Repair.

The repair reconstructs context, never code. This check is a SEPARATE, deterministic
snapshot of code health captured at the moment of repair and recorded alongside it — the
external signal that answers "CTX dropped, but is the code still healthy?". It reuses the
static verifier (vitals/verify).

Honesty boundary — what a skip does and does not catch: because verify() returns an empty
list both when the code is clean AND when ruff is absent, this module checks for the ruff
binary itself (shutil.which) and reports `skipped` (never `pass`) when ruff is missing or
there are no checkable files. It does NOT detect a ruff that is present but whose run fails
(timeout, crash, unparseable output): vitals/verify swallows those and returns no findings,
which surfaces here as a clean `pass`. That residual is a known limitation, inherited from
the gate's fail-silent contract; the consumer that records this result should not treat a
`pass` as a hard guarantee that the checker actually executed cleanly."""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from ..vitals.verify import verify


@dataclass(frozen=True)
class DownstreamCheck:
    ran: bool
    tool: str
    target: tuple[str, ...]
    findings: int
    result: str          # "pass" | "fail" | "skipped"
    note: str

    def to_dict(self) -> dict:
        return {"ran": self.ran, "tool": self.tool, "target": list(self.target),
                "findings": self.findings, "result": self.result, "note": self.note}


def run_downstream_check(target: list[str], cwd: Path) -> DownstreamCheck:
    """Run ruff over the checkable, existing files in `target` under `cwd`.

    pass    = ran with 0 findings
    fail    = ran with >0 findings
    skipped = no checkable files, or ruff is unavailable (NEVER silently a pass)
    """
    checkable = [p for p in target if p.endswith(".py") and (cwd / p).exists()]
    if not checkable:
        # target echoes the raw request here (nothing was checkable); other branches echo the filtered set
        return DownstreamCheck(ran=False, tool="ruff", target=tuple(target),
                               findings=0, result="skipped",
                               note="no changed files to check")
    if shutil.which("ruff") is None:
        return DownstreamCheck(ran=False, tool="ruff", target=tuple(checkable),
                               findings=0, result="skipped", note="ruff unavailable")
    diags = verify(checkable, cwd)
    findings = len(diags)
    return DownstreamCheck(ran=True, tool="ruff", target=tuple(checkable),
                           findings=findings, result="pass" if findings == 0 else "fail",
                           note=f"{findings} finding(s)")


def demo_sample_target() -> tuple[Path, list[str]]:
    """(cwd, files) pointing at the bundled clean demo module, so the demo's --verify runs
    ruff over a real, known-clean file for an honest, reproducible pass."""
    from . import demo_sample
    d = Path(demo_sample.__file__).parent
    return d, ["clean_module.py"]
