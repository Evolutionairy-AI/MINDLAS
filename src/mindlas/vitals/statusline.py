"""Claude Code status-line rendering. Best-effort; ALWAYS exits 0 so a
failure degrades to header-only and never blanks the user's whole status line."""
from __future__ import annotations

import json
import re
import sys

from .config import ledger_path
from .ledger import Ledger
from .render import render_statusline


def _version(mid: str) -> str:
    """A clean version ('4.8' or '4') from a model id — never an 8-digit date suffix.
    The 1-2 digit limit + (?<!\\d)/(?!\\d) boundaries reject date runs like 20250514."""
    m = re.search(r"(?<!\d)(\d{1,2})[-.](\d{1,2})(?!\d)", mid)
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    m = re.search(r"(?<!\d)(\d{1,2})(?!\d)", mid)
    return m.group(1) if m else ""


def model_label(payload: dict, *, with_context: bool = True) -> str:
    model = payload.get("model") or {}
    name = model.get("display_name") or ""
    ver = _version(model.get("id") or "")
    # don't append a version when display_name already carries one (avoid "Opus 4.5 4.5")
    label = f"{name} {ver}" if (name and ver and not any(c.isdigit() for c in name)) else name
    if with_context:
        cw = (payload.get("context_window") or {}).get("context_window_size") or 0
        if cw and int(cw) >= 1_000_000:
            label = f"{label} (1M context)" if label else "(1M context)"
    return label


def _project(payload: dict) -> str:
    ws = payload.get("workspace") or {}
    path = ws.get("project_dir") or ws.get("current_dir") or payload.get("cwd") or ""
    if not path:
        return ""
    return path.replace("\\", "/").rstrip("/").split("/")[-1]


def _pct(payload: dict):
    p = (payload.get("context_window") or {}).get("used_percentage")
    return float(p) if isinstance(p, (int, float)) else None


def _header(payload: dict) -> dict:
    return {"model_label": model_label(payload), "project": _project(payload), "pct": _pct(payload)}


def _verify_reading(events, sid, now):
    """Best-effort live VERIFY reading for the status line; None on any error so the line
    never breaks (mirrors verify_segment's fail-silent contract)."""
    try:
        from ..runtime.verification_state import build_verification_debt_signals
        from ..features.verification_debt import VerificationDebtScorer
        sig = build_verification_debt_signals(events, session_id=sid or None, now_turn=now)
        return VerificationDebtScorer().read(sig)
    except Exception:
        return None


def _blast_reading(events, sid):
    """Best-effort live BLAST reading for the status line; None on any error so the line never
    breaks (mirrors _verify_reading's fail-silent contract)."""
    try:
        from ..runtime.blast_state import build_change_blast_signals
        from ..features.blast_radius import ChangeBlastRadiusScorer
        sig = build_change_blast_signals(events, session_id=sid or None)
        return ChangeBlastRadiusScorer().read(sig)
    except Exception:
        return None


def _loop_reading(events, sid):
    """Best-effort live LOOP reading for the status line; None on any error so the line never
    breaks (mirrors _blast_reading's fail-silent contract)."""
    try:
        from ..runtime.tool_loop_state import build_tool_failure_loop_signals
        from ..features.tool_failure_loop import ToolFailureLoopScorer
        now = max((e.turn for e in events), default=0)
        sig = build_tool_failure_loop_signals(events, session_id=sid or None, now_turn=now)
        return ToolFailureLoopScorer().read(sig)
    except Exception:
        return None
