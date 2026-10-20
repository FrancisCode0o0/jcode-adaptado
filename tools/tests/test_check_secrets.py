"""Tests for tools/check_secrets.py and the .githooks/pre-commit hook.

Origin: plan step 1.0 (automation "secret detector", 2026-10-20).
Fake keys are assembled at runtime from pieces, so this file itself never
contains a string the detector would flag (it must pass its own check).
Run: python -m pytest tools/tests -q
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOLS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR))

import check_secrets  # imported after sys.path is set up just above

# 50 URL-safe characters: AQ. + 50 = 53, the length seen on 2026-10-20.
FAKE_AQ_KEY = "AQ" + "." + "Ab3_-x9" * 7 + "q"
FAKE_AIZA_KEY = "AI" + "za" + "Sy" + "B" * 33
FAKE_KAGGLE_JSON = '{"username": "fran", "key": "' + "0123456789abcdef" * 2 + '"}'
FAKE_GITHUB_TOKEN = "gh" + "p_" + "A1b2C3d4" * 5
FAKE_SK_KEY = "s" + "k-" + "proj" + "Z9y8" * 6
FAKE_PRIVATE_KEY = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"


@pytest.mark.parametrize(
    ("line", "expected_rule_fragment"),
    [
        (f'export GEMINI_API_KEY="{FAKE_AQ_KEY}"', "AQ. format"),
        (f"key = {FAKE_AIZA_KEY}", "AIza format"),
        (FAKE_KAGGLE_JSON, "kaggle.json"),
        (f"token: {FAKE_GITHUB_TOKEN}", "github-token"),
        (f"DEEPSEEK={FAKE_SK_KEY}", "sk-"),
        (FAKE_PRIVATE_KEY, "private-key"),
    ],
)
def test_each_rule_detects_its_key(line: str, expected_rule_fragment: str) -> None:
    """Every rule fires on a realistic line; checks the rule name, not the value."""
    rule_names = check_secrets.find_secrets_in_line(line)
    assert any(expected_rule_fragment in name for name in rule_names), rule_names


@pytest.mark.parametrize(
    "line",
    [
        '"api_key_env": "GEMINI_API_KEY"',  # jcode settings.json: a name, not a key
        "echo ${#GEMINI_API_KEY}",  # length check from the plan
        "model = gemini-3.5-flash-lite",
        "sk-learn and scikit-learn are fine",
        "AQ.short",  # too short to be a key
        "commit d2fd981 and b37d004cb7",  # git hashes are hex, not keys
        "sha256:" + "0cf3cae568d36aa9576b28dfb35f11328f1cb974ca7647d9475ebb86c75ac6e3",
    ],
)
def test_ordinary_lines_pass(line: str) -> None:
    """Lines that appear in the project today must not be flagged (false positives)."""
    assert check_secrets.find_secrets_in_line(line) == []


def test_aq_key_inside_longer_token_is_not_matched_partially() -> None:
    """A key glued to more token characters is not a key on its own."""
    assert (
        check_secrets.find_secrets_in_line("x" + FAKE_AQ_KEY + "yyyyyyyyyyyyyyyyyyyyyyyyyy") == []
    )


def test_unified_diff_reports_added_line_numbers_only() -> None:
    """Removed lines are ignored; added lines get their new-file line number."""
    diff_text = (
        "diff --git a/cfg.sh b/cfg.sh\n"
        "--- a/cfg.sh\n"
        "+++ b/cfg.sh\n"
        "@@ -3 +3,2 @@\n"
        f"-old={FAKE_AIZA_KEY}\n"
        "+harmless=1\n"
        f"+export K={FAKE_AQ_KEY}\n"
    )
    findings = check_secrets.scan_unified_diff(diff_text)
    assert [(f.path, f.line_number) for f in findings] == [("cfg.sh", 4)]


def test_report_never_prints_the_value(capsys: pytest.CaptureFixture[str]) -> None:
    """The output must not leak the key into a terminal, log or chat."""
    findings = check_secrets.scan_text("secrets.env", f"K={FAKE_AQ_KEY}\n")
    exit_code = check_secrets.report(findings)
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "secrets.env:1" in captured.err
    assert FAKE_AQ_KEY not in captured.out + captured.err
    assert FAKE_AQ_KEY[3:20] not in captured.out + captured.err


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """A throw-away git repo with this repo's checker and hook installed."""
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    (tmp_path / "tools").mkdir()
    shutil.copy(TOOLS_DIR / "check_secrets.py", tmp_path / "tools")
    (tmp_path / ".githooks").mkdir()
    hook = tmp_path / ".githooks" / "pre-commit"
    shutil.copy(REPO_ROOT / ".githooks" / "pre-commit", hook)
    hook.chmod(0o755)
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "test@example.invalid"],
        ["config", "user.name", "test"],
        ["config", "core.hooksPath", ".githooks"],
    ):
        subprocess.run(["git", *args], cwd=tmp_path, check=True)
    return tmp_path


def git_commit(repo: Path, file_name: str, content: str) -> subprocess.CompletedProcess[str]:
    (repo / file_name).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", file_name], cwd=repo, check=True)
    return subprocess.run(
        ["git", "commit", "-q", "-m", "test"],
        check=False,
        cwd=repo,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def test_hook_blocks_commit_with_key(temp_repo: Path) -> None:
    """End to end: the real hook refuses the commit and names the file, not the key."""
    result = git_commit(temp_repo, "bad.sh", f'export GEMINI_API_KEY="{FAKE_AQ_KEY}"\n')
    assert result.returncode != 0
    assert "bad.sh:1" in result.stderr
    assert FAKE_AQ_KEY not in result.stdout + result.stderr


def test_hook_allows_clean_commit(temp_repo: Path) -> None:
    """End to end: an ordinary file is committed normally."""
    result = git_commit(temp_repo, "ok.sh", 'echo "${#GEMINI_API_KEY}"\n')
    assert result.returncode == 0, result.stderr


def test_checker_and_its_tests_pass_their_own_scan() -> None:
    """These files ship in public repos, so they must not trip the detector."""
    for path in (TOOLS_DIR / "check_secrets.py", Path(__file__)):
        assert check_secrets.scan_text(path.name, path.read_text(encoding="utf-8")) == []
