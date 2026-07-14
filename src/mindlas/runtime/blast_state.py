"""Deterministic Change Blast Radius signals. Git diff (+ untracked) is the primary
source via the shared runtime.git_helpers primitives; a Mindlas edit/write event fallback
covers the non-git case. `.mindlas/` is excluded everywhere. The classifier is a PARALLEL
extension of the shared 4-kind classify_path, not a replacement, so the locked
classify_path tests stay green."""
from __future__ import annotations

import fnmatch
import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import git_helpers, paths
from .verification_state import classify_path
from ..vitals.events import EventKind
from .project_markers import (CENTRAL_CONFIG_FILES as _CENTRAL_CONFIG,
                              CENTRAL_CONFIG_GLOBS as _CENTRAL_CONFIG_GLOBS,
                              TEST_DIR_NAMES as _TEST_DIRS)

_DOCS_EXT = (".md", ".rst", ".adoc")


def classify_path_blast(path: str) -> str:
    """production | test | config | docs | neutral. Shared classify_path first, then BLAST
    overrides: anything under `.github/` is config; `.md`/`.rst`/`.adoc` or anything under
    `docs/` is docs."""
    p = PurePosixPath(path.replace("\\", "/"))
    parts = p.parts
    if parts and parts[0] == ".github":
        return "config"
    if "docs" in parts or p.suffix in _DOCS_EXT:
        return "docs"
    return classify_path(path)


@dataclass(frozen=True)
class FileChange:
    path: str
    status: str              # modified | added | deleted | renamed
    kind: str                # production | test | config | docs | neutral
    top_dir: str
    directory: str
    concern_key: str
    additions: int
    deletions: int
    changed_lines: int
    centrality: str          # high | normal


def concern_key(path: str, kind: str) -> str:
    """Deterministic per-file concern: full path under the source root for code, bucket
    keys for config/docs/neutral. Not semantic — just a stable approximation."""
    p = PurePosixPath(path.replace("\\", "/"))
    parts = list(p.parts)
    if kind == "config":
        return f"config:{p.name}".lower()
    if kind == "docs":
        if len(parts) > 1 and parts[0] == "docs":
            section = PurePosixPath(parts[1]).stem
        else:
            section = p.stem
        return f"docs:{section}".lower()
    if kind == "neutral":
        top = parts[0] if len(parts) > 1 else p.stem
        return f"neutral:{top}".lower()
    # production | test
    if parts and parts[0] == "src":
        parts = parts[1:]
    if kind == "test" and parts and parts[0] in _TEST_DIRS:
        parts = parts[1:]
    if not parts:
        return p.stem.lower()
    stem = PurePosixPath(parts[-1]).stem
    if kind == "test":
        if stem.startswith("test_"):
            stem = stem[len("test_"):]
        elif stem.endswith("_test"):
            stem = stem[: -len("_test")]
    segs = parts[:-1] + ([stem] if stem else [])
    return "/".join(segs).lower()


_CENTRAL_SOURCE = {"__init__.py", "cli.py", "config.py", "settings.py",
                   "paths.py", "scorecard.py", "base.py"}
# _CENTRAL_CONFIG / _CENTRAL_CONFIG_GLOBS now come from runtime.project_markers (imported above).


def centrality(name: str, kind: str) -> str:
    """high | normal. High-centrality files carry higher COORDINATION risk, not higher
    correctness risk. The `src/**` patterns are approximated by basename on production files."""
    if kind == "production" and name in _CENTRAL_SOURCE:
        return "high"
    if name in _CENTRAL_CONFIG or any(fnmatch.fnmatch(name, g) for g in _CENTRAL_CONFIG_GLOBS):
        return "high"
    return "normal"


@dataclass(frozen=True)
class ChangeBlastSignals:
    session_id: str
    changed_files: tuple[str, ...]
    changed_file_count: int
    changed_lines: int
    additions: int
    deletions: int
    production_files_changed: tuple[str, ...]
    test_files_changed: tuple[str, ...]
    config_files_changed: tuple[str, ...]
    docs_files_changed: tuple[str, ...]
    neutral_files_changed: tuple[str, ...]
    directory_count: int
    concern_count: int
    file_kind_count: int
    production_without_tests: bool
    config_mixed_with_source: bool
    docs_mixed_with_source: bool
    high_centrality_files: tuple[str, ...]
    deleted_files: tuple[str, ...]
    renamed_files: tuple[str, ...]
    max_lines_in_one_file: int
    current_diff_hash: str


def _dirs(path: str) -> tuple[str, str]:
    p = PurePosixPath(path.replace("\\", "/"))
    return (p.parts[0] if len(p.parts) > 1 else "."), str(p.parent)


def _make_fc(path: str, status: str, additions: int, deletions: int) -> FileChange:
    kind = classify_path_blast(path)
    top_dir, directory = _dirs(path)
    name = PurePosixPath(path.replace("\\", "/")).name
    return FileChange(
        path=path, status=status, kind=kind, top_dir=top_dir, directory=directory,
        concern_key=concern_key(path, kind), additions=additions, deletions=deletions,
        changed_lines=additions + deletions, centrality=centrality(name, kind))


_STATUS_WORD = {"A": "added", "D": "deleted", "R": "renamed"}
_STATUS_ORDER = {"deleted": 3, "renamed": 2, "added": 1, "modified": 0}


