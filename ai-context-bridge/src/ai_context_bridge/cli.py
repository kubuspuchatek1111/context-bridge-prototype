"""Local Git checkpoints with optional metadata-only LLM suggestions."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, Sequence

STATE_PREFIX = "<!-- bridge-state: "
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["next_step", "insights"],
    "properties": {
        "next_step": {"type": "string", "description": "One-sentence suggested next step"},
        "insights": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "confidence"],
                "properties": {
                    "text": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                },
            },
        },
    },
}

WORDS = {
    "pl": {
        "facts": "Fakty", "insights": "Wnioski modelu", "notes": "Moje notatki",
        "unknown": "Czego nie wiem", "since": "Od czasu checkpointu",
        "no_commits": "Brak commitów.", "no_changes": "Brak zmian.", "no_stashes": "Brak stashy.",
        "no_new_commits": "Brak nowych commitów.", "no_changed_files": "Brak zmienionych plików.",
        "branch_warning": "OSTRZEŻENIE: branch zmienił się z {old} na {new}.",
        "branch_same": "Branch bez zmian: {branch}.", "missing": "Nie znaleziono checkpointu: {path}",
        "unknown_test": "Czy testy przechodzą: nie sprawdzono (nie da się tego wywnioskować z metadanych).",
        "question": "Co jest następnym krokiem? ", "written": "Zapisano: {path}",
        "offline": "Brak pełnej konfiguracji BRIDGE_BASE_URL, BRIDGE_API_KEY, BRIDGE_MODEL — zapisuję checkpoint tylko z faktami.",
        "model_failed": "Nie udało się uzyskać poprawnej odpowiedzi modelu — zapisuję checkpoint tylko z faktami.",
    },
    "en": {
        "facts": "Facts", "insights": "Model insights", "notes": "My notes",
        "unknown": "What I don't know", "since": "Since checkpoint",
        "no_commits": "No commits.", "no_changes": "No changes.", "no_stashes": "No stashes.",
        "no_new_commits": "No new commits.", "no_changed_files": "No changed files.",
        "branch_warning": "WARNING: branch changed from {old} to {new}.",
        "branch_same": "Branch unchanged: {branch}.", "missing": "Checkpoint not found: {path}",
        "unknown_test": "Do tests pass: not checked (this cannot be inferred from metadata).",
        "question": "What is the next step? ", "written": "Saved: {path}",
        "offline": "BRIDGE_BASE_URL, BRIDGE_API_KEY, BRIDGE_MODEL are not all set — saving a facts-only checkpoint.",
        "model_failed": "The model did not return a valid response — saving a facts-only checkpoint.",
    },
}


class BridgeError(Exception):
    """An expected user-facing error."""


class Provider(Protocol):
    def complete(self, payload: dict[str, list[str]]) -> dict[str, Any]: ...


class OpenAICompatibleProvider:
    """Chat Completions provider configured only through BRIDGE_* variables."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def complete(self, payload: dict[str, list[str]]) -> dict[str, Any]:
        from urllib.error import URLError
        from urllib.request import Request, urlopen

        body = request_body(self.model, payload)
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BridgeError(f"Błąd połączenia z providerem LLM: {exc}") from exc
        try:
            content = result["choices"][0]["message"]["content"]
            return json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Provider zwrócił niepoprawny JSON.") from exc


def request_body(model: str, payload: dict[str, list[str]]) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": (
                "Based only on the supplied metadata, return JSON matching this schema: "
                + json.dumps(SCHEMA, ensure_ascii=False)
                + " Do not state or invent repository facts."
            )},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
    }


def validate_model_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"next_step", "insights"}:
        raise ValueError("Oczekiwano obiektu JSON next_step/insights.")
    step = value["next_step"]
    insights = value["insights"]
    if (not isinstance(step, str) or not step.strip() or "\n" in step or "\r" in step
            or step.strip()[-1] not in ".!?" or sum(step.count(mark) for mark in ".!?") != 1):
        raise ValueError("next_step musi być jednym zdaniem.")
    if not isinstance(insights, list):
        raise ValueError("insights musi być listą.")
    for item in insights:
        if (not isinstance(item, dict) or set(item) != {"text", "confidence"}
                or not isinstance(item["text"], str) or not item["text"].strip()
                or item["confidence"] not in ("low", "medium", "high")):
            raise ValueError("Niepoprawny wniosek lub etykieta confidence.")
    return {"next_step": step.strip(), "insights": insights}


