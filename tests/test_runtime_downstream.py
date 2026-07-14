import shutil
import pytest
from mindlas.runtime.downstream import run_downstream_check

_HAS_RUFF = shutil.which("ruff") is not None


def test_no_files_is_skipped(tmp_path):
    res = run_downstream_check([], tmp_path)
    assert res.ran is False
    assert res.result == "skipped"
    assert res.findings == 0


def test_missing_file_is_skipped(tmp_path):
    res = run_downstream_check(["does_not_exist.py"], tmp_path)
    assert res.ran is False
    assert res.result == "skipped"


def test_ruff_unavailable_is_skipped(tmp_path, monkeypatch):
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr("mindlas.runtime.downstream.shutil.which", lambda _: None)
    res = run_downstream_check(["m.py"], tmp_path)
    assert res.ran is False
    assert res.result == "skipped"
    assert res.note == "ruff unavailable"


@pytest.mark.skipif(not _HAS_RUFF, reason="ruff not installed")
def test_clean_file_passes(tmp_path):
    (tmp_path / "clean.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    res = run_downstream_check(["clean.py"], tmp_path)
    assert res.ran is True
    assert res.result == "pass"
    assert res.findings == 0


@pytest.mark.skipif(not _HAS_RUFF, reason="ruff not installed")
def test_broken_file_fails(tmp_path):
    (tmp_path / "bad.py").write_text("def f():\n    return undefined_name\n", encoding="utf-8")
    res = run_downstream_check(["bad.py"], tmp_path)
    assert res.ran is True
    assert res.result == "fail"
    assert res.findings >= 1


def test_to_dict_has_expected_keys(tmp_path):
    d = run_downstream_check([], tmp_path).to_dict()
    assert set(d) == {"ran", "tool", "target", "findings", "result", "note"}
    assert isinstance(d["target"], list)
    assert d["result"] == "skipped"


@pytest.mark.skipif(not _HAS_RUFF, reason="ruff not installed")
def test_demo_sample_passes():
    from mindlas.runtime.downstream import demo_sample_target, run_downstream_check
    cwd, files = demo_sample_target()
    res = run_downstream_check(files, cwd)
    assert res.result == "pass"
    assert res.findings == 0
