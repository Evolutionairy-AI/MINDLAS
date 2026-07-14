import subprocess

from mindlas.runtime.blast_state import (
    ChangeBlastSignals,
    FileChange,
    build_change_blast_signals,
    build_file_changes,
    build_signals_from_changes,
    centrality,
    classify_path_blast,
    concern_key,
)
from mindlas.vitals.events import Event

from _verify_helpers import init_git_repo, write


def _git(path, *args):
    subprocess.run(["git", *args], cwd=str(path), capture_output=True, text=True, check=True)


def test_classify_path_blast_overrides_docs_and_github():
    # inherited from the shared classifier
    assert classify_path_blast("src/mindlas/cli.py") == "production"
    assert classify_path_blast("tests/test_cli.py") == "test"
    assert classify_path_blast("pyproject.toml") == "config"
    assert classify_path_blast("src/pkg/foo_test.py") == "test"
    # BLAST-only overrides
    assert classify_path_blast("README.md") == "docs"            # M2 would say neutral
    assert classify_path_blast("docs/architecture/overview.adoc") == "docs"
    assert classify_path_blast("guide.rst") == "docs"
    assert classify_path_blast(".github/workflows/ci.yml") == "config"
    assert classify_path_blast("data/snapshot.json") == "neutral"


def test_concern_key_per_file_full_path_under_source_root():
    # production: strip leading src/, strip extension, keep the dir path (D2 example)
    assert concern_key("src/mindlas/runtime/scorecard.py", "production") == "mindlas/runtime/scorecard"
    assert concern_key("src/mindlas/actions/verify_gate.py", "production") == "mindlas/actions/verify_gate"
    # tests remap to the de-prefixed remainder
    assert concern_key("tests/test_payment.py", "test") == "payment"
    assert concern_key("tests/payments/test_api.py", "test") == "payments/api"
    # config / docs / neutral buckets
    assert concern_key("pyproject.toml", "config") == "config:pyproject.toml"
    assert concern_key("docs/architecture/overview.md", "docs") == "docs:architecture"
    assert concern_key("README.md", "docs") == "docs:readme"
    assert concern_key("data/snapshot.json", "neutral") == "neutral:data"


def test_centrality_high_for_central_source_and_config():
    assert centrality("cli.py", "production") == "high"
    assert centrality("__init__.py", "production") == "high"
    assert centrality("paths.py", "production") == "high"
    assert centrality("pyproject.toml", "config") == "high"
    assert centrality("app.sln", "config") == "high"
    assert centrality("ordinary.py", "production") == "normal"
    assert centrality("cli.py", "neutral") == "normal"        # kind-gated: only production names


def test_filechange_is_frozen_and_has_no_package_key():
    fc = FileChange(path="src/a.py", status="modified", kind="production", top_dir="src",
                    directory="src", concern_key="a", additions=3, deletions=1,
                    changed_lines=4, centrality="normal")
    assert fc.path == "src/a.py" and fc.changed_lines == 4
    assert not hasattr(fc, "package_key")                     # D7: dropped


def test_build_signals_classifies_kinds_and_counts(tmp_path):
    init_git_repo(tmp_path)
    write(tmp_path, "src/app/a.py", "x = 1\n")          # production, concern app/a, dir src/app
    write(tmp_path, "tests/test_a.py", "x = 1\n")       # test
    write(tmp_path, "pyproject.toml", "[x]\n")          # config (high-centrality)
    write(tmp_path, "README.md", "# hi\n")              # docs
    write(tmp_path, "data/x.json", "{}\n")              # neutral
    sig = build_change_blast_signals([], session_id="s", project_root=tmp_path)
    assert sig.production_files_changed == ("src/app/a.py",)
    assert sig.test_files_changed == ("tests/test_a.py",)
    assert sig.config_files_changed == ("pyproject.toml",)
    assert sig.docs_files_changed == ("README.md",)
    assert sig.neutral_files_changed == ("data/x.json",)
    assert sig.changed_file_count == 5
    assert sig.directory_count == 4                     # src/app, tests, ., data
    assert sig.concern_count == 5
    assert sig.file_kind_count == 5
    assert sig.production_without_tests is False        # tests present
    assert sig.config_mixed_with_source is True
    assert sig.docs_mixed_with_source is True
    assert sig.high_centrality_files == ("pyproject.toml",)
    assert sig.current_diff_hash.startswith("sha256:")


