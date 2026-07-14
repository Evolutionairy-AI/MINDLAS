import json
import subprocess
from mindlas.actions.patch_splitter import PatchSplitter
from mindlas.runtime.split_types import SplitContext, SplitPreview, SplitResult
from _verify_helpers import init_git_repo, write


def _git(path, *args):
    subprocess.run(["git", *args], cwd=str(path), capture_output=True, text=True, check=True)


def _broad_repo(tmp_path):
    init_git_repo(tmp_path)
    write(tmp_path, "pyproject.toml", "[tool.x]\n" * 5)
    write(tmp_path, "src/pkg_a/alpha.py", "a = 1\n" * 120)
    write(tmp_path, "src/pkg_b/beta.py", "b = 2\n" * 120)
    write(tmp_path, "docs/guide.md", "# guide\n" * 40)
    write(tmp_path, "tests/test_alpha.py", "def test_a():\n    assert True\n" * 15)
    write(tmp_path, "src/pkg_a/fresh.py", "c = 3\n" * 20)          # untracked new source


def _ctx(tmp_path):
    return SplitContext(events=(), session_id="s", now_turn=4, project_root=tmp_path)


def test_preview_plans_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    _broad_repo(tmp_path)
    pv = PatchSplitter().preview(_ctx(tmp_path))
    assert isinstance(pv, SplitPreview)
    assert pv.before >= 70 and len(pv.plan.bundles) >= 2       # broad -> ALERT, multi-bundle
    assert not (tmp_path / ".mindlas" / "splits").exists()     # preview writes nothing


