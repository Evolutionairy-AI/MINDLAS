from mindlas.features.base import band_4, RiskScorer, RiskReading, RiskFact  # noqa: F401


def test_band_4_boundaries():
    assert band_4(0, watch=40, warning=65, alert=80) == "STABLE"
    assert band_4(39, watch=40, warning=65, alert=80) == "STABLE"
    assert band_4(40, watch=40, warning=65, alert=80) == "WATCH"
    assert band_4(64, watch=40, warning=65, alert=80) == "WATCH"
    assert band_4(65, watch=40, warning=65, alert=80) == "WARNING"
    assert band_4(79, watch=40, warning=65, alert=80) == "WARNING"
    assert band_4(80, watch=40, warning=65, alert=80) == "ALERT"
    assert band_4(100, watch=40, warning=65, alert=80) == "ALERT"


from mindlas.runtime.state import ContextRotSignals
from mindlas.features.context_rot import score_context_rot


def _sig(**kw):
    base = dict(session_id="s", turn_count=0, session_mass_pct=0.0, large_tool_outputs=0,
                task_contract_age_turns=0, unresolved_assumptions=0, last_repair_age_turns=0,
                mass_is_measured=True)
    base.update(kw)
    return ContextRotSignals(**base)


def test_score_zero_for_fresh_empty_session():
    assert score_context_rot(_sig()) == 0


def test_each_term_is_capped():
    assert score_context_rot(_sig(session_mass_pct=100.0)) == 30      # mass: 100 * 0.30
    assert score_context_rot(_sig(turn_count=200)) == 20             # turns: count / 2
    assert score_context_rot(_sig(large_tool_outputs=50)) == 15      # outputs: count * 3
    assert score_context_rot(_sig(task_contract_age_turns=100)) == 15  # age * 0.75
    assert score_context_rot(_sig(unresolved_assumptions=50)) == 10  # count * 2
    assert score_context_rot(_sig(last_repair_age_turns=100)) == 10  # age * 0.5


def test_score_clamps_to_100():
    s = _sig(session_mass_pct=100.0, turn_count=200, large_tool_outputs=50,
             task_contract_age_turns=100, unresolved_assumptions=50, last_repair_age_turns=100)
    assert score_context_rot(s) == 100   # 30+20+15+15+10+10


from mindlas.features.context_rot import context_rot_trigger


def test_trigger_fires_at_warning_score():
    assert context_rot_trigger(_sig(), 65) is True
    assert context_rot_trigger(_sig(), 64) is False


def test_trigger_fires_on_stale_contract_even_if_score_low():
    assert context_rot_trigger(_sig(task_contract_age_turns=26), 10) is True


def test_trigger_quiet_on_fresh_low_session():
    assert context_rot_trigger(_sig(task_contract_age_turns=3), 20) is False


from mindlas.features.context_rot import ContextRotScorer


def test_read_alert_reading_has_correction_and_command():
    s = _sig(session_mass_pct=100.0, turn_count=200, large_tool_outputs=50,
             task_contract_age_turns=100, unresolved_assumptions=50, last_repair_age_turns=100)
    r = ContextRotScorer().read(s)
    assert r.risk_id == "context_rot"
    assert r.short_label == "ROT"
    assert r.score == 100
    assert r.state == "ALERT"
    assert r.direction == "up"
    assert r.confidence == "high"           # mass_is_measured=True
    assert "Context Repair" in r.correction
    assert r.suggested_commands == ("/mindlas-repair",)
    assert any("turns" in f.text for f in r.facts)


def test_read_stable_reading_has_no_correction():
    r = ContextRotScorer().read(_sig())
    assert r.state == "STABLE"
    assert r.correction == ""
    assert r.suggested_commands == ()


def test_read_confidence_medium_when_mass_proxied():
    assert ContextRotScorer().read(_sig(mass_is_measured=False, turn_count=10)).confidence == "medium"


def test_read_mass_fact_marks_estimated_when_proxied():
    r = ContextRotScorer().read(_sig(mass_is_measured=False, session_mass_pct=42.0))
    assert any("estimated" in f.text for f in r.facts)


def test_estimated_mass_alone_caps_at_warning():
    # score 80 (30 mass + 50 non-mass), but mass is estimated and non-mass (50) < 65
    s = _sig(mass_is_measured=False, session_mass_pct=100.0, turn_count=40,
             large_tool_outputs=4, task_contract_age_turns=25, last_repair_age_turns=6)
    r = ContextRotScorer().read(s)
    assert r.score == 80
    assert r.state == "WARNING"             # capped: estimated mass can't fire a strong ALERT alone


def test_measured_mass_reaches_alert_at_same_signals():
    s = _sig(mass_is_measured=True, session_mass_pct=100.0, turn_count=40,
             large_tool_outputs=4, task_contract_age_turns=25, last_repair_age_turns=6)
    assert ContextRotScorer().read(s).state == "ALERT"


def test_estimated_alert_allowed_with_strong_corroboration():
    s = _sig(mass_is_measured=False, session_mass_pct=100.0, turn_count=200,
             large_tool_outputs=50, task_contract_age_turns=100,
             unresolved_assumptions=50, last_repair_age_turns=100)
    assert ContextRotScorer().read(s).state == "ALERT"   # non-mass alone = 70 >= 65


from mindlas.vitals import fixtures
from mindlas.runtime.state import build_context_rot_signals


def _band_for(events):
    from mindlas.features.context_rot import ContextRotScorer
    now = max((e.turn for e in events), default=0)
    return ContextRotScorer().read(build_context_rot_signals(events, now_turn=now)).state


def test_fixture_context_rot_low_is_stable():
    assert _band_for(fixtures.context_rot_low()) == "STABLE"


def test_fixture_context_rot_alert_is_alert():
    assert _band_for(fixtures.context_rot_alert()) == "ALERT"


from pathlib import Path
from mindlas.vitals.ledger import Ledger

_FIX = Path(__file__).parent / "fixtures"


def test_golden_jsonl_low_is_stable():
    assert _band_for(Ledger(_FIX / "context_rot_low.jsonl").events()) == "STABLE"


def test_golden_jsonl_alert_is_alert():
    assert _band_for(Ledger(_FIX / "context_rot_alert.jsonl").events()) == "ALERT"
