"""Execution of planned VerifyCommands. Routes on cmd.kind: an in-process AST check
(no subprocess, no .pyc) vs a subprocess (ruff/pytest) hardened with PYTHONDONTWRITEBYTECODE=1
and a timeout, never shell=True. A check that did not run is 'skipped', never 'pass' (the
honesty boundary). Time uses time.monotonic (wall-clock-free duration)."""
from __future__ import annotations

import ast
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .verify_plan import VerifyCommand

_TAIL = 4000
_COV_RANK = {"none": 0, "partial": 1, "targeted": 2, "full": 3}


@dataclass(frozen=True)
class VerifyCommandResult:
    label: str
    command: tuple[str, ...]
    exit_code: int
    status: str                 # "pass" | "fail" | "skipped" | "timeout"
    stdout_tail: str
    stderr_tail: str
    duration_ms: int


def _ast_check(cmd: VerifyCommand, root: Path) -> VerifyCommandResult:
    for rel in cmd.command[1:]:                       # skip the sentinel at index 0
        p = root / rel
        try:
            src = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue                                  # missing file: nothing to parse
        try:
            ast.parse(src, filename=str(rel))
        except SyntaxError as e:
            return VerifyCommandResult(cmd.label, cmd.command, 1, "fail",
                                       "", f"{rel}:{e.lineno}: {e.msg}", 0)
    return VerifyCommandResult(cmd.label, cmd.command, 0, "pass", "", "", 0)


def run_verify_command(cmd: VerifyCommand, *, project_root) -> VerifyCommandResult:
    root = Path(project_root)
    if cmd.kind == "in_process":
        return _ast_check(cmd, root)
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"              # no .pyc for any child
    start = time.monotonic()
    try:
        p = subprocess.run(list(cmd.command), cwd=str(root), capture_output=True, text=True,
                           encoding="utf-8", errors="replace",     # never cp1252-decode child output
                           timeout=cmd.timeout_seconds, env=env)   # never shell=True
    except subprocess.TimeoutExpired:
        dur = int((time.monotonic() - start) * 1000)
        return VerifyCommandResult(cmd.label, cmd.command, -1, "timeout",
                                   "", f"timed out after {cmd.timeout_seconds}s", dur)
    except OSError as e:                              # tool not found -> did not run
        dur = int((time.monotonic() - start) * 1000)
        return VerifyCommandResult(cmd.label, cmd.command, -1, "skipped", "",
                                   f"could not run: {e}", dur)
    dur = int((time.monotonic() - start) * 1000)
    status = "pass" if p.returncode == 0 else "fail"
    return VerifyCommandResult(cmd.label, cmd.command, p.returncode, status,
                               (p.stdout or "")[-_TAIL:], (p.stderr or "")[-_TAIL:], dur)


def run_verify_commands(commands, *, project_root) -> tuple[VerifyCommandResult, ...]:
    results: list[VerifyCommandResult] = []
    for cmd in commands:
        r = run_verify_command(cmd, project_root=project_root)
        results.append(r)
        if r.status in ("fail", "timeout"):           # stop on first failure
            break
    return tuple(results)


def summarize_verify_status(results) -> str:
    results = list(results)
    if any(r.status == "fail" for r in results):
        return "fail"
    if any(r.status == "timeout" for r in results):
        return "timeout"
    if not any(r.status in ("pass", "fail", "timeout") for r in results):
        return "skipped"                              # nothing actually ran
    return "pass"


def coverage_of(commands, results) -> str:
    best = "none"
    for cmd, res in zip(commands, results):
        if res.status == "pass" and _COV_RANK[cmd.coverage] > _COV_RANK[best]:
            best = cmd.coverage
    return best
