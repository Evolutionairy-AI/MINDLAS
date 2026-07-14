"""Shared git primitives for the reliability gauges. Promoted out of
verification_state.py so both Verification Debt and Change Blast Radius read git
the same way. Every helper is fail-soft: a missing git, a non-repo, or a timeout yields an
empty/None result, never an exception (the gauges must never break a status line).

`.mindlas/` — Mindlas's own project-local state dir — is filtered everywhere: it is never an
agent change, and `diff_hash` additionally excludes it from the tracked diff via the
`:(exclude).mindlas/` pathspec so a gauge writing its own artifacts can never move the
freshness hash."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


def _run_git(args: list[str], root: Path) -> str | None:
    """stdout on success, None on any failure (missing git, not a repo, timeout)."""
    try:
        # encoding pinned: git emits UTF-8; Windows' default cp1252 decode dies in the reader
        # thread on any non-cp1252 diff byte, silently yielding EMPTY output (a wrong reading).
        p = subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=15)
    except Exception:
        return None
    return p.stdout if p.returncode == 0 else None


def is_mindlas(path: str) -> bool:
    """Mindlas's own project-local state dir — never an agent change."""
    return path.replace("\\", "/").startswith(".mindlas/")


def _rename_newpath(token: str) -> str:
    """numstat `-M` renders a rename path as `{old => new}`, `old => new`, or
    `pre{old => new}post`. Recover the post-image path so it matches name-status keys."""
    if "=>" in token:
        if "{" in token and "}" in token:
            pre, rest = token.split("{", 1)
            mid, post = rest.split("}", 1)
            new = mid.split("=>", 1)[1].strip()
            token = pre + new + post
        else:
            token = token.split("=>", 1)[1].strip()
    return token.replace("\\", "/")


def numstat(args: list[str], root: Path) -> tuple[list[tuple[str, int, int]], int]:
    """Per-file `(path, added, deleted)` plus aggregate `added + deleted`, with `.mindlas/`
    excluded. `-` (binary) counts as 0. Pass `-M` in args for deterministic rename paths."""
    out = _run_git(["diff", "--numstat", *args], root) or ""
    per_file: list[tuple[str, int, int]] = []
    total = 0
    for row in out.splitlines():
        parts = row.split("\t")
        if len(parts) >= 3:
            path = _rename_newpath(parts[2])
            if is_mindlas(path):
                continue
            added = 0 if parts[0] == "-" else int(parts[0])
            deleted = 0 if parts[1] == "-" else int(parts[1])
            per_file.append((path, added, deleted))
            total += added + deleted
    return per_file, total


def name_status(args: list[str], root: Path, *, find_renames: bool = True) -> list[tuple[str, str]]:
    """`(status_code, post_image_path)` per changed file, `.mindlas/` excluded. `-M` is added
    by default so rename detection does not depend on the repo's `diff.renames` config."""
    extra = ["-M"] if (find_renames and "-M" not in args) else []
    out = _run_git(["diff", "--name-status", *extra, *args], root) or ""
    rows: list[tuple[str, str]] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if not parts or not parts[0]:
            continue
        code = parts[0]
        path = parts[2] if (code.startswith("R") and len(parts) >= 3) else (
            parts[1] if len(parts) >= 2 else "")
        path = path.replace("\\", "/")
        if path and not is_mindlas(path):
            rows.append((code, path))
    return rows


def untracked(root: Path) -> list[str]:
    out = _run_git(["ls-files", "--others", "--exclude-standard"], root) or ""
    return [ln for ln in out.splitlines() if ln.strip() and not is_mindlas(ln)]


def diff_hash(root: Path, untracked: list[str]) -> str:
    """sha256 over the tracked unstaged+staged diff text (with `.mindlas/` EXCLUDED) plus
    each sorted untracked path\\0content (raw bytes, so binary/encoded files still count)."""
    unstaged = _run_git(["diff", "--", ":(exclude).mindlas/"], root) or ""
    staged = _run_git(["diff", "--cached", "--", ":(exclude).mindlas/"], root) or ""
    blob = (unstaged + staged).encode("utf-8", "replace")
    for u in sorted(untracked):
        try:
            data = (root / u).read_bytes()
        except Exception:
            data = b""
        blob += u.encode("utf-8") + b"\0" + data
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def file_diff(path: str, root: Path) -> str:
    """Combined unstaged + staged `git diff -M -- <path>` text for one pathspec (the Patch
    Splitter uses this to write per-bundle tracked patches; included here so the helper module
    is complete)."""
    unstaged = _run_git(["diff", "-M", "--", path], root) or ""
    staged = _run_git(["diff", "-M", "--cached", "--", path], root) or ""
    return unstaged + staged
