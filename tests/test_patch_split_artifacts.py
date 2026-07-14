import subprocess
from mindlas.runtime.blast_state import build_file_changes, build_signals_from_changes
from mindlas.runtime.patch_split_plan import plan_patch_split
from mindlas.runtime.patch_split_artifacts import write_split_artifacts
from mindlas.runtime import git_helpers
from mindlas.features.blast_radius import score_change_blast_radius
from _verify_helpers import init_git_repo, write


def _git(path, *args):
    subprocess.run(["git", *args], cwd=str(path), capture_output=True, text=True, check=True)


def _plan_for(tmp_path):
    changes, dh = build_file_changes([], session_id="s", project_root=tmp_path)
    sig = build_signals_from_changes(changes, session_id="s", diff_hash=dh)
    untracked = git_helpers.untracked(tmp_path)
    return plan_patch_split(sig, changes, session_id="s", score_fn=score_change_blast_radius,
                            untracked=untracked, now="20260630T101500"), sig


def test_writes_manifests_patches_and_untracked_copies(tmp_path):
    init_git_repo(tmp_path)
    write(tmp_path, "src/pkg/tracked.py", "a = 1\n")        # committed then edited -> tracked diff
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    write(tmp_path, "src/pkg/tracked.py", "a = 1\nb = 2\n")
    write(tmp_path, "pyproject.toml", "[x]\n")              # untracked config
    write(tmp_path, "src/pkg/fresh.py", "c = 3\n")          # untracked new source
    plan, _sig = _plan_for(tmp_path)
    manifest_path, manifest = write_split_artifacts(plan, project_root=tmp_path)

    ssess = tmp_path / ".mindlas" / "splits" / "sessions" / "s"      # plan session_id="s"
    run = ssess / plan.split_id
    assert (run / "split_manifest.json").exists()
    assert (run / "split_summary.md").exists()
    assert (ssess / "latest_split_manifest.json").exists()
    assert manifest["type"] == "patch_splitter" and manifest["bundle_count"] == len(plan.bundles)
    # each bundle dir has a bundle_manifest.json + a tracked.patch (possibly empty)
    for b in plan.bundles:
        bdir = run / b.bundle_id
        assert (bdir / "bundle_manifest.json").exists()
        assert (bdir / "tracked.patch").exists()
    # the tracked edit lands in some tracked.patch as real diff text
    patches = "".join((p / "tracked.patch").read_text(encoding="utf-8")
                      for p in run.glob("bundle_*"))
    assert "tracked.py" in patches and "+b = 2" in patches
    # the untracked new source is copied verbatim under untracked/
    copied = list(run.glob("bundle_*/untracked/src/pkg/fresh.py"))
    assert copied and copied[0].read_text(encoding="utf-8") == "c = 3\n"


def test_artifact_write_leaves_source_and_hash_unchanged(tmp_path):
    import hashlib
    init_git_repo(tmp_path)
    write(tmp_path, "src/a.py", "a = 1\n")
    write(tmp_path, "src/b.py", "b = 2\n")

    def _src_hashes():
        return {p.relative_to(tmp_path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in tmp_path.rglob("*")
                if p.is_file() and ".mindlas" not in p.relative_to(tmp_path).parts
                and ".git" not in p.relative_to(tmp_path).parts}

    before_hashes = _src_hashes()
    before_diff = git_helpers.diff_hash(tmp_path, git_helpers.untracked(tmp_path))
    plan, _sig = _plan_for(tmp_path)
    write_split_artifacts(plan, project_root=tmp_path)
    assert _src_hashes() == before_hashes                  # source tree byte-for-byte identical
    assert git_helpers.diff_hash(tmp_path, git_helpers.untracked(tmp_path)) == before_diff  # C6


def test_untracked_copy_failure_raises_and_writes_no_pass_manifest(tmp_path, monkeypatch):
    # A3: a failed untracked-file copy must RAISE (not be silently swallowed) and must NOT leave a
    # finalized split_manifest.json that would claim complete coverage.
    import pytest
    import mindlas.runtime.patch_split_artifacts as art
    init_git_repo(tmp_path)
    write(tmp_path, "src/pkg/fresh.py", "c = 3\n")         # untracked -> will be copied
    plan, _sig = _plan_for(tmp_path)

    def _boom(src, dst):
        raise OSError("simulated copy failure")

    monkeypatch.setattr(art.shutil, "copyfile", _boom)
    with pytest.raises(OSError):
        write_split_artifacts(plan, project_root=tmp_path)
    run = tmp_path / ".mindlas" / "splits" / "sessions" / "s" / plan.split_id
    assert not (run / "split_manifest.json").exists()      # no finalized (pass) manifest
    assert not (tmp_path / ".mindlas" / "splits" / "sessions" / "s" / "latest_split_manifest.json").exists()
