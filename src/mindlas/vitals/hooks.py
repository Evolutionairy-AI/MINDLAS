"""Claude Code hook dispatch. Best-effort, non-blocking, always exit 0."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from . import capture
from .config import ledger_path, loop_alert_path
from .events import Event, EventKind
from .ledger import Ledger


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_turn(led: Ledger) -> int:
    return led.count_kind(EventKind.USER_PROMPT) + 1


def _current_turn(led: Ledger) -> int:
    return max((e.turn for e in led.events()), default=0)


def _maybe_reseed(p: dict, sid: str, hook_event: str) -> dict | None:
    """The live reseed wire. Called from _on_session_start
    ONLY (see the note there for why not UserPromptSubmit). If a Context Repair left a
    pending_resume flag, inject the validated warm pack as `additionalContext` and consume
    the flag (one-shot). Returns the hook output dict, or None when there's nothing to
    reseed. Best-effort — never raises.

    The flag AND the pack are session-keyed, so two sessions in one project never
    cross-contaminate. The flag is looked up under THIS session's id first (a /clear that keeps
    the uuid), then under the id recorded in current_session.json — the session that was live in
    this project just before this start — so a /clear that mints a fresh uuid still reseeds.
    This runs BEFORE the new session stamps that pointer (see _on_session_start ordering).

    CWD TRAP: the flag + pack live in the per-PROJECT tree (runtime.paths, keyed off the project
    root). The hook subprocess's own cwd may differ, so the payload's cwd is threaded into every
    paths.* call as the explicit root — never via os.environ. We also gate on cwd being present:
    without it we cannot reliably resolve the project tree, so we decline rather than guess."""
    try:
        import json
        from pathlib import Path
        from ..runtime import paths
        cwd = p.get("cwd")
        if not cwd or not sid:
            return None
        root = Path(cwd)                                   # thread the root; never mutate os.environ
        flag_sid = sid
        marker = paths.pending_resume_path(flag_sid, root)
        if not marker.exists():
            # Fallback: the session that was live just before this start. A /clear may mint a
            # fresh uuid; that session's repair left the flag under its OLD id, which the
            # pointer still records.
            try:
                prev = json.loads(paths.current_session_path(root).read_text(
                    encoding="utf-8")).get("session_id") or ""
            except (OSError, ValueError):
                prev = ""
            if not prev or prev == sid:
                return None
            flag_sid = prev
            marker = paths.pending_resume_path(flag_sid, root)
            if not marker.exists():
                return None
        # Pull the pack by session id — no shared marker, no embedded path.
        pack_file = paths.latest_pack_path(flag_sid, root)
        if not pack_file.exists():
            return None                                    # flag without a readable pack -> decline
        pack_text = pack_file.read_text(encoding="utf-8")  # read the pack...
        marker.unlink()                                    # ...then consume the one-shot flag
        # Carry the prior pack's structured contract into THIS reseeded session, so a SECOND repair
        # here inherits the objective/constraints instead of re-extracting from the reset ledger
        # (which would otherwise make the objective the bare "resume..." prompt). Best-effort.
        try:
            src_contract = paths.pack_contract_path(flag_sid, root)
            if src_contract.exists():
                dest = paths.inherited_contract_path(sid, root)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(src_contract.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass
        # Arm the measured-after capture — the reseeded session's next status-line
        # render (the only surface with the real window %) records the true post-repair CTX.
        try:
            ap = paths.after_pending_path(sid, root)       # keyed by the RESEEDED session's id
            ap.parent.mkdir(parents=True, exist_ok=True)
            ap.write_text(json.dumps({"state": "AFTER_PENDING"}), encoding="utf-8")
        except Exception:
            pass
        return {"hookSpecificOutput": {"hookEventName": hook_event,
                                       "additionalContext": pack_text}}
    except Exception:
        return None


def _stamp_current_session(sid: str, p: dict) -> None:
    """Record the live session id in a project-local pointer so the repair CLI (a `!`-shell
    subprocess that receives no session_id) can address THIS session's ledger + pack tree by its
    real uuid instead of guessing newest-by-mtime. Hooks always carry session_id — this is the
    reliable channel. Best-effort — never raises."""
    try:
        import json
        from pathlib import Path
        from ..runtime import paths
        cwd = p.get("cwd")
        if not cwd:
            return
        path = paths.current_session_path(Path(cwd))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"session_id": sid, "ts": _ts()}), encoding="utf-8")
    except Exception:
        pass


def _snapshot_transcript(sid: str, p: dict) -> None:
    """Cold store: copy the full session transcript into the
    cold-store dir so nothing is lost when the warm pack reseeds a lean context. The transcript
    file is cumulative, so the latest copy is a superset of every earlier one — we overwrite a
    per-session snapshot (lossless, bounded). Best-effort — never raises."""
    try:
        import shutil
        from pathlib import Path
        from ..runtime import paths
        src = p.get("transcript_path")
        if not src:
            return
        src_path = Path(src)
        if not src_path.exists():
            return
        cwd = p.get("cwd")
        root = Path(cwd) if cwd else None
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in sid) or "session"
        dest_dir = paths.cold_store_dir(root) / "transcripts"
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dest_dir / f"{safe}{src_path.suffix or '.jsonl'}")
    except Exception:
        pass


def _on_session_start(led: Ledger, sid: str, p: dict) -> dict:
    # SessionStart carries source/cwd/transcript_path but NOT the model name — no hook payload does.
    led.append(Event(sid, 0, _ts(), EventKind.SESSION_START, target=p.get("source")))
    # Reseed BEFORE stamping the live-session pointer: the reseed's fallback resolves the
    # repairing session's flag via the pointer the PREVIOUS session left. Gate on the
    # pending_resume flag EXISTING (not on source == "clear", whose values are unconfirmed).
    reseed = _maybe_reseed(p, sid, "SessionStart")
    _stamp_current_session(sid, p)   # record THIS session as the live one for the repair CLI
    return reseed if reseed else {}


def _on_user_prompt(led: Ledger, sid: str, p: dict) -> dict:
    led.append(capture.prompt_event(sid, _next_turn(led), _ts(), p.get("prompt", "")))
    _stamp_current_session(sid, p)   # keep the live-session pointer fresh for the repair CLI
    # NB: the reseed lives ONLY in _on_session_start, deliberately NOT here. The
    # `/mindlas-repair` slash command writes the pending_resume marker mid-turn (via its
    # `!mindlas context repair --apply`); a reseed consumer on UserPromptSubmit would then
    # consume that brand-new marker on the SAME turn, so it would never survive to the `/clear`
    # that is the whole point. SessionStart (fired by `/clear`/a fresh session) is the sole
    # consumer.
    return {}


def _on_pre_tool(led: Ledger, sid: str, p: dict) -> dict:
    """Two jobs at PreToolUse, both inline and autonomous.

    1. Before an edit, snapshot the target file's pre-agent baseline, so the gate's later
       verdict can attribute a defect to the agent even when the first edit introduced it.
    2. At a propagation boundary (commit, push, deploy), run the trust gate: verify the
       agent's changes, record a verdict, and block in live mode or pass through in shadow.
    """
    from pathlib import Path
    from . import gate as gate_mod
    from .capture import _target_of, classify_tool
    from .config import verify_baseline_path, verdict_ledger_path, test_state_path

    tool = p.get("tool_name")
    if not tool:
        return {}
    tool_input = p.get("tool_input") or {}
    cwd = Path(p.get("cwd") or ".")
    baseline = verify_baseline_path(sid)

    cls = classify_tool(tool, tool_input)
    if cls in ("edit", "write"):
        gate_mod.seed_pre_edit(_target_of(tool, tool_input), cwd, baseline)
        return {}

    # LOOP guard: while a stop boundary is active, a retry of the SAME stopped command
    # signature is surfaced (warn) or denied (block) before it runs.
    if cls == "command":
        guard = _loop_guard(tool, tool_input, p)
        if guard:
            return guard

    command = _target_of(tool, tool_input) if cls == "command" else None
    boundary = gate_mod.boundary_of(command)
    if boundary:
        return gate_mod.run_gate(led.events(), cwd, baseline,
                                 verdict_ledger_path(sid), boundary, command or "",
                                 test_state_path(sid))
    return {}


def _on_post_tool(led: Ledger, sid: str, p: dict) -> dict:
    tool = p.get("tool_name")
    if not tool:
        return {}
    turn = max(_current_turn(led), 1)
    led.append(capture.tool_event(sid, turn, _ts(),
                                  tool, p.get("tool_input") or {}, p.get("tool_response")))
    if capture.classify_tool(tool, p.get("tool_input") or {}) in ("edit", "write"):
        _maybe_launch_tests(led, sid, p, turn)   # keep the test verdict fresh on the fast path
    return {}


def _on_post_tool_failure(led: Ledger, sid: str, p: dict) -> dict:
    """PostToolUseFailure fires when a tool call FAILS (nonzero exit / exception / timeout).
    PostToolUse fires only on success, so failure telemetry can arrive ONLY here. Record it as
    a failed tool_call — its markers carry the ("tool_error", <category>) discriminator. The live
    payload carries the failure as a top-level STRING `error`; `tool_error` is
    probed first for compat. After recording, the LOOP live wire evaluates the trigger and, when a
    blind retry loop is detected, surfaces the correction (terminal + additionalContext). Best-effort
    and non-blocking (like every hook)."""
    tool = p.get("tool_name")
    if not tool:
        return {}
    turn = max(_current_turn(led), 1)
    led.append(capture.tool_failure_event(sid, turn, _ts(), tool,
                                          p.get("tool_input") or {},
                                          p.get("tool_error") or p.get("error") or {}))
    return _loop_alert(led, sid, p)


_LOOP_DIRECTIVE = (
    "[MINDLAS] Tool Failure Loop detected: the same tool call keeps failing with no new evidence "
    "(signature {sig}, {n} consecutive failures). STOP retrying the same command unchanged. "
    "Change the plan first — different command, fixed environment assumption, or ask the user. "
    "To record a controlled stop boundary, run: mindlas loop stop --apply (or /mindlas-loop-stop).")


def _loop_alert(led: Ledger, sid: str, p: dict) -> dict:
    """The LOOP live wire. When the deterministic trigger
    fires, surface the correction BOTH ways: a terminal alert for the human and an
    `additionalContext` injection for the agent, so the loop is interrupted in-session instead of
    only moving a gauge. De-duplicated per loop hash (one alert per distinct loop, not one per
    failure). Never fires while a stop boundary is already active. Best-effort — never raises."""
    try:
        import json as _json
        from pathlib import Path
        from ..runtime.tool_loop_state import build_tool_failure_loop_signals
        from ..features.tool_failure_loop import tool_failure_loop_trigger
        cwd = p.get("cwd")
        events = led.events()
        now = max(_current_turn(led), 1)
        # project_root must be a Path (a str breaks the stop-state read and the active stop
        # would fail to suppress the alert — caught by test_no_alert_while_stop_active).
        sig = build_tool_failure_loop_signals(events, session_id=sid, now_turn=now,
                                              project_root=(Path(cwd) if cwd else None))
        if not tool_failure_loop_trigger(sig):
            return {}
        state_file = loop_alert_path(sid)
        try:
            prev = _json.loads(state_file.read_text(encoding="utf-8")).get("loop_hash")
        except (OSError, ValueError):
            prev = None
        if prev == sig.current_loop_hash:
            return {}                                       # already alerted for THIS loop
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(_json.dumps({"loop_hash": sig.current_loop_hash}), encoding="utf-8")
        text = _LOOP_DIRECTIVE.format(sig=sig.active_failure_signature or sig.current_loop_hash,
                                      n=sig.consecutive_failure_count)
        try:
            _notify_terminal(text)
        except Exception:
            pass
        return {"hookSpecificOutput": {"hookEventName": "PostToolUseFailure",
                                       "additionalContext": text}}
    except Exception:
        return {}


def loop_guard_mode() -> str:
    """MINDLAS_LOOP_GUARD: off | warn | block (default warn). Mirrors the gate's shadow-first
    posture — warn never blocks; block returns a PreToolUse deny."""
    import os
    m = os.environ.get("MINDLAS_LOOP_GUARD", "warn").strip().lower()
    return m if m in ("off", "warn", "block") else "warn"


_GUARD_TEXT = ("[mindlas] This exact command signature is under an active Stop boundary "
               "(stop_id {stop_id}) — it repeatedly failed with no new evidence. Do not retry it "
               "unchanged; change the plan first. Release with: mindlas loop release")


def _loop_guard(tool: str, tool_input: dict, p: dict) -> dict | None:
    """While a stop boundary is active, catch a retry of the SAME stopped command signature at
    PreToolUse. warn (default): terminal notice, never blocks. block: PreToolUse deny with the
    reason (opt-in via MINDLAS_LOOP_GUARD=block, mirroring the intercept-compact caution). Returns None to
    let the normal PreToolUse flow continue. Best-effort — never raises."""
    try:
        import json as _json
        from pathlib import Path
        from ..runtime import paths
        from ..runtime.tool_loop_state import _canonical_command, _sha8
        mode = loop_guard_mode()
        if mode == "off":
            return None
        cwd = p.get("cwd")
        sid = p.get("session_id")
        if not sid:
            return None                                     # no session id -> cannot resolve this session's stop
        # CWD TRAP: stop state is project-local — thread the payload cwd as the explicit root.
        active = paths.active_stop_path(sid, Path(cwd) if cwd else None)
        if not active.exists():
            return None
        data = _json.loads(active.read_text(encoding="utf-8"))
        if not bool(data.get("active", True)):
            return None
        stopped_fp = data.get("active_command_fingerprint") or ""
        if not stopped_fp:
            return None
        from .capture import _target_of
        command = _target_of(tool, tool_input) or ""
        incoming_fp = f"{tool}:{_sha8(_canonical_command(command))}"
        if incoming_fp != stopped_fp:
            return None
        text = _GUARD_TEXT.format(stop_id=data.get("stop_id", "?"))
        try:
            _notify_terminal(text)
        except Exception:
            pass
        if mode == "block":
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                           "permissionDecision": "deny",
                                           "permissionDecisionReason": text}}
        return None                                          # warn: surface only, never block
    except Exception:
        return None


def _on_stop(led: Ledger, sid: str, p: dict) -> dict:
    turn = max(_current_turn(led), 1)
    # Stop carries the agent's final message — capture its Tier-2 `done_claim` marker.
    led.append(capture.assistant_event(sid, turn, _ts(), p.get("last_assistant_message")))

    # Snapshot the (cumulative) transcript into the cold store at turn end.
    _snapshot_transcript(sid, p)

    # Continuous verification: launch a background test run so the test state is fresh for
    # the next propagation boundary. Non-blocking; the gate never waits on it.
    _post_turn_verify(led, sid, p, turn)
    return {}


def _on_compact(led: Ledger, sid: str, p: dict) -> dict:
    # turn 0 is valid: a compaction can fire before the first user prompt
    led.append(Event(sid, _current_turn(led), _ts(), EventKind.COMPACT_BOUNDARY,
                     target=p.get("trigger")))
    return {}


_COMPACT_RECO = (
    "[mindlas] Native context compaction is about to run. Consider Mindlas Context Repair "
    "instead — it preserves an evidence-indexed working-state pack and records before/after:\n"
    "    mindlas context repair --preview")


def intercept_compact_mode() -> str:
    """context_repair.intercept_native_compact: off | warn | block (default warn)."""
    import os
    m = os.environ.get("MINDLAS_INTERCEPT_COMPACT", "warn").strip().lower()
    return m if m in ("off", "warn", "block") else "warn"


def _on_pre_compact(led: Ledger, sid: str, p: dict) -> dict:
    """Log the compaction boundary, then (warn/block) surface the Context Repair recommendation.
    Do NOT block native compact by default — always return {} (no block) until a
    blocking path is tested. `block` currently behaves like `warn` plus a logged intent."""
    led.append(Event(sid, _current_turn(led), _ts(), EventKind.COMPACT_BOUNDARY, target=p.get("trigger")))
    # Native compaction is about to discard context — snapshot the full transcript
    # to the cold store FIRST so nothing is lost.
    _snapshot_transcript(sid, p)
    mode = intercept_compact_mode()
    if mode != "off":
        try:
            _notify_terminal(_COMPACT_RECO)
        except Exception:
            pass
    return {}   # never block native compact


def _maybe_launch_tests(led: Ledger, sid: str, p: dict, turn: int,
                       min_interval: float = 8.0) -> None:
    """Launch the background test run, debounced. Called after edits and at turn end, so
    the test verdict is fresh by the time the agent reaches a commit or push in the same
    turn, not only across turns. The debounce collapses a flurry of edits into one run so
    a busy session does not spawn a pile of suites. This is local CPU only, no tokens."""
    import os
    import time
    from pathlib import Path
    # MINDLAS_TESTTIER alone controls the background test tier; MINDLAS_GATE=off no longer
    # silently disables it too (the gate and the tier are separate switches).
    if os.environ.get("MINDLAS_TESTTIER") == "0":
        return
    try:
        from . import testtier
        from .config import (test_baseline_path, test_state_path, ledger_path,
                             session_dir)
        marker = session_dir(sid) / "test_launch.ts"
        now = time.time()
        try:
            last = float(marker.read_text(encoding="utf-8"))
        except Exception:
            last = 0.0
        if now - last < min_interval:
            return                       # a run started recently; do not pile on
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(now), encoding="utf-8")
        cwd = Path(p.get("cwd") or ".")
        testtier.launch_background(ledger_path(sid), cwd, test_baseline_path(sid),
                                   test_state_path(sid), turn)
    except Exception:
        pass


def _post_turn_verify(led: Ledger, sid: str, p: dict, turn: int) -> None:
    # Turn-end backstop; the after-edit launch in PostToolUse is the primary path.
    _maybe_launch_tests(led, sid, p, turn)


def _notify_terminal(text: str) -> None:
    """Best-effort: write the alert to the user's terminal device, bypassing hook capture."""
    device = "CON" if sys.platform.startswith("win") else "/dev/tty"
    try:
        with open(device, "w", encoding="utf-8") as tty:
            tty.write("\n" + text + "\n")
    except OSError:
        pass  # no controlling terminal -> the additionalContext path still delivers it


_ROUTES = {
    "SessionStart": _on_session_start,
    "UserPromptSubmit": _on_user_prompt,
    "PreToolUse": _on_pre_tool,
    "PostToolUse": _on_post_tool,
    "PostToolUseFailure": _on_post_tool_failure,
    "Stop": _on_stop,
    "PreCompact": _on_pre_compact,
    "PostCompact": _on_compact,
}


def dispatch(event_name: str, payload: dict) -> dict:
    handler = _ROUTES.get(event_name)
    if handler is None:
        return {}
    try:
        sid = payload.get("session_id") if isinstance(payload, dict) else None
        if not sid:
            return {}
        return handler(Ledger(ledger_path(sid)), sid, payload)
    except Exception:
        return {}  # never break the session


def main(event_name: str) -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        payload = {}
    out = dispatch(event_name, payload or {})
    if out:
        try:
            print(json.dumps(out))
        except (TypeError, ValueError):
            pass
    return 0  # always succeed; hooks must never block