def _reconcile_measured_after(payload: dict, measured_score: int) -> None:
    """If a `/clear` reseed left an `after_pending` pointer, record THIS reseeded
    session's live MEASURED CTX as the true 'after' of the last repair, re-render the scorecard
    (preferring measured over modeled), and consume the pointer. The status line is the only
    surface that sees the real window %, so the capture has to happen here. Strictly best-effort
    and wrapped — it must NEVER break the status line (which always exits 0)."""
    try:
        import json as _json
        from pathlib import Path
        from ..runtime import paths
        from ..runtime.scorecard import (scorecard_from_corrections, render_scorecard_md,
                                          scorecard_to_json)
        ws = payload.get("workspace") or {}
        root = ws.get("project_dir") or ws.get("current_dir") or payload.get("cwd")
        if not root:
            return
        root = Path(root)                                # thread the root; never mutate os.environ
        sid = payload.get("session_id") or ""
        if not sid:
            return
        ptr = paths.after_pending_path(sid, root)
        if not ptr.exists():
            return
        cpath = paths.corrections_path(root)
        lines = [ln for ln in cpath.read_text(encoding="utf-8").splitlines() if ln.strip()] \
            if cpath.exists() else []
        if not lines:
            ptr.unlink()                                  # nothing to reconcile -> just disarm
            return
        last = _json.loads(lines[-1])
        last["measured_after_ctx"] = int(round(measured_score))
        lines[-1] = _json.dumps(last)
        cpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
        meta = {}
        if paths.scorecard_meta_path(root).exists():
            try:
                meta = _json.loads(paths.scorecard_meta_path(root).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
        sc = scorecard_from_corrections(meta.get("session_id", "(unknown)"),
                                        meta.get("task", "(unknown task)"),
                                        tuple(_json.loads(ln) for ln in lines))
        paths.reports_dir(root).mkdir(parents=True, exist_ok=True)
        paths.scorecard_md_path(root).write_text(render_scorecard_md(sc), encoding="utf-8")
        paths.scorecard_json_path(root).write_text(scorecard_to_json(sc), encoding="utf-8")
        ptr.unlink()                                       # one-shot: measured after captured
    except Exception:
        pass


def _persist_context_pct(sid: str, pct: float) -> None:
    """Cache the latest MEASURED window % so the CLI status surfaces (which never see a live
    payload) reuse it and agree with this line, instead of proxying mass to ~100%. Best-effort:
    a failure here must never break the status line (which always exits 0)."""
    try:
        from .config import context_pct_path
        p = context_pct_path(sid)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"pct": float(pct)}), encoding="utf-8")
    except Exception:
        pass


def _ctx_reliability_line(payload: dict, sid: str) -> str:
    """The Rot-first reliability line. Rot, VERIFY, BLAST, and LOOP are all live,
    each best-effort (None -> that segment falls back to its '--' placeholder). A Rot scorer error
    degrades Rot to '--' too, so it never blanks the whole band down to header-only."""
    from ..runtime.state import build_context_rot_signals
    from ..features.context_rot import ContextRotScorer
    from ..runtime.render import render_ctx_statusline
    events = Ledger(ledger_path(sid)).events() if sid else []
    pct = _pct(payload)                        # measured used_percentage, or None -> proxied
    if pct is not None and sid:
        _persist_context_pct(sid, pct)         # so the CLI status surfaces reuse this measured %
    now = max((e.turn for e in events), default=0)
    try:
        sig = build_context_rot_signals(events, context_pct=pct, now_turn=now)
        reading = ContextRotScorer().read(sig)
    except Exception:
        reading = None                          # bad Rot signal -> '--', not a blanked band
    if reading is not None:
        _reconcile_measured_after(payload, reading.score)   # capture the measured after
    return render_ctx_statusline(reading, _verify_reading(events, sid, now),
                                 _blast_reading(events, sid), _loop_reading(events, sid))


def build_statusline_text(payload: dict) -> str:
    """The status-line string: the model/project header + the CTX-first reliability line."""
    sid = payload.get("session_id") or ""
    header = render_statusline(_header(payload))
    ctx_line = _ctx_reliability_line(payload, sid)
    return f"{header}\n{ctx_line}" if header else ctx_line


def run_statusline() -> int:
    payload: dict = {}
    try:
        loaded = json.load(sys.stdin)
        if isinstance(loaded, dict):
            payload = loaded
    except Exception:           # never let a degenerate stdin escape -> non-zero -> blank line
        payload = {}
    try:
        print(build_statusline_text(payload))
    except Exception:
        try:
            print(render_statusline(_header(payload)))   # header-only fallback
        except Exception:
            pass
    return 0
