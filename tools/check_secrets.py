#!/usr/bin/env python3
"""Block commits that would publish an API key, token or private key.

Origin: mejorador-audio plan step 1.0 (automation "secret detector",
2026-10-20); merged in jcode 0.10.0-ma.3 (plan step 1.0.b1) with the version
the Abschrift chat wrote for 0.10.0-ma.2 (Abschrift plan, requirement E6).
One file for every repository: jcode-adaptado, mejorador-audio, abschrift.

Why: the repositories are public. Once a key is pushed it is public, so the
check must run *before* the commit (git pre-commit hook); a CI run can only
raise the alarm after the fact.

Two independent checks, so a key is caught even when its format is unknown:
  1. Patterns of known key formats (Google "AQ." and "AIza", "sk-", NVIDIA
     "nvapi-", Groq "gsk_", GitHub tokens, private key blocks, Kaggle,
     quoted assignments such as api_key = "...").
  2. The VALUES of the environment variables that hold a secret right now
     (names with KEY, TOKEN, SECRET, PASSWORD, PASSWD or CREDENTIAL, the
     same rule as jcode's run_command). Whatever format a new provider's key
     has, once it is exported in the shell, a file containing it is refused.

Usage (from the repository root):
    python3 tools/check_secrets.py               # = --staged (pre-commit)
    python3 tools/check_secrets.py --staged      # lines added in the staged diff
    python3 tools/check_secrets.py --all         # every tracked file (audit, CI)
    python3 tools/check_secrets.py --range A..B  # lines added by those commits
    git log -p ... | python3 tools/check_secrets.py --diff -
                                                 # added lines of a diff/log (file or stdin)
    python3 tools/check_secrets.py FILE...       # the given files
    python3 tools/check_secrets.py --install-hook
        # only sets 'git config core.hooksPath .githooks'; it never writes a
        # hook file, so the versioned .githooks/pre-commit is never replaced

Exit codes: 0 = nothing found, 1 = possible secret found, 2 = usage/git error.
The hook treats 2 like 1 (fails closed). The script never prints a matched
value, only file, line and rule name (or the variable NAME), so its own
output cannot leak the key into a terminal, a log or a chat.
There is deliberately no "allow" marker: a marker would let anyone (or any
agent) wave a real key through. Tests build fake keys from pieces instead.
Standard library only; runs on Python 3.10+ (Windows and Linux).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# Characters allowed inside URL-safe base64 style tokens.
TOKEN_CHARS = r"A-Za-z0-9_\-"

# A token must not be glued to more token characters on either side;
# otherwise a long random string would match at many offsets.
NOT_TOKEN_BEFORE = rf"(?<![{TOKEN_CHARS}])"
NOT_TOKEN_AFTER = rf"(?![{TOKEN_CHARS}])"

# Each rule: (name, compiled pattern). Lengths are ranges, not exact values,
# because providers change formats without notice (Google moved from
# "AIza..." to "AQ...." keys of 53 characters; mejorador-audio plan,
# 2026-10-20). Add the pattern of every new provider BEFORE its key is used.
# PENDING: OpenCode Zen (Abschrift 0.1.e) - Fran sends prefix and length
# (never the key); until then the value check (2.) and "sk-" cover it.
RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "google-api-key (AQ. format)",
        re.compile(rf"{NOT_TOKEN_BEFORE}AQ\.[{TOKEN_CHARS}]{{40,70}}{NOT_TOKEN_AFTER}"),
    ),
    (
        "google-api-key (AIza format)",
        re.compile(rf"{NOT_TOKEN_BEFORE}AIza[{TOKEN_CHARS}]{{35}}{NOT_TOKEN_AFTER}"),
    ),
    (
        "kaggle-key (kaggle.json)",
        re.compile(r'"key"\s*:\s*"[0-9a-f]{32}"'),
    ),
    (
        "kaggle-key (environment variable)",
        re.compile(r"KAGGLE_KEY\s*=\s*['\"]?[0-9a-f]{32}"),
    ),
    (
        "openai-style key (sk-...)",
        re.compile(rf"{NOT_TOKEN_BEFORE}sk-[{TOKEN_CHARS}]{{20,}}"),
    ),
    (
        "nvidia-api-key (nvapi-...)",
        re.compile(rf"{NOT_TOKEN_BEFORE}nvapi-[{TOKEN_CHARS}]{{20,}}"),
    ),
    (
        "groq-api-key (gsk_...)",
        re.compile(rf"{NOT_TOKEN_BEFORE}gsk_[A-Za-z0-9]{{20,}}"),
    ),
    (
        "github-token",
        re.compile(r"(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"),
    ),
    (
        "private-key block",
        re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----"),
    ),
    (
        # A quoted literal of 16+ token characters right after a name that
        # says what it is. Variable references ("$K", "${{ secrets.X }}")
        # and JSON names ("api_key_env": "...") do not match.
        "secret assignment (api_key = \"...\")",
        re.compile(
            r"(?i)(?<![A-Za-z0-9])[A-Za-z0-9_.]*(?:api[_-]?key|secret|token|password|passwd)"
            r"\s*[:=]\s*['\"][A-Za-z0-9_\-+/=.]{16,}['\"]"
        ),
    ),
]

# Variable names that hold secrets (same parts as jcode_extras.SECRET_NAME_PARTS;
# kept separate because this file also runs in repositories without jcode).
SECRET_NAME_PARTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL")
# Shorter values ("1", "true", "en_US") would match everywhere.
MIN_SECRET_VALUE_LENGTH = 12
# GIT_CONFIG_KEY_<n> holds a git option NAME (e.g. "http.extraHeader"), not a secret.
GIT_CONFIG_NAME = re.compile(r"^GIT_CONFIG_KEY_\d+$")

# Files larger than this are skipped (audio, archives, model weights).
MAX_SCAN_BYTES = 2_000_000
HOOKS_PATH = ".githooks"


@dataclass(frozen=True)
class Finding:
    """One suspicious line. Holds no part of the matched text on purpose."""

    path: str
    line_number: int
    rule_name: str


def is_secret_name(name: str) -> bool:
    """True for variable names that typically hold a key, token or password."""
    upper = name.upper()
    return any(part in upper for part in SECRET_NAME_PARTS)


def secret_values(environ: Mapping[str, str]) -> dict[str, str]:
    """{value: variable name} of the secrets currently set in the environment."""
    values: dict[str, str] = {}
    for name, value in environ.items():
        if GIT_CONFIG_NAME.match(name) or not is_secret_name(name):
            continue
        if len(value) >= MIN_SECRET_VALUE_LENGTH:
            values[value] = name
    return values


def find_secrets_in_line(line: str, values: Mapping[str, str] | None = None) -> list[str]:
    """Return the names of every rule (and secret variable) that matches this line."""
    found = [name for name, pattern in RULES if pattern.search(line)]
    found += [f"value of ${name}" for value, name in (values or {}).items() if value in line]
    return found


def run_git(args: list[str]) -> str:
    """Run a git command and return stdout; raise SystemExit(2) on failure."""
    try:
        completed = subprocess.run(
            ["git", *args],
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"check_secrets: git {' '.join(args)} failed: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    return completed.stdout


# "@@ -12,3 +40,5 @@" -> the added side starts at line 40.
HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def scan_unified_diff(diff_text: str, values: Mapping[str, str] | None = None) -> list[Finding]:
    """Scan only the added lines of a zero-context unified diff (one or many patches)."""
    findings: list[Finding] = []
    current_path = "?"
    next_line_number = 0
    for raw_line in diff_text.splitlines():
        if raw_line.startswith("+++ "):
            current_path = raw_line[4:].removeprefix("b/")
            continue
        hunk = HUNK_HEADER.match(raw_line)
        if hunk:
            next_line_number = int(hunk.group(1))
            continue
        if raw_line.startswith("+"):
            for rule_name in find_secrets_in_line(raw_line[1:], values):
                findings.append(Finding(current_path, next_line_number, rule_name))
            next_line_number += 1
    return findings


DIFF_OPTIONS = ["--unified=0", "--no-color", "--no-ext-diff", "--no-textconv", "--diff-filter=ACMR"]


def scan_staged_changes(values: Mapping[str, str]) -> list[Finding]:
    """Scan lines added by the staged diff (what the next commit would add)."""
    return scan_unified_diff(run_git(["diff", "--cached", *DIFF_OPTIONS]), values)


def scan_commit_range(revision_range: str, values: Mapping[str, str]) -> list[Finding]:
    """Scan the lines every commit in the range adds (also those a later
    commit removes again: the history keeps them)."""
    if revision_range.startswith("-"):
        print("check_secrets: --range needs a revision range like A..B", file=sys.stderr)
        raise SystemExit(2)
    patches = run_git(["log", "--format=", "-p", *DIFF_OPTIONS, revision_range, "--"])
    return scan_unified_diff(patches, values)


def read_diff(source: str) -> str:
    """A unified diff from a file or, with '-', from stdin (bytes decoded leniently)."""
    try:
        if source == "-":
            return sys.stdin.buffer.read().decode("utf-8", errors="replace")
        with open(source, "rb") as handle:
            return handle.read().decode("utf-8", errors="replace")
    except OSError as error:
        print(f"check_secrets: cannot read {source}: {error}", file=sys.stderr)
        raise SystemExit(2) from error


def scan_text(path: str, text: str, values: Mapping[str, str] | None = None) -> list[Finding]:
    """Scan every line of one file's text."""
    findings: list[Finding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for rule_name in find_secrets_in_line(line, values):
            findings.append(Finding(path, line_number, rule_name))
    return findings


def scan_paths(paths: list[str], values: Mapping[str, str]) -> list[Finding]:
    """Scan files on disk, skipping large and binary files."""
    findings: list[Finding] = []
    for path in paths:
        try:
            with open(path, "rb") as handle:
                data = handle.read(MAX_SCAN_BYTES + 1)
        except OSError:
            continue  # Deleted in the work tree but still tracked.
        if len(data) > MAX_SCAN_BYTES or b"\0" in data:
            continue  # Binary or too large to be a config/source file.
        findings.extend(scan_text(path, data.decode("utf-8", errors="replace"), values))
    return findings


def scan_tracked_files(values: Mapping[str, str]) -> list[Finding]:
    """Scan every file git tracks."""
    paths = [path for path in run_git(["ls-files", "-z"]).split("\0") if path]
    return scan_paths(paths, values)


def install_hook() -> int:
    """Point git at the versioned hooks folder; never write a hook file."""
    root = Path(run_git(["rev-parse", "--show-toplevel"]).strip())
    if not (root / HOOKS_PATH / "pre-commit").is_file():
        print(f"check_secrets: {HOOKS_PATH}/pre-commit missing in {root}", file=sys.stderr)
        return 2
    current = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"], capture_output=True, text=True, check=False
    ).stdout.strip()
    if current == HOOKS_PATH:
        print(f"check_secrets: core.hooksPath is already {HOOKS_PATH}")
        return 0
    if current:
        print(
            f"check_secrets: core.hooksPath is '{current}', not {HOOKS_PATH}; nothing changed. "
            f"Check why, then: git config core.hooksPath {HOOKS_PATH}",
            file=sys.stderr,
        )
        return 2
    run_git(["config", "core.hooksPath", HOOKS_PATH])
    print(f"check_secrets: core.hooksPath set to {HOOKS_PATH} (hook {HOOKS_PATH}/pre-commit)")
    return 0