def _merge_status(prev: str | None, new: str) -> str:
    if prev is None:
        return new
    return prev if _STATUS_ORDER[prev] >= _STATUS_ORDER[new] else new


def _git_file_changes(root: Path) -> tuple[tuple[FileChange, ...], str]:
    status_by_path: dict[str, str] = {}
    for extra in ([], ["--cached"]):
        for code, path in git_helpers.name_status(["-M", *extra], root):
            status_by_path[path] = _merge_status(
                status_by_path.get(path), _STATUS_WORD.get(code[:1], "modified"))
    lines_by_path: dict[str, tuple[int, int]] = {}
    for extra in ([], ["--cached"]):
        per_file, _total = git_helpers.numstat(["-M", *extra], root)
        for path, added, deleted in per_file:
            a, d = lines_by_path.get(path, (0, 0))
            lines_by_path[path] = (a + added, d + deleted)
    untracked = git_helpers.untracked(root)
    for u in untracked:
        status_by_path.setdefault(u, "added")
        try:
            n = len((root / u).read_text(encoding="utf-8", errors="replace").splitlines())
        except Exception:
            n = 0
        a, d = lines_by_path.get(u, (0, 0))
        lines_by_path[u] = (a + n, d)
    fcs = tuple(_make_fc(p, status_by_path[p], *lines_by_path.get(p, (0, 0)))
                for p in sorted(status_by_path))
    return fcs, git_helpers.diff_hash(root, untracked)


def _fallback_file_changes(events) -> tuple[tuple[FileChange, ...], str]:
    agg: dict[str, tuple[int, int]] = {}
    for e in events:
        if (e.kind == EventKind.TOOL_CALL and e.cls in ("edit", "write") and e.target
                and not git_helpers.is_mindlas(e.target)):
            a, d = agg.get(e.target, (0, 0))
            agg[e.target] = (a + (e.lines_added or 0), d + (e.lines_deleted or 0))
    fcs = tuple(_make_fc(p, "modified", a, d) for p, (a, d) in sorted(agg.items()))
    dh = "sha256:" + hashlib.sha256(
        ("\0".join(sorted(agg))).encode("utf-8", "replace")).hexdigest()
    return fcs, dh


def _select_changes(events, root: Path) -> tuple[tuple[FileChange, ...], str]:
    """Pick the change source: git (primary) or the edit/write-event fallback (no git)."""
    if git_helpers._run_git(["rev-parse", "--is-inside-work-tree"], root) is not None:
        return _git_file_changes(root)
    return _fallback_file_changes(events)


def build_signals_from_changes(changes: tuple[FileChange, ...], *,
                               session_id: str = "session",
                               diff_hash: str = "") -> ChangeBlastSignals:
    """Aggregate a FileChange tuple into ChangeBlastSignals. Reusable so the Patch
    Splitter planner can score an arbitrary bundle's records with the same formula."""
    changes = tuple(changes)
    prod = tuple(fc.path for fc in changes if fc.kind == "production")
    tests = tuple(fc.path for fc in changes if fc.kind == "test")
    cfg = tuple(fc.path for fc in changes if fc.kind == "config")
    docs = tuple(fc.path for fc in changes if fc.kind == "docs")
    neutral = tuple(fc.path for fc in changes if fc.kind == "neutral")
    return ChangeBlastSignals(
        session_id=session_id,
        changed_files=tuple(fc.path for fc in changes),
        changed_file_count=len(changes),
        changed_lines=sum(fc.changed_lines for fc in changes),
        additions=sum(fc.additions for fc in changes),
        deletions=sum(fc.deletions for fc in changes),
        production_files_changed=prod, test_files_changed=tests,
        config_files_changed=cfg, docs_files_changed=docs, neutral_files_changed=neutral,
        directory_count=len({fc.directory for fc in changes}),
        concern_count=len({fc.concern_key for fc in changes}),
        file_kind_count=len({fc.kind for fc in changes}),
        production_without_tests=bool(prod) and not tests,
        config_mixed_with_source=bool(cfg) and bool(prod),
        docs_mixed_with_source=bool(docs) and bool(prod),
        high_centrality_files=tuple(fc.path for fc in changes if fc.centrality == "high"),
        deleted_files=tuple(fc.path for fc in changes if fc.status == "deleted"),
        renamed_files=tuple(fc.path for fc in changes if fc.status == "renamed"),
        max_lines_in_one_file=max((fc.changed_lines for fc in changes), default=0),
        current_diff_hash=diff_hash)


def build_file_changes(events, session_id=None, *,
                       project_root=None) -> tuple[tuple[FileChange, ...], str]:
    """Public accessor returning the per-file FileChange records + diff hash (the Patch Splitter
    planner needs the records the aggregate signals throw away)."""
    events = list(events or [])
    root = Path(project_root) if project_root is not None else paths.project_root()
    return _select_changes(events, root)


def build_change_blast_signals(events, session_id=None, *,
                               project_root=None) -> ChangeBlastSignals:
    events = list(events or [])
    sid = session_id or (events[0].session_id if events else "session")
    root = Path(project_root) if project_root is not None else paths.project_root()
    changes, dh = _select_changes(events, root)
    return build_signals_from_changes(changes, session_id=sid, diff_hash=dh)
