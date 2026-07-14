from mindlas.runtime.verification_state import VerificationDebtSignals
from mindlas.features.verification_debt import (
    score_verification_debt, WATCH_MIN, WARNING_MIN, ALERT_MIN,
    verification_debt_trigger,
)


def _sig(**kw):
    base = dict(session_id="s", changed_files=(), changed_file_count=0, changed_lines=0,
                production_files_changed=(), test_files_changed=(), config_files_changed=(),
                production_without_tests=False, config_mixed_with_source=False,
                turns_since_last_pass=0, last_verifier_status="unknown", last_verifier_turn=None,
                unresolved_failures=0, completion_claim_without_evidence=False,
                verification_coverage="none", evidence_is_fresh=False, current_diff_hash="sha256:x")
    base.update(kw)
    return VerificationDebtSignals(**base)


def test_clean_repo_scores_zero_even_with_unknown_and_turns():
    # A5: clean files+lines and no unresolved failures -> 0, regardless of unknown/turns terms
    assert score_verification_debt(_sig(turns_since_last_pass=9, last_verifier_status="unknown")) == 0


def test_changed_source_raises_score():
    s = _sig(changed_files=("a.py",), changed_file_count=1, changed_lines=40,
             production_files_changed=("a.py",), production_without_tests=True)
    assert score_verification_debt(s) > WATCH_MIN


def test_single_failed_file_is_warning_not_alert():
    # A single failed file scores ~50 (WARNING). ALERT (>=70) on the GAUGE needs a heavy
    # change set; the "VERIFY 95 ALERT" in the spec is the ACTION's after-score cap, not this.
    s = _sig(changed_files=("a.py",), changed_file_count=1, changed_lines=20,
             production_files_changed=("a.py",), production_without_tests=True,
             last_verifier_status="fail", unresolved_failures=1)
    score = score_verification_debt(s)
    assert WARNING_MIN <= score < ALERT_MIN


def test_heavy_change_with_fail_drives_alert():
    s = _sig(changed_files=tuple(f"f{i}.py" for i in range(6)), changed_file_count=6,
             changed_lines=300, production_files_changed=("f0.py",), production_without_tests=True,
             turns_since_last_pass=7, last_verifier_status="fail", unresolved_failures=1)
    assert score_verification_debt(s) >= ALERT_MIN


def test_fresh_full_pass_reduces_score():
    s = _sig(changed_files=("a.py",), changed_file_count=1, changed_lines=20,
             production_files_changed=("a.py",), production_without_tests=True,
             last_verifier_status="pass", verification_coverage="full", evidence_is_fresh=True)
    assert score_verification_debt(s) < WATCH_MIN


def test_score_is_clamped_0_100():
    s = _sig(changed_files=tuple(f"f{i}.py" for i in range(50)), changed_file_count=50,
             changed_lines=9999, production_files_changed=("f0.py",),
             production_without_tests=True, completion_claim_without_evidence=True,
             last_verifier_status="fail", unresolved_failures=5, config_mixed_with_source=True)
    assert 0 <= score_verification_debt(s) <= 100


def test_bands_are_25_50_70():
    assert (WATCH_MIN, WARNING_MIN, ALERT_MIN) == (25, 50, 70)


def test_trigger_fires_on_high_score():
    s = _sig(changed_file_count=1, changed_lines=10)
    assert verification_debt_trigger(s, 65) is True


def test_trigger_fires_on_many_changed_files_when_stale():
    s = _sig(changed_file_count=5, evidence_is_fresh=False)
    assert verification_debt_trigger(s, 10) is True


def test_trigger_fires_on_production_without_tests():
    s = _sig(changed_file_count=2, production_files_changed=("a.py", "b.py"),
             production_without_tests=True)
    assert verification_debt_trigger(s, 10) is True


def test_trigger_fires_on_completion_claim():
    assert verification_debt_trigger(_sig(completion_claim_without_evidence=True), 5) is True


def test_trigger_silent_when_clean():
    assert verification_debt_trigger(_sig(), 0) is False


from mindlas.features.verification_debt import VerificationDebtScorer


def test_scorer_identity_and_alert_band():
    sc = VerificationDebtScorer()
    assert (sc.risk_id, sc.label, sc.short_label) == ("verification_debt", "Verification Debt", "VERIFY")
    s = _sig(changed_files=tuple(f"f{i}.py" for i in range(6)), changed_file_count=6,
             changed_lines=300, production_files_changed=("f0.py",), production_without_tests=True,
             turns_since_last_pass=7, last_verifier_status="fail", unresolved_failures=1)
    r = sc.read(s)
    assert r.state == "ALERT"
    assert r.short_label == "VERIFY"
    assert r.can_verify is True
    assert r.suggested_commands == ("/mindlas-verify",)
    assert any(f.key == "files" for f in r.facts)


def test_scorer_stable_band_has_no_correction():
    r = VerificationDebtScorer().read(_sig())
    assert r.state == "STABLE"
    assert r.correction == "" and r.suggested_commands == ()
