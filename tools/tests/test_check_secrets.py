"""Tests for tools/check_secrets.py and the .githooks/pre-commit hook.

Origin: mejorador-audio plan step 1.0 (automation "secret detector",
2026-10-20); extended in jcode 0.10.0-ma.3 (merge with the Abschrift version
of 0.10.0-ma.2: more patterns, values of exported keys, --range, a hook
installer that only sets core.hooksPath).
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
FAKE_NVIDIA_KEY = "nv" + "api-" + "Qw3_" * 16
FAKE_GROQ_KEY = "gs" + "k_" + "Rt5Yu7" * 8
FAKE_PRIVATE_KEY = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"
FAKE_ENCRYPTED_KEY = "-----BEGIN " + "ENCRYPTED PRIVATE KEY-----"
FAKE_ASSIGNMENT = "api" + "_key = '" + "z1X2c3V4b5N6m7Q8w9" + "'"
# A key in a format no rule knows (e.g. a new provider): only the value check finds it.
FAKE_UNKNOWN_FORMAT = "zen" + "9" * 6 + "Lk" * 12
FAKE_VARIABLE = "OPENCODE_API" + "_KEY"


def clean_env(**extra: str) -> dict[str, str]:
    """Environment without any real secret variable (the CI or a shell may have some)."""
    env = {name: value for name, value in os.environ.items()
           if not check_secrets.is_secret_name(name)}
    env.update(extra)
    return env


@pytest.mark.parametrize(
    ("line", "expected_rule_fragment"),
    [
        (f'export GEMINI_API_KEY="{FAKE_AQ_KEY}"', "AQ. format"),
        (f"key = {FAKE_AIZA_KEY}", "AIza format"),
        (FAKE_KAGGLE_JSON, "kaggle.json"),
        (f"token: {FAKE_GITHUB_TOKEN}", "github-token"),
        (f"DEEPSEEK={FAKE_SK_KEY}", "sk-"),
        (f"NVIDIA={FAKE_NVIDIA_KEY}", "nvapi-"),
        (f"GROQ={FAKE_GROQ_KEY}", "gsk_"),
        (FAKE_PRIVATE_KEY, "private-key"),
        (FAKE_ENCRYPTED_KEY, "private-key"),
        (FAKE_ASSIGNMENT, "secret assignment"),
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
        '"api_key_env": "OPENCODE_API_KEY"',
        "echo ${#GEMINI_API_KEY}",  # length check from the plan
        'export GEMINI_API_KEY="$K"',  # the plan's read -rs line: a reference
        "token: ${{ secrets.GITHUB_TOKEN }}",  # GitHub Actions reference
        "model = gemini-3.5-flash-lite",
        '"model": "deepseek-ai/deepseek-v4.1-flash"',
        "sk-learn and scikit-learn are fine",
        "AQ.short",  # too short to be a key
        "commit d2fd981 and b37d004cb7",  # git hashes are hex, not keys
        "sha256:" + "0cf3cae568d36aa9576b28dfb35f11328f1cb974ca7647d9475ebb86c75ac6e3",
        "password = input('Password: ')",  # no literal
    ],
)
def test_ordinary_lines_pass(line: str) -> None:
    """Lines that appear in the projects today must not be flagged (false positives)."""
    assert check_secrets.find_secrets_in_line(line) == []


def test_aq_key_inside_longer_token_is_not_matched_partially() -> None:
    """A key glued to more token characters is not a key on its own."""
    glued = "x" + FAKE_AQ_KEY + "y" * 26
    assert check_secrets.find_secrets_in_line(glued) == []


def test_there_is_no_allow_marker() -> None:
    """ma.2 had 'check-secrets: allow'; ma.3 drops it on purpose: an agent
    could add it to a line with a real key."""
    line = f"K={FAKE_AQ_KEY}  # check-secrets: allow"
    assert check_secrets.find_secrets_in_line(line) != []


def test_secret_values_follow_the_jcode_name_rule() -> None:
    """Names with KEY/TOKEN/SECRET/PASSWORD/PASSWD/CREDENTIAL count; short
    values and GIT_CONFIG_KEY_<n> (an option name) do not."""
    environ = {
        FAKE_VARIABLE: FAKE_UNKNOWN_FORMAT,
        "my_password": "q" * 12,
        "SHORT_TOKEN": "abc",
        "GIT_CONFIG_KEY_0": "http.extraHeader.something",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    assert check_secrets.secret_values(environ) == {
        FAKE_UNKNOWN_FORMAT: FAKE_VARIABLE,
        "q" * 12: "my_password",
    }


def test_value_of_exported_key_is_found_whatever_its_format() -> None:
    """A provider format no rule knows is caught once its variable is set."""
    line = f"base_url: x  key: {FAKE_UNKNOWN_FORMAT}"
    assert check_secrets.find_secrets_in_line(line) == []
    values = {FAKE_UNKNOWN_FORMAT: FAKE_VARIABLE}
    assert check_secrets.find_secrets_in_line(line, values) == [f"value of ${FAKE_VARIABLE}"]


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
    values = {FAKE_UNKNOWN_FORMAT: FAKE_VARIABLE}
    findings = check_secrets.scan_text(
        "secrets.env", f"K={FAKE_AQ_KEY}\nZ={FAKE_UNKNOWN_FORMAT}\n", values
    )
    exit_code = check_secrets.report(findings, "test")
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert exit_code == 1
    assert "secrets.env:1" in output and "secrets.env:2" in output
    assert f"${FAKE_VARIABLE}" in output  # the NAME helps; the value never appears
    for secret in (FAKE_AQ_KEY, FAKE_AQ_KEY[3:20], FAKE_UNKNOWN_FORMAT, FAKE_UNKNOWN_FORMAT[4:16]):
        assert secret not in output


# ---------------------------------------------------------------- with git


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """A throw-away git repo with this repo's checker and hook installed."""
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    shutil.copy(TOOLS_DIR / "check_secrets.py", repo / "tools")
    (repo / ".githooks").mkdir()
    hook = repo / ".githooks" / "pre-commit"
    shutil.copy(REPO_ROOT / ".githooks" / "pre-commit", hook)
    hook.chmod(0o755)
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "test@example.invalid"],
        ["config", "user.name", "test"],
        ["config", "core.hooksPath", ".githooks"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True)
    return repo


