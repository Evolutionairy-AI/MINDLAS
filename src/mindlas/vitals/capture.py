"""Translate a Claude Code hook payload into a normalized Event.

Defensive: reads fields with .get() and tolerates schema variation across
Claude Code versions — capture must never raise into the hook path.
"""
from __future__ import annotations

import hashlib
import re

from .config import test_patterns
from .events import Event, EventKind, Marker

_READ = frozenset({"Read", "NotebookRead"})
_EDIT = frozenset({"Edit", "MultiEdit", "NotebookEdit"})
_WRITE = frozenset({"Write"})
_SEARCH = frozenset({"Grep", "Glob"})
_COMMANDISH = frozenset({"Bash", "PowerShell", "BashOutput"})

_CORRECTION = re.compile(r"\b(no|nope|again|i said|don'?t|stop|undo|revert|that'?s wrong|not what)\b", re.I)
_DONE = re.compile(r"\b(done|works|working|fixed|that should do it|all set|complete|good to go)\b", re.I)


def _command_text(tool_input: dict) -> str:
    return str(tool_input.get("command") or tool_input.get("cmd") or "")


def _target_of(tool: str, tool_input: dict) -> str | None:
    if tool in _COMMANDISH:
        return _command_text(tool_input) or None
    for key in ("file_path", "path", "notebook_path", "pattern", "url"):
        if tool_input.get(key):
            return str(tool_input[key])
    return None


def classify_tool(tool: str, tool_input: dict) -> str:
    if tool in _READ:
        return "read"
    if tool in _EDIT:
        return "edit"
    if tool in _WRITE:
        return "write"
    if tool in _SEARCH:
        return "search"
    if tool in _COMMANDISH:
        cmd = _command_text(tool_input).lower()
        return "test_run" if any(p in cmd for p in test_patterns()) else "command"
    return "other"


def _args_payload(tool: str, tool_input: dict) -> str:
    """The salient argument content that distinguishes two calls to the same target.

    For edits/writes this is the new content, so re-applying the *same* edit repeats
    the key while a *different* edit to the same file gets a different key — this is
    what lets the Compliance signal detect oscillation/thrash. (MultiEdit/NotebookEdit
    expose no top-level new_string and fall back to tool:target — acceptable for v1.)
    """
    if tool in _EDIT:
        return str(tool_input.get("new_string") or "")
    if tool in _WRITE:
        return str(tool_input.get("content") or "")
    return ""


def make_args_key(tool: str, tool_input: dict) -> str:
    target = _target_of(tool, tool_input) or ""
    payload = _args_payload(tool, tool_input)
    if payload:
        digest = hashlib.blake2s(payload.encode("utf-8"), digest_size=4).hexdigest()
        return f"{tool}:{target}:{digest}"
    return f"{tool}:{target}"


def detect_markers(kind: str, text: str | None) -> tuple[str, ...]:
    markers: list[str] = []
    if kind == EventKind.USER_PROMPT and _CORRECTION.search(text or ""):
        markers.append(Marker.CORRECTION)
    if kind == EventKind.ASSISTANT_MSG and _DONE.search(text or ""):
        markers.append(Marker.DONE_CLAIM)
    return tuple(markers)


def _line_deltas(cls: str | None, tool_input: dict | None) -> tuple[int | None, int | None]:
    """Line counts for edit/write tools, from the PostToolUse tool_input. None for
    non-editing tools. Conservative: counts newlines in the changed text."""
    if not tool_input or cls not in ("edit", "write"):
        return None, None
    def _n(s: str) -> int:
        return (s.count("\n") + (1 if s and not s.endswith("\n") else 0)) if s else 0
    if cls == "write":
        return _n(str(tool_input.get("content") or "")), 0
    # edit (Edit/MultiEdit): sum old/new over one or many edits
    edits = tool_input.get("edits") or [tool_input]
    added = sum(_n(str(e.get("new_string") or "")) for e in edits)
    deleted = sum(_n(str(e.get("old_string") or "")) for e in edits)
    return added, deleted


def _output_chars(tool_response) -> int | None:
    """Character size of a PostToolUse tool_response, for the Context Rot mass/large-output
    signals. None when no response is provided (keeps old call sites unchanged)."""
    if tool_response is None:
        return None
    if isinstance(tool_response, str):
        return len(tool_response)
    try:
        import json
        return len(json.dumps(tool_response, ensure_ascii=False))
    except (TypeError, ValueError):
        return len(str(tool_response))


def tool_event(session_id: str, turn: int, ts: str, tool: str, tool_input: dict,
               tool_response=None) -> Event:
    cls = classify_tool(tool, tool_input)
    la, ld = _line_deltas(cls, tool_input)
    return Event(
        session_id=session_id, turn=turn, ts=ts, kind=EventKind.TOOL_CALL,
        tool=tool, target=_target_of(tool, tool_input),
        cls=cls, markers=(),
        args_key=make_args_key(tool, tool_input),
        lines_added=la, lines_deleted=ld,
        output_chars=_output_chars(tool_response),
    )


