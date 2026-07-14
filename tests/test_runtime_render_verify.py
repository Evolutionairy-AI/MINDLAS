from pathlib import Path
from mindlas.runtime.render import render_verify_gauge
from mindlas.features.verification_debt import VerificationDebtScorer
from mindlas.runtime.verification_state import demo_verification_debt_alert_signals

_GOLDEN = Path(__file__).parent / "vitals_golden" / "verification_debt_alert.txt"


def _alert_reading():
    return VerificationDebtScorer().read(demo_verification_debt_alert_signals())


def test_verify_gauge_renders_label_band_and_correction():
    out = render_verify_gauge(_alert_reading(), plain=True)
    assert out.startswith("VERIFY")
    assert "ALERT" in out
    assert "Correction:" in out
    assert "/mindlas-verify" in out


def test_verify_gauge_matches_golden():
    out = render_verify_gauge(_alert_reading(), plain=True)
    if not _GOLDEN.exists():
        _GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        _GOLDEN.write_text(out, encoding="utf-8")
    assert out == _GOLDEN.read_text(encoding="utf-8")
