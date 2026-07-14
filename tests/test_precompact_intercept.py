from mindlas.vitals.events import Event
from mindlas.vitals.ledger import Ledger
from mindlas.vitals.config import ledger_path
from mindlas.vitals import hooks


def test_intercept_mode_defaults_to_warn(monkeypatch):
    monkeypatch.delenv("MINDLAS_INTERCEPT_COMPACT", raising=False)
    assert hooks.intercept_compact_mode() == "warn"


def test_intercept_mode_parses_values(monkeypatch):
    for v, exp in [("off", "off"), ("WARN", "warn"), ("block", "block"), ("garbage", "warn")]:
        monkeypatch.setenv("MINDLAS_INTERCEPT_COMPACT", v)
        assert hooks.intercept_compact_mode() == exp


def test_recommendation_names_the_repair_command():
    assert "mindlas context repair" in hooks._COMPACT_RECO


def test_pre_compact_logs_boundary_and_never_blocks(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path))
    monkeypatch.setenv("MINDLAS_INTERCEPT_COMPACT", "off")   # silent: no terminal write in test
    sid = "s_pc"
    led = Ledger(ledger_path(sid))
    led.append(Event(sid, 3, "t3", "user_prompt", target="some real task statement here"))
    out = hooks._on_pre_compact(led, sid, {"trigger": "manual"})
    assert out == {}                                          # never blocks
    assert Ledger(ledger_path(sid)).events()[-1].kind == "compact_boundary"
