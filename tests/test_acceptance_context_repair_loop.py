import json
from mindlas.vitals import fixtures
from mindlas.runtime.state import build_context_rot_signals
from mindlas.features.context_rot import ContextRotScorer, context_rot_trigger
from mindlas.actions.context_repair import ContextRepair, RepairContext
from mindlas.runtime import paths
from mindlas.runtime.scorecard import build_scorecard, scorecard_to_json


def test_context_rot_to_repair_to_scorecard(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    events = fixtures.context_rot_alert()
    now = max(e.turn for e in events)

    # 1. Context Rot crosses the threshold and the trigger fires.
    sig = build_context_rot_signals(events, now_turn=now)
    before_reading = ContextRotScorer().read(sig)
    assert before_reading.state == "ALERT"
    assert context_rot_trigger(sig, before_reading.score) is True

    # 2-6. Context Repair previews (validation passes), then applies (writes the pack).
    rc = RepairContext(events=tuple(events), session_id=sig.session_id, now_turn=now)
    pv = ContextRepair().preview(rc)
    assert pv.validation.status == "pass"
    res = ContextRepair().apply(rc, now="20260628T120000")
    assert res.applied is True
    assert paths.latest_pack_path(sig.session_id).exists()
    # The repair produced the scorecard (review Point-1: no session without a scorecard).
    assert paths.scorecard_md_path().exists()
    assert paths.scorecard_json_path().exists()

    # 7. The CTX bar shrinks (modeled post-repair).
    assert res.modeled_after_ctx < res.before

    # 8. The scorecard records the before/after correction.
    corrections = tuple(json.loads(ln) for ln in
                        paths.corrections_path().read_text(encoding="utf-8").splitlines() if ln.strip())
    sc = build_scorecard(session_id=sig.session_id, task="(demo)", ctx_max=res.before,
                         ctx_final=res.modeled_after_ctx, ctx_alerts=1, corrections=corrections)
    j = json.loads(scorecard_to_json(sc))
    assert j["corrections"][-1]["before"] == res.before
    assert j["corrections"][-1]["modeled_after_ctx"] == res.modeled_after_ctx
    assert j["rails_export_ready"] is False
