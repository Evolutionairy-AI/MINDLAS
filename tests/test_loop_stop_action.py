import hashlib
import json
import subprocess
from mindlas.actions.loop_stop import LoopStop
from mindlas.runtime.loop_stop_types import StopContext, StopPreview, StopResult
from mindlas.vitals.capture import tool_failure_event, tool_event
from _verify_helpers import init_git_repo, write


def _fail(turn, cmd="pytest -q", msg="Command timed out after 600s", tool="Bash"):
    return tool_failure_event("s", turn, f"t{turn}", tool, {"command": cmd},
                              {"type": "timeout", "message": msg})


def _loop_events():
    from mindlas.vitals.events import Event
    return (Event("s", 1, "t1", "user_prompt", target="Fix the failing tests"),
            _fail(2), _fail(3), _fail(4))


def _ctx(root, events=None):
    return StopContext(events=(events if events is not None else _loop_events()),
                       session_id="s", now_turn=4, project_root=root)


def test_preview_triggers_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    pv = LoopStop().preview(_ctx(tmp_path))
    assert isinstance(pv, StopPreview) and pv.trigger is True and pv.before >= 70
    assert not (tmp_path / ".mindlas" / "stops").exists()          # preview writes nothing


def test_apply_writes_boundary_correction_and_scorecard(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    r = LoopStop().apply(_ctx(tmp_path), now="20260701T120000")
    assert isinstance(r, StopResult) and r.applied is True and r.status == "controlled"
    assert r.controlled_after_loop == 15
    run = tmp_path / ".mindlas" / "stops" / "sessions" / "s" / r.stop_id
    assert (run / "stop_manifest.json").exists()
    assert (tmp_path / ".mindlas" / "stops" / "sessions" / "s" / "active_stop.json").exists()
    rec = json.loads((tmp_path / ".mindlas" / "reports"
                      / "corrections.jsonl").read_text().splitlines()[-1])
    assert list(rec)[0] == "type" and rec["type"] == "loop_stop"
    assert rec["controlled_after_loop"] == 15 and rec["before"] == r.before
    assert rec["failure_signature"] == r.failure_signature
    assert rec["rails_labels"]["source_files_modified"] is False
    assert rec["rails_labels"]["commands_run_by_stop"] == 0
    assert rec["rails_labels"]["stop_active"] is True
    assert rec["rails_labels"]["built_in_tool_failures_live_captured"] is False   # seeded, not live-captured
    sc = json.loads((tmp_path / ".mindlas" / "reports" / "latest_scorecard.json").read_text())
    assert sc["features"]["tool_failure_loop"]["final_status"] == "controlled"
    assert sc["rails_export_ready"] is True


def test_apply_no_trigger_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    from mindlas.vitals.events import Event
    clean = (Event("s", 1, "t1", "user_prompt", target="do it"), tool_event("s", 2, "t2", "Bash",
                                                                             {"command": "ls"}))
    r = LoopStop().apply(_ctx(tmp_path, events=clean), now="20260701T120100")
    assert r.applied is False and r.status == "no_stop"
    assert not (tmp_path / ".mindlas" / "stops").exists()          # no artifacts
    assert not (tmp_path / ".mindlas" / "reports" / "corrections.jsonl").exists()   # no correction


def test_apply_already_active_returns_already_stopped(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    LoopStop().apply(_ctx(tmp_path), now="20260701T120000")        # first stop -> active
    before_ls = sorted(p.name for p in (tmp_path / ".mindlas" / "stops").iterdir())
    n_corrections = len((tmp_path / ".mindlas" / "reports" / "corrections.jsonl").read_text()
                        .splitlines())
    r = LoopStop().apply(_ctx(tmp_path), now="20260701T120200")    # second apply -> suppressed
    assert r.applied is False and r.status == "already_stopped"
    assert sorted(p.name for p in (tmp_path / ".mindlas" / "stops").iterdir()) == before_ls
    assert len((tmp_path / ".mindlas" / "reports" / "corrections.jsonl").read_text()
               .splitlines()) == n_corrections   # nothing new written


def test_apply_leaves_source_unchanged_and_runs_no_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    init_git_repo(tmp_path)
    write(tmp_path, "src/a.py", "a = 1\n")

    def _src_hashes():
        return {p.relative_to(tmp_path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in tmp_path.rglob("*")
                if p.is_file() and ".mindlas" not in p.relative_to(tmp_path).parts
                and ".git" not in p.relative_to(tmp_path).parts}

    def _cached():
        return subprocess.run(["git", "diff", "--cached"], cwd=str(tmp_path),
                              capture_output=True, text=True, check=True).stdout

    before_src, before_idx = _src_hashes(), _cached()
    LoopStop().apply(_ctx(tmp_path), now="20260701T120300")
    assert _src_hashes() == before_src                             # source identical
    assert _cached() == before_idx                                 # git index untouched


def test_apply_threads_root_to_all_write_surfaces(tmp_path, monkeypatch):
    # Explicit StopContext.project_root drives stops AND corrections AND scorecard to the
    # SAME root even when MINDLAS_PROJECT_ROOT points elsewhere; nothing leaks to the env root.
    env_root = tmp_path / "env_elsewhere"
    target = tmp_path / "target"
    env_root.mkdir()
    target.mkdir()
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(env_root))       # deliberately NOT the ctx root
    r = LoopStop().apply(_ctx(target), now="20260701T120400")
    assert r.applied is True
    assert (target / ".mindlas" / "stops" / "sessions" / "s" / r.stop_id / "stop_manifest.json").exists()
    assert (target / ".mindlas" / "reports" / "corrections.jsonl").exists()
    assert (target / ".mindlas" / "reports" / "latest_scorecard.json").exists()
    assert not (env_root / ".mindlas" / "reports").exists()        # nothing leaked to env root
    assert not (env_root / ".mindlas" / "stops").exists()
