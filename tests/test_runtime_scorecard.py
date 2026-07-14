import json
from pathlib import Path
from mindlas.runtime.scorecard import build_scorecard, render_scorecard_md, scorecard_to_json

_GOLDEN = Path(__file__).parent / "vitals_golden" / "scorecard.md"


def _sc():
    return build_scorecard(
        session_id="abc123", task="Refactor the auth module",
        ctx_max=84, ctx_final=18, ctx_alerts=1,
        corrections=({"type": "context_repair", "applied": True, "validation": "pass",
                      "before": 84, "modeled_after_ctx": 18, "evidence_preserved": 12,
                      "constraints_preserved": 3,
                      "downstream": {"ran": True, "tool": "ruff",
                                     "target": ["clean_module.py"], "findings": 0,
                                     "result": "pass", "note": "0 finding(s)"},
                      "human_decision": "accepted",
                      "pack_path": ".mindlas/context/latest.md"},))


def test_scorecard_md_matches_golden():
    assert render_scorecard_md(_sc()) == _GOLDEN.read_text(encoding="utf-8")


def test_scorecard_json_shape():
    j = json.loads(scorecard_to_json(_sc()))
    assert j["session_id"] == "abc123"
    assert j["features"]["context_rot"] == {"max": 84, "final": 18, "alerts": 1}
    assert j["corrections"][0]["modeled_after_ctx"] == 18
    assert j["corrections"][0]["downstream"]["result"] == "pass"
    assert j["rails_export_ready"] is False
    assert j["rails_labels"] == {}
    assert j["features"]["verification_debt"] == {"max": 0, "final": 0, "alerts": 0,
                                                   "last_result": "none", "coverage": "none"}
    assert j["features"]["change_blast_radius"] == {"max": 0, "final": 0, "final_status": "planned",
                                                    "alerts": 0, "last_result": "none",
                                                    "bundle_count": 0}
    assert j["features"]["tool_failure_loop"] == {"max": 0, "final": 0, "final_status": "controlled",
                                                  "alerts": 0, "last_result": "none",
                                                  "active_stop": False, "failure_signature": ""}
    assert j["corrections"][0] == _sc().corrections[0]   # full record passes through verbatim
