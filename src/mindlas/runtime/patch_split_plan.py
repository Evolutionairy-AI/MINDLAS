"""Deterministic Patch Splitter planning. Pure: turns ChangeBlastSignals + FileChange
records into a validated SplitPlan of coherent bundles, computing a PLANNED after-BLAST score. No
IO, no source mutation, no LLM. The per-bundle BLAST score is supplied via an injected `score_fn`
so this runtime module never imports features/. Grouping is a deterministic approximation, not
semantic understanding — related production+test files are paired by unique filename stem."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import PurePosixPath

from .blast_state import FileChange, build_signals_from_changes
from .split_types import SplitBundle

_MAX_SLUG = 40
# priority categories (lower = earlier in replay order; config first, docs last).
_CAT_CONFIG, _CAT_SRC, _CAT_NEUTRAL, _CAT_TESTS, _CAT_DOCS = 1, 2, 3, 4, 5
_CAT_SLUG = {_CAT_CONFIG: "config", _CAT_SRC: "src", _CAT_NEUTRAL: "neutral",
             _CAT_TESTS: "tests", _CAT_DOCS: "docs"}
_CAT_REASON = {
    _CAT_CONFIG: "configuration changes",
    _CAT_SRC: "source changes under {key}",
    _CAT_NEUTRAL: "neutral/data changes under {key}",
    _CAT_TESTS: "unpaired test changes under {key}",
    _CAT_DOCS: "documentation changes under {key}",
}


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return (s[:_MAX_SLUG] or "bundle")


def _stem(path: str) -> str:
    return PurePosixPath(path.replace("\\", "/")).stem


def _test_key(path: str) -> str:
    """Reduced stem of a test file for pairing: strip a `.spec`/`.test` infix, then a `test_`
    prefix or `_test` suffix."""
    st = _stem(path)
    for infix in (".spec", ".test"):
        if st.endswith(infix):
            st = st[: -len(infix)]
    if st.startswith("test_"):
        st = st[len("test_"):]
    elif st.endswith("_test"):
        st = st[: -len("_test")]
    return st


def _group_slug(cat: int, key: str) -> str:
    if cat == _CAT_CONFIG:
        return "config"
    k = key[4:] if key.startswith("src/") else key
    if k in ("", "."):
        k = _CAT_SLUG[cat]
    return _slug(k)


def _assign_groups(file_changes: tuple[FileChange, ...]) -> list[tuple[int, str, list[FileChange]]]:
    """Passes 2-3: bucket files into (priority_category, group_key, files). Config/docs/neutral
    isolate by kind+directory; production groups by directory; a test pairs into its production
    file's directory group when exactly one production file shares its reduced stem, else it lands
    in an unpaired-test bundle."""
    prod = [f for f in file_changes if f.kind == "production"]
    prod_by_stem: dict[str, list[FileChange]] = {}
    for p in prod:
        prod_by_stem.setdefault(_stem(p.path), []).append(p)

    paired_dir: dict[str, str] = {}
    for t in (f for f in file_changes if f.kind == "test"):
        cands = prod_by_stem.get(_test_key(t.path), [])
        if len(cands) == 1:                       # unique deterministic pairing only
            paired_dir[t.path] = cands[0].directory

    groups: dict[tuple[int, str], list[FileChange]] = {}

    def add(cat: int, key: str, fc: FileChange) -> None:
        groups.setdefault((cat, key), []).append(fc)

    for f in file_changes:
        if f.kind == "config":
            add(_CAT_CONFIG, "config", f)
        elif f.kind == "production":
            add(_CAT_SRC, f.directory, f)
        elif f.kind == "test":
            if f.path in paired_dir:
                add(_CAT_SRC, paired_dir[f.path], f)
            else:
                add(_CAT_TESTS, f.directory, f)
        elif f.kind == "docs":
            add(_CAT_DOCS, f.directory, f)
        else:                                     # neutral
            add(_CAT_NEUTRAL, f.directory, f)

    return [(cat, key, files) for (cat, key), files in
            sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1]))]


def _rebalance(files: list[FileChange], *, max_files: int, max_lines: int,
               allow_single_large: bool) -> list[tuple[list[FileChange], list[str]]]:
    """Pass 5: chunk one group's files into (files, warnings) sub-bundles honoring the caps. A
    single file larger than max_lines becomes its own sub-bundle with a warning (there is no
    deterministic further split of one file; allow_single_large is reserved and always kept)."""
    ordered = sorted(files, key=lambda f: f.path)
    subs: list[tuple[list[FileChange], list[str]]] = []
    cur: list[FileChange] = []
    cur_lines = 0
    for f in ordered:
        if f.changed_lines > max_lines:
            if cur:
                subs.append((cur, []))
                cur, cur_lines = [], 0
            subs.append(([f], ["single large file exceeds max_bundle_lines"]))
            continue
        if cur and (len(cur) + 1 > max_files or cur_lines + f.changed_lines > max_lines):
            subs.append((cur, []))
            cur, cur_lines = [], 0
        cur.append(f)
        cur_lines += f.changed_lines
    if cur:
        subs.append((cur, []))
    return subs or [([], [])]


def _coordination_penalty(bundle_count: int) -> int:
    if bundle_count <= 1:
        return 0
    if bundle_count <= 3:
        return 5
    if bundle_count <= 6:
        return 10
    return 15


def _validate(file_changes, bundles) -> tuple[str, list[str]]:
    """Pass 6: every changed file in exactly one bundle. fail on missing/overlap; warning if any
    bundle carries a warning; else pass."""
    all_paths = {f.path for f in file_changes}
    covered = [p for b in bundles for p in b.files]
    counts = Counter(covered)
    missing = sorted(all_paths - set(covered))
    overlap = sorted(p for p, n in counts.items() if n > 1)
    messages: list[str] = []
    if missing:
        messages.append("missing files: " + ", ".join(missing))
    if overlap:
        messages.append("files in multiple bundles: " + ", ".join(overlap))
    if missing or overlap:
        return "fail", messages
    if any(b.warnings for b in bundles):
        for b in bundles:
            messages += [f"{b.bundle_id}: {w}" for w in b.warnings]
        return "warning", messages
    return "pass", messages


def _build_bundles(groups, *, max_files, max_lines, allow_single_large,
                   score_fn, session_id, untracked) -> tuple[SplitBundle, ...]:
    """Rebalance every group, then assign deterministic bundle_ids in the total order
    (priority_category, group_key, first_path)."""
    raw: list[tuple[int, str, list[FileChange], list[str]]] = []
    for cat, key, files in groups:
        for sub_files, warns in _rebalance(list(files), max_files=max_files, max_lines=max_lines,
                                           allow_single_large=allow_single_large):
            if sub_files:
                raw.append((cat, key, sub_files, warns))
    raw.sort(key=lambda r: (r[0], r[1], min(f.path for f in r[2])))

    bundles: list[SplitBundle] = []
    for i, (cat, key, files, warns) in enumerate(raw, start=1):
        files_sorted = sorted(files, key=lambda f: f.path)
        paths_t = tuple(f.path for f in files_sorted)
        kinds = tuple(sorted({f.kind for f in files_sorted}))
        concerns = tuple(sorted({f.concern_key for f in files_sorted}))
        has_tests = any(f.kind == "test" for f in files_sorted)
        has_prod = any(f.kind == "production" for f in files_sorted)
        sig = build_signals_from_changes(tuple(files_sorted), session_id=session_id, diff_hash="")
        warnings = list(warns)
        if has_prod and not has_tests:
            warnings.append("production files without related tests in this bundle")
        all_untracked = bool(files_sorted) and all(f.path in untracked for f in files_sorted)
        bundles.append(SplitBundle(
            bundle_id=f"bundle_{i:02d}_{_group_slug(cat, key)}",
            name=("config" if cat == _CAT_CONFIG else key),
            reason=_CAT_REASON[cat].format(key=key),
            files=paths_t, file_count=len(paths_t),
            changed_lines=sum(f.changed_lines for f in files_sorted),
            kinds=kinds, concerns=concerns,
            coverage="untracked_only" if all_untracked else "complete",
            risk_score=score_fn(sig), has_tests=has_tests, has_production=has_prod,
            warnings=tuple(warnings)))
    return tuple(bundles)


from .split_types import SplitPlan    # noqa: E402  (kept next to its use; SplitBundle imported above)


def _split_id(stamp: str, original_diff_hash: str) -> str:
    suffix = original_diff_hash.split(":")[-1][:4] or "0000"
    return f"{stamp}_{suffix}"


def plan_patch_split(signals, file_changes, *, session_id, score_fn,
                     untracked=(), max_bundle_files=5, max_bundle_lines=250,
                     allow_single_large=True, now=None) -> SplitPlan:
    """Deterministic split plan. `score_fn` is injected so runtime never imports
    features/. `now` is the CLI-injected timestamp for apply, None for preview (stamped 'preview')."""
    before = score_fn(signals)
    stamp = now or "preview"
    split_id = _split_id(stamp, signals.current_diff_hash)

    if not file_changes:                                   # empty-diff guard (never max([]))
        return SplitPlan(split_id=split_id, session_id=session_id, created_at=stamp,
                         before_blast=before, planned_after_blast=before,
                         original_diff_hash=signals.current_diff_hash, bundles=(),
                         validation_status="pass",
                         validation_messages=("empty diff; nothing to split",))

    groups = _assign_groups(tuple(file_changes))
    bundles = _build_bundles(groups, max_files=max_bundle_files, max_lines=max_bundle_lines,
                             allow_single_large=allow_single_large, score_fn=score_fn,
                             session_id=session_id, untracked=frozenset(untracked))
    status, messages = _validate(tuple(file_changes), bundles)
    penalty = _coordination_penalty(len(bundles))
    planned_after = max(0, min(before, max((b.risk_score for b in bundles), default=0) + penalty))
    return SplitPlan(split_id=split_id, session_id=session_id, created_at=stamp,
                     before_blast=before, planned_after_blast=planned_after,
                     original_diff_hash=signals.current_diff_hash, bundles=bundles,
                     validation_status=status, validation_messages=tuple(messages))
