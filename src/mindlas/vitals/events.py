"""The normalized event — the shared contract every component reads."""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum

class EventKind(StrEnum):
    """Canonical Event.kind values — the ledger's on-disk vocabulary. A StrEnum so members ARE
    strings: they serialize to their value in the JSONL ledger and compare equal to plain strings,
    keeping the wire format and every `e.kind == ...` call site unchanged. Values are pinned
    explicitly (not auto()) because they are a wire contract — a member rename must never change the
    serialized string. (Event.cls for tool_call — read/edit/write/test_run/search/command/other —
    and the Event.markers vocabulary are deliberately bare strings, not enums.)"""
    SESSION_START = "session_start"
    USER_PROMPT = "user_prompt"
    TOOL_CALL = "tool_call"
    ASSISTANT_MSG = "assistant_msg"
    COMPACT_BOUNDARY = "compact_boundary"
    CONTEXT_REPAIR_END = "context_repair_end"


class Marker(StrEnum):
    """Canonical Event.markers tags — the lexical / discriminator flags stored in Event.markers.
    A StrEnum for the same wire-safety reasons as EventKind (serializes to its value, compares equal
    to plain strings). NOTE: the tool_error error-CATEGORY vocabulary (the 2nd marker on a tool_error
    event — timeout/permission/not_found/parse/empty/unavailable/command_fail/unknown) is deliberately
    NOT modeled here: those strings collide with unrelated verifier-status and other vocabularies, so
    they need a separate, surgical ErrorCategory pass rather than a blanket consolidation."""
    CORRECTION = "correction"
    DONE_CLAIM = "done_claim"
    TOOL_ERROR = "tool_error"


# For kind == EventKind.USER_PROMPT, `target` holds the verbatim prompt text.


@dataclass(frozen=True)
class Event:
    session_id: str
    turn: int
    ts: str
    kind: str
    tool: str | None = None
    target: str | None = None
    cls: str | None = None
    markers: tuple[str, ...] = ()
    args_key: str | None = None
    lines_added: int | None = None
    lines_deleted: int | None = None
    output_chars: int | None = None
    exit_code: int | None = None       # nonzero exit / timeout signal of a FAILED tool call
    error_text: str | None = None      # canonicalized failure message (tool_error events)


def event_to_json(e: Event) -> str:
    d = {
        "session_id": e.session_id, "turn": e.turn, "ts": e.ts, "kind": e.kind,
        "tool": e.tool, "target": e.target, "cls": e.cls,
        "markers": list(e.markers), "args_key": e.args_key,
        "lines_added": e.lines_added, "lines_deleted": e.lines_deleted,
        "output_chars": e.output_chars,
    }
    # Emit the failure fields ONLY when set, so ledger lines written before these fields existed
    # keep their exact key set byte-for-byte and any existing exact-JSON expectation is preserved.
    if e.exit_code is not None:
        d["exit_code"] = e.exit_code
    if e.error_text is not None:
        d["error_text"] = e.error_text
    return json.dumps(d, ensure_ascii=False)


def event_from_json(line: str) -> Event:
    d = json.loads(line)
    return Event(
        session_id=d["session_id"], turn=int(d["turn"]), ts=d.get("ts", ""),
        kind=d["kind"], tool=d.get("tool"), target=d.get("target"),
        cls=d.get("cls"), markers=tuple(d.get("markers") or ()),
        args_key=d.get("args_key"),
        lines_added=d.get("lines_added"), lines_deleted=d.get("lines_deleted"),
        output_chars=d.get("output_chars"),
        exit_code=d.get("exit_code"), error_text=d.get("error_text"),
    )
