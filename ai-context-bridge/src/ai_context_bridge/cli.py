"""Command line interface for local Git work checkpoints."""

from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


HEADINGS = {
    "pl": {
        "facts": "Fakty",
        "notes": "Moje notatki",
        "unknown": "Czego nie wiem",
        "since": "Od czasu checkpointu",
        "no_commits": "Brak commitów.",
        "no_changes": "Brak zmian.",
        "no_stashes": "Brak stashy.",
        "no_new_commits": "Brak nowych commitów.",
        "no_changed_files": "Brak zmienionych plików.",
        "branch_warning": "OSTRZEŻENIE: branch zmienił się z {old} na {new}.",
        "branch_same": "Branch bez zmian: {branch}.",
        "missing_checkpoint": "Nie znaleziono checkpointu: {path}",
        "unknown_test": "Czy testy przechodzą: nie sprawdzono",
        "question": "Co jest następnym krokiem? ",
        "exclude_hint": "Podpowiedź: dodaj .bridge/ do .git/info/exclude, aby nie śledzić checkpointów.",
        "written": "Zapisano: {path}",
    },
    "en": {
        "facts": "Facts",
        "notes": "My notes",
        "unknown": "What I don't know",
        "since": "Since checkpoint",
        "no_commits": "No commits.",
        "no_changes": "No changes.",
        "no_stashes": "No stashes.",
        "no_new_commits": "No new commits.",
        "no_changed_files": "No changed files.",
        "branch_warning": "WARNING: branch changed from {old} to {new}.",
        "branch_same": "Branch unchanged: {branch}.",
        "missing_checkpoint": "Checkpoint not found: {path}",
        "unknown_test": "Do tests pass: not checked",
        "question": "Co jest następnym krokiem? ",
        "exclude_hint": "Tip: add .bridge/ to .git/info/exclude to keep checkpoints untracked.",
        "written": "Saved: {path}",
    },
}

STATE_PREFIX = "<!-- bridge-state: "


class BridgeError(Exception):
    """An expected user-facing error."""


def git(repo: Path, *args: str, check: bool = True) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise BridgeError("Nie znaleziono programu git. Zainstaluj git i spróbuj ponownie.") from exc
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise BridgeError(detail or f"Polecenie git zakończyło się kodem {result.returncode}.")
    return result.stdout


def resolve_repo(path: str | None) -> Path:
    if shutil.which("git") is None:
        raise BridgeError("Nie znaleziono programu git. Zainstaluj git i spróbuj ponownie.")
    requested = Path(path or ".").expanduser().resolve()
    if not requested.exists() or not requested.is_dir():
        raise BridgeError(f"Ścieżka nie jest katalogiem repozytorium git: {requested}")
    try:
        root = git(requested, "rev-parse", "--show-toplevel").strip()
    except BridgeError as exc:
        raise BridgeError(f"Katalog nie jest repozytorium git: {requested}") from exc
    return Path(root).resolve()


def current_branch(repo: Path) -> str:
    branch = git(repo, "branch", "--show-current").strip()
    if branch:
        return branch
    # An empty repository still has a current, unborn branch.
    return git(repo, "symbolic-ref", "--short", "HEAD", check=False).strip() or "(detached HEAD)"


def current_head(repo: Path) -> str | None:
    head = git(repo, "rev-parse", "--verify", "HEAD", check=False).strip()
    return head or None


def status_paths(repo: Path) -> list[tuple[str, str]]:
    """Return status codes and paths without interpreting diff contents."""
    raw = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain=v1", "-z"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout
    fields = raw.decode("utf-8", "surrogateescape").split("\0")
    entries: list[tuple[str, str]] = []
    i = 0
    while i < len(fields) and fields[i]:
        entry = fields[i]
        code, path = entry[:2], entry[3:]
        entries.append((code, path))
        i += 1
        if "R" in code or "C" in code:
            i += 1  # NUL format includes the old path as the next field.
    return entries


def diff_stat(repo: Path) -> list[str]:
    """Extract only filenames and numeric change counts from git diff --stat."""
    output = git(repo, "diff", "--stat", "--")
    files: list[str] = []
    for line in output.splitlines():
        match = re.match(r"^(.*?)\s+\|\s+(\d+)(?:\s|$)", line)
        if match:
            files.append(f"{match.group(1)}: {match.group(2)} zmian")
    return files


def commit_rows(repo: Path, limit: int = 10) -> list[tuple[str, str, str]]:
    output = git(repo, "log", f"-n{limit}", "--date=short", "--pretty=format:%h%x09%ad%x09%s", check=False)
    return [tuple(line.split("\t", 2)) for line in output.splitlines() if line]


def stash_rows(repo: Path) -> list[tuple[str, str, str]]:
    output = git(repo, "stash", "list", "--format=%H%x09%gd%x09%gs", check=False)
    rows = []
    for line in output.splitlines():
        bits = line.split("\t", 2)
        if len(bits) == 3:
            rows.append((bits[0][:12], bits[1], bits[2]))
    return rows


def state_comment(state: dict[str, Any]) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(state, ensure_ascii=True).encode()).decode()
    return f"{STATE_PREFIX}{encoded} -->"


