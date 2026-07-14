"""Closing the loop for Tool Failure Loop (feat/failure-loop): the live capture of the REAL
PostToolUseFailure payload shape, the in-session alert injection, the PreToolUse retry guard,
and the release re-arm. The payload fixtures mirror the shape captured from a live Claude Code
session (top-level string `error` like "Exit code 127\\n...: command not found")."""
import json

from mindlas.vitals.capture import tool_failure_event
from mindlas.vitals.config import ledger_path, session_dir
from mindlas.vitals.hooks import dispatch
from mindlas.vitals.ledger import Ledger

_LIVE_ERROR = "Exit code 127\n/usr/bin/bash: line 1: nonexistent_command_xyz: command not found"


def _env(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(proj))
    return proj


def _fail_payload(proj, sid="s1", command="nonexistent_command_xyz --flag"):
    # the REAL live shape: error is a top-level STRING, no tool_error key
    return {"session_id": sid, "tool_name": "Bash",
            "tool_input": {"command": command}, "error": _LIVE_ERROR, "cwd": str(proj)}


# --- capture: the real string-error payload becomes a meaningful failed tool_call ---

def test_tool_failure_event_parses_live_string_error():
    e = tool_failure_event("s", 1, "t1", "Bash", {"command": "x --flag"}, _LIVE_ERROR)
    assert e.kind == "tool_call"
    assert "tool_error" in e.markers
    assert "not_found" in e.markers          # classified from "command not found", not "unknown"
    assert e.exit_code == 127                # parsed off the "Exit code 127" prefix
    assert e.error_text                      # canonicalized message, not empty


def test_tool_failure_event_still_accepts_dict_and_garbage():
    d = tool_failure_event("s", 1, "t1", "Bash", {"command": "x"},
                           {"exit_code": 2, "message": "permission denied"})
    assert d.exit_code == 2 and "permission" in d.markers
    g = tool_failure_event("s", 1, "t1", "Bash", {"command": "x"}, None)
    assert "tool_error" in g.markers         # degrades, never raises


def test_dispatch_post_tool_failure_records_failed_call(tmp_path, monkeypatch):
    proj = _env(tmp_path, monkeypatch)
    dispatch("UserPromptSubmit", {"session_id": "s1", "prompt": "run the tool", "cwd": str(proj)})
    dispatch("PostToolUseFailure", _fail_payload(proj))
    evs = [e for e in Ledger(ledger_path("s1")).events() if e.kind == "tool_call"]
    assert evs and "tool_error" in evs[-1].markers and evs[-1].exit_code == 127


# --- the live wire: repeated identical failures inject the stop directive once ---

def test_second_identical_failure_injects_loop_directive(tmp_path, monkeypatch):
    # Two identical same-command failures already clear the score trigger (the spec's own
    # example: "two identical permission failures on the same command still triggers via the
    # score") — so the wire fires at the SECOND blind retry, not the third.
    proj = _env(tmp_path, monkeypatch)
    dispatch("UserPromptSubmit", {"session_id": "s1", "prompt": "run it", "cwd": str(proj)})
    out1 = dispatch("PostToolUseFailure", _fail_payload(proj))
    out2 = dispatch("PostToolUseFailure", _fail_payload(proj))
    assert out1 == {}                         # a single failure is not a loop
    hso = out2["hookSpecificOutput"]
    assert hso["hookEventName"] == "PostToolUseFailure"
    assert "Tool Failure Loop" in hso["additionalContext"]
    assert "mindlas loop stop --apply" in hso["additionalContext"]


def test_alert_fires_once_per_loop_hash(tmp_path, monkeypatch):
    proj = _env(tmp_path, monkeypatch)
    dispatch("UserPromptSubmit", {"session_id": "s1", "prompt": "run it", "cwd": str(proj)})
    out2 = None
    for i in range(2):
        out2 = dispatch("PostToolUseFailure", _fail_payload(proj))
    assert out2 and "hookSpecificOutput" in out2
    out3 = dispatch("PostToolUseFailure", _fail_payload(proj))
    out4 = dispatch("PostToolUseFailure", _fail_payload(proj))
    assert out3 == {} and out4 == {}          # same loop -> no re-nag
    assert (session_dir("s1") / "loop_alert.json").exists()


