def test_latest_ledger_picks_newest(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path))
    from mindlas.vitals.config import latest_ledger, ledger_path
    import os, time
    a = ledger_path("old"); a.parent.mkdir(parents=True); a.write_text("{}", encoding="utf-8")
    b = ledger_path("new"); b.parent.mkdir(parents=True); b.write_text("{}", encoding="utf-8")
    old = time.time() - 100
    os.utime(a, (old, old))                      # make "old" genuinely older
    assert latest_ledger() == b


def test_latest_ledger_none_when_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path))
    from mindlas.vitals.config import latest_ledger
    assert latest_ledger() is None


def test_claude_config_dir_respects_override(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    from mindlas.vitals.config import claude_settings_path
    assert claude_settings_path() == tmp_path / "cc" / "settings.json"