@dataclass
class Facts:
    branch: str
    head: str | None
    commits: list[tuple[str, str, str]]
    changes: list[tuple[str, str]]
    stashes: list[tuple[str, str, str]]

    def model_payload(self) -> dict[str, list[str]]:
        # This intentionally excludes branch/status codes, hashes, dates, stash data,
        # file contents, and all diff content.
        return {
            "commit_messages": [subject for _, _, subject in self.commits],
            "changed_files": sorted({path for _, path in self.changes}),
        }


def git(repo: Path, *args: str, check: bool = True) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args], check=False, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError as exc:
        raise BridgeError("Nie znaleziono programu git.") from exc
    if check and result.returncode:
        raise BridgeError(result.stderr.strip() or result.stdout.strip() or "Polecenie git nie powiodło się.")
    return result.stdout


def resolve_repo(path: str | None) -> Path:
    requested = Path(path or ".").expanduser().resolve()
    if not requested.is_dir():
        raise BridgeError(f"Ścieżka nie jest katalogiem repozytorium git: {requested}")
    try:
        return Path(git(requested, "rev-parse", "--show-toplevel").strip()).resolve()
    except BridgeError as exc:
        raise BridgeError(f"Katalog nie jest repozytorium git: {requested}") from exc


def branch(repo: Path) -> str:
    name = git(repo, "branch", "--show-current").strip()
    return name or git(repo, "symbolic-ref", "--short", "HEAD", check=False).strip() or "(detached HEAD)"


def head(repo: Path) -> str | None:
    value = git(repo, "rev-parse", "--verify", "HEAD", check=False).strip()
    return value or None


def status_paths(repo: Path) -> list[tuple[str, str]]:
    result = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain=v1", "-z"], check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    fields = result.stdout.decode("utf-8", "surrogateescape").split("\0")
    rows: list[tuple[str, str]] = []
    i = 0
    while i < len(fields) and fields[i]:
        entry = fields[i]
        rows.append((entry[:2], entry[3:]))
        i += 2 if "R" in entry[:2] or "C" in entry[:2] else 1
    return rows


def commits(repo: Path, limit: int = 10) -> list[tuple[str, str, str]]:
    out = git(repo, "log", f"-n{limit}", "--date=short", "--pretty=format:%H%x09%ad%x09%s", check=False)
    return [tuple(line.split("\t", 2)) for line in out.splitlines() if line]  # type: ignore[list-item]


def stashes(repo: Path) -> list[tuple[str, str, str]]:
    out = git(repo, "stash", "list", "--format=%H%x09%gd%x09%gs", check=False)
    rows = []
    for line in out.splitlines():
        bits = line.split("\t", 2)
        if len(bits) == 3:
            rows.append((bits[0], bits[1], bits[2]))
    return rows


def collect_facts(repo: Path) -> Facts:
    return Facts(branch(repo), head(repo), commits(repo), status_paths(repo), stashes(repo))


def render_facts(facts: Facts, lang: str) -> str:
    w = WORDS[lang]
    lines = [f"## {w['facts']}", f"- Branch: `{facts.branch}`."]
    if facts.commits:
        lines.extend(f"- Commit `{sha}` ({date}): {subject}" for sha, date, subject in facts.commits)
    else:
        lines.append(f"- {w['no_commits']}")
    if facts.changes:
        lines.extend(f"- Status `{code}`: `{path}`" for code, path in facts.changes)
    else:
        lines.append(f"- {w['no_changes']}")
    if facts.stashes:
        lines.extend(f"- Stash `{sha}` ({ref}): {subject}" for sha, ref, subject in facts.stashes)
    else:
        lines.append(f"- {w['no_stashes']}")
    return "\n".join(lines)


def state_comment(state: dict[str, Any]) -> str:
    blob = base64.urlsafe_b64encode(json.dumps(state).encode()).decode()
    return f"{STATE_PREFIX}{blob} -->"


def read_state(markdown: str) -> dict[str, Any]:
    for line in markdown.splitlines():
        if line.startswith(STATE_PREFIX) and line.endswith(" -->"):
            try:
                return json.loads(base64.urlsafe_b64decode(line[len(STATE_PREFIX):-4]).decode())
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                return {}
    return {}


def configured_provider() -> OpenAICompatibleProvider | None:
    base_url = os.environ.get("BRIDGE_BASE_URL")
    api_key = os.environ.get("BRIDGE_API_KEY")
    model = os.environ.get("BRIDGE_MODEL")
    if not (base_url and api_key and model):
        return None
    return OpenAICompatibleProvider(base_url, api_key, model)


def get_model_result(provider: Provider, payload: dict[str, list[str]]) -> dict[str, Any] | None:
    for attempt in range(2):
        try:
            return validate_model_result(provider.complete(payload))
        except (ValueError, BridgeError):
            if attempt == 1:
                return None
    return None


