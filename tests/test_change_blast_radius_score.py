from mindlas.runtime.blast_state import ChangeBlastSignals
from mindlas.features.blast_radius import (
    score_change_blast_radius, change_blast_trigger,
    WATCH_MIN, ALERT_MIN, ChangeBlastRadiusScorer,
)


def _sig(**kw) -> ChangeBlastSignals:
    base = dict(
        session_id="s", changed_files=(), changed_file_count=0, changed_lines=0,
        additions=0, deletions=0, production_files_changed=(), test_files_changed=(),
        config_files_changed=(), docs_files_changed=(), neutral_files_changed=(),
        directory_count=0, concern_count=0, file_kind_count=0,
        production_without_tests=False, config_mixed_with_source=False,
        docs_mixed_with_source=False, high_centrality_files=(), deleted_files=(),
        renamed_files=(), max_lines_in_one_file=0, current_diff_hash="sha256:x",
    )
    base.update(kw)
    return ChangeBlastSignals(**base)


def _mid():
    return _sig(changed_file_count=6, changed_lines=120, directory_count=3,
                concern_count=3, file_kind_count=2)


def test_clean_tree_scores_zero():
    assert score_change_blast_radius(_sig()) == 0


def test_small_coherent_patch_is_stable():
    s = _sig(changed_file_count=2, changed_lines=20, directory_count=1,
             concern_count=1, file_kind_count=1)
    assert score_change_blast_radius(s) < WATCH_MIN          # cohesion credit pulls it to STABLE


def test_broad_multi_dir_patch_is_alert():
    s = _sig(changed_file_count=9, changed_lines=412, directory_count=4, concern_count=5,
             file_kind_count=3, high_centrality_files=("pyproject.toml",),
             production_without_tests=True, config_mixed_with_source=True,
             docs_mixed_with_source=True)
    assert score_change_blast_radius(s) >= ALERT_MIN


def test_config_mixed_adds_exactly_ten():
    s = _sig(changed_file_count=6, changed_lines=120, directory_count=3,
             concern_count=3, file_kind_count=2, config_mixed_with_source=True)
    assert score_change_blast_radius(s) - score_change_blast_radius(_mid()) == 10


def test_docs_mixed_adds_exactly_five():
    s = _sig(changed_file_count=6, changed_lines=120, directory_count=3,
             concern_count=3, file_kind_count=2, docs_mixed_with_source=True)
    assert score_change_blast_radius(s) - score_change_blast_radius(_mid()) == 5


def test_production_without_tests_adds_exactly_ten():
    s = _sig(changed_file_count=6, changed_lines=120, directory_count=3,
             concern_count=3, file_kind_count=2, production_without_tests=True)
    assert score_change_blast_radius(s) - score_change_blast_radius(_mid()) == 10


def test_centrality_adds_five_per_file_capped_at_ten():
    one = _sig(changed_file_count=6, changed_lines=120, directory_count=3,
               concern_count=3, file_kind_count=2, high_centrality_files=("a",))
    two = _sig(changed_file_count=6, changed_lines=120, directory_count=3,
               concern_count=3, file_kind_count=2, high_centrality_files=("a", "b"))
    assert score_change_blast_radius(one) - score_change_blast_radius(_mid()) == 5
    assert score_change_blast_radius(two) - score_change_blast_radius(_mid()) == 10


def test_score_is_clamped_0_to_100():
    huge = _sig(changed_file_count=50, changed_lines=9000, directory_count=20,
                concern_count=20, file_kind_count=5, high_centrality_files=("a", "b", "c"),
                deleted_files=("d1", "d2"), renamed_files=("r1", "r2"),
                production_without_tests=True, config_mixed_with_source=True,
                docs_mixed_with_source=True)
    assert score_change_blast_radius(huge) == 100
    tiny = _sig(changed_file_count=1, changed_lines=1, directory_count=1,
                concern_count=1, file_kind_count=1)
    assert score_change_blast_radius(tiny) == 0              # negative sum clamps to 0


def test_each_trigger_clause_fires_in_isolation():
    assert change_blast_trigger(_sig(changed_file_count=7, changed_lines=420, directory_count=3,
                                     concern_count=3, file_kind_count=4,
                                     high_centrality_files=("cli.py",),
                                     docs_mixed_with_source=True,
                                     max_lines_in_one_file=150)) is True   # ONLY score>=65 (=72)
    assert change_blast_trigger(_sig(changed_file_count=8, concern_count=1,
                                     directory_count=1, changed_lines=10)) is True  # files>=8
    assert change_blast_trigger(_sig(changed_file_count=4, concern_count=4,
                                     directory_count=1, changed_lines=10)) is True  # concerns>=4
    assert change_blast_trigger(_sig(changed_file_count=3, concern_count=2, directory_count=4,
                                     changed_lines=120, file_kind_count=2)) is True  # dirs>=4 & lines
    assert change_blast_trigger(_sig(changed_file_count=3, concern_count=2, directory_count=1,
                                     changed_lines=10, config_mixed_with_source=True)) is True
    assert change_blast_trigger(_sig(changed_file_count=5, concern_count=2, directory_count=1,
                                     changed_lines=10, production_without_tests=True)) is True
    assert change_blast_trigger(_sig(changed_file_count=2, concern_count=1, directory_count=1,
                                     changed_lines=10, max_lines_in_one_file=400)) is True
    assert change_blast_trigger(_sig(changed_file_count=4, concern_count=2, directory_count=1,
                                     changed_lines=10, high_centrality_files=("a", "b"))) is True


def test_trigger_silent_on_small_coherent_patch():
    assert change_blast_trigger(_sig(changed_file_count=2, changed_lines=20, directory_count=1,
                                     concern_count=1, file_kind_count=1)) is False


def test_scorer_identifiers_match_decisions():
    sc = ChangeBlastRadiusScorer()
    assert sc.risk_id == "change_blast_radius"
    assert sc.label == "Change Blast Radius"
    assert sc.short_label == "BLAST"


def test_read_elevated_reading_recommends_patch_splitter():
    s = _sig(changed_file_count=9, changed_lines=412, directory_count=4, concern_count=5,
             file_kind_count=3, config_mixed_with_source=True)
    r = ChangeBlastRadiusScorer().read(s)
    assert r.state == "ALERT"
    assert r.score >= ALERT_MIN
    assert r.direction == "up"
    assert r.can_verify is False
    assert r.suggested_commands == ("/mindlas-blast-split",)
    assert r.correction and r.summary
    assert any(f.text == "9 files changed" for f in r.facts)
    assert any(f.text == "config changed alongside source" for f in r.facts)


def test_read_uses_band_4_warning_is_reachable():
    # mid-base (44) + config (+10) = 54 -> WARNING, which the 3-band state_for_score cannot produce
    s = _sig(changed_file_count=6, changed_lines=120, directory_count=3, concern_count=3,
             file_kind_count=2, config_mixed_with_source=True)
    r = ChangeBlastRadiusScorer().read(s)
    assert r.score == 54 and r.state == "WARNING"


def test_read_clean_tree_is_stable_with_no_correction():
    r = ChangeBlastRadiusScorer().read(_sig())
    assert r.state == "STABLE"
    assert r.correction == "" and r.suggested_commands == ()
    assert r.can_verify is False
