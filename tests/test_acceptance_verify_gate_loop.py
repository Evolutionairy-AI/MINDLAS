import json
from mindlas.actions.verify_gate import VerifyGate
from mindlas.runtime.verify_types import VerifyContext
from mindlas.vitals.events import Event
from _verify_helpers import init_git_repo, write


def _edit(turn, target):
    return Event(session_id="s", turn=turn, ts="t", kind="tool_call", cls="edit",
                 target=target, lines_added=10, lines_deleted=0)


def _ctx(tmp_path):
    return VerifyContext(events=(_edit(1, "src/app.py"),), session_id="s", now_turn=5,
                         project_root=tmp_path)


def test_acceptance_pass_path(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))        # A9: env root == ctx root
    init_git_repo(tmp_path)
    write(tmp_path, "pyproject.toml", "[tool.x]\n")
    write(tmp_path, "src/app.py", "a = 1\nb = 2\n")                  # clean -> AST passes

    pv = VerifyGate().preview(_ctx(tmp_path))
    assert pv.before >= 25 and pv.status == "planned"               # debt elevated, plan exists
    vr = VerifyGate().apply(_ctx(tmp_path), now="20260630T130000")
    assert vr.status == "pass" and vr.after < vr.before

    vd = tmp_path / ".mindlas" / "verification" / "sessions" / "s"
    assert (vd / "latest_verifier_result.json").exists()
    assert list((vd / "results").glob("*_verify_gate.json"))
    scj = json.loads((tmp_path / ".mindlas" / "reports" / "latest_scorecard.json").read_text())
    assert scj["features"]["verification_debt"]["last_result"] == "pass"
    assert scj["rails_export_ready"] is True                        # labels present
    # A1/A2: no byproducts left in the repo
    assert list(tmp_path.rglob("*.pyc")) == [] and not (tmp_path / ".pytest_cache").exists()


def test_acceptance_fail_path(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    init_git_repo(tmp_path)
    write(tmp_path, "pyproject.toml", "[tool.x]\n")
    write(tmp_path, "src/app.py", "def f(:\n")                      # syntax error -> AST fails

    vr = VerifyGate().apply(_ctx(tmp_path), now="20260630T130100")
    assert vr.status == "fail" and vr.after >= 95
    md = (tmp_path / ".mindlas" / "reports" / "latest_scorecard.md").read_text()
    assert "Verify Gate: VERIFY" in md and "result fail" in md
    assert "(modeled)" not in md.split("## Corrections Applied")[1]  # verify line not modeled


def test_acceptance_source_files_unchanged(tmp_path, monkeypatch):
    # amendment 8: Verify Gate may write under .mindlas/, but it must NEVER modify source files.
    import hashlib
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    init_git_repo(tmp_path)
    write(tmp_path, "pyproject.toml", "[tool.x]\n")
    write(tmp_path, "src/app.py", "a = 1\nb = 2\n")

    def _src_hashes():
        # every source file outside Mindlas's own state dir, by content
        return {p.relative_to(tmp_path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in tmp_path.rglob("*")
                if p.is_file() and ".mindlas" not in p.relative_to(tmp_path).parts
                and ".git" not in p.relative_to(tmp_path).parts}

    before = _src_hashes()
    VerifyGate().apply(_ctx(tmp_path), now="20260630T130200")
    assert _src_hashes() == before          # source tree byte-for-byte identical
