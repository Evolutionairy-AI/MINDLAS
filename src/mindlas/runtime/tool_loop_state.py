"""Deterministic Tool Failure Loop signals. Robust to the real Mindlas Event shape:
failures are captured as tool_call events whose markers contain "tool_error"
(capture.tool_failure_event); EVERY field access is getattr-safe so a malformed or partial
event never raises. Reads the active stop record from .mindlas/stops/active_stop.json. No source
mutation, no commands, no LLM."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from . import paths
from ..vitals.capture import canonicalize_error, classify_error_category
from ..vitals.events import EventKind, Marker

_EVIDENCE_MARKERS = frozenset({"verify_gate", "patch_splitter", "context_repair"})
_CMD_PATH = re.compile(r"(?:[a-zA-Z]:)?[\\/](?:[\w.\-]+[\\/])*[\w.\-]+")
_CMD_NUM = re.compile(r"\d+")
_CMD_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class ToolAttempt:
    session_id: str
    turn: int
    ts: str
    tool_name: str
    command: str
    target: str
    status: str              # success | fail | timeout | unknown
    category: str            # timeout | permission | not_found | parse | empty | unavailable | command_fail | unknown
    exit_code: int | None
    error_text: str
    signature: str
    command_fingerprint: str
    is_failure: bool


@dataclass(frozen=True)
class ToolFailureLoopSignals:
    session_id: str
    window_turns: int
    tool_call_count: int
    failed_tool_call_count: int
    failure_rate_pct: int
    consecutive_failure_count: int
    same_signature_failure_count: int            # whole-window (diagnostic fact)
    same_command_retry_count: int                # whole-window (diagnostic fact)
    same_signature_failure_count_active: int     # since the last evidence boundary
    same_command_retry_count_active: int         # since the last evidence boundary
    retry_without_new_evidence_count: int
    unique_failure_signature_count: int
    timeout_count: int
    permission_denied_count: int
    not_found_count: int
    parse_error_count: int
    empty_result_count: int
    unavailable_count: int
    turns_since_last_success: int
    success_since_last_failure: bool
    active_tool_name: str
    active_command_fingerprint: str
    active_failure_signature: str
    active_failure_category: str
    last_failure_turn: int | None
    stop_active: bool
    last_stop_turn: int | None
    current_loop_hash: str


def _sha8(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:8]


def _canonical_command(command: str) -> str:
    """Canonical command form: lowercase, absolute paths -> <path>, numbers -> <n>, ws collapsed."""
    s = _CMD_PATH.sub("<path>", (command or "").lower())
    s = _CMD_NUM.sub("<n>", s)
    return _CMD_WS.sub(" ", s).strip()


def _int(v, default=0) -> int:
    return v if isinstance(v, int) else default


def _markers(e) -> tuple:
    return tuple(getattr(e, "markers", ()) or ())


def _is_tool_call(e) -> bool:
    return getattr(e, "kind", None) == EventKind.TOOL_CALL


def _is_failure(e) -> bool:
    return Marker.TOOL_ERROR in _markers(e)


def _is_evidence(e) -> bool:
    """An event between two failures that produces NEW evidence — a user message, a
    successful tool call (incl. edit/write), or an explicit verify/split/repair marker."""
    kind = getattr(e, "kind", None)
    markers = _markers(e)
    if kind == EventKind.USER_PROMPT:
        return True
    if _EVIDENCE_MARKERS & set(markers):
        return True
    if kind == EventKind.TOOL_CALL and Marker.TOOL_ERROR not in markers:
        return True
    return False


def _attempt_of(e) -> ToolAttempt:
    markers = _markers(e)
    tool_name = str(getattr(e, "tool", None) or getattr(e, "cls", None) or "unknown")
    command = str(getattr(e, "target", None) or "")           # target holds command text
    exit_code = getattr(e, "exit_code", None)
    if not isinstance(exit_code, int):
        exit_code = None
    error_text = str(getattr(e, "error_text", None) or "")
    is_failure = Marker.TOOL_ERROR in markers
    category = next((m for m in markers if m != Marker.TOOL_ERROR), "") if is_failure else ""
    if is_failure and not category:
        category = classify_error_category(error_text, exit_code=exit_code)
    if is_failure:
        status = "timeout" if category == "timeout" else "fail"
    else:
        status = "success"
    signature = (f"{category or 'unknown'}:{tool_name}:{_sha8(canonicalize_error(error_text))}"
                 if is_failure else "")
    command_fingerprint = f"{tool_name}:{_sha8(_canonical_command(command))}"
    return ToolAttempt(
        session_id=str(getattr(e, "session_id", "") or ""), turn=_int(getattr(e, "turn", 0)),
        ts=str(getattr(e, "ts", "") or ""), tool_name=tool_name, command=command, target=command,
        status=status, category=(category or "unknown"), exit_code=exit_code,
        error_text=error_text, signature=signature, command_fingerprint=command_fingerprint,
        is_failure=is_failure)


def _read_stop_state(sid, project_root) -> tuple[bool, int | None]:
    try:
        p = paths.active_stop_path(sid, project_root)
        if not p.exists():
            return False, None
        data = json.loads(p.read_text(encoding="utf-8"))
        # Respect an explicit active=false (a deactivated stop is NOT active, even though the file
        # persists on disk). Missing "active" defaults to True (back-compat with older files).
        if not bool(data.get("active", True)):
            return False, None
        turn = data.get("stop_turn")
        return True, (turn if isinstance(turn, int) else None)
    except (OSError, ValueError, TypeError):
        return False, None


def build_tool_failure_loop_signals(events, *, session_id=None, now_turn=None,
                                    project_root=None, window_turns=20) -> ToolFailureLoopSignals:
    events = list(events or [])
    sid = (session_id or (getattr(events[0], "session_id", None) if events else None) or "session")
    if now_turn is None:
        now_turn = max((_int(getattr(e, "turn", 0)) for e in events), default=0)
    lo = now_turn - window_turns + 1
    window = [e for e in events if _int(getattr(e, "turn", 0)) >= lo]
    stop_active, last_stop_turn = _read_stop_state(sid, project_root)

    attempts = [_attempt_of(e) for e in window if _is_tool_call(e)]
    failures = [a for a in attempts if a.is_failure]
    tool_call_count = len(attempts)
    failed = len(failures)
    failure_rate_pct = round(100 * failed / tool_call_count) if tool_call_count else 0

    # `consecutive` = trailing failed tool calls since the last NEW-EVIDENCE boundary
    # (user prompt / successful tool call / verify-split-repair marker), scanning the window in
    # reverse. Evidence — not merely a successful tool call — breaks the run, so failures interrupted
    # by the user's new guidance are NOT one blind loop.
    consecutive = 0
    for e in reversed(window):
        if _is_tool_call(e) and _is_failure(e):
            consecutive += 1
        elif _is_evidence(e):
            break

    last_failure = failures[-1] if failures else None
    active_signature = last_failure.signature if last_failure else ""
    active_cmd_fp = last_failure.command_fingerprint if last_failure else ""
    active_tool = last_failure.tool_name if last_failure else ""
    active_category = last_failure.category if last_failure else "unknown"
    last_failure_turn = last_failure.turn if last_failure else None

    same_signature = (sum(1 for a in failures if a.signature == active_signature)
                      if active_signature else 0)
    same_command = (sum(1 for a in failures if a.command_fingerprint == active_cmd_fp)
                    if active_cmd_fp else 0)
    unique_sigs = len({a.signature for a in failures})

    # The ACTIVE evidence segment = events after the last new-evidence boundary. Same-
    # signature / same-command repetition WITHIN it is the blind-retry signal used for scoring AND
    # trigger; the whole-window `same_signature`/`same_command` above stay as diagnostic facts.
    last_evidence_idx = -1
    for idx, e in enumerate(window):
        if _is_evidence(e):
            last_evidence_idx = idx
    active_failures = [_attempt_of(e) for e in window[last_evidence_idx + 1:]
                       if _is_tool_call(e) and _is_failure(e)]
    same_signature_active = (sum(1 for a in active_failures if a.signature == active_signature)
                             if active_signature else 0)
    same_command_active = (sum(1 for a in active_failures if a.command_fingerprint == active_cmd_fp)
                           if active_cmd_fp else 0)

    retry_without_new_evidence = 0
    last_fail_idx: dict[str, int] = {}
    for idx, e in enumerate(window):
        if _is_tool_call(e) and _is_failure(e):
            sig = _attempt_of(e).signature
            if sig in last_fail_idx and not any(
                    _is_evidence(ev) for ev in window[last_fail_idx[sig] + 1:idx]):
                retry_without_new_evidence += 1
            last_fail_idx[sig] = idx

    def _cat(name: str) -> int:
        return sum(1 for a in failures if a.category == name)

    successes = [a for a in attempts if not a.is_failure]
    last_success_turn = successes[-1].turn if successes else None
    turns_since_last_success = (max(0, now_turn - last_success_turn)
                                if last_success_turn is not None else now_turn)
    if last_failure is None:
        success_since_last_failure = True
    else:
        success_since_last_failure = bool(last_success_turn is not None
                                          and last_success_turn > last_failure.turn)

    current_loop_hash = (_sha8(active_signature + "|" + active_cmd_fp)
                         if (active_signature or active_cmd_fp)
                         else _sha8("|".join(sorted(a.signature for a in failures))))

    return ToolFailureLoopSignals(
        session_id=sid, window_turns=window_turns,
        tool_call_count=tool_call_count, failed_tool_call_count=failed,
        failure_rate_pct=failure_rate_pct, consecutive_failure_count=consecutive,
        same_signature_failure_count=same_signature, same_command_retry_count=same_command,
        same_signature_failure_count_active=same_signature_active,
        same_command_retry_count_active=same_command_active,
        retry_without_new_evidence_count=retry_without_new_evidence,
        unique_failure_signature_count=unique_sigs,
        timeout_count=_cat("timeout"), permission_denied_count=_cat("permission"),
        not_found_count=_cat("not_found"), parse_error_count=_cat("parse"),
        empty_result_count=_cat("empty"), unavailable_count=_cat("unavailable"),
        turns_since_last_success=turns_since_last_success,
        success_since_last_failure=success_since_last_failure,
        active_tool_name=active_tool, active_command_fingerprint=active_cmd_fp,
        active_failure_signature=active_signature, active_failure_category=active_category,
        last_failure_turn=last_failure_turn, stop_active=stop_active,
        last_stop_turn=last_stop_turn, current_loop_hash=current_loop_hash)
