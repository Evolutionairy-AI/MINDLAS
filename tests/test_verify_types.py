from mindlas.runtime.verify_types import VerifyContext, VerifyResult
from mindlas.runtime.verify_exec import VerifyCommandResult


def test_verify_context_defaults():
    ctx = VerifyContext(events=(), session_id="s", now_turn=4)
    assert ctx.project_root is None and ctx.allow_full_suite is False and ctx.max_seconds == 120


def test_verify_result_to_dict_is_partitionable_verify_record():
    r = VerifyResult(applied=True, before=82, after=18, status="pass", coverage="targeted",
                     commands=(VerifyCommandResult("c", ("x",), 0, "pass", "", "", 5),),
                     changed_files_covered=("a.py",), diff_hash="sha256:z",
                     result_path="/tmp/r.json", rails_labels={"k": "v"}, human_decision=None)
    d = r.to_dict()
    assert list(d)[0] == "type" and d["type"] == "verify_gate"   # first key -> partitionable
    assert d["before"] == 82 and d["after"] == 18                # before+after present
    assert d["status"] == "pass" and d["coverage"] == "targeted"
    assert d["commands_run"] == 1 and d["rails_labels"] == {"k": "v"}
