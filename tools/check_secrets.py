#!/usr/bin/env python3
"""Block commits that would publish an API key, token or private key.

Origin: plan step 1.0 (automation "secret detector", 2026-10-20).
Why: both repositories are public. Once a key is pushed it is public, so the
check must run *before* the commit (git pre-commit hook); a CI run can only
raise the alarm after the fact.

Usage:
    python tools/check_secrets.py --staged   # lines added in the staged diff
    python tools/check_secrets.py --all      # every tracked file (audit)

Exit codes: 0 = nothing found, 1 = possible secret found, 2 = usage/git error.
The script never prints a matched value, only file, line and rule name, so
its own output cannot leak the key into a terminal, a log or a chat.
Standard library only; runs on Python 3.10+ (Windows and Linux).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass

# Characters allowed inside URL-safe base64 style tokens.
TOKEN_CHARS = r"A-Za-z0-9_\-"

# A token must not be glued to more token characters on either side;
# otherwise a long random string would match at many offsets.
NOT_TOKEN_BEFORE = rf"(?<![{TOKEN_CHARS}])"
NOT_TOKEN_AFTER = rf"(?![{TOKEN_CHARS}])"

# Each rule: (name, compiled pattern). Lengths are ranges, not exact values,
# because providers change formats without notice (Google moved from
# "AIza..." to "AQ...." keys of 53 characters; see plan, 2026-10-20).
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
        "github-token",
        re.compile(r"(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"),
    ),
    (
        "private-key block",
        re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    ),
]

# Files larger than this are skipped in --all mode (audio, archives).
MAX_SCAN_BYTES = 2_000_000


@dataclass(frozen=True)
class Finding:
    """One suspicious line. Holds no part of the matched text on purpose."""

    path: str
    line_number: int
    rule_name: str


def find_secrets_in_line(line: str) -> list[str]:
    """Return the names of every rule that matches this line."""
    return [name for name, pattern in RULES if pattern.search(line)]


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


def scan_unified_diff(diff_text: str) -> list[Finding]:
    """Scan only the added lines of a zero-context unified diff."""
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
            for rule_name in find_secrets_in_line(raw_line[1:]):
                findings.append(Finding(current_path, next_line_number, rule_name))
            next_line_number += 1
    return findings


def scan_staged_changes() -> list[Finding]:
    """Scan lines added by the staged diff (what the next commit would add)."""
    diff_text = run_git(
        ["diff", "--cached", "--unified=0", "--no-color", "--no-ext-diff", "--diff-filter=ACMR"]
    )
    return scan_unified_diff(diff_text)


def scan_text(path: str, text: str) -> list[Finding]:
    """Scan every line of one file's text."""
    findings: list[Finding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for rule_name in find_secrets_in_line(line):
            findings.append(Finding(path, line_number, rule_name))
    return findings


def scan_tracked_files() -> list[Finding]:
    """Scan every file git tracks, skipping large and binary files."""
    findings: list[Finding] = []
    for path in run_git(["ls-files", "-z"]).split("\0"):
        if not path:
            continue
        try:
            with open(path, "rb") as handle:
                data = handle.read(MAX_SCAN_BYTES + 1)
        except OSError:
            continue  # Deleted in the work tree but still tracked.
        if len(data) > MAX_SCAN_BYTES or b"\0" in data:
            continue  # Binary or too large to be a config/source file.
        findings.extend(scan_text(path, data.decode("utf-8", errors="replace")))
    return findings


def report(findings: list[Finding]) -> int:
    """Print findings without their values; return the exit code."""
    if not findings:
        print("check_secrets: OK, no keys or tokens found.")
        return 0
    print("check_secrets: BLOCKED. Possible secret (value not shown):", file=sys.stderr)
    for finding in findings:
        print(
            f"  {finding.path}:{finding.line_number}  [{finding.rule_name}]",
            file=sys.stderr,
        )
    print(
        "Remove it from the file (keys live only in environment variables),\n"
        "then 'git add' the file again. Do NOT use --no-verify.",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true", help="scan the staged diff")
    mode.add_argument("--all", action="store_true", help="scan all tracked files")
    arguments = parser.parse_args(argv)
    findings = scan_staged_changes() if arguments.staged else scan_tracked_files()
    return report(findings)


if __name__ == "__main__":
    sys.exit(main())
