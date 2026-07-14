"""Verification gate. Turns a drift trigger into a verified, localized defect.

The architecture this implements, grounded in the self-correction literature:

  Detection decides WHEN to verify. It is a trigger, never the correction content.
  Verification runs a fast, read-only static check on the files the agent recently
  changed and obtains ground truth. Only a NEW, located defect the agent's edits
  introduced is ever delivered, in the self-debug shape (file, line, what is wrong,
  one action). If the check passes, or no checker applies, the gate returns None and
  the caller stays silent.

Silence on any failure-to-verify is the design, not a gap. A false-positive from the
detector costs one wasted check, never a damaging injection. The worst outcome is a
miss. That is what keeps this on the right side of the degradation result: a healthy
session passes verification and produces silence.

v0 scope: synchronous, sub-second, read-only static checks (ruff + a syntax compile)
on Python files. No test suite, no async, no side effects. Fits the hook time budget
and runs on any laptop. The slow, async test-suite tier is a later layer; the static
tier alone tests the thesis.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .events import Event, EventKind

_PREFIX = "[mindlas]"
# Only genuine breakage is surfaced, never work-in-progress lint. An unused import or
# unused local is a normal mid-edit state (you import, then use it next turn); flagging
# it would confuse a working trajectory, which is the exact failure mode to avoid. These
# codes are runtime-breaking or syntax-breaking and cannot be a deliberate intermediate:
#   F821 undefined name        F822 undefined name in __all__
#   F823 local used before assignment   F704 yield/await outside function
#   F706 return outside function        E9   syntax / parse errors
_DEFECT_SELECT = ("F821", "F822", "F823", "F704", "F706", "E9")


@dataclass(frozen=True)
class Diagnostic:
    file: str
    line: int
    code: str
    message: str

    def signature(self) -> tuple:
        # Identity for baseline diffing. Line is deliberately excluded so an edit that
        # shifts a pre-existing diagnostic to a new line is not mistaken for a new one.
        # This biases toward silence on ambiguity, the safe direction.
        return (self.file, self.code, self.message)


def changed_files(events: list[Event], recent_turns: int | None = None) -> list[str]:
    """Files the agent edited or wrote, most-recent first, deduped, restricted to
    paths that are checkable.

    Scope is cumulative by default (recent_turns=None): every file touched since the
    session began. This is deliberate. The detector runs one cumulative drift signal
    that fires when a behavioral pattern crosses threshold, which can be several turns
    after the edit that introduced a defect. Anchoring verification to a recent window
    would miss a defect in an earlier-but-still-dirty file. Covering the full set of
    files changed since the baseline frame makes the exact firing turn irrelevant.

    Pass an integer recent_turns only to deliberately narrow the window.
    """
    if not events:
        return []
    lo = None
    if recent_turns is not None:
        lo = max(e.turn for e in events) - recent_turns + 1
    seen: list[str] = []
    for e in reversed(events):
        if lo is not None and e.turn < lo:
            break
        if e.kind == EventKind.TOOL_CALL and e.cls in ("edit", "write") and e.target:
            if e.target not in seen:
                seen.append(e.target)
    return [f for f in seen if _checkable(f)]


def _checkable(path: str) -> bool:
    return path.endswith(".py")


def _run_ruff(paths: list[str], cwd: Path) -> list[Diagnostic]:
    """Run ruff on the given paths, return defect-class diagnostics. Never raises;
    any failure to run yields an empty list, which the caller reads as silence."""
    existing = [p for p in paths if (cwd / p).exists()]
    if not existing:
        return []
    select = ",".join(_DEFECT_SELECT)
    try:
        proc = subprocess.run(
            ["ruff", "check", "--select", select, "--output-format", "json",
             "--no-cache", "--force-exclude", *existing],
            cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10,
        )
    except Exception:
        return []
    out = proc.stdout.strip()
    if not out:
        return []
    try:
        rows = json.loads(out)
    except Exception:
        return []
    diags: list[Diagnostic] = []
    for r in rows:
        loc = r.get("location") or {}
        fn = r.get("filename", "") or "?"
        if fn != "?":
            try:
                # ruff returns absolute, OS-native paths; relativize cross-platform.
                # (The old str(cwd)+"/" strip failed on Windows, where paths use "\".)
                fn = Path(fn).relative_to(cwd).as_posix()
            except ValueError:
                fn = Path(fn).name
        diags.append(Diagnostic(
            file=fn,
            line=int(loc.get("row") or 0),
            code=str(r.get("code") or "?"),
            message=str(r.get("message") or "").strip(),
        ))
    return diags


def verify(paths: list[str], cwd: Path) -> list[Diagnostic]:
    """Run the synchronous static checks on the changed files. v0 is ruff; the union
    point is here so the syntax-compile and (later) type tiers slot in alongside."""
    return _run_ruff(paths, cwd)


def load_baseline(path: Path) -> dict:
    """Per-file baseline: {filepath: set of diagnostic signatures at first sight}."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {f: {tuple(s) for s in sigs} for f, sigs in raw.items()}
    except Exception:
        return {}


def save_baseline(path: Path, baseline: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        serial = {f: sorted(list(sigs)) for f, sigs in baseline.items()}
        path.write_text(json.dumps(serial), encoding="utf-8")
    except Exception:
        pass


def render_defect(diag: Diagnostic) -> str:
    """The located defect, in the self-debug shape: an external observation with a
    file and line, plus one concrete action. No self-evaluation, no second person."""
    where = f"{diag.file} line {diag.line}"
    obs = f"{where}: {diag.message} ({diag.code})."
    action = f"Fix {where} before the next edit."
    return f"{_PREFIX} {obs} {action}"


def gate(events: list[Event], cwd: Path, baseline_path: Path,
         seed_baseline_if_empty: bool = True) -> str | None:
    """Full pipeline. Verify every file changed since the session began, diff each
    against its own first-sight baseline, return a located defect for the first new
    regression or None.

    Each file gets its own baseline, seeded the first time the gate sees that file.
    A file first touched at turn 7 is judged against its state at turn 7, so breakage
    that was already present when the agent reached it is never blamed on the agent.
    Because the file set is cumulative, a defect in an earlier-but-still-dirty file is
    covered no matter how many turns after the edit the cumulative drift finally fires.
    """
    files = changed_files(events)            # cumulative: all edited files since start
    if not files:
        return None
    baseline = load_baseline(baseline_path)
    regressions: list[Diagnostic] = []
    seeded_new = False

    for f in files:
        current = verify([f], cwd)
        cur_sigs = {d.signature() for d in current}
        if f not in baseline:
            # First sight of this file: record its state, surface nothing from it.
            baseline[f] = cur_sigs
            seeded_new = True
            continue
        regressions.extend(d for d in current if d.signature() not in baseline[f])

    if seeded_new:
        save_baseline(baseline_path, baseline)
    if not regressions:
        return None
    # Deliver one defect: first changed file with a regression, lowest line.
    regressions.sort(key=lambda d: (files.index(d.file) if d.file in files else 99, d.line))
    return render_defect(regressions[0])
