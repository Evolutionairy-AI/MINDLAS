from mindlas.features.verification_debt import verify_after_score


def test_pass_fresh_full_caps_to_10():
    assert verify_after_score(82, 25, status="pass", coverage="full", evidence_is_fresh=True) == 10


def test_pass_fresh_targeted_caps_to_18():
    assert verify_after_score(82, 30, status="pass", coverage="targeted", evidence_is_fresh=True) == 18


def test_pass_fresh_partial_caps_to_35():
    assert verify_after_score(82, 60, status="pass", coverage="partial", evidence_is_fresh=True) == 35


def test_pass_not_fresh_no_cap():
    # freshness flipped false -> raw re-derived score stands (the honesty mechanism)
    assert verify_after_score(82, 70, status="pass", coverage="full", evidence_is_fresh=False) == 70


def test_fail_floor_95():
    assert verify_after_score(82, 50, status="fail", coverage="none", evidence_is_fresh=False) == 95
    assert verify_after_score(97, 50, status="fail", coverage="none", evidence_is_fresh=False) == 97


def test_timeout_floor_80_and_skipped_floor_70():
    assert verify_after_score(50, 10, status="timeout", coverage="none", evidence_is_fresh=False) == 80
    assert verify_after_score(50, 10, status="skipped", coverage="none", evidence_is_fresh=False) == 70


def test_pass_fresh_already_below_cap_keeps_lower():
    assert verify_after_score(40, 6, status="pass", coverage="full", evidence_is_fresh=True) == 6
