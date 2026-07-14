import json


def _cc(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "mindlas"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))


def test_install_statusline_sets_entry(tmp_path, monkeypatch):
    _cc(tmp_path, monkeypatch)
    from mindlas.vitals.install import install_statusline
    from mindlas.vitals.config import claude_settings_path
    assert install_statusline() == "installed"
    s = json.loads(claude_settings_path().read_text(encoding="utf-8"))
    assert s["statusLine"]["type"] == "command" and "statusline" in s["statusLine"]["command"]


def test_install_backs_up_and_uninstall_restores(tmp_path, monkeypatch):
    _cc(tmp_path, monkeypatch)
    from mindlas.vitals.install import install_statusline
    from mindlas.vitals.config import claude_settings_path
    original = {"type": "command", "command": "my-custom-line.sh"}
    claude_settings_path().parent.mkdir(parents=True)
    claude_settings_path().write_text(json.dumps({"statusLine": original, "other": 1}), encoding="utf-8")
    install_statusline()
    install_statusline()                          # idempotent: must NOT clobber the backup with ours
    install_statusline(uninstall=True)
    s = json.loads(claude_settings_path().read_text(encoding="utf-8"))
    assert s["statusLine"] == original and s["other"] == 1   # restored, other keys preserved


def test_uninstall_removes_when_no_backup(tmp_path, monkeypatch):
    _cc(tmp_path, monkeypatch)
    from mindlas.vitals.install import install_statusline
    from mindlas.vitals.config import claude_settings_path
    install_statusline()
    install_statusline(uninstall=True)
    s = json.loads(claude_settings_path().read_text(encoding="utf-8"))
    assert "statusLine" not in s


def test_uninstall_does_not_resurrect_abandoned_line(tmp_path, monkeypatch):
    _cc(tmp_path, monkeypatch)
    from mindlas.vitals.install import install_statusline
    from mindlas.vitals.config import claude_settings_path
    original = {"type": "command", "command": "my-A.sh"}
    claude_settings_path().parent.mkdir(parents=True)
    claude_settings_path().write_text(json.dumps({"statusLine": original}), encoding="utf-8")
    install_statusline()                          # backs up A, sets ours
    install_statusline(uninstall=True)            # restores A, consumes backup
    assert json.loads(claude_settings_path().read_text(encoding="utf-8"))["statusLine"] == original
    # user abandons the restored line, then re-installs and uninstalls
    claude_settings_path().write_text(json.dumps({}), encoding="utf-8")
    install_statusline()                          # no existing -> nothing to back up
    install_statusline(uninstall=True)            # ours, no backup -> remove; must NOT resurrect A
    assert "statusLine" not in json.loads(claude_settings_path().read_text(encoding="utf-8"))


def test_double_uninstall_is_idempotent(tmp_path, monkeypatch):
    _cc(tmp_path, monkeypatch)
    from mindlas.vitals.install import install_statusline
    from mindlas.vitals.config import claude_settings_path
    original = {"type": "command", "command": "my-A.sh"}
    claude_settings_path().parent.mkdir(parents=True)
    claude_settings_path().write_text(json.dumps({"statusLine": original}), encoding="utf-8")
    install_statusline()
    install_statusline(uninstall=True)
    install_statusline(uninstall=True)            # second uninstall: current isn't ours -> leave it
    assert json.loads(claude_settings_path().read_text(encoding="utf-8"))["statusLine"] == original


def test_install_rejects_non_object_settings(tmp_path, monkeypatch):
    _cc(tmp_path, monkeypatch)
    import pytest
    from mindlas.vitals.install import install_statusline
    from mindlas.vitals.config import claude_settings_path
    claude_settings_path().parent.mkdir(parents=True)
    claude_settings_path().write_text("[1,2,3]", encoding="utf-8")   # valid JSON, not an object
    with pytest.raises(ValueError):
        install_statusline()


def test_resolve_base_prefers_absolute_exe(tmp_path, monkeypatch):
    exe = tmp_path / "mindlas.exe"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr("sys.argv", [str(exe)])
    from mindlas.vitals.install import resolve_base
    base = resolve_base()
    assert base == str(exe.resolve()).replace("\\", "/") and "\\" not in base


def test_cmd_is_ours_truth_table():
    from mindlas.vitals.install import _cmd_is_ours
    assert _cmd_is_ours("D:/x/.venv/Scripts/mindlas hook PostToolUse") is True
    assert _cmd_is_ours('"C:/My Tools/mindlas.exe" hook Stop') is True
    assert _cmd_is_ours("D:/x/.venv/Scripts/mindlas statusline") is False   # no "hook"
    assert _cmd_is_ours('node "C:/Users/m/.claude/hooks/gsd-context-monitor.js"') is False
    assert _cmd_is_ours(None) is False
    assert _cmd_is_ours(123) is False


