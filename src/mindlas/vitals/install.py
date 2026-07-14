"""Reversible, idempotent wiring of Mindlas into Claude Code config.
Status line: edits <claude>/settings.json, backing up any existing entry.
Hooks: merges the Agent-Vitals hooks into <claude>/settings.json (additive; coexists).
Slash command: writes <claude>/commands/mindlas-card.md."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from .config import claude_settings_path, statusline_backup_path


def resolve_base() -> str:
    """How to invoke mindlas from Claude Code's own environment. Prefer the absolute path of
    the running mindlas executable (resolves regardless of PATH — the venv-install case);
    fall back to a bare global name only when we weren't launched as the console script."""
    exe = Path(sys.argv[0])
    if exe.name.lower().startswith("mindlas") and exe.exists():
        return str(exe.resolve()).replace("\\", "/")
    if shutil.which("mindlas"):
        return "mindlas"
    return str(exe.resolve()).replace("\\", "/")


def _quoted(base: str) -> str:
    return f'"{base}"' if " " in base else base


def _is_ours(cmd) -> bool:
    return isinstance(cmd, str) and "statusline" in cmd and "mindlas" in cmd.lower()


# (event, matcher, timeout) — the canonical Mindlas hook set. MUST stay in lock-step with the
# bundled settings.hooks.json (guarded by test_hook_specs_match_bundled_template).
_HOOK_SPECS = (
    ("SessionStart", None, 10),
    ("UserPromptSubmit", None, 10),
    ("PreToolUse", "*", 10),
    ("PostToolUse", "*", 10),
    ("PostToolUseFailure", "*", 10),
    ("Stop", None, 15),
    ("PreCompact", "*", 10),
    ("PostCompact", None, 10),
)


def _cmd_is_ours(cmd) -> bool:
    """A hook entry is Mindlas's iff its command runs `mindlas ... hook ...`. The `hook` check
    excludes our own `mindlas statusline`; the `mindlas` check excludes foreign (e.g. GSD) hooks.
    Detection is intentionally substring-based (matching the sibling `_is_ours`); it assumes no
    foreign hook command embeds the literal `mindlas`."""
    return isinstance(cmd, str) and "mindlas" in cmd.lower() and "hook" in cmd.lower()


def _hook_groups(base: str) -> dict:
    """The Mindlas matcher-groups to merge into settings['hooks'], keyed by event. `base` is the
    already-resolved invocation path; it is quoted here so a path with spaces survives the shell."""
    groups = {}
    for event, matcher, timeout in _HOOK_SPECS:
        entry = {"type": "command", "command": f"{_quoted(base)} hook {event}", "timeout": timeout}
        groups[event] = {"matcher": matcher, "hooks": [entry]} if matcher else {"hooks": [entry]}
    return groups


def _strip_our_hooks(settings: dict) -> None:
    """Remove every Mindlas-owned hook entry in place, entry-by-entry, then prune any group whose
    `hooks` became empty, any event whose group-list became empty, and the `hooks` key if it
    emptied. Foreign entries (e.g. GSD) and hand-merged groups keep all their non-Mindlas entries.
    Uses .get() throughout so a malformed settings blob can never raise."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            if not isinstance(group, dict):
                kept_groups.append(group)            # leave foreign shapes untouched
                continue
            inner = group.get("hooks")
            if not isinstance(inner, list):
                kept_groups.append(group)            # no hooks list -> not ours, keep as-is
                continue
            kept = [e for e in inner
                    if not (isinstance(e, dict) and _cmd_is_ours(e.get("command")))]
            if len(kept) == len(inner):
                kept_groups.append(group)            # removed nothing of ours -> keep untouched
            elif kept:
                group["hooks"] = kept                # removed our entries; others remain
                kept_groups.append(group)
            # else: removed our entries and none remain -> drop the emptied group
        if kept_groups:
            hooks[event] = kept_groups
        else:
            del hooks[event]
    if not hooks:
        settings.pop("hooks", None)


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def install_statusline(uninstall: bool = False) -> str:
    path = claude_settings_path()
    settings: dict = {}
    if path.exists():
        settings = json.loads(path.read_text(encoding="utf-8"))   # raise on corrupt: caller reports
    if not isinstance(settings, dict):
        raise ValueError("settings.json top-level is not a JSON object")

    backup = statusline_backup_path()
    current = settings.get("statusLine")
    current_cmd = current.get("command") if isinstance(current, dict) else current

    if uninstall:
        # Only touch what we own, so uninstall is reversible AND idempotent. Restore the
        # backed-up original if there is one, else remove our entry; then consume the backup
        # so a future cycle can't resurrect a line the user has since abandoned.
        if _is_ours(current_cmd):
            if backup.exists():
                settings["statusLine"] = json.loads(backup.read_text(encoding="utf-8"))
            else:
                settings.pop("statusLine", None)
        backup.unlink(missing_ok=True)
        _write_json(path, settings)
        return "uninstalled"

    if current is not None and not _is_ours(current_cmd):
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(json.dumps(current), encoding="utf-8")
    settings["statusLine"] = {"type": "command",
                              "command": f"{_quoted(resolve_base())} statusline",
                              "padding": 0}
    _write_json(path, settings)
    return "installed"


def install_hooks(uninstall: bool = False) -> str:
    """Idempotently merge (install) or remove (uninstall) the Mindlas hooks in settings.json.
    Strip-then-append: a re-install never duplicates and self-heals a stale path. Additive only —
    no backup needed, since we never overwrite a user value."""
    path = claude_settings_path()
    settings: dict = {}
    if path.exists():
        settings = json.loads(path.read_text(encoding="utf-8"))   # raise on corrupt: caller reports
    if not isinstance(settings, dict):
        raise ValueError("settings.json top-level is not a JSON object")

    _strip_our_hooks(settings)
    if uninstall:
        _write_json(path, settings)
        return "uninstalled"

    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("settings.json 'hooks' is not a JSON object")
    for event, group in _hook_groups(resolve_base()).items():
        bucket = hooks.setdefault(event, [])
        if not isinstance(bucket, list):
            raise ValueError(f"settings.json hooks['{event}'] is not a list")
        bucket.append(group)
    _write_json(path, settings)
    return "installed"


