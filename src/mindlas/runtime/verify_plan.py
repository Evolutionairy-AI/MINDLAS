"""Python-only command planning for the Verify Gate. Pure: turns
VerificationDebtSignals + a project root into an ordered tuple of VerifyCommand. Plans only what
the repo actually supports (config + tool availability gated); never invents commands; a check
that cannot be planned is reported as skipped, never silently as pass."""
from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..vitals.verify import _DEFECT_SELECT
from .project_markers import (PYTHON_PROJECT_MARKERS as _PY_CONFIG,
                              PYTEST_CONFIG_FILES as _PYTEST_CONFIG)

_AST_SENTINEL = "<mindlas:ast-syntax-check>"
# _PY_CONFIG drives detect_project_stack + the L2 ruff gate; _PYTEST_CONFIG gates the pytest
# tiers. Both are the canonical sets from runtime.project_markers (single source of truth).
_MUTATING_NAMES = {"install", "add", "rm", "update", "upgrade", "commit", "push", "format"}
_MUTATING_FLAGS = {"--fix", "--write", "--in-place"}


@dataclass(frozen=True)
class VerifyCommand:
    label: str
    command: tuple[str, ...]
    reason: str
    coverage: str               # "partial" | "targeted" | "full"
    kind: str = "subprocess"    # "subprocess" | "in_process"
    timeout_seconds: int = 120


def detect_project_stack(root: Path) -> tuple[str, ...]:
    """("python",) when a Python project marker is present, else ("unknown",) — non-Python
    repos plan nothing (deferred)."""
    if any((Path(root) / m).exists() for m in _PY_CONFIG):
        return ("python",)
    return ("unknown",)


def _infer_test_paths(prod_path: str) -> tuple[str, ...]:
    p = PurePosixPath(prod_path.replace("\\", "/"))
    if p.suffix != ".py":
        return ()
    stem = p.stem
    cands = [f"tests/test_{stem}.py"]
    parts = p.parts
    if "src" in parts:
        sub = parts[parts.index("src") + 1:-1]   # package path under src/, sans filename
        if sub:
            cands.append("tests/" + "/".join(sub) + f"/test_{stem}.py")
    return tuple(dict.fromkeys(cands))            # de-dupe, preserve order


def _targeted_tests(signals, root: Path) -> tuple[str, ...]:
    targets: list[str] = []
    for f in signals.test_files_changed:
        if f.endswith(".py") and (root / f).exists() and f not in targets:
            targets.append(f)
    for f in signals.production_files_changed:
        for cand in _infer_test_paths(f):
            if (root / cand).exists() and cand not in targets:
                targets.append(cand)
    return tuple(targets)


def plan_verify_commands(signals, *, project_root, allow_full_suite=False,
                         max_seconds=120) -> tuple[VerifyCommand, ...]:
    root = Path(project_root)
    py_changed = tuple(f for f in signals.changed_files if f.endswith(".py"))
    # A changed .py file is itself a Python-stack signal — the L1 AST check needs no
    # project config, so plan it even when detect_project_stack is ("unknown",). Config-gated tiers
    # (ruff/pytest) still require their own markers below, so a no-config repo plans AST only.
    if detect_project_stack(root) != ("python",) and not py_changed:
        return ()
    cmds: list[VerifyCommand] = []

    if py_changed:                                                          # L1 — syntax (no config)
        cmds.append(VerifyCommand(
            label="ast syntax check", command=(_AST_SENTINEL, *py_changed),
            reason="parse changed .py files for syntax errors",
            coverage="partial", kind="in_process", timeout_seconds=max_seconds))

    if py_changed and shutil.which("ruff") and any((root / m).exists() for m in _PY_CONFIG):
        cmds.append(VerifyCommand(                                         # L2 — lint
            label="ruff check",
            command=("ruff", "check", "--select", ",".join(_DEFECT_SELECT),
                     "--output-format", "json", "--no-cache", "--force-exclude", *py_changed),
            reason="lint changed .py files for runtime-breaking defects",
            coverage="partial", kind="subprocess", timeout_seconds=max_seconds))

    targets = _targeted_tests(signals, root)                               # L3 — targeted tests
    if targets and any((root / m).exists() for m in _PYTEST_CONFIG):       # config-gated
        cmds.append(VerifyCommand(
            label="pytest targeted",
            command=(sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *targets),
            reason="run tests covering the changed files",
            coverage="targeted", kind="subprocess", timeout_seconds=max_seconds))

    if allow_full_suite and any((root / m).exists() for m in _PYTEST_CONFIG):  # L4 — full
        cmds.append(VerifyCommand(
            label="pytest full",
            command=(sys.executable, "-m", "pytest", "-p", "no:cacheprovider"),
            reason="run the full test suite", coverage="full",
            kind="subprocess", timeout_seconds=max_seconds))

    return tuple(c for c in cmds if is_safe_verify_command(c))


def is_safe_verify_command(cmd: VerifyCommand) -> bool:
    """Exact-token / exact-flag matching — NEVER substring, so file paths like
    src/address.py or tests/test_add_user.py are immune. (shell=True is structurally impossible:
    the executor hardcodes shell=False, so there is no shell clause to check here.)"""
    for token in cmd.command:
        name = PurePosixPath(token.replace("\\", "/")).name   # paths are immune
        if name in _MUTATING_NAMES:
            return False
        if token in _MUTATING_FLAGS:
            return False
    return True