def test_hook_groups_emit_seven_events_with_quoted_abs_path():
    import mindlas.vitals.install as inst
    groups = inst._hook_groups("C:/My Tools/mindlas.exe")
    assert set(groups) == {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
                           "PostToolUseFailure", "Stop", "PreCompact", "PostCompact"}
    # matcher only on the wildcard events; quoted because the path has a space
    assert groups["PostToolUse"]["matcher"] == "*"
    assert "matcher" not in groups["SessionStart"]
    cmd = groups["Stop"]["hooks"][0]["command"]
    assert cmd == '"C:/My Tools/mindlas.exe" hook Stop'
    assert groups["Stop"]["hooks"][0]["timeout"] == 15


def test_hook_specs_match_bundled_template():
    import json, importlib.resources as res
    from mindlas.vitals.install import _HOOK_SPECS
    text = (res.files("mindlas.vitals") / "settings.hooks.json").read_text(encoding="utf-8")
    tmpl = json.loads(text)["hooks"]
    bundled = {ev: (groups[0].get("matcher"), groups[0]["hooks"][0].get("timeout"))
               for ev, groups in tmpl.items()}
    canonical = {ev: (matcher, timeout) for ev, matcher, timeout in _HOOK_SPECS}
    assert canonical == bundled


def test_strip_removes_only_ours_and_prunes():
    from mindlas.vitals.install import _strip_our_hooks
    gsd = {"matcher": "Bash", "hooks": [{"type": "command", "command": 'node "x/gsd.js"'}]}
    ours = {"hooks": [{"type": "command", "command": "x/mindlas hook Stop", "timeout": 15}]}
    settings = {"hooks": {"PostToolUse": [gsd, ours], "Stop": [ours]}, "other": 1}
    _strip_our_hooks(settings)
    assert settings["hooks"]["PostToolUse"] == [gsd]   # ours dropped, GSD kept
    assert "Stop" not in settings["hooks"]             # event emptied -> pruned
    assert settings["other"] == 1


def test_strip_drops_hooks_key_when_emptied():
    from mindlas.vitals.install import _strip_our_hooks
    ours = {"hooks": [{"type": "command", "command": "x/mindlas hook SessionStart"}]}
    settings = {"hooks": {"SessionStart": [ours]}}
    _strip_our_hooks(settings)
    assert "hooks" not in settings


def test_strip_surgical_within_mixed_group():
    from mindlas.vitals.install import _strip_our_hooks
    mixed = {"hooks": [
        {"type": "command", "command": 'node "x/gsd.js"'},
        {"type": "command", "command": "x/mindlas hook PostToolUse", "timeout": 10},
    ]}
    settings = {"hooks": {"PostToolUse": [mixed]}}
    _strip_our_hooks(settings)
    kept = settings["hooks"]["PostToolUse"][0]["hooks"]
    assert kept == [{"type": "command", "command": 'node "x/gsd.js"'}]


def test_strip_tolerates_malformed_entries_and_missing_hooks():
    from mindlas.vitals.install import _strip_our_hooks
    settings = {"hooks": {
        "SessionStart": [{"matcher": "*"},                       # group with no "hooks" key
                         {"hooks": ["not-a-dict", {"no": "command"}]}],
    }}
    _strip_our_hooks(settings)                                   # must not raise
    # nothing of ours was present, so BOTH groups survive (incl. the no-"hooks"-key group)
    assert len(settings["hooks"]["SessionStart"]) == 2


def test_strip_noop_without_hooks_key():
    from mindlas.vitals.install import _strip_our_hooks
    settings = {"other": 1}
    _strip_our_hooks(settings)
    assert settings == {"other": 1}


def test_strip_preserves_foreign_only_and_empty_groups():
    from mindlas.vitals.install import _strip_our_hooks
    settings = {"hooks": {
        "PostToolUse": [{"hooks": [{"type": "command", "command": 'node "x/gsd.js"'}]}],
        "Stop": [{"hooks": []}],            # already empty: we didn't empty it -> keep
    }}
    _strip_our_hooks(settings)
    assert settings["hooks"]["PostToolUse"][0]["hooks"] == [
        {"type": "command", "command": 'node "x/gsd.js"'}]
    assert settings["hooks"]["Stop"] == [{"hooks": []}]   # preserved untouched


def _events(settings):
    return set(settings.get("hooks", {}))