def test_no_alert_while_stop_active(tmp_path, monkeypatch):
    proj = _env(tmp_path, monkeypatch)
    from mindlas.runtime import paths
    active = paths.active_stop_path("s1", proj)
    active.parent.mkdir(parents=True, exist_ok=True)
    active.write_text(json.dumps({"active": True, "stop_id": "x",
                                  "active_command_fingerprint": ""}), encoding="utf-8")
    dispatch("UserPromptSubmit", {"session_id": "s1", "prompt": "run it", "cwd": str(proj)})
    outs = [dispatch("PostToolUseFailure", _fail_payload(proj)) for _ in range(3)]
    assert all(o == {} for o in outs)         # trigger suppressed by the active stop


# --- the guard: a retry of the STOPPED signature is caught at PreToolUse ---

def _write_active_stop(proj, command, tool="Bash", active=True):
    from mindlas.runtime import paths
    from mindlas.runtime.tool_loop_state import _canonical_command, _sha8
    fp = f"{tool}:{_sha8(_canonical_command(command))}"
    p = paths.active_stop_path("s1", proj)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"active": active, "stop_id": "20260702T000000_abcd",
                             "active_command_fingerprint": fp}), encoding="utf-8")
    return fp


def test_guard_blocks_stopped_signature_in_block_mode(tmp_path, monkeypatch):
    proj = _env(tmp_path, monkeypatch)
    monkeypatch.setenv("MINDLAS_LOOP_GUARD", "block")
    cmd = "nonexistent_command_xyz --flag"
    _write_active_stop(proj, cmd)
    out = dispatch("PreToolUse", {"session_id": "s1", "tool_name": "Bash",
                                  "tool_input": {"command": cmd}, "cwd": str(proj)})
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert "Stop boundary" in hso["permissionDecisionReason"]


def test_guard_lets_different_command_through_in_block_mode(tmp_path, monkeypatch):
    proj = _env(tmp_path, monkeypatch)
    monkeypatch.setenv("MINDLAS_LOOP_GUARD", "block")
    _write_active_stop(proj, "nonexistent_command_xyz --flag")
    out = dispatch("PreToolUse", {"session_id": "s1", "tool_name": "Bash",
                                  "tool_input": {"command": "echo hello"}, "cwd": str(proj)})
    assert "permissionDecision" not in json.dumps(out)   # a changed plan is never guarded


def test_guard_warn_mode_never_blocks(tmp_path, monkeypatch):
    proj = _env(tmp_path, monkeypatch)
    monkeypatch.delenv("MINDLAS_LOOP_GUARD", raising=False)   # default = warn
    cmd = "nonexistent_command_xyz --flag"
    _write_active_stop(proj, cmd)
    out = dispatch("PreToolUse", {"session_id": "s1", "tool_name": "Bash",
                                  "tool_input": {"command": cmd}, "cwd": str(proj)})
    assert "permissionDecision" not in json.dumps(out)


def test_guard_respects_released_stop(tmp_path, monkeypatch):
    proj = _env(tmp_path, monkeypatch)
    monkeypatch.setenv("MINDLAS_LOOP_GUARD", "block")
    cmd = "nonexistent_command_xyz --flag"
    _write_active_stop(proj, cmd, active=False)               # released -> not guarded
    out = dispatch("PreToolUse", {"session_id": "s1", "tool_name": "Bash",
                                  "tool_input": {"command": cmd}, "cwd": str(proj)})
    assert "permissionDecision" not in json.dumps(out)


# --- release: `mindlas loop release` writes active=false and LOOP re-arms ---

