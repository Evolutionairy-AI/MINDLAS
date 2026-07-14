"""Write the Stop boundary. Source-read-only + environment-read-only: writes ONLY under
.mindlas/stops/ (stop_manifest.json, stop_card.md, latest_stop.json, active_stop.json). Runs no
commands, mutates no source, calls no LLM. .mindlas/ is excluded from the git diff hash, so
writing the stop boundary never perturbs BLAST/VERIFY freshness."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import paths

_HONESTY = ("Stop did not repair the failed tool. It interrupted a repeated failure loop and "
            "recorded a controlled stop boundary.")

DEFAULT_NEXT_ACTIONS = (
    "Stop retrying the same tool command.",
    "Inspect the failure signature and change the plan before resuming.",
    "If code changed, run Verify Gate after the plan changes.",
)


@dataclass(frozen=True)
class StopArtifactPayload:
    stop_id: str
    session_id: str
    created_at: str
    stop_turn: int
    before: int
    controlled_after_loop: int
    status: str
    failure_signature: str
    active_tool_name: str
    active_command_fingerprint: str
    active_failure_category: str
    consecutive_failure_count: int
    same_signature_failure_count: int
    same_command_retry_count: int
    retry_without_new_evidence_count: int
    recommended_next_actions: tuple[str, ...]


def _manifest(p: StopArtifactPayload) -> dict:
    return {
        "type": "loop_stop", "stop_id": p.stop_id, "session_id": p.session_id,
        "created_at": p.created_at, "stop_turn": p.stop_turn, "before": p.before,
        "controlled_after_loop": p.controlled_after_loop, "status": p.status, "active": True,
        "failure_signature": p.failure_signature, "active_tool_name": p.active_tool_name,
        "active_command_fingerprint": p.active_command_fingerprint,
        "active_failure_category": p.active_failure_category,
        "consecutive_failure_count": p.consecutive_failure_count,
        "same_signature_failure_count": p.same_signature_failure_count,
        "same_command_retry_count": p.same_command_retry_count,
        "retry_without_new_evidence_count": p.retry_without_new_evidence_count,
        "recommended_next_actions": list(p.recommended_next_actions),
        "honesty": _HONESTY,
    }


def _stop_card_md(p: StopArtifactPayload) -> str:
    L = [
        "# Mindlas Stop Card", "",
        f"Status: {p.status}",
        f"LOOP: {p.before} -> {p.controlled_after_loop} controlled",
        f"Failure signature: {p.failure_signature}",
        f"Tool: {p.active_tool_name}",
        f"Category: {p.active_failure_category}", "",
        "## Why Mindlas stopped",
        "The same command or failure signature repeated without new evidence.", "",
        "## What not to do next",
        "- Do not retry the same command unchanged.",
        "- Do not continue editing around an unresolved tool failure.",
        "- Do not mark the task complete.", "",
        "## Safe next actions",
        "1. Inspect the failure signature.",
        "2. Change the plan, command, environment assumption, or target file.",
        "3. If code changed, run Verify Gate after the plan changes.",
        "4. Resume only after the reason for the loop has changed.", "",
        _HONESTY, "",
    ]
    return "\n".join(L)


def write_stop_artifacts(payload: StopArtifactPayload, *,
                         project_root: Path) -> tuple[str, str, dict]:
    """Write stop_manifest.json + stop_card.md under .mindlas/stops/<stop_id>/, plus the
    latest_stop.json and active_stop.json pointers at the stops root. Return
    (manifest_path, active_stop_path, manifest_dict)."""
    root = Path(project_root)
    run = paths.stop_run_dir(payload.session_id, payload.stop_id, root)
    run.mkdir(parents=True, exist_ok=True)
    manifest = _manifest(payload)
    text = json.dumps(manifest, indent=2)
    manifest_path = run / "stop_manifest.json"
    manifest_path.write_text(text, encoding="utf-8")
    (run / "stop_card.md").write_text(_stop_card_md(payload), encoding="utf-8")
    latest = paths.latest_stop_path(payload.session_id, root)
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(text, encoding="utf-8")
    active = paths.active_stop_path(payload.session_id, root)
    active.write_text(text, encoding="utf-8")
    return str(manifest_path), str(active), manifest
