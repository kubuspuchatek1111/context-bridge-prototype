import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT = Path(__file__).resolve().parents[1]


def run_git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    run_git(path, "init", "-q")
    run_git(path, "config", "user.name", "Bridge Test")
    run_git(path, "config", "user.email", "bridge@example.test")
    (path / "hello.txt").write_text("first\n", encoding="utf-8")
    run_git(path, "add", "hello.txt")
    run_git(path, "commit", "-m", "initial commit", "-q")
    return path


def run_bridge(*args: str, cwd: Path, input_text: str = "") -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "ai_context_bridge.cli", *args],
        cwd=cwd,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )


def test_pause_and_resume(repo: Path) -> None:
    (repo / "hello.txt").write_text("first\nsecond\n", encoding="utf-8")
    paused = run_bridge("pause", "--repo", str(repo), cwd=repo, input_text="Dokończyć parser\n")
    assert paused.returncode == 0, paused.stderr
    checkpoint = repo / ".bridge" / "checkpoint.md"
    assert checkpoint.is_file()
    content = checkpoint.read_text(encoding="utf-8")
    assert "Fakty" in content
    assert "Moje notatki" in content
    assert "Dokończyć parser" in content
    assert "hello.txt" in content
    assert str(checkpoint) in paused.stdout
    assert ".git/info/exclude" in paused.stdout

    (repo / "new.txt").write_text("later\n", encoding="utf-8")
    resumed = run_bridge("resume", "--repo", str(repo), cwd=repo)
    assert resumed.returncode == 0, resumed.stderr
    assert "Od czasu checkpointu" in resumed.stdout
    assert "new.txt" in resumed.stdout


def test_non_repository_has_clear_error(tmp_path: Path) -> None:
    result = run_bridge("resume", "--repo", str(tmp_path), cwd=tmp_path)
    assert result.returncode != 0
    assert "Katalog nie jest repozytorium git" in result.stderr


def test_empty_repository_pause(tmp_path: Path) -> None:
    repo = tmp_path / "empty"
    repo.mkdir()
    run_git(repo, "init", "-q")
    result = run_bridge("pause", "--repo", str(repo), cwd=repo, input_text="\n")
    assert result.returncode == 0, result.stderr
    content = (repo / ".bridge" / "checkpoint.md").read_text(encoding="utf-8")
    assert "Brak commitów" in content
    assert "Moje notatki" in content


def test_resume_warns_when_branch_changes(repo: Path) -> None:
    result = run_bridge("pause", "--repo", str(repo), cwd=repo, input_text="krok\n")
    assert result.returncode == 0, result.stderr
    run_git(repo, "switch", "-c", "different-branch")
    resumed = run_bridge("resume", "--repo", str(repo), cwd=repo)
    assert resumed.returncode == 0, resumed.stderr
    assert "OSTRZEŻENIE" in resumed.stdout