def prompt_event(session_id: str, turn: int, ts: str, prompt: str) -> Event:
    return Event(
        session_id=session_id, turn=turn, ts=ts, kind=EventKind.USER_PROMPT,
        tool=None, target=prompt, cls=None,
        markers=detect_markers(EventKind.USER_PROMPT, prompt), args_key=None,
    )


def assistant_event(session_id: str, turn: int, ts: str, text: str | None) -> Event:
    """The agent's turn-closing message. We keep only the Tier-2 `done_claim` marker,
    not the raw text — the marker is the signal, and the message can be large."""
    return Event(
        session_id=session_id, turn=turn, ts=ts, kind=EventKind.ASSISTANT_MSG,
        tool=None, target=None, cls=None,
        markers=detect_markers(EventKind.ASSISTANT_MSG, text), args_key=None,
    )


_ABS_PATH = re.compile(r"(?:[a-zA-Z]:)?[\\/](?:[\w.\-]+[\\/])*[\w.\-]+")
_QUOTED_TMP = re.compile(r"""['"][^'"]*(?:tmp|temp)[^'"]*['"]""", re.I)
_NUM = re.compile(r"\d+")
_WS = re.compile(r"\s+")

_CATEGORY_RULES = (
    ("permission", ("permission denied", "access denied", "eacces", "unauthorized")),
    ("not_found", ("not found", "no such file", "enoent", "command not found")),
    ("parse", ("parse error", "syntax error", "json decode", "invalid json", "invalid syntax",
               "schema")),
    ("empty", ("empty result", "no output", "0 results")),
    ("unavailable", ("rate limit", "service unavailable", "connection refused", "network")),
)


def canonicalize_error(text: str) -> str:
    """Collapse a raw error into a stable, signature-friendly form — lowercased, quoted
    temp filenames -> <file>, absolute paths -> <path>, numbers -> <n>, whitespace collapsed, first
    160 chars. Overfitting to paths/line numbers is exactly what a stable signature must avoid."""
    s = (text or "").lower()
    s = _QUOTED_TMP.sub("<file>", s)
    s = _ABS_PATH.sub("<path>", s)
    s = _NUM.sub("<n>", s)
    s = _WS.sub(" ", s).strip()
    return s[:160]


def classify_error_category(text: str, *, exit_code: int | None = None,
                            timed_out: bool = False) -> str:
    """The canonical failure category. Timeout is a strong hint checked first; then the
    keyword rules in listed order; then a nonzero exit -> command_fail; else unknown."""
    low = (text or "").lower()
    if timed_out or "timed out" in low or "timeout" in low:
        return "timeout"
    for cat, needles in _CATEGORY_RULES:
        if any(n in low for n in needles):
            return cat
    if exit_code is not None and exit_code != 0:
        return "command_fail"
    return "unknown"


_EXIT_CODE_PREFIX = re.compile(r"^\s*exit code (\d+)", re.IGNORECASE)


def _tool_error_fields(tool_error) -> tuple[int | None, bool, str]:
    """Defensively pull (exit_code, timed_out, message) from an UNDOCUMENTED PostToolUseFailure
    error payload. The live payload carries a TOP-LEVEL
    STRING `error` like "Exit code 127\\n/usr/bin/bash: line 1: x: command not found" — parse the
    exit code off that prefix. A dict shape (nested fields) is still probed for forward/backward
    compat. Degrades to (None, False, '') so a missing/foreign shape never raises."""
    if isinstance(tool_error, str):
        m = _EXIT_CODE_PREFIX.match(tool_error)
        exit_code = int(m.group(1)) if m else None
        # timeout is detected downstream by classify_error_category's text rules
        return exit_code, False, tool_error
    te = tool_error if isinstance(tool_error, dict) else {}
    exit_code = te.get("exit_code")
    if not isinstance(exit_code, int):
        exit_code = None
    timed_out = "timeout" in str(te.get("type") or "").lower()
    message = (te.get("message") or te.get("stderr") or te.get("stdout")
               or te.get("error") or "")
    return exit_code, timed_out, str(message)


def tool_failure_event(session_id: str, turn: int, ts: str, tool: str,
                       tool_input: dict, tool_error) -> Event:
    """Build a FAILED tool_call Event from a PostToolUseFailure payload. The
    ("tool_error", <category>) markers are the discriminator tool_loop_state uses to tell a failed
    call from a successful one; error_text is the canonicalized message; exit_code carries the
    nonzero code when present. Never raises on a malformed tool_error/tool_input."""
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    exit_code, timed_out, raw_message = _tool_error_fields(tool_error)
    category = classify_error_category(raw_message, exit_code=exit_code, timed_out=timed_out)
    return Event(
        session_id=session_id, turn=turn, ts=ts, kind=EventKind.TOOL_CALL,
        tool=tool, target=_target_of(tool, tool_input),
        cls=classify_tool(tool, tool_input),
        markers=(Marker.TOOL_ERROR, category),
        args_key=make_args_key(tool, tool_input),
        exit_code=exit_code,
        error_text=canonicalize_error(raw_message),
    )
