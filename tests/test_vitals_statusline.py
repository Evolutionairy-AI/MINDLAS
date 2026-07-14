from mindlas.vitals import fixtures
from mindlas.vitals.render import render_statusline


_HEADER = {"model_label": "Opus 4.8 (1M context)", "project": "Machine_Mindprints", "pct": 22}


def test_statusline_header_reproduces_model_project_context():
    out = render_statusline(_HEADER, plain=True)
    assert "Opus 4.8 (1M context)" in out and "Machine_Mindprints" in out and "22%" in out
    assert out.splitlines() == [out]              # exactly one line


def test_statusline_null_pct_renders_placeholder():
    h = {"model_label": "Opus", "project": "p", "pct": None}
    out = render_statusline(h, plain=True)
    assert "··%" in out


import io
import json
import dataclasses
from mindlas.vitals.config import ledger_path
from mindlas.vitals.ledger import Ledger


def _seed(sid, events):
    led = Ledger(ledger_path(sid))
    for e in events:
        led.append(dataclasses.replace(e, session_id=sid))
    return led


_PAYLOAD = {
    "session_id": "s1",
    "model": {"display_name": "Opus", "id": "claude-opus-4-8"},
    "workspace": {"project_dir": "/x/Machine_Mindprints"},
    "context_window": {"used_percentage": 22, "context_window_size": 1000000},
}


def test_model_label_and_bare_name():
    from mindlas.vitals.statusline import model_label
    assert model_label(_PAYLOAD) == "Opus 4.8 (1M context)"
    assert model_label(_PAYLOAD, with_context=False) == "Opus 4.8"


def test_model_label_handles_date_suffixed_ids():
    from mindlas.vitals.statusline import model_label

    def lbl(name, mid):
        return model_label({"model": {"display_name": name, "id": mid}}, with_context=False)

    assert lbl("Opus", "claude-opus-4-8") == "Opus 4.8"
    assert lbl("Opus", "claude-opus-4-1-20250805") == "Opus 4.1"
    assert lbl("Sonnet", "claude-sonnet-4-20250514") == "Sonnet 4"      # date is NOT a minor
    out = lbl("Opus", "claude-opus-4-20250514")
    assert out == "Opus 4" and "2025" not in out                        # never emit a date
    # a display_name already carrying a version is not doubled
    assert lbl("Opus 4.5", "claude-opus-4-5") == "Opus 4.5"


def test_run_statusline_renders_reliability_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))   # isolate the project-tree reads
    _seed("s1", fixtures.context_rot_alert())
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_PAYLOAD)))
    from mindlas.vitals.statusline import run_statusline
    assert run_statusline() == 0
    out = capsys.readouterr().out
    assert "Machine_Mindprints" in out and "Rot" in out         # header + reliability band


def test_run_statusline_never_raises_on_garbage(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path))
    monkeypatch.setattr("sys.stdin", io.StringIO("not json at all"))
    from mindlas.vitals.statusline import run_statusline
    assert run_statusline() == 0                  # never non-zero -> never blanks the line


def test_run_statusline_survives_unreadable_stdin(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path))
    monkeypatch.setattr("sys.stdin", None)        # degenerate stdin must not raise / go non-zero
    from mindlas.vitals.statusline import run_statusline
    assert run_statusline() == 0


