import json
from mindlas.runtime.scorecard import build_scorecard, render_scorecard_md, scorecard_to_json


def _ctx(before, after):
    return {"type": "context_repair", "before": before, "modeled_after_ctx": after,
            "validation": "pass", "evidence_preserved": 3}


def _vfy(before, after, status="pass", coverage="targeted"):
    return {"type": "verify_gate", "before": before, "after": after, "status": status,
            "coverage": coverage, "diff_hash": "sha256:z", "commands_run": 2,
            "rails_labels": {"verification_debt_before": before, "verification_debt_after": after}}


def _build(corrections):
    # readers partition CTX themselves; build_scorecard computes verify/rails.
    ctx = [c for c in corrections if c.get("type") == "context_repair"]
    befores = [c["before"] for c in ctx if "before" in c]
    afters = [c.get("modeled_after_ctx", c.get("after")) for c in ctx
              if ("modeled_after_ctx" in c or "after" in c)]
    return build_scorecard(session_id="s", task="t",
                           ctx_max=max(befores, default=0),
                           ctx_final=(afters[-1] if afters else 0), ctx_alerts=len(ctx),
                           corrections=tuple(corrections))


def test_verify_record_does_not_perturb_ctx():
    sc = _build([_ctx(78, 31), _vfy(82, 18)])
    assert (sc.ctx_max, sc.ctx_final, sc.ctx_alerts) == (78, 31, 1)     # CTX untouched by verify
    assert (sc.verify_max, sc.verify_final, sc.verify_alerts) == (82, 18, 1)
    j = json.loads(scorecard_to_json(sc))
    assert j["features"]["context_rot"] == {"max": 78, "final": 31, "alerts": 1}
    assert j["features"]["verification_debt"]["final"] == 18


def test_verify_correction_line_has_no_modeled():
    md = render_scorecard_md(_build([_vfy(82, 18, status="pass", coverage="targeted")]))
    assert "Verify Gate: VERIFY 82 → 18, result pass, coverage targeted" in md
    assert "VERIFY 82 → 18 (modeled)" not in md and "Verify Gate" in md
    assert "(modeled)" not in md.split("## Corrections Applied")[1]     # no modeled on verify line


def test_rails_export_ready_is_generic():
    ctx_only = json.loads(scorecard_to_json(_build([_ctx(78, 31)])))
    assert ctx_only["rails_export_ready"] is False and ctx_only["rails_labels"] == {}
    with_vfy = json.loads(scorecard_to_json(_build([_ctx(78, 31), _vfy(82, 18)])))
    assert with_vfy["rails_export_ready"] is True
    assert with_vfy["rails_labels"]["verification_debt_after"] == 18


def test_ctx_only_markdown_row_unchanged():
    md = render_scorecard_md(_build([_ctx(78, 31)]))
    assert "| Context Rot | 78 | 31 | 1 | 1 |" in md
    assert "| Verification Debt | -- | -- | -- | -- |" in md       # placeholder when no verify


def test_verify_scorecard_matches_golden():
    from pathlib import Path
    out = render_scorecard_md(_build([_ctx(78, 31), _vfy(82, 18, status="pass", coverage="targeted")]))
    golden = Path(__file__).parent / "vitals_golden" / "verify_gate_scorecard.md"
    if not golden.exists():
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(out, encoding="utf-8")          # self-seed on first run
    assert out == golden.read_text(encoding="utf-8")
