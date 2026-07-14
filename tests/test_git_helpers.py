import subprocess

from _verify_helpers import init_git_repo, write
from mindlas.features.verification_debt import VerificationDebtScorer, score_verification_debt
from mindlas.runtime import git_helpers
from mindlas.runtime.verification_state import build_verification_debt_signals


def _git(path, *args):
    subprocess.run(["git", *args], cwd=str(path), capture_output=True, text=True, check=True)


def test_is_mindlas():
    assert git_helpers.is_mindlas(".mindlas/splits/x.json") is True
    assert git_helpers.is_mindlas(".mindlas\\reports\\c.jsonl") is True   # windows sep normalized
    assert git_helpers.is_mindlas("src/app.py") is False


def test_run_git_returns_none_outside_repo(tmp_path):
    assert git_helpers._run_git(["rev-parse", "--is-inside-work-tree"], tmp_path) is None


def test_numstat_per_file_and_total_excludes_mindlas(tmp_path):
    init_git_repo(tmp_path)
    write(tmp_path, "src/a.py", "x = 1\nx = 2\n")              # untracked: not in tracked numstat
    write(tmp_path, "baseline.txt", "seed\nmore\nthird\n")     # tracked edit: +2 lines
    write(tmp_path, ".mindlas/reports/c.jsonl", '{"a":1}\n')   # must be excluded
    per_file, total = git_helpers.numstat([], tmp_path)
    paths = [p for (p, _a, _d) in per_file]
    assert "baseline.txt" in paths
    assert not any(p.replace("\\", "/").startswith(".mindlas/") for p in paths)
    assert total >= 2


def test_name_status_detects_delete_and_rename_with_M(tmp_path):
    init_git_repo(tmp_path)
    write(tmp_path, "keep.py", "a = 1\n")
    write(tmp_path, "old_name.py", "b = 2\nb = 3\nb = 4\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "two")
    _git(tmp_path, "rm", "-q", "keep.py")                      # staged delete
    _git(tmp_path, "mv", "old_name.py", "new_name.py")         # staged rename
    rows = git_helpers.name_status(["-M", "--cached"], tmp_path)
    codes = {path: code for code, path in rows}
    assert codes.get("keep.py", "").startswith("D")
    assert codes.get("new_name.py", "").startswith("R")        # -M makes rename deterministic


def test_untracked_excludes_mindlas(tmp_path):
    init_git_repo(tmp_path)
    write(tmp_path, "src/new.py", "a = 1\n")
    write(tmp_path, ".mindlas/splits/m.json", "{}\n")
    assert "src/new.py" in git_helpers.untracked(tmp_path)
    assert not any(u.replace("\\", "/").startswith(".mindlas/")
                   for u in git_helpers.untracked(tmp_path))


def test_diff_hash_excludes_mindlas_and_tracks_untracked(tmp_path):
    init_git_repo(tmp_path)
    write(tmp_path, "src/new.py", "a = 1\n")
    h1 = git_helpers.diff_hash(tmp_path, git_helpers.untracked(tmp_path))
    # a .mindlas-only change must NOT move the hash (belt-and-suspenders)
    write(tmp_path, ".mindlas/splits/m.json", '{"x": 1}\n')
    h2 = git_helpers.diff_hash(tmp_path, git_helpers.untracked(tmp_path))
    assert h1 == h2 and h1.startswith("sha256:")
    # a real source change DOES move the hash
    write(tmp_path, "src/new.py", "a = 2\n")
    h3 = git_helpers.diff_hash(tmp_path, git_helpers.untracked(tmp_path))
    assert h3 != h1


def test_diff_hash_excludes_tracked_and_staged_mindlas(tmp_path):
    # Pathspec proof: a TRACKED/STAGED .mindlas/ change must not move the hash either, not
    # just an untracked one. The untracked filter cannot mask this — only the
    # `:(exclude).mindlas/` pathspec on BOTH the `git diff` and `git diff --cached` calls can.
    init_git_repo(tmp_path)
    write(tmp_path, "src/new.py", "a = 1\n")
    base = git_helpers.diff_hash(tmp_path, git_helpers.untracked(tmp_path))
    # force a .mindlas/ file into TRACKED history (bypass .gitignore), then commit it
    write(tmp_path, ".mindlas/state.json", '{"v": 1}\n')
    _git(tmp_path, "add", "-f", ".mindlas/state.json")
    _git(tmp_path, "commit", "-q", "-m", "track mindlas state")
    # unstaged tracked change -> only `git diff -- :(exclude).mindlas/` keeps the hash stable
    write(tmp_path, ".mindlas/state.json", '{"v": 2}\n')
    assert git_helpers.diff_hash(tmp_path, git_helpers.untracked(tmp_path)) == base
    # staged tracked change -> only `git diff --cached -- :(exclude).mindlas/` keeps it stable
    _git(tmp_path, "add", "-f", ".mindlas/state.json")
    assert git_helpers.diff_hash(tmp_path, git_helpers.untracked(tmp_path)) == base


def test_file_diff_returns_patch_text_for_one_path(tmp_path):
    init_git_repo(tmp_path)
    write(tmp_path, "baseline.txt", "seed\nmore\n")
    out = git_helpers.file_diff("baseline.txt", tmp_path)
    assert "baseline.txt" in out and "+more" in out


def test_git_helpers_promotion_does_not_perturb_verification_debt(tmp_path):
    """REGRESSION GUARD — must stay green after the git-primitives repoint. Promoting the git
    primitives and adding the `.mindlas` exclude must NOT move Verification Debt's PUBLIC score OR
    state. Deterministic VERIFY-alert fixture: 5 untracked production files (80 changed lines
    each) + a config file, no tests, no stored verifier result. Hand-computed score:
    file min(20, 6*4)=20 + lines min(15, 410/20)=15 + turns 0 + status `unknown` 12 +
    production_without_tests 15 + config_mixed 10 = 72 -> band_4(72, 25/50/70) = ALERT."""
    init_git_repo(tmp_path)
    body = "x = 1\n" * 80                                   # 80 changed lines per file
    for i in range(5):
        write(tmp_path, f"src/app/mod{i}.py", body)         # production, no matching tests
    write(tmp_path, "pyproject.toml", "[tool.x]\n" * 10)    # config -> config_mixed_with_source

    sig = build_verification_debt_signals([], session_id="s", project_root=tmp_path)
    # signal preconditions the literals depend on (document WHY the score is what it is)
    assert sig.changed_file_count == 6
    assert sig.production_without_tests is True
    assert sig.config_mixed_with_source is True
    assert sig.evidence_is_fresh is False
    assert sig.last_verifier_status == "unknown"

    # PUBLIC score AND state literals — locked, must not drift after the repoint
    score = score_verification_debt(sig)
    reading = VerificationDebtScorer().read(sig)
    assert score == 72
    assert reading.score == 72
    assert reading.state == "ALERT"