def git_commit(repo: Path, file_name: str, content: str, **env: str):
    (repo / file_name).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", file_name], cwd=repo, check=True)
    return subprocess.run(
        ["git", "commit", "-q", "-m", "test"],
        check=False,
        cwd=repo,
        capture_output=True,
        text=True,
        env=clean_env(GIT_TERMINAL_PROMPT="0", **env),
    )


def run_checker(repo: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(repo / "tools" / "check_secrets.py"), *args],
        cwd=repo, capture_output=True, text=True, check=False, env=clean_env(**env),
    )


def test_hook_blocks_commit_with_key(temp_repo: Path) -> None:
    """End to end: the real hook refuses the commit and names the file, not the key."""
    result = git_commit(temp_repo, "bad.sh", f'export GEMINI_API_KEY="{FAKE_AQ_KEY}"\n')
    assert result.returncode != 0
    assert "bad.sh:1" in result.stderr
    assert FAKE_AQ_KEY not in result.stdout + result.stderr


def test_hook_blocks_value_of_exported_key(temp_repo: Path) -> None:
    """In the VM the keys are exported: a file containing one is refused,
    even in a format no pattern knows (e.g. the OpenCode Zen key)."""
    result = git_commit(temp_repo, "notes.md", f"zen {FAKE_UNKNOWN_FORMAT}\n",
                        **{FAKE_VARIABLE: FAKE_UNKNOWN_FORMAT})
    assert result.returncode != 0
    assert f"value of ${FAKE_VARIABLE}" in result.stderr
    assert FAKE_UNKNOWN_FORMAT not in result.stdout + result.stderr


def test_hook_allows_clean_commit(temp_repo: Path) -> None:
    """End to end: an ordinary file is committed normally."""
    result = git_commit(temp_repo, "ok.sh", 'echo "${#GEMINI_API_KEY}"\n')
    assert result.returncode == 0, result.stderr


def test_staged_content_counts_not_the_work_tree(temp_repo: Path) -> None:
    """The commit contains what is staged: removing the key from the file
    after 'git add' does not make the commit clean."""
    bad = temp_repo / "cfg.sh"
    bad.write_text(f"K={FAKE_AQ_KEY}\n", encoding="utf-8")
    subprocess.run(["git", "add", "cfg.sh"], cwd=temp_repo, check=True)
    bad.write_text("K=$K\n", encoding="utf-8")
    assert run_checker(temp_repo).returncode == 1  # no argument = --staged