def test_install_hooks_writes_seven_events_with_abs_path(tmp_path, monkeypatch):
    _cc(tmp_path, monkeypatch)
    from mindlas.vitals.install import install_hooks
    from mindlas.vitals.config import claude_settings_path
    assert install_hooks() == "installed"
    s = json.loads(claude_settings_path().read_text(encoding="utf-8"))
    assert _events(s) == {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
                          "PostToolUseFailure", "Stop", "PreCompact", "PostCompact"}
    cmd = s["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
    assert "hook PostToolUse" in cmd and "mindlas" in cmd.lower()


def test_install_hooks_coexists_with_existing(tmp_path, monkeypatch):
    _cc(tmp_path, monkeypatch)
    from mindlas.vitals.install import install_hooks
    from mindlas.vitals.config import claude_settings_path
    gsd = {"hooks": [{"type": "command", "command": 'node "x/gsd.js"'}]}
    claude_settings_path().parent.mkdir(parents=True)
    claude_settings_path().write_text(json.dumps({"hooks": {"SessionStart": [gsd]}}), encoding="utf-8")
    install_hooks()
    s = json.loads(claude_settings_path().read_text(encoding="utf-8"))
    cmds = [e["command"] for g in s["hooks"]["SessionStart"] for e in g["hooks"]]
    assert 'node "x/gsd.js"' in cmds                         # GSD kept
    assert any("mindlas" in c.lower() and "hook" in c for c in cmds)   # ours added


def test_install_hooks_is_idempotent(tmp_path, monkeypatch):
    _cc(tmp_path, monkeypatch)
    from mindlas.vitals.install import install_hooks
    from mindlas.vitals.config import claude_settings_path
    install_hooks()
    install_hooks()
    s = json.loads(claude_settings_path().read_text(encoding="utf-8"))
    for ev in ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "PreCompact", "PostCompact"):
        ours = [g for g in s["hooks"][ev]
                if any("mindlas" in e["command"].lower() and "hook" in e["command"]
                       for e in g["hooks"])]
        assert len(ours) == 1                                # exactly one Mindlas group per event


def test_install_hooks_self_heals_stale_path(tmp_path, monkeypatch):
    _cc(tmp_path, monkeypatch)
    from mindlas.vitals.install import install_hooks
    from mindlas.vitals.config import claude_settings_path
    stale = {"hooks": [{"type": "command", "command": "OLD/path/mindlas hook Stop", "timeout": 15}]}
    claude_settings_path().parent.mkdir(parents=True)
    claude_settings_path().write_text(json.dumps({"hooks": {"Stop": [stale]}}), encoding="utf-8")
    install_hooks()
    s = json.loads(claude_settings_path().read_text(encoding="utf-8"))
    cmds = [e["command"] for g in s["hooks"]["Stop"] for e in g["hooks"]]
    assert len(cmds) == 1 and "OLD/path" not in cmds[0]      # stale replaced, no duplicate


def test_install_hooks_creates_missing_settings(tmp_path, monkeypatch):
    _cc(tmp_path, monkeypatch)
    from mindlas.vitals.install import install_hooks
    from mindlas.vitals.config import claude_settings_path
    assert not claude_settings_path().exists()
    install_hooks()
    assert claude_settings_path().exists()


def test_install_hooks_rejects_non_object_settings(tmp_path, monkeypatch):
    _cc(tmp_path, monkeypatch)
    import pytest
    from mindlas.vitals.install import install_hooks
    from mindlas.vitals.config import claude_settings_path
    claude_settings_path().parent.mkdir(parents=True)
    claude_settings_path().write_text("[1,2,3]", encoding="utf-8")
    with pytest.raises(ValueError):
        install_hooks()


def test_uninstall_hooks_removes_ours_keeps_gsd(tmp_path, monkeypatch):
    _cc(tmp_path, monkeypatch)
    from mindlas.vitals.install import install_hooks
    from mindlas.vitals.config import claude_settings_path
    gsd = {"hooks": [{"type": "command", "command": 'node "x/gsd.js"'}]}
    claude_settings_path().parent.mkdir(parents=True)
    claude_settings_path().write_text(json.dumps({"hooks": {"SessionStart": [gsd]}}), encoding="utf-8")
    install_hooks()
    assert install_hooks(uninstall=True) == "uninstalled"
    s = json.loads(claude_settings_path().read_text(encoding="utf-8"))
    assert s["hooks"]["SessionStart"] == [gsd]               # GSD intact
    assert "PostToolUse" not in s["hooks"]                   # our solo events gone


def test_uninstall_hooks_clean_removes_hooks_key(tmp_path, monkeypatch):
    _cc(tmp_path, monkeypatch)
    from mindlas.vitals.install import install_hooks
    from mindlas.vitals.config import claude_settings_path
    install_hooks()
    install_hooks(uninstall=True)
    s = json.loads(claude_settings_path().read_text(encoding="utf-8"))
    assert "hooks" not in s          # key fully pruned, not just emptied


def test_install_hooks_rejects_non_dict_hooks(tmp_path, monkeypatch):
    _cc(tmp_path, monkeypatch)
    import pytest
    from mindlas.vitals.install import install_hooks
    from mindlas.vitals.config import claude_settings_path
    claude_settings_path().parent.mkdir(parents=True)
    claude_settings_path().write_text(json.dumps({"hooks": [1, 2, 3]}), encoding="utf-8")
    with pytest.raises(ValueError):
        install_hooks()                       # append path must not raise AttributeError


def test_install_hooks_rejects_non_list_event_value(tmp_path, monkeypatch):
    _cc(tmp_path, monkeypatch)
    import pytest
    from mindlas.vitals.install import install_hooks
    from mindlas.vitals.config import claude_settings_path
    claude_settings_path().parent.mkdir(parents=True)
    claude_settings_path().write_text(json.dumps({"hooks": {"Stop": "x"}}), encoding="utf-8")
    with pytest.raises(ValueError):
        install_hooks()
