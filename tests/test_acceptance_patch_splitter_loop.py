import hashlib
import json
from mindlas.actions.patch_splitter import PatchSplitter
from mindlas.runtime.split_types import SplitContext
from mindlas.runtime.blast_state import build_change_blast_signals
from mindlas.runtime import git_helpers
from mindlas.features.blast_radius import score_change_blast_radius
from _verify_helpers import init_git_repo, write


def _fixture(tmp_path):
    # D3 pinned fixture: 4 source dirs + config + docs + a related test + an untracked new source,
    # >= 450 changed lines. Reliably ALERT (~90-100).
    init_git_repo(tmp_path)
    write(tmp_path, "pyproject.toml", "[tool.x]\nname='p'\n" * 6)          # config, high-centrality
    write(tmp_path, "src/pkg_a/alpha.py", "a = 1\n" * 130)                 # dir src/pkg_a
    write(tmp_path, "src/pkg_b/beta.py", "b = 2\n" * 130)                  # dir src/pkg_b
    write(tmp_path, "src/pkg_c/gamma.py", "c = 3\n" * 130)                 # dir src/pkg_c
    write(tmp_path, "docs/guide.md", "# guide\n" * 40)                     # dir docs
    write(tmp_path, "tests/test_alpha.py", "def test_a():\n    assert True\n" * 12)   # dir tests
    write(tmp_path, "src/pkg_a/delta.py", "d = 4\n" * 20)                  # untracked new source


def _ctx(tmp_path):
    return SplitContext(events=(), session_id="s", now_turn=6, project_root=tmp_path)


def _src_hashes(tmp_path):
    return {p.relative_to(tmp_path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in tmp_path.rglob("*")
            if p.is_file() and ".mindlas" not in p.relative_to(tmp_path).parts
            and ".git" not in p.relative_to(tmp_path).parts}


def test_acceptance_patch_splitter_loop(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    _fixture(tmp_path)

    # 3. BLAST is ALERT
    sig = build_change_blast_signals([], session_id="s", project_root=tmp_path)
    assert score_change_blast_radius(sig) >= 70                         # ALERT band

    # 4-5. preview plans >= 2 bundles and writes nothing
    pv = PatchSplitter().preview(_ctx(tmp_path))
    assert len(pv.plan.bundles) >= 2
    assert not (tmp_path / ".mindlas" / "splits").exists()

    # 7-8. source + diff hash stable across apply
    src_before = _src_hashes(tmp_path)
    diff_before = git_helpers.diff_hash(tmp_path, git_helpers.untracked(tmp_path))

    # 6. apply writes the manifest + bundles
    r = PatchSplitter().apply(_ctx(tmp_path), now="20260630T140000")
    assert r.applied is True and r.planned_after < r.before
    run = tmp_path / ".mindlas" / "splits" / "sessions" / "s" / r.split_id
    assert (run / "split_manifest.json").exists()
    assert (tmp_path / ".mindlas" / "splits" / "sessions" / "s" / "latest_split_manifest.json").exists()
    # every changed file appears in exactly one bundle
    manifest = json.loads((run / "split_manifest.json").read_text())
    covered = [f for b in manifest["bundles"] for f in b["files"]]
    assert sorted(covered) == sorted(sig.changed_files) and len(covered) == len(set(covered))
    # untracked file represented in a bundle artifact
    assert list(run.glob("bundle_*/untracked/src/pkg_a/delta.py"))

    assert _src_hashes(tmp_path) == src_before                         # source unchanged
    assert git_helpers.diff_hash(tmp_path, git_helpers.untracked(tmp_path)) == diff_before  # C6

    # 9. correction appended (planned, partitionable)
    rec = json.loads((tmp_path / ".mindlas" / "reports"
                      / "corrections.jsonl").read_text().splitlines()[-1])
    assert rec["type"] == "patch_splitter" and rec["planned_after_blast"] == r.planned_after

    # 10-11. scorecard row + RAILS present; correction line says planned, not modeled
    md = (tmp_path / ".mindlas" / "reports" / "latest_scorecard.md").read_text()
    assert "Change Blast Radius" in md and "planned" in md
    assert "(modeled)" not in md.split("## Corrections Applied")[1]
    scj = json.loads((tmp_path / ".mindlas" / "reports" / "latest_scorecard.json").read_text())
    assert scj["features"]["change_blast_radius"]["final_status"] == "planned"
    assert scj["rails_export_ready"] is True

    # 12. output recommends Verify Gate next
    assert rec["rails_labels"]["verify_gate_recommended_after_split"] is True
