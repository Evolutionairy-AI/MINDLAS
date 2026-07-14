import subprocess
from pathlib import Path


def init_git_repo(path: Path) -> None:
    """Init a quiet git repo with one committed baseline file, so `git diff` works."""
    def g(*args):
        subprocess.run(["git", *args], cwd=str(path), capture_output=True, text=True, check=True)
    g("init", "-q")
    g("config", "user.email", "t@t.t")
    g("config", "user.name", "t")
    g("config", "commit.gpgsign", "false")
    (path / "baseline.txt").write_text("seed\n", encoding="utf-8")
    g("add", "-A")
    g("commit", "-q", "-m", "seed")


def write(path: Path, rel: str, text: str) -> None:
    p = path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