def pause(repo: Path, lang: str, dry_run: bool = False, provider: Provider | None = None) -> Path | None:
    w = WORDS[lang]
    facts = collect_facts(repo)
    payload = facts.model_payload()
    if dry_run:
        selected = provider or configured_provider()
        model = selected.model if isinstance(selected, OpenAICompatibleProvider) else os.environ.get("BRIDGE_MODEL", "<BRIDGE_MODEL>")
        print(json.dumps(request_body(model, payload), ensure_ascii=False, indent=2))
        return None

    selected = provider if provider is not None else configured_provider()
    result = get_model_result(selected, payload) if selected is not None else None
    if selected is None:
        print(w["offline"])
    elif result is None:
        print(w["model_failed"])
    else:
        print(f"{w['insights']}:")
        if result["insights"]:
            for item in result["insights"]:
                print(f"- [{item['confidence']}] {item['text']}")
        else:
            print("- (brak)")
        print(f"Szkic: {result['next_step']}")
    proposed = result["next_step"] if result else ""
    answer = input(f"{w['question']}[{proposed}] ")
    next_step = answer.strip() or proposed or ("Do ustalenia" if lang == "pl" else "To be determined")

    timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    sections = ["# Checkpoint", render_facts(facts, lang), f"## {w['insights']}"]
    if result and result["insights"]:
        sections.extend(f"- [{item['confidence']}] {item['text']}" for item in result["insights"])
    else:
        sections.append("- (brak)" if lang == "pl" else "- (none)")
    sections.extend([f"## {w['notes']}", f"- {timestamp} — {next_step}", f"## {w['unknown']}", f"- {w['unknown_test']}"])
    checkpoint = "\n\n".join(sections) + "\n" + state_comment({
        "branch": facts.branch, "head": facts.head,
        "worktree_paths": [path for _, path in facts.changes],
    }) + "\n"
    path = repo / ".bridge" / "checkpoint.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(checkpoint, encoding="utf-8")
    print(w["written"].format(path=path))
    return path


def resume(repo: Path, lang: str) -> None:
    w = WORDS[lang]
    path = repo / ".bridge" / "checkpoint.md"
    if not path.is_file():
        raise BridgeError(w["missing"].format(path=path))
    saved = path.read_text(encoding="utf-8")
    state = read_state(saved)
    print("\n".join(line for line in saved.splitlines() if not line.startswith(STATE_PREFIX)).rstrip())
    print(f"\n## {w['since']}")
    now_branch, now_head = branch(repo), head(repo)
    old_branch, old_head = state.get("branch"), state.get("head")
    if old_branch and old_branch != now_branch:
        print(w["branch_warning"].format(old=old_branch, new=now_branch))
    else:
        print(w["branch_same"].format(branch=now_branch))
    added: list[tuple[str, str, str]] = []
    if old_head and now_head and old_head != now_head:
        revs = git(repo, "rev-list", "--reverse", f"{old_head}..HEAD", check=False).splitlines()
        for sha in revs:
            row = git(repo, "show", "-s", "--date=short", "--format=%H%x09%ad%x09%s", sha, check=False).strip().split("\t", 2)
            if len(row) == 3:
                added.append(tuple(row))
    if added:
        for sha, date, subject in added:
            print(f"- Commit `{sha}` ({date}): {subject}")
    else:
        print(f"- {w['no_new_commits']}")
    changed = set()
    if old_head and now_head and old_head != now_head:
        changed.update(git(repo, "diff", "--name-only", f"{old_head}..HEAD", "--", check=False).splitlines())
    baseline = set(state.get("worktree_paths", []))
    changed.update(p for _, p in status_paths(repo) if p not in baseline and p != ".bridge" and not p.startswith(".bridge/"))
    if changed:
        print("\n".join(f"- `{name}`" for name in sorted(changed)))
    else:
        print(f"- {w['no_changed_files']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bridge", description="Zapisz i wznow lokalny checkpoint pracy.")
    sub = parser.add_subparsers(dest="command", required=True)
    pause_parser = sub.add_parser("pause")
    resume_parser = sub.add_parser("resume")
    for child in (pause_parser, resume_parser):
        child.add_argument("--repo", metavar="PATH", help="Ścieżka repozytorium git (domyślnie bieżący katalog).")
        child.add_argument("--lang", choices=("pl", "en"), default="pl")
    pause_parser.add_argument("--dry-run", action="store_true", help="Pokaż dokładne żądanie bez połączenia sieciowego.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        repo = resolve_repo(args.repo)
        if args.command == "pause":
            pause(repo, args.lang, args.dry_run)
        else:
            resume(repo, args.lang)
    except (BridgeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"bridge: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
