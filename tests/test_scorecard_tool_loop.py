import json
from pathlib import Path
from mindlas.runtime.scorecard import build_scorecard, render_scorecard_md, scorecard_to_json


def _ctx(before, after):
    return {"type": "context_repair", "before": before, "modeled_after_ctx": after,
            "validation": "pass", "evidence_preserved": 3}


def _verify(before, after, status="allow"):
    return {"type": "verify_gate", "before": before, "after": after, "status": status,
            "coverage": "targeted",
            "rails_labels": {"verify_before": before, "verify_after": after,
                             "verification_debt_status": status}}


def _loop(before, after, sig="timeout:Bash:8f12abcd", status="controlled"):
    return {"type": "loop_stop", "before": before, "controlled_after_loop": after,
            "status": status, "stop_id": "20260701T120000_8f12", "failure_signature": sig,
            "active_tool_name": "Bash",
            "rails_labels": {"tool_failure_loop_before": before,
                             "tool_failure_loop_controlled_after": after,
                             "loop_stop_status": status, "stop_active": True,
                             "source_files_modified": False, "commands_run_by_stop": 0}}


def _blast(before, after, bundles=5, status="validated"):
    # A shipped patch_splitter correction record (planned after-score), for the
    # all-four-types partition test. build_scorecard reads before/planned_after_blast/status/bundle_count.
    return {"type": "patch_splitter", "before": before, "planned_after_blast": after,
            "status": status, "bundle_count": bundles,
            "rails_labels": {"change_blast_radius_before": before,
                             "change_blast_radius_planned_after": after}}


def _build(corrections):
    ctx = [c for c in corrections if c.get("type") == "context_repair"]
    befores = [c["before"] for c in ctx if "before" in c]
    afters = [c.get("modeled_after_ctx", c.get("after")) for c in ctx
              if ("modeled_after_ctx" in c or "after" in c)]
    return build_scorecard(session_id="s", task="t", ctx_max=max(befores, default=0),
                           ctx_final=(afters[-1] if afters else 0), ctx_alerts=len(ctx),
                           corrections=tuple(corrections))


def test_loop_correction_line_says_controlled_not_modeled():
    md = render_scorecard_md(_build([_loop(86, 15)]))
    assert ("- Stop: LOOP 86 → 15 controlled, signature timeout:Bash:8f12abcd, stop active") in md
    corr = md.split("## Corrections Applied")[1]
    assert "(modeled)" not in corr and "planned" not in corr and "evidence-based" not in corr
    assert "Context Repair" not in corr                 # not misrouted to the CTX branch


def test_loop_row_and_json_block_are_controlled():
    sc = _build([_loop(86, 15)])
    md = render_scorecard_md(sc)
    assert "| Tool Failure Loop | 86 | 15 controlled | 1 | 1 |" in md
    j = json.loads(scorecard_to_json(sc))
    tfl = j["features"]["tool_failure_loop"]
    assert tfl == {"max": 86, "final": 15, "final_status": "controlled", "alerts": 1,
                   "last_result": "controlled", "active_stop": True,
                   "failure_signature": "timeout:Bash:8f12abcd"}
    assert j["rails_export_ready"] is True


def test_rails_labels_merge_loop_after_verify():
    # A loop_stop correction after a verify_gate correction keeps BOTH label sets.
    sc = _build([_verify(60, 20), _loop(86, 15)])
    labels = sc.rails_labels
    assert labels["verify_before"] == 60 and labels["verification_debt_status"] == "allow"
    assert labels["tool_failure_loop_controlled_after"] == 15
    assert labels["source_files_modified"] is False


def test_all_four_correction_types_partition_cleanly():
    # The FINAL scorecard with ALL four correction types present at once. Every feature
    # row and every correction line must read ONLY its own type — no cross-contamination, each honesty
    # label stays type-correct (CTX modeled / VERIFY evidence / BLAST planned / LOOP controlled).
    sc = _build([_ctx(78, 31), _verify(60, 20), _blast(82, 42), _loop(86, 15)])
    md = render_scorecard_md(sc)
    assert "| Context Rot | 78 | 31 | 1 | 1 |" in md
    assert "| Verification Debt | 60 | 20 | 1 | 1 |" in md
    assert "| Change Blast Radius | 82 | 42 planned | 1 | 1 |" in md
    assert "| Tool Failure Loop | 86 | 15 controlled | 1 | 1 |" in md
    j = json.loads(scorecard_to_json(sc))
    assert j["features"]["context_rot"]["max"] == 78
    assert j["features"]["verification_debt"]["max"] == 60
    assert j["features"]["change_blast_radius"]["max"] == 82
    assert j["features"]["tool_failure_loop"]["max"] == 86
    assert j["rails_export_ready"] is True
    corr = md.split("## Corrections Applied")[1]
    assert corr.count("Context Repair") == 1 and corr.count("Verify Gate") == 1
    assert corr.count("Patch Splitter") == 1 and corr.count("- Stop:") == 1
    # each line keeps its own honesty qualifier; none bleeds onto another
    assert "(modeled)" in corr and "planned" in corr and "controlled" in corr


def test_loop_does_not_perturb_ctx_verify_blast_rows():
    sc = _build([_ctx(78, 31), _loop(86, 15)])
    assert (sc.ctx_max, sc.ctx_final, sc.ctx_alerts) == (78, 31, 1)      # CTX untouched
    md = render_scorecard_md(sc)
    assert "| Context Rot | 78 | 31 | 1 | 1 |" in md
    assert "| Verification Debt | -- | -- | -- | -- |" in md            # VERIFY untouched
    assert "| Change Blast Radius | -- | -- | -- | -- |" in md          # BLAST untouched


def test_no_loop_record_leaves_placeholder_and_empty_block():
    sc = _build([_ctx(78, 31)])
    md = render_scorecard_md(sc)
    assert "| Tool Failure Loop | -- | -- | -- | -- |" in md
    j = json.loads(scorecard_to_json(sc))
    assert j["features"]["tool_failure_loop"] == {"max": 0, "final": 0, "final_status": "controlled",
                                                  "alerts": 0, "last_result": "none",
                                                  "active_stop": False, "failure_signature": ""}


def test_loop_stop_scorecard_matches_golden():
    out = render_scorecard_md(_build([_ctx(78, 31), _loop(86, 15)]))
    golden = Path(__file__).parent / "vitals_golden" / "loop_stop_scorecard.md"
    if not golden.exists():
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(out, encoding="utf-8")            # self-seed on first run
    assert out == golden.read_text(encoding="utf-8")