def test_build_signals_excludes_mindlas_and_hash_is_stable(tmp_path):
    init_git_repo(tmp_path)
    write(tmp_path, "src/app.py", "a = 1\n")
    h1 = build_change_blast_signals([], project_root=tmp_path).current_diff_hash
    write(tmp_path, ".mindlas/splits/m.json", '{"x": 1}\n')
    sig2 = build_change_blast_signals([], project_root=tmp_path)
    assert not any(f.replace("\\", "/").startswith(".mindlas/") for f in sig2.changed_files)
    assert sig2.current_diff_hash == h1                 # .mindlas write does not move the hash


def test_build_signals_detects_delete_and_rename(tmp_path):
    init_git_repo(tmp_path)
    write(tmp_path, "old.py", "b = 2\nb = 3\n")
    write(tmp_path, "gone.py", "c = 4\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "two")
    _git(tmp_path, "rm", "-q", "gone.py")
    _git(tmp_path, "mv", "old.py", "renamed.py")
    sig = build_change_blast_signals([], project_root=tmp_path)
    assert sig.deleted_files == ("gone.py",)
    assert sig.renamed_files == ("renamed.py",)


def test_build_signals_fallback_when_not_a_repo(tmp_path):
    ev = Event(session_id="s", turn=1, ts="t", kind="tool_call", cls="edit",
               target="src/app.py", lines_added=4, lines_deleted=1)
    sig = build_change_blast_signals([ev], session_id="s", project_root=tmp_path)
    assert sig.changed_files == ("src/app.py",)
    assert sig.changed_lines == 5
    assert sig.current_diff_hash.startswith("sha256:")


def test_build_signals_clean_repo_is_empty(tmp_path):
    init_git_repo(tmp_path)
    sig = build_change_blast_signals([], project_root=tmp_path)
    assert sig.changed_file_count == 0 and sig.changed_lines == 0
    assert isinstance(sig, ChangeBlastSignals)


def test_build_file_changes_returns_records_and_hash(tmp_path):
    init_git_repo(tmp_path)
    write(tmp_path, "src/app/a.py", "x = 1\n")
    write(tmp_path, "tests/test_a.py", "x = 1\n")
    changes, dh = build_file_changes([], session_id="s", project_root=tmp_path)
    assert dh.startswith("sha256:")
    paths_ = {c.path for c in changes}
    assert paths_ == {"src/app/a.py", "tests/test_a.py"}
    assert all(hasattr(c, "concern_key") and hasattr(c, "kind") for c in changes)


def test_signals_from_changes_equals_full_builder(tmp_path):
    # R2: the extracted aggregation must produce the SAME ChangeBlastSignals as the one-call builder.
    init_git_repo(tmp_path)
    write(tmp_path, "src/app/a.py", "x = 1\nx = 2\n")
    write(tmp_path, "pyproject.toml", "[x]\n")
    write(tmp_path, "README.md", "# hi\n")
    changes, dh = build_file_changes([], session_id="s", project_root=tmp_path)
    from_changes = build_signals_from_changes(changes, session_id="s", diff_hash=dh)
    full = build_change_blast_signals([], session_id="s", project_root=tmp_path)
    assert from_changes == full


def test_signals_from_empty_changes_is_clean(tmp_path):
    sig = build_signals_from_changes((), session_id="s", diff_hash="sha256:x")
    assert sig.changed_file_count == 0 and sig.changed_lines == 0
    assert sig.current_diff_hash == "sha256:x" and sig.session_id == "s"