def test_loop_release_deactivates_stop_and_rearms(tmp_path, monkeypatch, capsys):
    proj = _env(tmp_path, monkeypatch)
    from mindlas import cli
    from mindlas.runtime import paths
    from mindlas.vitals.hooks import dispatch
    from mindlas.runtime.tool_loop_state import build_tool_failure_loop_signals
    dispatch("SessionStart", {"session_id": "s1", "cwd": str(proj)})   # stamp the live session so the CLI resolves it
    _write_active_stop(proj, "nonexistent_command_xyz --flag")
    assert build_tool_failure_loop_signals([], session_id="s1", project_root=proj).stop_active is True
    rc = cli.main(["loop", "release"])
    out = capsys.readouterr().out
    assert rc == 0 and "released" in out
    data = json.loads(paths.active_stop_path("s1", proj).read_text(encoding="utf-8"))
    assert data["active"] is False
    assert build_tool_failure_loop_signals([], session_id="s1", project_root=proj).stop_active is False
    rc2 = cli.main(["loop", "release"])                       # idempotent
    assert rc2 == 0 and "already released" in capsys.readouterr().out


def test_loop_release_clears_the_alert_dedupe_cache(tmp_path, monkeypatch, capsys):
    # The one-shot loop-alert cache dedupes on loop hash; release re-arms the gauge, so it must
    # also clear the cache or a byte-identical loop recurring after release never re-nudges.
    from mindlas import cli
    from mindlas.vitals.config import loop_alert_path
    proj = _env(tmp_path, monkeypatch)
    dispatch("SessionStart", {"session_id": "s1", "cwd": str(proj)})
    _write_active_stop(proj, "nonexistent_command_xyz --flag")
    cache = loop_alert_path("s1")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"loop_hash": "abc123"}), encoding="utf-8")
    assert cli.main(["loop", "release"]) == 0
    assert not cache.exists()                                  # cache cleared -> loop can re-nudge


def test_loop_release_syncs_latest_stop_pointer(tmp_path, monkeypatch, capsys):
    # Regression: `loop release` flipped active_stop.json but not latest_stop.json, so
    # `mindlas loop latest` reported a released boundary as still Active. Both pointers are
    # co-written active=true by write_stop_artifacts; release must sync both.
    proj = _env(tmp_path, monkeypatch)
    from mindlas import cli
    from mindlas.runtime import paths
    from mindlas.vitals.hooks import dispatch
    dispatch("SessionStart", {"session_id": "s1", "cwd": str(proj)})
    fp = _write_active_stop(proj, "nonexistent_command_xyz --flag")
    lp = paths.latest_stop_path("s1", proj)                    # mirror what --apply writes
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(json.dumps({"active": True, "stop_id": "20260702T000000_abcd",
                              "status": "controlled", "failure_signature": "not_found:Bash:x",
                              "active_tool_name": "Bash", "active_command_fingerprint": fp}),
                  encoding="utf-8")
    assert cli.main(["loop", "release"]) == 0
    capsys.readouterr()
    assert json.loads(paths.active_stop_path("s1", proj).read_text(encoding="utf-8"))["active"] is False
    assert json.loads(lp.read_text(encoding="utf-8"))["active"] is False   # <-- the fix
    assert cli.main(["loop", "latest"]) == 0
    assert "Active: false" in capsys.readouterr().out


def test_loop_release_does_not_clobber_a_newer_stop(tmp_path, monkeypatch, capsys):
    # If latest_stop.json points at a DIFFERENT (newer) stop than the one being released,
    # release must leave it untouched.
    proj = _env(tmp_path, monkeypatch)
    from mindlas import cli
    from mindlas.runtime import paths
    from mindlas.vitals.hooks import dispatch
    dispatch("SessionStart", {"session_id": "s1", "cwd": str(proj)})
    _write_active_stop(proj, "nonexistent_command_xyz --flag")   # stop_id 20260702T000000_abcd
    lp = paths.latest_stop_path("s1", proj)
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(json.dumps({"active": True, "stop_id": "20260703T111111_newer"}),
                  encoding="utf-8")
    assert cli.main(["loop", "release"]) == 0
    capsys.readouterr()
    assert json.loads(lp.read_text(encoding="utf-8"))["active"] is True   # newer pointer preserved


def test_loop_release_without_stop_is_noop(tmp_path, monkeypatch, capsys):
    _env(tmp_path, monkeypatch)
    from mindlas import cli
    rc = cli.main(["loop", "release"])
    assert rc == 0 and "No stop boundary" in capsys.readouterr().out
