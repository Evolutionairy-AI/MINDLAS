import subprocess
from mindlas.cli import main
from _verify_helpers import init_git_repo, write


def _git(path, *args):
    subprocess.run(["git", *args], cwd=str(path), capture_output=True, text=True, check=True)


def _seed_broad(monkeypatch, tmp_path):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    init_git_repo(tmp_path)
    write(tmp_path, "pyproject.toml", "[tool.x]\n" * 5)
    write(tmp_path, "src/pkg_a/alpha.py", "a = 1\n" * 120)
    write(tmp_path, "src/pkg_b/beta.py", "b = 2\n" * 120)
    write(tmp_path, "docs/guide.md", "# g\n" * 40)
    write(tmp_path, "tests/test_alpha.py", "def test_a():\n    assert True\n" * 15)
    monkeypatch.setattr("mindlas.cli._latest_events", lambda: [])


def test_blast_status_prints_gauge(tmp_path, monkeypatch, capsys):
    _seed_broad(monkeypatch, tmp_path)
    assert main(["blast", "status"]) == 0
    assert "BLAST" in capsys.readouterr().out


def test_blast_split_preview_writes_nothing(tmp_path, monkeypatch, capsys):
    _seed_broad(monkeypatch, tmp_path)
    assert main(["blast", "split", "--preview"]) == 0
    out = capsys.readouterr().out
    assert "preview" in out.lower() and "no source files" in out.lower()
    assert not (tmp_path / ".mindlas" / "splits").exists()


def test_blast_split_preview_coherent_shows_no_fake_win(tmp_path, monkeypatch, capsys):
    # D6 / DECISIONS Part C: a coherent (single-bundle) diff must NOT print a fake "X -> X planned".
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    init_git_repo(tmp_path)
    write(tmp_path, "src/only.py", "a = 1\n")
    monkeypatch.setattr("mindlas.cli._latest_events", lambda: [])
    assert main(["blast", "split", "--preview"]) == 0
    out = capsys.readouterr().out
    assert "already coherent" in out.lower()          # the honest explanation, not a reduction line
    assert "planned after=" not in out                # no fabricated planned-reduction cell
    assert not (tmp_path / ".mindlas" / "splits").exists()


def test_blast_split_apply_then_latest(tmp_path, monkeypatch, capsys):
    _seed_broad(monkeypatch, tmp_path)
    assert main(["blast", "split", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "planned" in out.lower() and "verify gate" in out.lower()    # recommends Verify Gate next
    assert (tmp_path / ".mindlas" / "splits" / "sessions" / "session" / "latest_split_manifest.json").exists()
    assert main(["blast", "latest"]) == 0
    assert "Split ID" in capsys.readouterr().out


def test_blast_latest_without_split_is_graceful(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr("mindlas.cli._latest_events", lambda: [])
    assert main(["blast", "latest"]) == 0
    assert "No patch split" in capsys.readouterr().out
