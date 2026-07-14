import sys
from mindlas.runtime.verify_plan import VerifyCommand, _AST_SENTINEL
from mindlas.runtime.verify_exec import run_verify_command
from _verify_helpers import write


def _ast_cmd(*files):
    return VerifyCommand(label="ast", command=(_AST_SENTINEL, *files), reason="r",
                         coverage="partial", kind="in_process")


def test_ast_check_passes_on_valid_python(tmp_path):
    write(tmp_path, "ok.py", "a = 1\n")
    r = run_verify_command(_ast_cmd("ok.py"), project_root=tmp_path)
    assert r.status == "pass" and r.exit_code == 0


def test_ast_check_fails_on_syntax_error(tmp_path):
    write(tmp_path, "bad.py", "def f(:\n")
    r = run_verify_command(_ast_cmd("bad.py"), project_root=tmp_path)
    assert r.status == "fail" and "bad.py" in r.stderr_tail


def test_ast_check_writes_no_pycache(tmp_path):
    write(tmp_path, "ok.py", "a = 1\n")
    run_verify_command(_ast_cmd("ok.py"), project_root=tmp_path)
    assert not (tmp_path / "__pycache__").exists()
    assert list(tmp_path.rglob("*.pyc")) == []


def test_subprocess_passes_on_exit_zero(tmp_path):
    cmd = VerifyCommand(label="echo", command=(sys.executable, "-c", "pass"),
                        reason="r", coverage="partial")
    r = run_verify_command(cmd, project_root=tmp_path)
    assert r.status == "pass" and r.exit_code == 0


def test_subprocess_fails_on_nonzero(tmp_path):
    cmd = VerifyCommand(label="boom", command=(sys.executable, "-c", "import sys; sys.exit(3)"),
                        reason="r", coverage="partial")
    r = run_verify_command(cmd, project_root=tmp_path)
    assert r.status == "fail" and r.exit_code == 3


def test_subprocess_missing_tool_is_skipped(tmp_path):
    cmd = VerifyCommand(label="nope", command=("mindlas-no-such-tool-xyz",),
                        reason="r", coverage="partial")
    r = run_verify_command(cmd, project_root=tmp_path)
    assert r.status == "skipped"


def test_subprocess_timeout_is_recorded(tmp_path):
    cmd = VerifyCommand(label="slow", command=(sys.executable, "-c", "import time; time.sleep(5)"),
                        reason="r", coverage="partial", timeout_seconds=1)
    r = run_verify_command(cmd, project_root=tmp_path)
    assert r.status == "timeout" and "timed out" in r.stderr_tail


from mindlas.runtime.verify_exec import (
    run_verify_commands, summarize_verify_status, coverage_of, VerifyCommandResult,
)


def _res(label, status):
    return VerifyCommandResult(label, ("x",), 0 if status == "pass" else 1, status, "", "", 1)


def test_summarize_precedence():
    assert summarize_verify_status([_res("a", "pass"), _res("b", "fail")]) == "fail"
    assert summarize_verify_status([_res("a", "pass"), _res("b", "timeout")]) == "timeout"
    assert summarize_verify_status([_res("a", "skipped")]) == "skipped"
    assert summarize_verify_status([_res("a", "pass"), _res("b", "pass")]) == "pass"
    assert summarize_verify_status([]) == "skipped"


def test_run_commands_stops_on_first_failure(tmp_path):
    import sys
    from mindlas.runtime.verify_plan import VerifyCommand
    boom = VerifyCommand(label="boom", command=(sys.executable, "-c", "import sys; sys.exit(1)"),
                         reason="r", coverage="partial")
    after = VerifyCommand(label="after", command=(sys.executable, "-c", "pass"),
                          reason="r", coverage="targeted")
    results = run_verify_commands((boom, after), project_root=tmp_path)
    assert [r.label for r in results] == ["boom"]          # "after" never ran


def test_coverage_of_takes_highest_passed():
    from mindlas.runtime.verify_plan import VerifyCommand
    cmds = (VerifyCommand("a", ("x",), "r", "partial"),
            VerifyCommand("b", ("y",), "r", "targeted"))
    results = (_res("a", "pass"), _res("b", "pass"))
    assert coverage_of(cmds, results) == "targeted"
    # a failed higher tier does not grant its coverage
    assert coverage_of(cmds, (_res("a", "pass"), _res("b", "fail"))) == "partial"


def test_coverage_of_all_skipped_is_none():
    # amendment 4: nothing passed -> no coverage credit (e.g. AST skipped + pytest skipped).
    from mindlas.runtime.verify_plan import VerifyCommand
    cmds = (VerifyCommand("a", ("x",), "r", "partial"),
            VerifyCommand("b", ("y",), "r", "targeted"))
    assert coverage_of(cmds, (_res("a", "skipped"), _res("b", "skipped"))) == "none"
