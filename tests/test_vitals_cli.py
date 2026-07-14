import json

from mindlas.cli import main
from mindlas.vitals.config import ledger_path
from mindlas.vitals.ledger import Ledger
from mindlas.vitals import fixtures
from mindlas.vitals.events import event_to_json


def test_hook_subcommand_reads_stdin(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path))
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(
        json.dumps({"session_id": "s9", "source": "startup", "model": "m"})))
    assert main(["hook", "SessionStart"]) == 0
    assert Ledger(ledger_path("s9")).count_kind("session_start") == 1


import io as _io
import dataclasses as _dc


def test_statusline_render_reads_stdin(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path))
    p = ledger_path("s1"); p.parent.mkdir(parents=True)
    p.write_text("\n".join(event_to_json(_dc.replace(e, session_id="s1"))
                 for e in fixtures.context_rot_alert()), encoding="utf-8")
    payload = {"session_id": "s1", "model": {"display_name": "Opus", "id": "claude-opus-4-8"},
               "workspace": {"project_dir": "/x/Machine_Mindprints"},
               "context_window": {"used_percentage": 22, "context_window_size": 1000000}}
    monkeypatch.setattr("sys.stdin", _io.StringIO(json.dumps(payload)))
    assert main(["statusline"]) == 0
    assert "Machine_Mindprints" in capsys.readouterr().out


def test_install_statusline_via_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "m"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    assert main(["install-statusline"]) == 0
    assert "installed" in capsys.readouterr().out.lower()
    from mindlas.vitals.config import claude_settings_path
    assert claude_settings_path().exists()


def test_install_statusline_corrupt_settings_reports_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "m"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    from mindlas.vitals.config import claude_settings_path
    claude_settings_path().parent.mkdir(parents=True)
    claude_settings_path().write_text("{ not json", encoding="utf-8")
    assert main(["install-statusline"]) == 1     # non-zero, clear message, no crash
    assert "could not" in capsys.readouterr().out.lower()


def test_install_hooks_via_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "mindlas"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    assert main(["install-hooks"]) == 0
    assert "installed" in capsys.readouterr().out.lower()
    from mindlas.vitals.config import claude_settings_path
    assert claude_settings_path().exists()
    assert main(["install-hooks", "--uninstall"]) == 0
    assert "uninstalled" in capsys.readouterr().out.lower()


def test_install_hooks_corrupt_settings_reports_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "mindlas"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    from mindlas.vitals.config import claude_settings_path
    claude_settings_path().parent.mkdir(parents=True)
    claude_settings_path().write_text("{ not json", encoding="utf-8")
    assert main(["install-hooks"]) == 1
    assert "could not" in capsys.readouterr().out.lower()


def test_install_hooks_malformed_hooks_reports_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "mindlas"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    from mindlas.vitals.config import claude_settings_path
    claude_settings_path().parent.mkdir(parents=True)
    claude_settings_path().write_text(json.dumps({"hooks": [1, 2, 3]}), encoding="utf-8")
    assert main(["install-hooks"]) == 1
    assert "could not" in capsys.readouterr().out.lower()
