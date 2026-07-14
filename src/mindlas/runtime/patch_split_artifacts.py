"""Write the Patch Splitter queue. Source-read-only: tracked changes are captured as
read-only `git diff` text (git_helpers.file_diff), untracked files are COPIED (never moved) under
`untracked/`. Everything lands under `.mindlas/splits/` — which git_helpers excludes from the diff
hash — so writing the queue can never move BLAST's or VERIFY's freshness hash."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import git_helpers, paths
from .split_types import SplitPlan


def _summary_md(plan: SplitPlan) -> str:
    L = ["# Mindlas Patch Split", "",
         f"Original BLAST: {plan.before_blast}",
         f"Planned after BLAST: {plan.planned_after_blast}",
         f"Bundles: {len(plan.bundles)}",
         # "Coverage validation" (union-complete + non-overlapping), NOT bare
         # "Validation" — the latter reads as "tests passed" to Verify-Gate-primed readers.
         f"Coverage validation: {plan.validation_status}",
         "",
         "Patch Splitter did not modify source files. The after-score is a planned blast score "
         "computed over the validated split queue.", ""]
    for i, b in enumerate(plan.bundles, start=1):
        L += [f"## Bundle {i}: {b.name}", f"Reason: {b.reason}", "Files:"]
        L += [f"- {f}" for f in b.files]
        if b.warnings:
            L += [f"- warning: {w}" for w in b.warnings]
        L.append("")
    L += ["Suggested order:"]
    L += [f"{i}. Apply {b.bundle_id}" for i, b in enumerate(plan.bundles, start=1)]
    L.append(f"{len(plan.bundles) + 1}. Run Verify Gate")
    return "\n".join(L) + "\n"


def write_split_artifacts(plan: SplitPlan, *, project_root: Path) -> tuple[str, dict]:
    root = Path(project_root)
    run = paths.split_run_dir(plan.session_id, plan.split_id, root)
    run.mkdir(parents=True, exist_ok=True)
    untracked = set(git_helpers.untracked(root))

    bundle_entries: list[dict] = []
    for b in plan.bundles:
        bdir = run / b.bundle_id
        bdir.mkdir(parents=True, exist_ok=True)
        tracked_files: list[str] = []
        untracked_files: list[str] = []
        patch_parts: list[str] = []
        for path in b.files:
            if path in untracked:
                untracked_files.append(path)
                dest = bdir / "untracked" / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                # Copy content; never move/modify source. A copy failure RAISES (not silently
                # swallowed) — it happens before split_manifest.json is finalized, so a partial
                # queue can never masquerade as a `pass` / complete-coverage manifest.
                shutil.copyfile(root / path, dest)
            else:
                tracked_files.append(path)
                patch_parts.append(git_helpers.file_diff(path, root))
        (bdir / "tracked.patch").write_text("".join(patch_parts), encoding="utf-8")
        (bdir / "bundle_manifest.json").write_text(json.dumps({
            "bundle_id": b.bundle_id, "name": b.name, "reason": b.reason,
            "files": list(b.files), "tracked_files": tracked_files,
            "untracked_files": untracked_files, "file_count": b.file_count,
            "changed_lines": b.changed_lines, "kinds": list(b.kinds),
            "concerns": list(b.concerns), "coverage": b.coverage, "risk_score": b.risk_score,
            "has_tests": b.has_tests, "has_production": b.has_production,
            "warnings": list(b.warnings),
            "restore_note": "Copy files from untracked/ into the repo root when materializing "
                            "this bundle.",
        }, indent=2), encoding="utf-8")
        bundle_entries.append({"bundle_id": b.bundle_id, "name": b.name, "reason": b.reason,
                               "files": list(b.files), "risk_score": b.risk_score,
                               "warnings": list(b.warnings)})

    manifest = {
        "type": "patch_splitter", "split_id": plan.split_id, "session_id": plan.session_id,
        "created_at": plan.created_at, "before_blast": plan.before_blast,
        "planned_after_blast": plan.planned_after_blast,
        "original_diff_hash": plan.original_diff_hash,
        "validation_status": plan.validation_status,
        "validation_messages": list(plan.validation_messages),
        "bundle_count": len(plan.bundles), "bundles": bundle_entries,
    }
    text = json.dumps(manifest, indent=2)
    manifest_path = run / "split_manifest.json"
    manifest_path.write_text(text, encoding="utf-8")
    (run / "split_summary.md").write_text(_summary_md(plan), encoding="utf-8")
    latest = paths.latest_split_manifest_path(plan.session_id, root)
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(text, encoding="utf-8")
    return str(manifest_path), manifest
