import hashlib
import json
from mindlas.actions.loop_stop import LoopStop
from mindlas.runtime.loop_stop_types import StopContext
from mindlas.runtime.tool_loop_state import build_tool_failure_loop_signals
from mindlas.runtime import git_helpers
from mindlas.features.tool_failure_loop import score_tool_failure_loop, ALERT_MIN
from mindlas.vitals.events import Event
from mindlas.vitals.capture import tool_failure_event
from mindlas.vitals.ledger import Ledger
from mindlas.vitals.config import ledger_path
from mindlas.vitals.statusline import build_statusline_text
from _verify_helpers import init_git_repo, write


def _fail(turn, cmd="pytest -q", msg="Command timed out after 600s", tool="Bash"):
    return tool_failure_event("s", turn, f"t{turn}", tool, {"command": cmd},
                              {"type": "timeout", "message": msg})


def _seed_ledger(sid):
    led = Ledger(ledger_path(sid))
    led.append(Event(sid, 1, "t1", "user_prompt", target="Fix the failing tests"))
    for t in (2, 3, 4, 5):
        led.append(_fail(t))
    return led.events()


def _src_hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*")
            if p.is_file() and ".mindlas" not in p.relative_to(root).parts
            and ".git" not in p.relative_to(root).parts}


def test_acceptance_loop_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    init_git_repo(tmp_path)
    write(tmp_path, "src/a.py", "a = 1\n")
    events = _seed_ledger("s")
    ctx = StopContext(events=tuple(events), session_id="s", now_turn=5, project_root=tmp_path)

    # 2. LOOP is ALERT
    sig = build_tool_failure_loop_signals(events, now_turn=5, project_root=tmp_path)
    assert score_tool_failure_loop(sig) >= ALERT_MIN

    # 3-4. preview triggers, writes nothing
    pv = LoopStop().preview(ctx)
    assert pv.trigger is True
    assert not (tmp_path / ".mindlas" / "stops").exists()

    # 7-8. source + diff hash stable across apply
    src_before = _src_hashes(tmp_path)
    diff_before = git_helpers.diff_hash(tmp_path, git_helpers.untracked(tmp_path))

    # 5-6. apply writes the manifest/card/latest/active + correction
    r = LoopStop().apply(ctx, now="20260701T140000")
    assert r.applied is True and r.status == "controlled" and r.controlled_after_loop == 15
    run = tmp_path / ".mindlas" / "stops" / "sessions" / "s" / r.stop_id
    assert (run / "stop_manifest.json").exists() and (run / "stop_card.md").exists()
    assert (tmp_path / ".mindlas" / "stops" / "sessions" / "s" / "latest_stop.json").exists()
    assert (tmp_path / ".mindlas" / "stops" / "sessions" / "s" / "active_stop.json").exists()

    assert _src_hashes(tmp_path) == src_before                         # source unchanged
    assert git_helpers.diff_hash(tmp_path, git_helpers.untracked(tmp_path)) == diff_before  # C6

    # 8-9. correction appended (controlled, partitionable)
    rec = json.loads((tmp_path / ".mindlas" / "reports"
                      / "corrections.jsonl").read_text().splitlines()[-1])
    assert rec["type"] == "loop_stop" and rec["controlled_after_loop"] == 15
    assert rec["rails_labels"]["commands_run_by_stop"] == 0

    # 9-10. scorecard row + RAILS; correction line says controlled, not modeled/planned
    md = (tmp_path / ".mindlas" / "reports" / "latest_scorecard.md").read_text()
    assert "Tool Failure Loop" in md and "controlled" in md
    corr = md.split("## Corrections Applied")[1]
    assert "(modeled)" not in corr and "planned" not in corr and "evidence-based" not in corr
    scj = json.loads((tmp_path / ".mindlas" / "reports" / "latest_scorecard.json").read_text())
    assert scj["features"]["tool_failure_loop"]["final_status"] == "controlled"
    assert scj["rails_export_ready"] is True

    # 11. statusline shows LOOP controlled/STABLE after the stop (stop_active caps <= 24)
    payload = {"session_id": "s", "context_window": {"used_percentage": 40.0}}
    line = build_statusline_text(payload)
    assert "Loop" in line     # the loop gauge column is present (stop-active state covered below)
    loop_after = build_tool_failure_loop_signals(events, now_turn=5, project_root=tmp_path)
    assert loop_after.stop_active is True and score_tool_failure_loop(loop_after) <= 24

    # 12. output says do-not-retry (in the stop card + manifest honesty text)
    assert "Do not retry the same command unchanged" in (run / "stop_card.md").read_text()