def report(findings: list[Finding], scanned: str) -> int:
    """Print findings without their values; return the exit code."""
    if not findings:
        print(f"check_secrets: OK, no keys or tokens found ({scanned}).")
        return 0
    print("check_secrets: BLOCKED. Possible secret (value not shown):", file=sys.stderr)
    for finding in findings:
        print(f"  {finding.path}:{finding.line_number}  [{finding.rule_name}]", file=sys.stderr)
    print(
        "Remove it from the file (keys live only in environment variables),\n"
        "then 'git add' the file again. Do NOT use --no-verify.",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true", help="scan the staged diff (default)")
    mode.add_argument("--all", action="store_true", help="scan all tracked files")
    mode.add_argument("--range", metavar="A..B", help="scan the lines these commits add")
    mode.add_argument("--diff", metavar="FILE", help="scan the added lines of a diff ('-' = stdin)")
    mode.add_argument("--install-hook", action="store_true", help="set core.hooksPath")
    parser.add_argument("files", nargs="*", help="scan these files instead")
    arguments = parser.parse_args(argv)
    if arguments.files and (arguments.staged or arguments.all or arguments.range
                            or arguments.diff or arguments.install_hook):
        parser.error("give either files or one of the options")
    if arguments.install_hook:
        return install_hook()
    values = secret_values(os.environ)
    if arguments.files:
        return report(scan_paths(arguments.files, values), f"{len(arguments.files)} file(s)")
    if arguments.all:
        return report(scan_tracked_files(values), "all tracked files")
    if arguments.range:
        return report(scan_commit_range(arguments.range, values), f"commits {arguments.range}")
    if arguments.diff:
        return report(scan_unified_diff(read_diff(arguments.diff), values), "diff")
    return report(scan_staged_changes(values), "staged changes")


if __name__ == "__main__":
    sys.exit(main())