def test_range_finds_a_key_removed_again_later(temp_repo: Path) -> None:
    """A key added in one commit and deleted in the next stays in history:
    --range (used before pushing agent commits) must still see it."""
    assert git_commit(temp_repo, "ok.txt", "base\n").returncode == 0
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=temp_repo,
                          capture_output=True, text=True, check=True).stdout.strip()
    for content in (f"K={FAKE_SK_KEY}\n", "K=gone\n"):
        (temp_repo / "cfg.sh").write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", "cfg.sh"], cwd=temp_repo, check=True)
        subprocess.run(["git", "commit", "-q", "--no-verify", "-m", "x"], cwd=temp_repo,
                       check=True, env=clean_env())
    result = run_checker(temp_repo, "--range", f"{base}..HEAD")
    assert result.returncode == 1
    assert "cfg.sh:1" in result.stderr
    assert run_checker(temp_repo, "--all").returncode == 0  # the tree itself is clean now


def test_range_rejects_options(temp_repo: Path, tmp_path: Path) -> None:
    """A range that starts with '-' must not reach git as an option
    (git log --output=FILE would write a file)."""
    target = tmp_path / "written-by-git.txt"
    assert run_checker(temp_repo, f"--range=--output={target}").returncode == 2
    assert not target.exists()


def test_files_mode_and_git_error_codes(temp_repo: Path, tmp_path: Path) -> None:
    """FILE... scans those files; outside a repository --all fails closed (2)."""
    loose = tmp_path / "loose.txt"
    loose.write_text(f"x={FAKE_NVIDIA_KEY}\n", encoding="utf-8")
    assert run_checker(temp_repo, str(loose)).returncode == 1
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    result = subprocess.run(
        [sys.executable, "-I", str(temp_repo / "tools" / "check_secrets.py"), "--all"],
        cwd=outside, capture_output=True, text=True, check=False,
        env=clean_env(GIT_CEILING_DIRECTORIES=str(tmp_path)),
    )
    assert result.returncode == 2


def test_install_hook_only_sets_hooks_path(temp_repo: Path) -> None:
    """--install-hook never writes a hook file (ma.2 overwrote the versioned
    one); it sets core.hooksPath and refuses to replace another value."""
    hook = temp_repo / ".githooks" / "pre-commit"
    original = hook.read_bytes()
    subprocess.run(["git", "config", "--unset", "core.hooksPath"], cwd=temp_repo, check=True)
    assert run_checker(temp_repo, "--install-hook").returncode == 0
    config = subprocess.run(["git", "config", "core.hooksPath"], cwd=temp_repo,
                            capture_output=True, text=True, check=True).stdout.strip()
    assert config == ".githooks"
    assert hook.read_bytes() == original
    assert not (temp_repo / ".git" / "hooks" / "pre-commit").exists()
    assert run_checker(temp_repo, "--install-hook").returncode == 0  # already set
    subprocess.run(["git", "config", "core.hooksPath", "elsewhere"], cwd=temp_repo, check=True)
    assert run_checker(temp_repo, "--install-hook").returncode == 2
    assert subprocess.run(["git", "config", "core.hooksPath"], cwd=temp_repo, capture_output=True,
                          text=True, check=True).stdout.strip() == "elsewhere"


def test_checker_and_its_tests_pass_their_own_scan() -> None:
    """These files ship in public repos, so they must not trip the detector."""
    for path in (TOOLS_DIR / "check_secrets.py", Path(__file__)):
        assert check_secrets.scan_text(path.name, path.read_text(encoding="utf-8")) == []


def test_this_repository_is_clean() -> None:
    """--all over this repository. Skipped when it runs from an unpacked ZIP
    without .git (Abschrift requirement A5: ma.2 had 98/99 there)."""
    if not (REPO_ROOT / ".git").exists() or shutil.which("git") is None:
        pytest.skip("not a git repository (unpacked ZIP)")
    result = subprocess.run(
        [sys.executable, "-I", str(TOOLS_DIR / "check_secrets.py"), "--all"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False, env=clean_env(),
    )
    assert result.returncode == 0, result.stderr
