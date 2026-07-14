from mindlas.runtime.blast_state import FileChange
from mindlas.runtime.patch_split_plan import (
    _slug, _test_key, _stem, _assign_groups, _rebalance,  # noqa: F401
    _coordination_penalty, _validate,
)


def _fc(path, kind, *, lines=10, status="modified"):
    from pathlib import PurePosixPath
    p = PurePosixPath(path)
    top = p.parts[0] if len(p.parts) > 1 else "."
    return FileChange(path=path, status=status, kind=kind, top_dir=top, directory=str(p.parent),
                      concern_key=path.lower(), additions=lines, deletions=0,
                      changed_lines=lines, centrality="normal")


def test_slug_is_lowercase_ascii_underscored_and_capped():
    assert _slug("Runtime/Scorecard.py") == "runtime_scorecard_py"
    assert _slug("!!!") == "bundle"                                  # empty -> fallback
    assert len(_slug("a" * 80)) == 40


def test_test_key_strips_test_affixes():
    assert _test_key("tests/test_scorecard.py") == "scorecard"
    assert _test_key("tests/scorecard_test.py") == "scorecard"
    assert _test_key("tests/api.spec.ts") == "api"


def test_assign_groups_pairs_test_with_production_and_isolates_config():
    files = (_fc("src/pkg/scorecard.py", "production"),
             _fc("tests/test_scorecard.py", "test"),
             _fc("pyproject.toml", "config"),
             _fc("docs/guide.md", "docs"),
             _fc("tests/test_orphan.py", "test"))       # no production match -> its own test group
    groups = {(cat, key): tuple(f.path for f in files_) for cat, key, files_ in _assign_groups(files)}
    # config isolated
    assert (1, "config") in groups and groups[(1, "config")] == ("pyproject.toml",)
    # scorecard test rides with its production directory (category 2 = src)
    src_group = [v for (cat, key), v in groups.items() if cat == 2]
    assert any("src/pkg/scorecard.py" in v and "tests/test_scorecard.py" in v for v in src_group)
    # orphan test is a test-only bundle (category 4)
    assert any(cat == 4 and "tests/test_orphan.py" in v for (cat, key), v in groups.items())
    # docs isolated (category 5)
    assert any(cat == 5 and "docs/guide.md" in v for (cat, key), v in groups.items())


def test_assign_groups_ambiguous_test_stays_unpaired():
    # two production files share the stem -> pairing is not unique -> test goes to a test bundle.
    files = (_fc("src/a/widget.py", "production"), _fc("src/b/widget.py", "production"),
             _fc("tests/test_widget.py", "test"))
    groups = _assign_groups(files)
    assert any(cat == 4 and "tests/test_widget.py" in tuple(f.path for f in fs)
               for cat, key, fs in groups)


def test_rebalance_splits_over_large_group_and_flags_single_large_file():
    small = [_fc(f"src/pkg/m{i}.py", "production", lines=10) for i in range(7)]   # 7 > max_files 5
    subs = _rebalance(small, max_files=5, max_lines=250, allow_single_large=True)
    assert len(subs) == 2 and all(len(files) <= 5 for files, _w in subs)
    big = [_fc("src/pkg/huge.py", "production", lines=900)]
    subs2 = _rebalance(big, max_files=5, max_lines=250, allow_single_large=True)
    assert len(subs2) == 1 and subs2[0][1] == ["single large file exceeds max_bundle_lines"]


def test_coordination_penalty_tiers():
    assert [_coordination_penalty(n) for n in (1, 2, 3, 4, 6, 7)] == [0, 5, 5, 10, 10, 15]


def test_validate_pass_warning_fail():
    from mindlas.runtime.split_types import SplitBundle
    def b(bid, files, warnings=()):
        return SplitBundle(bundle_id=bid, name=bid, reason="r", files=tuple(files),
                           file_count=len(files), changed_lines=1, kinds=("production",),
                           concerns=("x",), coverage="complete", risk_score=1,
                           has_tests=False, has_production=True, warnings=tuple(warnings))
    fcs = (_fc("a.py", "production"), _fc("b.py", "production"))
    ok = _validate(fcs, (b("b1", ["a.py"]), b("b2", ["b.py"])))
    assert ok[0] == "pass"
    warn = _validate(fcs, (b("b1", ["a.py"]), b("b2", ["b.py"], warnings=["w"])))
    assert warn[0] == "warning"
    missing = _validate(fcs, (b("b1", ["a.py"]),))
    assert missing[0] == "fail"
    overlap = _validate(fcs, (b("b1", ["a.py", "b.py"]), b("b2", ["b.py"])))
    assert overlap[0] == "fail"


from mindlas.runtime.blast_state import build_signals_from_changes  # noqa: E402
from mindlas.runtime.patch_split_plan import plan_patch_split  # noqa: E402
from mindlas.features.blast_radius import score_change_blast_radius  # noqa: E402


def _plan(files, *, now="20260630T101500", untracked=()):
    changes = tuple(files)
    sig = build_signals_from_changes(changes, session_id="s", diff_hash="sha256:ab12cd34")
    return plan_patch_split(sig, changes, session_id="s", score_fn=score_change_blast_radius,
                            untracked=untracked, now=now)


def test_empty_diff_is_a_noop_plan():
    plan = _plan(())
    assert plan.bundles == () and plan.before_blast == 0 and plan.planned_after_blast == 0
    assert plan.validation_status == "pass"
    assert plan.validation_messages == ("empty diff; nothing to split",)


def test_split_id_is_now_plus_hash_prefix():
    plan = _plan((_fc("src/a.py", "production"),))
    assert plan.split_id == "20260630T101500_ab12"          # now + first 4 hex of the diff hash
    assert plan.created_at == "20260630T101500"
    assert plan.original_diff_hash == "sha256:ab12cd34"


def test_broad_patch_splits_and_lowers_planned_after():
    files = (_fc("pyproject.toml", "config", lines=20),
             _fc("src/pkg_a/alpha.py", "production", lines=120),
             _fc("src/pkg_b/beta.py", "production", lines=120),
             _fc("docs/guide.md", "docs", lines=40),
             _fc("tests/test_alpha.py", "test", lines=30))
    plan = _plan(files)
    assert len(plan.bundles) >= 2
    # every changed file appears in exactly one bundle
    covered = [p for b in plan.bundles for p in b.files]
    assert sorted(covered) == sorted(f.path for f in files) and len(covered) == len(set(covered))
    # planned_after is the max bundle score + coordination penalty, never above `before`
    assert plan.planned_after_blast <= plan.before_blast
    assert plan.planned_after_blast < plan.before_blast     # a broad patch actually reduces
    # the scorecard-facing after-score is honestly bounded
    assert 0 <= plan.planned_after_blast <= 100


def test_planned_after_never_below_zero_or_above_before():
    files = (_fc("src/a.py", "production", lines=10), _fc("src/b.py", "production", lines=10))
    plan = _plan(files)
    assert 0 <= plan.planned_after_blast <= plan.before_blast


def test_no_untracked_marks_bundles_complete():
    plan = _plan((_fc("src/a.py", "production"),))
    assert all(b.coverage == "complete" for b in plan.bundles)


def test_all_untracked_bundle_is_untracked_only():
    plan = _plan((_fc("src/new.py", "production", status="added"),), untracked=("src/new.py",))
    assert any(b.coverage == "untracked_only" for b in plan.bundles)