def read_state(checkpoint: str) -> dict[str, Any]:
    for line in checkpoint.splitlines():
        if line.startswith(STATE_PREFIX) and line.endswith(" -->"):
            encoded = line[len(STATE_PREFIX) : -4]
            try:
                return json.loads(base64.urlsafe_b64decode(encoded.encode()).decode())
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                break
    return {}


def pause(repo: Path, lang: str) -> Path:
    words = HEADINGS[lang]
    branch = current_branch(repo)
    head = current_head(repo)
    commits = commit_rows(repo)
    changes = status_paths(repo)
    stats = diff_stat(repo)
    stashes = stash_rows(repo)
    next_step = input(words["question"])
    timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    lines = [f"# Checkpoint", "", f"## {words['facts']}"]
    lines.append(f"- Repozytorium: `{repo}`.")
    lines.append(f"- Branch: `{branch}` (repozytorium `{repo}`).")
    if commits:
        for short_hash, date, subject in commits:
            lines.append(f"- Commit `{short_hash}` ({date}): {subject}.")
    else:
        lines.append(f"- {words['no_commits']} Repozytorium `{repo}`.")
    if changes:
        for code, filename in changes:
            lines.append(f"- Status `{code}`: `{filename}`.")
    else:
        lines.append(f"- {words['no_changes']} Repozytorium `{repo}`.")
    if stats:
        for item in stats:
            filename, count = item.rsplit(": ", 1)
            lines.append(f"- Diff stat `{filename}`: {count}.")
    else:
        lines.append(f"- Diff stat: 0 zmian; repozytorium `{repo}`.")
    if stashes:
        for short_hash, ref, subject in stashes:
            lines.append(f"- Stash `{short_hash}` ({ref}): {subject}.")
    else:
        lines.append(f"- {words['no_stashes']} Repozytorium `{repo}`.")
    lines.extend([
        "",
        f"## {words['notes']}",
        f"- {timestamp} — {next_step}",
        "",
        f"## {words['unknown']}",
        f"- {words['unknown_test']}",
        "",
    ])
    state = {
        "branch": branch,
        "head": head,
        "worktree_paths": [path for _, path in changes],
    }
    lines.append(state_comment(state))
    checkpoint_dir = repo / ".bridge"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = checkpoint_dir / "checkpoint.md"
    checkpoint_path.write_text("\n".join(lines), encoding="utf-8")
    print(words["written"].format(path=checkpoint_path))
    print(words["exclude_hint"])
    return checkpoint_path


def resume(repo: Path, lang: str) -> None:
    words = HEADINGS[lang]
    checkpoint_path = repo / ".bridge" / "checkpoint.md"
    if not checkpoint_path.is_file():
        raise BridgeError(words["missing_checkpoint"].format(path=checkpoint_path))
    checkpoint = checkpoint_path.read_text(encoding="utf-8")
    state = read_state(checkpoint)
    visible_checkpoint = "\n".join(
        line for line in checkpoint.splitlines() if not line.startswith(STATE_PREFIX)
    )
    print(visible_checkpoint.rstrip())
    print(f"\n## {words['since']}")
    old_branch = state.get("branch")
    branch = current_branch(repo)
    if old_branch and old_branch != branch:
        print(words["branch_warning"].format(old=old_branch, new=branch))
    else:
        print(words["branch_same"].format(branch=branch))

    old_head = state.get("head")
    now_head = current_head(repo)
    new_commits: list[tuple[str, str, str]] = []
    if now_head and old_head and now_head != old_head:
        revs = git(repo, "rev-list", "--reverse", f"{old_head}..HEAD", check=False).splitlines()
        for commit in revs:
            details = git(repo, "show", "-s", "--date=short", "--format=%h%x09%ad%x09%s", commit, check=False).strip()
            bits = details.split("\t", 2)
            if len(bits) == 3:
                new_commits.append((bits[0], bits[1], bits[2]))
    if new_commits:
        for short_hash, date, subject in new_commits:
            print(f"- Commit `{short_hash}` ({date}): {subject}.")
    else:
        print(f"- {words['no_new_commits']}")

    changed: set[str] = set()
    if old_head and now_head and old_head != now_head:
        changed.update(git(repo, "diff", "--name-only", f"{old_head}..HEAD", "--", check=False).splitlines())
    baseline_paths = set(state.get("worktree_paths", []))
    changed.update(
        path
        for _, path in status_paths(repo)
        if path not in baseline_paths and not (path == ".bridge" or path.startswith(".bridge/"))
    )
    if changed:
        for path in sorted(changed):
            print(f"- `{path}`")
    else:
        print(f"- {words['no_changed_files']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bridge", description="Zapisz i wznow lokalny checkpoint pracy.")
    parser.add_argument("--lang", choices=("pl", "en"), default="pl", help="Język nagłówków i komunikatów.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("pause", "resume"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--repo", metavar="PATH", help="Jawna ścieżka repozytorium git (domyślnie bieżący katalog).")
        subparser.add_argument("--lang", choices=("pl", "en"), default=argparse.SUPPRESS, help="Język nagłówków i komunikatów.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        repo = resolve_repo(args.repo)
        if args.command == "pause":
            pause(repo, args.lang)
        else:
            resume(repo, args.lang)
    except (BridgeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"bridge: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
