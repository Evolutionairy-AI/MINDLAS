from mindlas.runtime.split_types import SplitContext, SplitBundle, SplitResult


def _bundle(bid, *files):
    return SplitBundle(bundle_id=bid, name=bid, reason="r", files=tuple(files),
                       file_count=len(files), changed_lines=10, kinds=("production",),
                       concerns=("x",), coverage="complete", risk_score=12,
                       has_tests=False, has_production=True, warnings=())


def test_split_context_defaults():
    ctx = SplitContext(events=(), session_id="s", now_turn=3)
    assert ctx.project_root is None and ctx.max_bundle_files == 5
    assert ctx.max_bundle_lines == 250 and ctx.allow_single_file_large_bundle is True
    assert ctx.human_decision is None                                # A5: default no override


def test_split_result_to_dict_is_partitionable_patch_splitter_record():
    r = SplitResult(applied=True, before=82, planned_after=34, status="pass",
                    split_id="20260630T101500_ab12", manifest_path="/x/m.json",
                    bundles=(_bundle("bundle_01_config", "pyproject.toml"),
                             _bundle("bundle_02_src", "src/a.py")),
                    original_diff_hash="sha256:ab12cd", rails_labels={"change_blast_before": 82})
    d = r.to_dict()
    assert list(d)[0] == "type" and d["type"] == "patch_splitter"     # first key -> partitionable
    assert d["before"] == 82 and d["planned_after_blast"] == 34       # C3 key name (not "planned_after")
    assert d["status"] == "pass" and d["bundle_count"] == 2
    assert d["split_id"] == "20260630T101500_ab12"
    assert d["original_diff_hash"] == "sha256:ab12cd"
    assert d["manifest_path"] == "/x/m.json"                          # A4: auditable manifest pointer
    assert d["human_decision"] is None                               # A5: carried (default None)
    assert d["rails_labels"] == {"change_blast_before": 82}
    assert "planned_after" not in d and "applied" not in d            # not the raw field name / applied flag
