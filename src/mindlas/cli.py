"""Command-line entry for the Mindlas reliability instrument."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .vitals import fixtures
from .vitals.ledger import Ledger
from .vitals import hooks as vitals_hooks


def _load_events(args):
    if args.demo:
        return getattr(fixtures, args.demo)()
    if args.from_ledger:
        return Ledger(args.from_ledger).events()
    return None


def _latest_events():
    """Events from the most-recent live session, or None if there are none yet."""
    from .vitals.config import latest_ledger
    led = latest_ledger()
    return Ledger(led).events() if led is not None else None


def _current_session_id():
    """The live session id recorded by the hooks (project-local pointer), or None if absent."""
    import json
    from .runtime import paths
    p = paths.current_session_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("session_id") or None
    except (OSError, ValueError):
        return None


def _resolve_session_events(args):
    """Pick the target session's events, preferring an explicit --session id, then the live
    session pointer the hooks maintain, then the newest-by-mtime heuristic. This is what kills
    the mtime guess: a repair addresses the EXACT live session, not whichever ledger was written
    last. Each preferred id is used only if its ledger actually exists, so an unresolved
    ${CLAUDE_SESSION_ID} placeholder (or a stale pointer) degrades cleanly to the old behavior."""
    from .vitals.config import ledger_path
    for sid in (getattr(args, "session", None), _current_session_id()):
        if sid:
            led = ledger_path(sid)
            if led.exists():
                return Ledger(led).events()
    return _latest_events()


_NO_SESSIONS = "No Mindlas sessions found yet — run a session with the hooks installed first."


def _persisted_context_pct(sid):
    """The last MEASURED context-window % the status-line hook cached for this session, so the CLI
    CTX reading matches the live status line instead of proxying mass to ~100%. None when never
    measured -> caller falls back to the proxy. Delegates to the shared reader in vitals.config so
    the CLI status surfaces and the Context Repair action stay in lock-step."""
    from .vitals.config import read_context_pct
    return read_context_pct(sid)


def _ctx_reading_from_events(events):
    """Best-effort live Rot reading for CLI status surfaces; None on any error so one bad gauge
    degrades to `--` instead of blanking the band or crashing the command (mirrors
    _verify_reading_from_events; callers must tolerate None)."""
    try:
        from .runtime.state import build_context_rot_signals
        from .features.context_rot import ContextRotScorer
        now = max((e.turn for e in events), default=0)
        pct = _persisted_context_pct(events[0].session_id if events else None)
        return ContextRotScorer().read(build_context_rot_signals(events, context_pct=pct, now_turn=now))
    except Exception:
        return None


def _verify_reading_from_events(events):
    """Best-effort live VERIFY reading for CLI status surfaces; None on any error so the
    status line never breaks (mirrors statusline._verify_reading)."""
    try:
        from .runtime.verification_state import build_verification_debt_signals
        from .features.verification_debt import VerificationDebtScorer
        sid = events[0].session_id if events else None
        now = max((e.turn for e in events), default=0)
        sig = build_verification_debt_signals(events, session_id=sid, now_turn=now)
        return VerificationDebtScorer().read(sig)
    except Exception:
        return None


def _blast_reading_from_events(events):
    """Best-effort live BLAST reading for CLI status surfaces; None on any error so the
    status line never breaks (mirrors _verify_reading_from_events)."""
    try:
        from .runtime.blast_state import build_change_blast_signals
        from .features.blast_radius import ChangeBlastRadiusScorer
        sid = events[0].session_id if events else None
        sig = build_change_blast_signals(events, session_id=sid)
        return ChangeBlastRadiusScorer().read(sig)
    except Exception:
        return None


def _loop_reading_from_events(events):
    """Best-effort live LOOP reading for CLI status surfaces; None on any error so the
    status line never breaks (mirrors _verify_reading_from_events)."""
    try:
        from .runtime.tool_loop_state import build_tool_failure_loop_signals
        from .features.tool_failure_loop import ToolFailureLoopScorer
        sid = events[0].session_id if events else None
        now = max((e.turn for e in events), default=0)
        sig = build_tool_failure_loop_signals(events, session_id=sid, now_turn=now)
        return ToolFailureLoopScorer().read(sig)
    except Exception:
        return None


def _cmd_status(args: argparse.Namespace) -> int:
    """The CTX-first one-line reliability status."""
    events = _load_events(args)
    if events is None:
        # Live-session pointer first, mtime only as a last resort — matches `context status`.
        # Newest-by-mtime alone picks whatever ledger was touched last (e.g. a stray `demo`
        # session), so the two surfaces read DIFFERENT sessions and disagree.
        events = _resolve_session_events(args)
    if events is None:
        print(_NO_SESSIONS)
        return 0
    from .runtime.render import render_ctx_statusline
    print(render_ctx_statusline(_ctx_reading_from_events(events),
                                _verify_reading_from_events(events),
                                _blast_reading_from_events(events),
                                _loop_reading_from_events(events),
                                plain=bool(getattr(args, "plain", False))))
    return 0


def _cmd_statusline(args: argparse.Namespace) -> int:
    from .vitals.statusline import run_statusline
    return run_statusline()


def _cmd_install_statusline(args: argparse.Namespace) -> int:
    from .vitals.install import install_statusline
    try:
        result = install_statusline(uninstall=args.uninstall)
    except (OSError, ValueError) as e:
        print(f"Could not update Claude Code settings.json: {e}")
        return 1
    print(f"Mindlas statusline {result}.")
    return 0


def _cmd_install_hooks(args: argparse.Namespace) -> int:
    from .vitals.install import install_hooks
    try:
        result = install_hooks(uninstall=args.uninstall)
    except (OSError, ValueError) as e:
        print(f"Could not update Claude Code hooks: {e}")
        return 1
    if result == "installed":
        print("Mindlas hooks installed — restart Claude Code to activate.")
    else:
        print(f"Mindlas hooks {result}.")
    return 0


def _cmd_hook(args: argparse.Namespace) -> int:
    return vitals_hooks.main(args.event)


def _cmd_verify_changed(args: argparse.Namespace) -> int:
    """Run the deterministic static verifier on the files changed this session. If clean,
    record an `allow` verdict (the Verification-Debt reset condition); if it
    finds an introduced defect, print the located finding and exit non-zero."""
    import time
    from .vitals import config as _config, gate
    from .vitals.verify import verify as static_verify, changed_files, render_defect

    sid = getattr(args, "session", None) or _config.latest_session_id()
    if not sid:
        print(_NO_SESSIONS)
        return 0
    lp = _config.ledger_path(sid)
    events = Ledger(lp).events() if lp.exists() else []
    files = changed_files(events)
    findings = static_verify(files, Path.cwd()) if files else []
    now_turn = max((e.turn for e in events), default=0)
    if not findings:
        v = gate.Verdict(ts=time.time(), boundary="mindlas verify --changed",
                         decision="allow", mode="manual", findings=[],
                         command="mindlas verify --changed", turn=now_turn)
        gate._record(_config.verdict_ledger_path(sid), v)
        print(f"[mindlas] verify clean on {len(files)} changed file(s) — Verification Debt reset.")
        return 0
    for d in findings:
        print(render_defect(d))
    return 1


def _cmd_verify_status(args: argparse.Namespace) -> int:
    # Live-session gauge. Resolve the target session (explicit --session -> live-session pointer ->
    # newest-by-mtime) so the gauge reads THIS session's verifier result, not a concurrent one.
    from .runtime.verification_state import build_verification_debt_signals
    from .features.verification_debt import VerificationDebtScorer
    from .runtime.render import render_verify_gauge
    events = _resolve_session_events(args) or []
    sid = (events[0].session_id if events else None)
    now = max((e.turn for e in events), default=0)
    sig = build_verification_debt_signals(events, session_id=sid, now_turn=now)
    print(render_verify_gauge(VerificationDebtScorer().read(sig),
                              plain=bool(getattr(args, "plain", False))))
    return 0


def _cmd_verify_gate(args: argparse.Namespace) -> int:
    import time
    from .actions.verify_gate import VerifyGate
    from .runtime.verify_types import VerifyContext
    events = _resolve_session_events(args) or []
    sid = events[0].session_id if events else "session"
    now_turn = max((e.turn for e in events), default=0)
    ctx = VerifyContext(events=tuple(events), session_id=sid, now_turn=now_turn,
                        allow_full_suite=bool(getattr(args, "full", False)))
    gate = VerifyGate()
    if getattr(args, "apply", False):
        vr = gate.apply(ctx, now=time.strftime("%Y%m%dT%H%M%S"))
        print(f"[mindlas] Verify Gate applied: VERIFY {vr.before} -> {vr.after} "
              f"(result {vr.status}, coverage {vr.coverage}, {len(vr.commands)} command(s)).")
        for r in vr.commands:
            print(f"  - {r.label}: {r.status}")
        return 0
    pv = gate.preview(ctx)                                  # default: --preview
    if pv.status == "skipped":
        print(f"[mindlas] Verify Gate: nothing to run -- {pv.explanation}")
        return 0
    # preview reports trigger status but never requires a trigger -- planned checks are
    # always shown when something is plannable, so the user can verify on demand below the alert line.
    print(f"[mindlas] Verify Gate plan -- VERIFY before={pv.before}, "
          f"trigger={'yes' if pv.trigger else 'no'} (preview does not require a trigger):")
    for c in pv.commands:
        print(f"  - {c.label} [{c.coverage}]: {' '.join(c.command)}")
    print("  (preview writes nothing; run `--apply` to execute)")
    return 0


def _cmd_verify_latest(args: argparse.Namespace) -> int:
    import json
    from .runtime import paths
    events = _resolve_session_events(args) or []
    sid = events[0].session_id if events else "session"
    p = paths.latest_verifier_result_path(sid)
    if not p.exists():
        print("[mindlas] No verifier result yet -- run `mindlas verify gate --apply` first.")
        return 0
    data = json.loads(p.read_text(encoding="utf-8"))
    print(f"VERIFY latest: status={data.get('status')} coverage={data.get('coverage')} "
          f"turn={data.get('verifier_turn')} diff={data.get('diff_hash')}")
    for c in data.get("commands", []):
        print(f"  - {c['label']}: {c['status']} (exit {c['exit_code']}, {c['duration_ms']}ms)")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    cmd = getattr(args, "verify_cmd", None)
    if cmd == "status":
        return _cmd_verify_status(args)
    if cmd == "gate":
        return _cmd_verify_gate(args)
    if cmd == "latest":
        return _cmd_verify_latest(args)
    # No positional: legacy back-compat (verify --changed reset).
    return _cmd_verify_changed(args)


def _cmd_blast(args: argparse.Namespace) -> int:
    cmd = getattr(args, "blast_cmd", None)
    if cmd == "split":
        return _cmd_blast_split(args)
    if cmd == "latest":
        return _cmd_blast_latest(args)
    return _cmd_blast_status(args)                       # "status" or no positional


def _cmd_blast_status(args: argparse.Namespace) -> int:
    from .runtime.blast_state import build_change_blast_signals
    from .features.blast_radius import ChangeBlastRadiusScorer
    from .runtime.render import render_blast_gauge
    # uuid keying: explicit --session -> live pointer -> mtime fallback, so the gauge reads the
    # EXACT live session, not whichever ledger was written last (mirrors loop/verify).
    events = _resolve_session_events(args) or []
    sid = events[0].session_id if events else None
    sig = build_change_blast_signals(events, session_id=sid)
    print(render_blast_gauge(ChangeBlastRadiusScorer().read(sig),
                             plain=bool(getattr(args, "plain", False))))
    return 0


def _cmd_blast_split(args: argparse.Namespace) -> int:
    import time
    from .actions.patch_splitter import PatchSplitter
    from .runtime.split_types import SplitContext
    # A split must target the EXACT session whose diff triggered it — under concurrent sessions the
    # newest-by-mtime ledger may belong to someone else's run. Resolve --session first (loop parity).
    events = _resolve_session_events(args) or []
    sid = events[0].session_id if events else "session"
    now_turn = max((e.turn for e in events), default=0)
    ctx = SplitContext(events=tuple(events), session_id=sid, now_turn=now_turn)
    ps = PatchSplitter()
    if getattr(args, "apply", False):
        r = ps.apply(ctx, now=time.strftime("%Y%m%dT%H%M%S"))
        if r.status == "no_split":
            print("[mindlas] Patch Splitter: diff is already coherent; no split produced.")
            return 0
        print(f"[mindlas] Patch Splitter completed. BLAST: {r.before} → {r.planned_after} planned")
        print(f"  Status: {r.status}  Bundles written: {len(r.bundles)}")
        print(f"  Manifest: {r.manifest_path}")
        print("  No source files were modified.")
        print("  Next suggested action: mindlas verify gate --preview")
        return 0
    pv = ps.preview(ctx)                                 # default: --preview
    if pv.plan.planned_after_blast >= pv.before or len(pv.plan.bundles) <= 1:
        # a no-reduction diff must NEVER print a fake "X -> X
        # planned" win. The action already computed the honest reason into pv.explanation.
        print(f"[mindlas] Patch Splitter preview — BLAST {pv.before}: {pv.explanation}")
        return 0
    print(f"[mindlas] Patch Splitter preview — BLAST before={pv.before}, "
          f"planned after={pv.plan.planned_after_blast}, trigger={'yes' if pv.trigger else 'no'}, "
          f"bundles={len(pv.plan.bundles)}, validation={pv.plan.validation_status}")
    for b in pv.plan.bundles:
        print(f"  - {b.name} [{b.file_count} files, {b.changed_lines} lines]: {b.reason}")
    print("  No source files will be modified. (preview writes nothing; run `--apply` to write "
          "the split queue.)")
    return 0


def _cmd_blast_latest(args: argparse.Namespace) -> int:
    import json
    from .runtime import paths
    events = _resolve_session_events(args) or []
    sid = events[0].session_id if events else "session"
    p = paths.latest_split_manifest_path(sid)
    if not p.exists():
        print("[mindlas] No patch split yet — run `mindlas blast split --apply` first.")
        return 0
    data = json.loads(p.read_text(encoding="utf-8"))
    print(f"Latest Patch Split\nSplit ID: {data.get('split_id')}")
    print(f"Original BLAST: {data.get('before_blast')}")
    print(f"Planned after BLAST: {data.get('planned_after_blast')}")
    # "Coverage validation", not bare "Validation" (avoid the tests-passed misread).
    print(f"Bundles: {data.get('bundle_count')}\n"
          f"Coverage validation: {data.get('validation_status')}")
    return 0


def _cmd_loop(args: argparse.Namespace) -> int:
    cmd = getattr(args, "loop_cmd", None)
    if cmd == "stop":
        return _cmd_loop_stop(args)
    if cmd == "release":
        return _cmd_loop_release(args)
    if cmd == "latest":
        return _cmd_loop_latest(args)
    return _cmd_loop_status(args)                        # "status" or no positional


def _cmd_loop_status(args: argparse.Namespace) -> int:
    from .runtime.tool_loop_state import build_tool_failure_loop_signals
    from .features.tool_failure_loop import ToolFailureLoopScorer
    from .runtime.render import render_loop_gauge
    # uuid keying: explicit --session -> live pointer -> mtime fallback, so the gauge reads the
    # EXACT live session, not whichever ledger was written last.
    events = _resolve_session_events(args) or []
    sid = events[0].session_id if events else None
    now = max((e.turn for e in events), default=0)
    sig = build_tool_failure_loop_signals(events, session_id=sid, now_turn=now)
    print(render_loop_gauge(ToolFailureLoopScorer().read(sig),
                            plain=bool(getattr(args, "plain", False))))
    return 0


def _cmd_loop_stop(args: argparse.Namespace) -> int:
    import time
    from pathlib import Path
    from .actions.loop_stop import LoopStop
    from .runtime.loop_stop_types import StopContext
    # uuid keying: a Stop must target the EXACT session whose loop triggered it — under concurrent
    # sessions the newest-by-mtime ledger may belong to someone else's clean run.
    events = _resolve_session_events(args) or []
    sid = events[0].session_id if events else "session"
    now_turn = max((e.turn for e in events), default=0)
    ctx = StopContext(events=tuple(events), session_id=sid, now_turn=now_turn)
    ls = LoopStop()
    if getattr(args, "apply", False):
        r = ls.apply(ctx, now=time.strftime("%Y%m%dT%H%M%S"))
        if r.status in ("no_stop", "already_stopped"):
            reason = ("a stop boundary is already active" if r.status == "already_stopped"
                      else "no repeated tool-failure loop detected")
            print(f"[mindlas] Stop not applied — {reason}.")
            return 0
        print(f"[mindlas] Stop applied. LOOP: {r.before} -> {r.controlled_after_loop} controlled")
        print(f"  Status: {r.status}  Stop ID: {r.stop_id}")
        print(f"  Failure signature: {r.failure_signature}  Tool: {r.active_tool_name}")
        # The card (stop_card.md) is the human-facing artifact; it is the manifest's sibling
        # in the run dir. r.manifest_path points at stop_manifest.json — surface the CARD, not it.
        print(f"  Stop card: {Path(r.manifest_path).with_name('stop_card.md')}")
        print("  No source files were modified. No commands were run.")
        print("  Do not retry the same command unchanged.")
        return 0
    pv = ls.preview(ctx)                                 # default: --preview
    if not pv.trigger:
        # a no-trigger preview prints the honest reason, never a fabricated "X -> X controlled".
        print(f"[mindlas] Stop preview — LOOP {pv.before}: {pv.explanation}")
        return 0
    print(f"[mindlas] Stop preview — LOOP before={pv.before}, trigger=yes, "
          f"signature={pv.signals.active_failure_signature}, tool={pv.signals.active_tool_name}")
    print(f"  Reason: {pv.explanation}")
    print("  Recommended next actions:")
    for i, a in enumerate(pv.recommended_next_actions, start=1):
        print(f"    {i}. {a}")
    print("  No source files will be modified. (preview writes nothing; run `--apply` to write "
          "the stop boundary.)")
    return 0


def _cmd_loop_release(args: argparse.Namespace) -> int:
    """`mindlas loop release` — deactivate the active stop boundary (write active=false).
    The reader (tool_loop_state._read_stop_state) honors the flag, so LOOP re-arms: the
    controlled cap lifts and a NEW failure loop can trigger Stop again. The stop artifacts
    stay on disk (history is never deleted by design). The `latest_stop.json` pointer is synced too
    so `mindlas loop latest` never reports a released boundary as still Active, and the one-shot
    loop-alert dedupe cache is cleared so a recurring loop re-fires the in-session nudge."""
    import json
    from .runtime import paths
    events = _resolve_session_events(args) or []
    sid = events[0].session_id if events else "session"
    p = paths.active_stop_path(sid)
    if not p.exists():
        print("[mindlas] No stop boundary to release.")
        return 0
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("[mindlas] Active stop file is unreadable; nothing released.")
        return 1
    if not bool(data.get("active", True)):
        print(f"[mindlas] Stop {data.get('stop_id', '?')} is already released.")
        return 0
    data["active"] = False
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    # Keep the latest_stop.json pointer consistent with the release so `mindlas loop latest`
    # does not misreport a released boundary as Active (the two are co-written active=true by
    # write_stop_artifacts, but only active_stop.json was being flipped here). Sync ONLY when it
    # points at the SAME stop we just released, so a newer stop is never clobbered; best-effort
    # (active_stop.json above is the authoritative flag the gauge reads). The per-run
    # stop_manifest.json under .mindlas/stops/<id>/ is the immutable historical record (never
    # deleted by design) and is deliberately left untouched.
    released_id = data.get("stop_id")
    lp = paths.latest_stop_path(sid)
    if lp.exists():
        try:
            ldata = json.loads(lp.read_text(encoding="utf-8"))
            if ldata.get("stop_id") == released_id and bool(ldata.get("active", True)):
                ldata["active"] = False
                lp.write_text(json.dumps(ldata, indent=2), encoding="utf-8")
        except (OSError, ValueError):
            pass
    # Clear the one-shot loop-alert dedupe cache: the gauge re-arms above, so a byte-identical
    # loop recurring after this release must re-fire the in-session nudge, not stay deduped.
    from .vitals.config import loop_alert_path
    loop_alert_path(sid).unlink(missing_ok=True)
    print(f"[mindlas] Stop {data.get('stop_id', '?')} released — LOOP re-armed.")
    print("  The stop artifacts remain under .mindlas/stops/ (history is kept).")
    return 0


def _cmd_loop_latest(args: argparse.Namespace) -> int:
    import json
    from .runtime import paths
    events = _resolve_session_events(args) or []
    sid = events[0].session_id if events else "session"
    p = paths.latest_stop_path(sid)
    if not p.exists():
        print("[mindlas] No Stop yet — run `mindlas loop stop --apply` first.")
        return 0
    data = json.loads(p.read_text(encoding="utf-8"))
    print(f"Latest Stop\nStop ID: {data.get('stop_id')}")
    print(f"Status: {data.get('status')}")
    print(f"Failure signature: {data.get('failure_signature')}")
    print(f"Tool: {data.get('active_tool_name')}")
    print(f"Active: {str(bool(data.get('active'))).lower()}")
    return 0


def _cmd_context(args: argparse.Namespace) -> int:
    """`mindlas context status|repair|resume`."""
    from datetime import datetime
    from .actions.context_repair import ContextRepair, RepairContext
    from .runtime import paths

    if args.context_cmd == "resume":
        # Manual continuation handoff: print the pack for a human to paste/continue from.
        # The SessionStart hook is the LIVE, sole consumer of the pending_resume
        # flag, so this read-only path deliberately does NOT unlink it — otherwise the two
        # would race over the one-shot flag. Use this only when the plugin
        # hook is not wired; with the plugin loaded, `/clear` triggers the hook reseed instead.
        # Both the flag and the pack are addressed by this session's id (mirrors the reseed hook).
        r_events = _resolve_session_events(args) or []
        r_sid = r_events[0].session_id if r_events else None
        marker = paths.pending_resume_path(r_sid) if r_sid else None
        pack_file = paths.latest_pack_path(r_sid) if r_sid else None
        if not marker or not marker.exists() or not pack_file or not pack_file.exists():
            print("No Context Repair pack to resume. Run `mindlas context repair --apply` first.")
            return 0
        print("# Manual continuation handoff — continue from the pack below.")
        print("# (With the plugin loaded, `/clear` auto-reseeds this pack via SessionStart;")
        print("#  this manual handoff does not consume the pending-resume marker.)\n")
        print(pack_file.read_text(encoding="utf-8"))
        return 0

    events = _load_events(args)
    if events is None:
        events = _resolve_session_events(args)
    if events is None:
        print(_NO_SESSIONS)
        return 0
    sid = events[0].session_id if events else "session"
    now = max((e.turn for e in events), default=0)

    if args.context_cmd == "status":
        # The detailed CTX gauge block (bar + contributing facts + correction), parallel to
        # `verify/blast/loop status`. The one-line four-gauge overview lives in `mindlas status`.
        from .runtime.render import render_ctx_gauge
        block = render_ctx_gauge(_ctx_reading_from_events(events),
                                 plain=bool(getattr(args, "plain", False)))
        pending = "yes" if paths.pending_resume_path(sid).exists() else "no"
        print(f"{block}\nPending resume: {pending}")
        return 0

    # repair
    rc = RepairContext(events=tuple(events), session_id=sid, now_turn=now)
    action = ContextRepair()
    if args.apply:
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        human = ("accepted" if getattr(args, "accept", False)
                 else "rejected" if getattr(args, "reject", False) else None)
        ds_check = None
        downstream = None
        if getattr(args, "verify", False):
            from .runtime.downstream import run_downstream_check, demo_sample_target
            if args.demo:
                cwd, files = demo_sample_target()
            else:
                pv = action.preview(rc)
                cwd, files = Path.cwd(), list(pv.pack_data.changed_files)
            ds_check = run_downstream_check(files, cwd)
            downstream = ds_check.to_dict()
        res = action.apply(rc, now=ts, downstream=downstream, human_decision=human)
        if not res.applied:
            print(f"Context Repair NOT applied — validation: {res.validation}. "
                  f"Draft written to {res.pack_path}")
            return 1
        print("Context Repair applied.")
        print(f"Rot (modeled post-repair): {res.before} → {res.modeled_after_ctx}")
        print(f"Pack: {res.pack_path}")
        print(f"Evidence preserved: {res.evidence_preserved}")
        print(f"Validation: {res.validation}")
        print(f"Constraints preserved: {res.constraints_preserved}")
        if ds_check is not None:
            label = "demo sample — " if args.demo else ""
            print(f"Downstream check: {ds_check.result} "
                  f"({label}{ds_check.tool}, {ds_check.findings} findings)")
        if human:
            print(f"Human decision: {human}")
        print(f"Scorecard: {paths.scorecard_md_path()}")
        return 0
    # preview (default)
    pv = action.preview(rc)
    print(pv.pack_text)
    print(f"\nRot (before): {pv.before}")
    print(f"Validation: {pv.validation.status}")
    if pv.validation.errors:
        print("Failures: " + ", ".join(pv.validation.errors))
    return 0


def _cmd_scorecard(args: argparse.Namespace) -> int:
    """Render the session scorecard, reconstructed from the session ledger + corrections.jsonl.

    Default = the corrections-only PROOF artifact: Rot (the headline) always renders, while
    Verify/Blast/Loop show '--' until their action runs this session. `--latest` = a LIVE snapshot:
    every gauge shows its current reading, like the status line. `--json` always emits the proof
    export (the machine/RAILS record stays canonical), independent of `--latest`."""
    import json
    from .runtime import paths
    from .runtime.scorecard import (build_scorecard, correction_after,
                                    render_scorecard_terminal, scorecard_to_json)
    from .vitals.context import extract_context

    events = _load_events(args)                       # --demo/--from-ledger peek, else None
    if events is None:
        events = _resolve_session_events(args)        # live-session pointer first (matches `status`)
    if events is None:
        print(_NO_SESSIONS)
        return 0
    reading = _ctx_reading_from_events(events)
    ctx_score = reading.score if reading is not None else 0        # reading degrades to None on error
    ctx_state = reading.state if reading is not None else "STABLE"

    corrections: tuple[dict, ...] = ()
    cp = paths.corrections_path()
    if cp.exists():
        corrections = tuple(json.loads(ln) for ln in cp.read_text(encoding="utf-8").splitlines()
                            if ln.strip())
    # scorecard_meta.json carries the session id + task the corrections belong to. A /clear reseed
    # moves the LIVE session to a new id, so the current-session events would give the wrong task —
    # the proof stays with the original session recorded here.
    meta: dict = {}
    mp = paths.scorecard_meta_path()
    if mp.exists():
        try:
            meta = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}

    # Context Rot aggregates come ONLY from context_repair corrections (the type-partition
    # invariant: a verify_gate/patch_splitter/loop_stop row must not perturb the Rot count).
    ctx_corr = [c for c in corrections if c.get("type") == "context_repair"]
    if ctx_corr:
        ctx_befores = [c["before"] for c in ctx_corr if "before" in c]
        ctx_afters = [correction_after(c) for c in ctx_corr if correction_after(c) is not None]
        ctx_max = max(ctx_befores) if ctx_befores else ctx_score
        ctx_final = ctx_afters[-1] if ctx_afters else ctx_score
        ctx_alerts = len(ctx_corr)
    else:                                             # no repair yet -> the live rot reading
        ctx_max = ctx_final = ctx_score
        ctx_alerts = 1 if ctx_state == "ALERT" else 0
    sid = meta.get("session_id") or (events[0].session_id if events else "session")
    task = meta.get("task") or extract_context(events).objective or "(unknown task)"

    # --latest = live snapshot: pass every gauge's CURRENT reading (from the live session) so the
    # card matches the status line. Default (and any --json) leaves these None -> proof artifact.
    ctx_live = verify_live = blast_live = loop_live = None
    if getattr(args, "latest", False) and not args.json:
        ctx_live = reading.score if reading is not None else None
        vr = _verify_reading_from_events(events)
        br = _blast_reading_from_events(events)
        lr = _loop_reading_from_events(events)
        verify_live = vr.score if vr is not None else None
        blast_live = br.score if br is not None else None
        loop_live = lr.score if lr is not None else None
    sc = build_scorecard(session_id=sid, task=task, ctx_max=ctx_max, ctx_final=ctx_final,
                         ctx_alerts=ctx_alerts, corrections=corrections, ctx_live=ctx_live,
                         verify_live=verify_live, blast_live=blast_live, loop_live=loop_live)
    if args.json:
        print(scorecard_to_json(sc))
    else:
        print(render_scorecard_terminal(sc))
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # py3.7+; no-op if already utf-8
        except (AttributeError, ValueError):
            pass  # e.g. pytest capture replaces stdout with a non-reconfigurable object

    parser = argparse.ArgumentParser(
        prog="mindlas", description="Mindlas — the live reliability instrument for coding agents")
    from . import __version__
    parser.add_argument("--version", action="version", version=f"mindlas {__version__}")
    sub = parser.add_subparsers(dest="command")

    stat = sub.add_parser("status", help="One-line Rot-first reliability status")
    stat.add_argument("--from-ledger", dest="from_ledger")
    stat.add_argument("--demo")
    stat.add_argument("--latest", action="store_true", help="the current/most-recent session (the default)")
    stat.add_argument("--plain", action="store_true", help="no ANSI color")
    stat.set_defaults(func=_cmd_status)

    ctx = sub.add_parser("context", help="Context Rot repair (preview/apply/resume/status)")
    ctx.add_argument("context_cmd", choices=["status", "repair", "resume"],
                     help="status | repair | resume (resume = print the pack for a manual "
                          "handoff; with the plugin loaded, /clear reseeds it automatically)")
    ctx.add_argument("--preview", action="store_true", help="dry-run: build the pack, write nothing")
    ctx.add_argument("--apply", action="store_true", help="apply the repair (write pack + record)")
    ctx.add_argument("--latest", action="store_true", help="the current/most-recent session")
    ctx.add_argument("--plain", action="store_true", help="uncolored gauge output (status)")
    ctx.add_argument("--session", help="target this exact session id (default: live-session "
                     "pointer written by the hooks, else most recent by mtime)")
    ctx.add_argument("--from-ledger", dest="from_ledger")
    ctx.add_argument("--demo")
    ctx.add_argument("--verify", action="store_true",
                     help="with --apply: run an independent ruff health check on changed files")
    _hd = ctx.add_mutually_exclusive_group()
    _hd.add_argument("--accept", action="store_true",
                     help="record human_decision=accepted in the outcome")
    _hd.add_argument("--reject", action="store_true",
                     help="record human_decision=rejected in the outcome")
    ctx.set_defaults(func=_cmd_context)

    sc = sub.add_parser("scorecard", help="Render the session scorecard (styled terminal or JSON)")
    sc.add_argument("--latest", action="store_true", help="the current/most-recent session")
    sc.add_argument("--from-ledger", dest="from_ledger")
    sc.add_argument("--demo")
    sc.add_argument("--json", action="store_true", help="machine-readable JSON export")
    sc.set_defaults(func=_cmd_scorecard)

    vfy = sub.add_parser("verify", help="Verification Debt gauge + Verify Gate")
    vfy.add_argument("verify_cmd", nargs="?", choices=["status", "gate", "latest"])
    vfy.add_argument("--changed", action="store_true", help="verify files changed this session (default)")
    vfy.add_argument("--session", help="session id (default: most recent)")
    vfy.add_argument("--preview", action="store_true")   # gate
    vfy.add_argument("--apply", action="store_true")      # gate
    vfy.add_argument("--full", action="store_true")        # gate
    vfy.add_argument("--plain", action="store_true", help="uncolored gauge output")
    vfy.set_defaults(func=_cmd_verify)

    bl = sub.add_parser("blast", help="Change Blast Radius gauge + Patch Splitter")
    bl.add_argument("blast_cmd", nargs="?", choices=["status", "split", "latest"])
    bl.add_argument("--session", help="target this exact session id (default: live-session "
                                      "pointer, then newest)")
    bl.add_argument("--preview", action="store_true")   # split
    bl.add_argument("--apply", action="store_true")      # split
    bl.add_argument("--plain", action="store_true", help="uncolored gauge output")
    bl.set_defaults(func=_cmd_blast)

    lp = sub.add_parser("loop", help="Tool Failure Loop gauge + Stop")
    lp.add_argument("loop_cmd", nargs="?", choices=["status", "stop", "latest", "release"])
    lp.add_argument("--session", help="target this exact session id (default: live-session "
                                      "pointer, then newest)")
    lp.add_argument("--preview", action="store_true")   # stop
    lp.add_argument("--apply", action="store_true")      # stop
    lp.add_argument("--plain", action="store_true", help="uncolored gauge output")
    lp.set_defaults(func=_cmd_loop)

    hk = sub.add_parser("hook", help="(internal) handle a Claude Code hook; reads JSON on stdin")
    hk.add_argument("event", help="hook event name, e.g. PostToolUse")
    hk.set_defaults(func=_cmd_hook)

    sl = sub.add_parser("statusline", help="(internal) render the Claude Code status line; reads JSON on stdin")
    sl.set_defaults(func=_cmd_statusline)

    inst_sl = sub.add_parser("install-statusline", help="wire the Mindlas status line into Claude Code settings.json")
    inst_sl.add_argument("--uninstall", action="store_true", help="restore your previous status line")
    inst_sl.set_defaults(func=_cmd_install_statusline)

    inst_hk = sub.add_parser("install-hooks", help="wire the Mindlas hooks into Claude Code settings.json")
    inst_hk.add_argument("--uninstall", action="store_true", help="remove the Mindlas hooks")
    inst_hk.set_defaults(func=_cmd_install_hooks)

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_usage()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