def test_apply_writes_queue_and_planned_correction(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    _broad_repo(tmp_path)
    r = PatchSplitter().apply(_ctx(tmp_path), now="20260630T120000")
    assert isinstance(r, SplitResult) and r.applied is True and r.status in ("pass", "warning")
    assert r.planned_after < r.before
    run = tmp_path / ".mindlas" / "splits" / "sessions" / "s" / r.split_id
    assert (run / "split_manifest.json").exists()
    assert (tmp_path / ".mindlas" / "splits" / "sessions" / "s" / "latest_split_manifest.json").exists()
    # correction record: partitionable, PLANNED key, first key type
    rec = json.loads((tmp_path / ".mindlas" / "reports"
                      / "corrections.jsonl").read_text().splitlines()[-1])
    assert list(rec)[0] == "type" and rec["type"] == "patch_splitter"
    assert rec["planned_after_blast"] == r.planned_after and rec["before"] == r.before
    assert rec["bundle_count"] == len(r.bundles)
    assert rec["rails_labels"]["source_files_modified"] is False
    assert rec["rails_labels"]["verify_gate_recommended_after_split"] is True
    # scorecard is written with the BLAST row live
    sc = json.loads((tmp_path / ".mindlas" / "reports" / "latest_scorecard.json").read_text())
    assert sc["features"]["change_blast_radius"]["final_status"] == "planned"
    assert sc["rails_export_ready"] is True


def test_apply_leaves_source_unchanged(tmp_path, monkeypatch):
    import hashlib
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    _broad_repo(tmp_path)

    def _src_hashes():
        return {p.relative_to(tmp_path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in tmp_path.rglob("*")
                if p.is_file() and ".mindlas" not in p.relative_to(tmp_path).parts
                and ".git" not in p.relative_to(tmp_path).parts}

    before = _src_hashes()
    PatchSplitter().apply(_ctx(tmp_path), now="20260630T120100")
    assert _src_hashes() == before          # source tree byte-for-byte identical


def test_apply_suppresses_coherent_patch(tmp_path, monkeypatch):
    # A single small coherent file yields one bundle -> no reduction -> suppress.
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    init_git_repo(tmp_path)
    write(tmp_path, "src/only.py", "a = 1\n")
    r = PatchSplitter().apply(_ctx(tmp_path), now="20260630T120200")
    assert r.applied is False and r.status == "no_split"
    assert not (tmp_path / ".mindlas" / "splits").exists()          # no artifacts
    assert not (tmp_path / ".mindlas" / "reports" / "corrections.jsonl").exists()   # no correction


def test_apply_suppresses_multibundle_no_reduction(tmp_path, monkeypatch):
    # >= 2 bundles but planned_after_blast >= before -> STILL suppress.
    # Proves suppression is driven by "no reduction", not merely "one bundle". The plan is stubbed
    # so the (before == planned == 50, 2-bundle) shape is deterministic and scorer-independent.
    from mindlas.runtime.split_types import SplitBundle, SplitPlan
    from mindlas.actions import patch_splitter as ps_mod
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    init_git_repo(tmp_path)
    write(tmp_path, "src/a.py", "a = 1\n")
    write(tmp_path, "src/b.py", "b = 2\n")

    def _b(bid):
        return SplitBundle(bundle_id=bid, name=bid, reason="r", files=(f"{bid}.py",),
                           file_count=1, changed_lines=1, kinds=("production",), concerns=("x",),
                           coverage="complete", risk_score=50, has_tests=False,
                           has_production=True, warnings=())

    fake = SplitPlan(split_id="20260630T0_0000", session_id="s", created_at="x",
                     before_blast=50, planned_after_blast=50, original_diff_hash="sha256:0",
                     bundles=(_b("bundle_01_a"), _b("bundle_02_b")),
                     validation_status="pass", validation_messages=())
    monkeypatch.setattr(ps_mod, "score_change_blast_radius", lambda sig: 50)
    monkeypatch.setattr(ps_mod, "plan_patch_split", lambda *a, **k: fake)
    r = PatchSplitter().apply(_ctx(tmp_path), now="20260630T120300")
    assert len(fake.bundles) == 2                                   # multi-bundle...
    assert r.applied is False and r.status == "no_split"           # ...yet suppressed (no reduction)
    assert not (tmp_path / ".mindlas" / "splits").exists()
    assert not (tmp_path / ".mindlas" / "reports" / "corrections.jsonl").exists()


def test_apply_threads_root_to_all_write_surfaces(tmp_path, monkeypatch):
    # The explicit SplitContext.project_root drives splits AND corrections AND scorecard to the
    # SAME root — even when MINDLAS_PROJECT_ROOT points elsewhere. A regression that reverts any
    # write to env/cwd resolution lands it under env_root and fails the target-root assertions.
    env_root = tmp_path / "env_elsewhere"
    target = tmp_path / "target"
    env_root.mkdir()
    target.mkdir()
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(env_root))       # deliberately NOT the ctx root
    _broad_repo(target)
    ctx = SplitContext(events=(), session_id="s", now_turn=4, project_root=target)
    r = PatchSplitter().apply(ctx, now="20260630T120500")
    assert r.applied is True
    assert (target / ".mindlas" / "splits" / "sessions" / "s" / r.split_id / "split_manifest.json").exists()
    assert (target / ".mindlas" / "reports" / "corrections.jsonl").exists()
    assert (target / ".mindlas" / "reports" / "latest_scorecard.md").exists()
    assert (target / ".mindlas" / "reports" / "latest_scorecard.json").exists()
    assert not (env_root / ".mindlas" / "reports").exists()        # nothing leaked to the env root
    assert not (env_root / ".mindlas" / "splits").exists()


def test_apply_preserves_git_index(tmp_path, monkeypatch):
    # Source-read-only includes the git INDEX — apply must never stage or unstage anything.
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    _broad_repo(tmp_path)
    _git(tmp_path, "add", "pyproject.toml")                         # put something in the index

    def _cached():
        return subprocess.run(["git", "diff", "--cached"], cwd=str(tmp_path),
                              capture_output=True, text=True, check=True).stdout

    before = _cached()
    assert before.strip()                                          # guard: index is genuinely non-empty
    PatchSplitter().apply(_ctx(tmp_path), now="20260630T120400")
    assert _cached() == before                                     # staged index byte-identical
