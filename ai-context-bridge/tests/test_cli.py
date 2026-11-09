import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_context_bridge import cli


PROJECT = Path(__file__).resolve().parents[1]


def run_git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args], check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
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


class FakeProvider:
    model = "test-model"

    def __init__(self, results):
        self.results = list(results)
        self.payloads = []

    def complete(self, payload):
        self.payloads.append(payload)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def sections(markdown: str) -> dict[str, str]:
    names = ["Fakty", "Wnioski modelu", "Moje notatki", "Czego nie wiem"]
    result = {}
    for i, name in enumerate(names):
        start = markdown.index(f"## {name}") + len(f"## {name}")
        end = markdown.find("\n## ", start)
        result[name] = markdown[start:end if end >= 0 else len(markdown)].strip()
    return result


def test_pause_and_resume(repo: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("builtins.input", lambda _: "Dokończyć parser")
    provider = FakeProvider([{"next_step": "Dokończyć parser.", "insights": [{"text": "Zmiana jest lokalna.", "confidence": "medium"}]}])
    (repo / "hello.txt").write_text("first\nsecond\n", encoding="utf-8")
    cli.pause(repo, "pl", provider=provider)
    out = capsys.readouterr().out
    checkpoint = repo / ".bridge" / "checkpoint.md"
    content = checkpoint.read_text(encoding="utf-8")
    assert "Zapisano:" in out and str(checkpoint) in out
    assert "Dokończyć parser" in content
    assert "[medium] Zmiana jest lokalna." in content
    assert "hello.txt" in content
    assert "git diff" not in json.dumps(provider.payloads)
    assert provider.payloads[0] == {"commit_messages": ["initial commit"], "changed_files": ["hello.txt"]}

    (repo / "new.txt").write_text("later\n", encoding="utf-8")
    cli.resume(repo, "pl")
    resumed = capsys.readouterr().out
    assert "Od czasu checkpointu" in resumed
    assert "new.txt" in resumed


def test_invalid_json_falls_back_after_one_retry(repo: Path, monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _: "ręczny krok")
    provider = FakeProvider([{"nope": True}, {"next_step": "still bad", "insights": [{"text": "x", "confidence": "certain"}]}])
    cli.pause(repo, "pl", provider=provider)
    content = (repo / ".bridge" / "checkpoint.md").read_text(encoding="utf-8")
    assert len(provider.payloads) == 2
    assert "ręczny krok" in content
    assert "Wnioski modelu\n\n- (brak)" in content
    assert "Szkic" not in content


def test_facts_section_is_identical_with_and_without_model(repo: Path, monkeypatch) -> None:
    for key in ("BRIDGE_BASE_URL", "BRIDGE_API_KEY", "BRIDGE_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("builtins.input", lambda _: "")
    facts_only = repo / "facts-only.md"
    with_model = repo / "with-model.md"
    cli.pause(repo, "pl", provider=None)
    (repo / ".bridge" / "checkpoint.md").replace(facts_only)
    provider = FakeProvider([{"next_step": "Review changes.", "insights": []}])
    cli.pause(repo, "pl", provider=provider)
    (repo / ".bridge" / "checkpoint.md").replace(with_model)
    assert sections(facts_only.read_text(encoding="utf-8"))["Fakty"] == sections(with_model.read_text(encoding="utf-8"))["Fakty"]


def test_dry_run_makes_no_network_call(repo: Path, monkeypatch, capsys) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("network call in dry-run")

    monkeypatch.setenv("BRIDGE_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("BRIDGE_API_KEY", "secret-must-not-print")
    monkeypatch.setenv("BRIDGE_MODEL", "demo")
    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    assert cli.pause(repo, "pl", dry_run=True) is None
    request = json.loads(capsys.readouterr().out)
    assert request["model"] == "demo"
    assert "secret-must-not-print" not in json.dumps(request)
    assert "commit_messages" in request["messages"][1]["content"]
    assert not (repo / ".bridge" / "checkpoint.md").exists()


def test_missing_config_facts_only_and_explicit_output(repo: Path, monkeypatch, capsys) -> None:
    for key in ("BRIDGE_BASE_URL", "BRIDGE_API_KEY", "BRIDGE_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("builtins.input", lambda _: "Do ustalenia")
    cli.pause(repo, "pl")
    output = capsys.readouterr().out
    assert "tylko z faktami" in output
    assert "Zapisano:" in output
    assert "Czy testy przechodzą: nie sprawdzono" in (repo / ".bridge" / "checkpoint.md").read_text(encoding="utf-8")


def test_non_repository_has_clear_error(tmp_path: Path, capsys) -> None:
    assert cli.main(["resume", "--repo", str(tmp_path)]) == 2
    assert "Katalog nie jest repozytorium git" in capsys.readouterr().err


def test_resume_warns_when_branch_changes(repo: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("builtins.input", lambda _: "krok")
    cli.pause(repo, "pl")
    capsys.readouterr()
    run_git(repo, "switch", "-c", "different-branch")
    cli.resume(repo, "pl")
    assert "OSTRZEŻENIE" in capsys.readouterr().out
