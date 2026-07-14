from mindlas.runtime.verify_plan import VerifyCommand, detect_project_stack
from _verify_helpers import write


def test_verify_command_defaults():
    c = VerifyCommand(label="x", command=("a", "b"), reason="r", coverage="partial")
    assert c.kind == "subprocess" and c.timeout_seconds == 120


def test_detect_python_stack(tmp_path):
    write(tmp_path, "pyproject.toml", "[tool.x]\n")
    assert detect_project_stack(tmp_path) == ("python",)


def test_detect_unknown_stack(tmp_path):
    assert detect_project_stack(tmp_path) == ("unknown",)


import sys
from mindlas.runtime.verify_plan import plan_verify_commands, _AST_SENTINEL
from mindlas.runtime.verification_state import VerificationDebtSignals


def _sig(**kw):
    base = dict(session_id="s", changed_files=(), changed_file_count=0, changed_lines=0,
                production_files_changed=(), test_files_changed=(), config_files_changed=(),
                production_without_tests=False, config_mixed_with_source=False,
                turns_since_last_pass=0, last_verifier_status="unknown", last_verifier_turn=None,
                unresolved_failures=0, completion_claim_without_evidence=False,
                verification_coverage="none", evidence_is_fresh=False, current_diff_hash="sha256:x")
    base.update(kw)
    return VerificationDebtSignals(**base)


def test_changed_py_plans_in_process_ast_first(tmp_path):
    write(tmp_path, "pyproject.toml", "[tool.x]\n")
    write(tmp_path, "src/app.py", "x = 1\n")
    cmds = plan_verify_commands(_sig(changed_files=("src/app.py",),
                                     production_files_changed=("src/app.py",)),
                                project_root=tmp_path)
    l1 = cmds[0]
    assert l1.kind == "in_process" and l1.command[0] == _AST_SENTINEL
    assert "src/app.py" in l1.command and l1.coverage == "partial"


def test_changed_test_file_plans_targeted_pytest(tmp_path):
    write(tmp_path, "pyproject.toml", "[tool.x]\n")
    write(tmp_path, "tests/test_app.py", "def test_x():\n    assert True\n")
    cmds = plan_verify_commands(_sig(changed_files=("tests/test_app.py",),
                                     test_files_changed=("tests/test_app.py",)),
                                project_root=tmp_path)
    pytest_cmds = [c for c in cmds if c.label == "pytest targeted"]
    # amendment 6: pytest is invoked as `sys.executable -m pytest`, never bare `pytest`.
    assert pytest_cmds and pytest_cmds[0].command[:5] == (
        sys.executable, "-m", "pytest", "-p", "no:cacheprovider")
    assert "tests/test_app.py" in pytest_cmds[0].command and pytest_cmds[0].coverage == "targeted"


def test_changed_py_without_config_plans_ast_only(tmp_path):
    # amendment 5: a changed .py file with NO Python project config still plans the L1 AST check
    # (it needs no config). Config-gated tiers (ruff/pytest) are absent.
    write(tmp_path, "src/app.py", "x = 1\n")                # no pyproject/setup.cfg/etc.
    cmds = plan_verify_commands(_sig(changed_files=("src/app.py",),
                                     production_files_changed=("src/app.py",)),
                                project_root=tmp_path)
    assert len(cmds) == 1
    assert cmds[0].kind == "in_process" and cmds[0].command[0] == _AST_SENTINEL


def test_infers_test_for_changed_production_module(tmp_path):
    write(tmp_path, "pyproject.toml", "[tool.x]\n")
    write(tmp_path, "src/pkg/widget.py", "y = 2\n")
    write(tmp_path, "tests/test_widget.py", "def test_y():\n    assert True\n")
    cmds = plan_verify_commands(_sig(changed_files=("src/pkg/widget.py",),
                                     production_files_changed=("src/pkg/widget.py",)),
                                project_root=tmp_path)
    pytest_cmds = [c for c in cmds if c.label == "pytest targeted"]
    assert pytest_cmds and "tests/test_widget.py" in pytest_cmds[0].command


def test_non_python_repo_plans_nothing(tmp_path):
    cmds = plan_verify_commands(_sig(changed_files=("a.js",)), project_root=tmp_path)
    assert cmds == ()


def test_full_suite_only_with_allow_flag(tmp_path):
    write(tmp_path, "pyproject.toml", "[tool.x]\n")
    write(tmp_path, "src/app.py", "x = 1\n")
    sig = _sig(changed_files=("src/app.py",), production_files_changed=("src/app.py",))
    assert not any(c.label == "pytest full" for c in
                   plan_verify_commands(sig, project_root=tmp_path))
    full = plan_verify_commands(sig, project_root=tmp_path, allow_full_suite=True)
    assert any(c.label == "pytest full" and c.coverage == "full" for c in full)


def test_ruff_planned_when_available_and_configured(tmp_path, monkeypatch):
    import mindlas.runtime.verify_plan as vp
    monkeypatch.setattr(vp.shutil, "which", lambda name: "/usr/bin/ruff" if name == "ruff" else None)
    write(tmp_path, "pyproject.toml", "[tool.x]\n")
    write(tmp_path, "src/app.py", "x = 1\n")
    cmds = plan_verify_commands(_sig(changed_files=("src/app.py",),
                                     production_files_changed=("src/app.py",)),
                                project_root=tmp_path)
    ruff = [c for c in cmds if c.label == "ruff check"]
    assert ruff and ruff[0].coverage == "partial"
    assert ruff[0].command[:2] == ("ruff", "check")
    assert "--select" in ruff[0].command and ",".join(vp._DEFECT_SELECT) in ruff[0].command
    assert "--no-cache" in ruff[0].command and "--force-exclude" in ruff[0].command


def test_ruff_not_planned_when_unavailable(tmp_path, monkeypatch):
    import mindlas.runtime.verify_plan as vp
    monkeypatch.setattr(vp.shutil, "which", lambda name: None)     # ruff absent
    write(tmp_path, "pyproject.toml", "[tool.x]\n")
    write(tmp_path, "src/app.py", "x = 1\n")
    cmds = plan_verify_commands(_sig(changed_files=("src/app.py",),
                                     production_files_changed=("src/app.py",)),
                                project_root=tmp_path)
    assert not any(c.label == "ruff check" for c in cmds)          # which-gated, skipped at plan time


from mindlas.runtime.verify_plan import is_safe_verify_command


def _cmd(*tokens):
    return VerifyCommand(label="x", command=tokens, reason="r", coverage="partial")


def test_safe_accepts_paths_that_contain_verb_substrings():
    # A6: substring matching would wrongly reject these paths; exact-token matching must accept.
    assert is_safe_verify_command(_cmd("ruff", "check", "src/address.py")) is True
    assert is_safe_verify_command(_cmd("pytest", "tests/test_add_user.py")) is True
    assert is_safe_verify_command(_cmd("pytest", "src/updater.py")) is True


def test_safe_rejects_exact_mutating_tokens_and_flags():
    assert is_safe_verify_command(_cmd("pip", "install", "x")) is False     # exact "install"
    assert is_safe_verify_command(_cmd("git", "commit", "-m", "x")) is False  # exact "commit"
    assert is_safe_verify_command(_cmd("ruff", "check", "--fix", "a.py")) is False  # mutating flag
    assert is_safe_verify_command(_cmd("black", "--write", "a.py")) is False
