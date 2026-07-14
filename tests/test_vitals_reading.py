import json
from mindlas.vitals.reading import RiskFact, RiskReading, state_for_score


def test_reading_round_trips_to_json():
    r = RiskReading(
        risk_id="verification_debt", label="Verification Debt", short_label="VERIFY",
        score=82, state="ALERT", direction="up", confidence="high",
        facts=(RiskFact(key="files", value=5, text="5 files changed since last passing verifier"),),
        summary="5 files / 312 lines changed since last passing test",
        correction="run the targeted verifier before continuing",
        suggested_commands=("mindlas verify --changed",), can_verify=True,
    )
    d = r.to_dict()
    assert json.loads(json.dumps(d))["facts"][0]["key"] == "files"
    assert d["score"] == 82 and d["short_label"] == "VERIFY"


def test_state_for_score_thresholds():
    assert state_for_score(10, watch=40, alert=65) == "OK"
    assert state_for_score(50, watch=40, alert=65) == "WATCH"
    assert state_for_score(90, watch=40, alert=65) == "ALERT"
