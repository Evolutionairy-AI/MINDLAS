import json
from pathlib import Path
from mindlas.runtime.scorecard import build_scorecard, render_scorecard_md, scorecard_to_json


def _ctx(before, after):
    return {"type": "context_repair", "before": before, "modeled_after_ctx": after,
            "validation": "pass", "evidence_preserved": 3}


def _blast(before, planned, status="pass", bundles=3):
    return {"type": "patch_splitter", "before": before, "planned_after_blast": planned,
            "status": status, "split_id": "20260630T101500_ab12", "bundle_count": bundles,
            "original_diff_hash": "sha256:ab12cd",
            "rails_labels": {"change_blast_before": before, "change_blast_planned_after": planned,
                             "source_files_modified": False,
                             "verify_gate_recommended_after_split": True}}


def _build(corrections):
    ctx = [c for c in corrections if c.get("type") == "context_repair"]
    befores = [c["before"] for c in ctx if "before" in c]
    afters = [c.get("modeled_after_ctx", c.get("after")) for c in ctx
              if ("modeled_after_ctx" in c or "after" in c)]
    return build_scorecard(session_id="s", task="t", ctx_max=max(befores, default=0),
                           ctx_final=(afters[-1] if afters else 0), ctx_alerts=len(ctx),
                           corrections=tuple(corrections))


def test_render_md_uses_change_blast_radius_label_and_no_after_score_qualifier():
    md = render_scorecard_md(build_scorecard(session_id="s", task="t", ctx_max=84, ctx_final=18,
                                             ctx_alerts=1, corrections=()))
    assert "| Change Blast Radius | -- | -- | -- | -- |" in md
    assert "| Blast Radius |" not in md                 # stale label must never reappear
    assert "evidence-based" not in md


def test_blast_correction_line_says_planned_not_modeled():
    md = render_scorecard_md(_build([_blast(82, 34, status="pass", bundles=3)]))
    assert "- Patch Splitter: BLAST 82 → 34 planned, bundles 3, validation pass" in md
    corr = md.split("## Corrections Applied")[1]
    assert "(modeled)" not in corr and "(evidence-based)" not in corr    # PLANNED honesty
    assert "Context Repair" not in corr                                  # not misrouted to the CTX branch


def test_blast_row_and_json_block_are_planned():
    sc = _build([_blast(82, 34, status="warning", bundles=2)])
    md = render_scorecard_md(sc)
    assert "| Change Blast Radius | 82 | 34 planned | 1 | 1 |" in md
    j = json.loads(scorecard_to_json(sc))
    cbr = j["features"]["change_blast_radius"]
    assert cbr == {"max": 82, "final": 34, "final_status": "planned", "alerts": 1,
                   "last_result": "warning", "bundle_count": 2}
    assert j["rails_export_ready"] is True                               # blast labels present
    assert j["rails_labels"]["change_blast_planned_after"] == 34


def _verify(before, after, status="allow"):
    return {"type": "verify_gate", "before": before, "after": after, "status": status,
            "coverage": "targeted",
            "rails_labels": {"verify_before": before, "verify_after": after,
                             "verification_debt_status": status}}


def test_rails_labels_merge_across_corrections_not_latest_only():
    # A6: a patch_splitter correction AFTER a verify_gate correction must NOT erase the verify
    # labels — top-level rails_labels merges label payloads across ALL corrections (later overrides
    # earlier on shared keys; the disjoint verify + blast keys both survive).
    sc = _build([_verify(60, 20), _blast(82, 34, status="pass", bundles=3)])
    labels = sc.rails_labels
    assert labels["verify_before"] == 60 and labels["verify_after"] == 20     # verify labels survive
    assert labels["verification_debt_status"] == "allow"
    assert labels["change_blast_planned_after"] == 34                         # ...alongside blast labels
    assert labels["source_files_modified"] is False
    j = json.loads(scorecard_to_json(sc))
    assert j["rails_export_ready"] is True
    assert (j["rails_labels"]["verify_before"] == 60
            and j["rails_labels"]["change_blast_planned_after"] == 34)


def test_blast_does_not_perturb_ctx_or_verify_rows():
    sc = _build([_ctx(78, 31), _blast(82, 34)])
    assert (sc.ctx_max, sc.ctx_final, sc.ctx_alerts) == (78, 31, 1)      # CTX untouched
    md = render_scorecard_md(sc)
    assert "| Context Rot | 78 | 31 | 1 | 1 |" in md
    assert "| Verification Debt | -- | -- | -- | -- |" in md            # VERIFY untouched (no record)


def test_no_blast_record_leaves_placeholder_row_and_none_block():
    sc = _build([_ctx(78, 31)])
    md = render_scorecard_md(sc)
    assert "| Change Blast Radius | -- | -- | -- | -- |" in md
    j = json.loads(scorecard_to_json(sc))
    assert j["features"]["change_blast_radius"] == {"max": 0, "final": 0, "final_status": "planned",
                                                    "alerts": 0, "last_result": "none",
                                                    "bundle_count": 0}


def test_patch_splitter_scorecard_matches_golden():
    out = render_scorecard_md(_build([_ctx(78, 31), _blast(82, 34, status="pass", bundles=3)]))
    golden = Path(__file__).parent / "vitals_golden" / "patch_splitter_scorecard.md"
    if not golden.exists():
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(out, encoding="utf-8")            # self-seed on first run
    assert out == golden.read_text(encoding="utf-8")
