"""The test tier. Continuous, ground-truth verification of logic, not just static breakage.

The static tier catches code that cannot run: undefined names, syntax errors. It cannot
catch code that runs and is wrong, the logic errors, the wrong edge case, the off-by-one.
That class only shows up when the tests run. This tier runs them.

The architecture is continuous, not synchronous-at-the-boundary, because a test suite is
too slow to run inside a hook while the agent waits. Instead: after the agent's edits, a
background run is launched and its result is cached as the current test state. At a
propagation boundary the gate reads that cached state synchronously and enforces against
it. The gate never waits on tests; it enforces against the freshest verified state and
says so when that state is stale or missing. This is how gating at machine speed works:
verify continuously, enforce instantly against the latest verdict.

Attribution is preserved the same way as the static tier: the set of tests failing before
the agent started is the baseline, and only NEWLY failing tests count as agent-introduced.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from .events import Event, EventKind
from ..runtime.project_markers import PYTEST_AUTORUN_MARKERS

_FAILED = re.compile(r"^FAILED\s+(\S+)")


@dataclass
class TestState:
    ts: float
    command: str
    new_failures: list      # test ids failing now that were not failing at baseline
    ran: bool               # whether a run completed (False = no command / could not run)
    covered_after_turn: int


def discover_command(events: list[Event], cwd: Path) -> str | None:
    """The test command to run. Prefer the command the agent itself ran, so it resolves
    in the same environment. Fall back to project markers. None means we cannot verify,
    and the tier stays silent rather than guessing."""
    for e in reversed(events):
        if e.kind == EventKind.TOOL_CALL and e.cls == "test_run" and e.target:
            cmd = e.target.strip()
            if cmd:
                return cmd
    if any((cwd / m).exists() for m in PYTEST_AUTORUN_MARKERS) or (cwd / "tests").is_dir():
        return "pytest -q --tb=no -rf -p no:cacheprovider"
    if (cwd / "package.json").exists():
        return "npm test --silent"
    return None


def _parse_failures(stdout: str, stderr: str) -> list:
    out = []
    for line in (stdout + "\n" + stderr).splitlines():
        m = _FAILED.match(line.strip())
        if m:
            out.append(m.group(1))
    return out


def run_once(command: str, cwd: Path, timeout: int = 120) -> tuple[bool, list]:
    """Run the suite once. Returns (ran, failing_test_ids). ran=False on any failure to
    execute, which the caller reads as 'no verdict', never as 'all clear'."""
    env = dict(os.environ)
    # Prevent stale .pyc reuse: rapid edits can share an mtime second, and Python may run
    # old bytecode, making a real failure look like a pass. No bytecode, no staleness.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run(command, cwd=str(cwd), shell=True, env=env,
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except Exception:
        return False, []
    failures = _parse_failures(proc.stdout, proc.stderr)
    # returncode 0 = all passed; 1 = tests failed; >1 = collection/other error
    if proc.returncode == 0:
        return True, []
    if failures:
        return True, failures
    # nonzero but no parseable failures: treat as unverified, not as a pass or a block
    return (proc.returncode == 1), failures


def load_baseline(path: Path) -> set:
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return set()


def save_baseline(path: Path, failing: set) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sorted(failing)), encoding="utf-8")
    except Exception:
        pass


def verify_tests(events: list[Event], cwd: Path, baseline_path: Path,
                 timeout: int = 120) -> tuple[bool, list]:
    """Run the suite, diff against the baseline of pre-agent failures, return
    (ran, new_failures). Seeds the baseline on first run and reports nothing then, so
    tests already broken before the agent started are never blamed on the agent."""
    command = discover_command(events, cwd)
    if not command:
        return False, []
    ran, failing = run_once(command, cwd, timeout)
    if not ran:
        return False, []
    if not baseline_path.exists():
        save_baseline(baseline_path, set(failing))
        return True, []
    baseline = load_baseline(baseline_path)
    return True, [t for t in failing if t not in baseline]


def write_state(state_path: Path, state: TestState) -> None:
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(asdict(state)), encoding="utf-8")
    except Exception:
        pass


def read_state(state_path: Path, max_age_s: float = 900.0) -> TestState | None:
    """The freshest cached test verdict, or None if absent or too stale to trust."""
    try:
        d = json.loads(state_path.read_text(encoding="utf-8"))
        st = TestState(**d)
        if time.time() - st.ts > max_age_s:
            return None
        return st
    except Exception:
        return None


def run_and_cache(events: list[Event], cwd: Path, baseline_path: Path,
                  state_path: Path, turn: int, timeout: int = 120) -> TestState:
    """Run the suite and cache the result as the current test state. This is the body the
    background launcher executes."""
    ran, new_failures = verify_tests(events, cwd, baseline_path, timeout)
    command = discover_command(events, cwd) or ""
    st = TestState(ts=time.time(), command=command, new_failures=new_failures,
                   ran=ran, covered_after_turn=turn)
    write_state(state_path, st)
    return st


def launch_background(ledger_path: Path, cwd: Path, baseline_path: Path,
                      state_path: Path, turn: int) -> None:
    """Fire a detached background test run that caches its result. Returns immediately so
    the hook never blocks. The child re-reads the ledger and runs the suite on its own.

    Never fires under test or CI: a detached suite run spawned from a test would leak an
    orphaned process that outlives the run. The gate's test tier is exercised directly in
    those contexts, not through the background launcher."""
    import os as _os
    if _os.environ.get("PYTEST_CURRENT_TEST") or _os.environ.get("CI") \
            or _os.environ.get("MINDLAS_TESTTIER") == "0":
        return
    try:
        code = (
            "import sys;"
            "from mindlas.vitals.ledger import Ledger;"
            "from mindlas.vitals import testtier;"
            "from pathlib import Path;"
            "ev=Ledger(Path(sys.argv[1])).events();"
            "testtier.run_and_cache(ev, Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]), int(sys.argv[5]))"
        )
        subprocess.Popen(
            [sys.executable, "-c", code, str(ledger_path), str(cwd),
             str(baseline_path), str(state_path), str(turn)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass
