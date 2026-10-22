"""Tests for vm/windows/install_ma3.ps1 (jcode 0.10.0-ma.3, mejorador-audio
plan step 1.0.b1) with PowerShell 7 and throw-away repositories: the two
"Windows working copies" push to local bare repositories instead of GitHub.
Skipped when pwsh is not installed. The script itself is written for
Windows PowerShell 5.1 as well (no syntax newer than 5.1).
Run: python -m pytest vm/tests/test_windows_install.py -q
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

VM_DIR = Path(__file__).resolve().parents[1]
REPO = VM_DIR.parent
SCRIPT = "jcode-adaptado/vm/windows/install_ma3.ps1"

pytestmark = pytest.mark.skipif(
    shutil.which("pwsh") is None or shutil.which("git") is None or not (REPO / ".git").exists(),
    reason="needs pwsh, git and a git repository (the package is built from git ls-files)")


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def working_copy(root: Path, name: str, files: dict[str, str]) -> Path:
    """A bare 'GitHub' plus a working copy with the block-D hook set up."""
    origin = root / f"{name}.git"
    git("init", "-q", "--bare", "-b", "main", str(origin), cwd=root)
    clone = root / name
    git("clone", "-q", str(origin), str(clone), cwd=root)
    git("config", "user.email", "t@example.invalid", cwd=clone)
    git("config", "user.name", "t", cwd=clone)
    git("config", "core.filemode", "false", cwd=clone)  # as Git for Windows: no exec bits on NTFS
    for path, content in files.items():
        (clone / path).parent.mkdir(parents=True, exist_ok=True)
        (clone / path).write_text(content)
    git("add", "-A", cwd=clone)
    git("commit", "-q", "-m", "block D state", cwd=clone)
    git("push", "-q", "origin", "main", cwd=clone)
    git("config", "core.hooksPath", ".githooks", cwd=clone)
    return clone


@pytest.fixture
def setup(tmp_path):
    package_zip = tmp_path / "ma3.zip"
    subprocess.run([sys.executable, str(REPO / "tools/make_package.py"), str(package_zip)],
                   check=True, capture_output=True)
    package = tmp_path / "package"
    zipfile.ZipFile(package_zip).extractall(package)
    hook = (REPO / ".githooks/pre-commit").read_text()
    checker = (REPO / "tools/check_secrets.py").read_text()
    base = {".githooks/pre-commit": hook, "tools/check_secrets.py": checker}
    jcode = working_copy(tmp_path, "jcode-adaptado", {**base, "jcode.py": "# old\n"})
    audio = working_copy(tmp_path, "mejorador-audio", {**base, "web/index.html": "<p>Hola</p>\n"})
    return package, jcode, audio


def run_install(package: Path, jcode: Path, audio: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG")}
    return subprocess.run(["pwsh", "-NoProfile", "-File", str(package / SCRIPT), "-Package",
                           str(package), "-JcodeRepo", str(jcode), "-AudioRepo", str(audio)],
                          capture_output=True, text=True, env=env, timeout=300, check=False)


def test_install_commits_and_pushes_both_repos(setup):
    package, jcode, audio = setup
    result = run_install(package, jcode, audio)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "WINDOWS DONE" in result.stdout
    for clone in (jcode, audio):
        assert git("status", "--porcelain", cwd=clone) == ""
        assert git("rev-parse", "HEAD", cwd=clone) == git("rev-parse", "origin/main", cwd=clone)
        assert "jcode 0.10.0-ma.3" in git("log", "-1", "--format=%s", cwd=clone)
    assert 'APP_VERSION = "0.10.0-ma.3"' in (jcode / "jcode.py").read_text()
    modes = git("ls-files", "-s", "vm/agent-hooks/pre-commit", ".githooks/pre-commit", cwd=jcode)
    assert all(line.startswith("100755") for line in modes.splitlines()), modes
    assert (audio / "tools/tests/test_check_secrets.py").exists()
    assert not (audio / "jcode.py").exists()  # only the shared files go to mejorador-audio
    again = run_install(package, jcode, audio)
    assert again.returncode == 0 and "nothing to commit" in again.stdout


def test_install_stops_on_local_changes(setup):
    package, jcode, audio = setup
    (audio / "web/index.html").write_text("<p>edited by hand</p>\n")
    result = run_install(package, jcode, audio)
    assert result.returncode == 1 and "local changes" in result.stdout
    assert (jcode / "jcode.py").read_text() == "# old\n"  # nothing copied anywhere


def test_install_stops_on_a_changed_package_file(setup):
    package, jcode, audio = setup
    target = package / "jcode-adaptado/jcode_cell.py"
    target.write_text(target.read_text() + "# tampered\n")
    result = run_install(package, jcode, audio)
    assert result.returncode == 1 and "changed since packing" in result.stdout
    assert (jcode / "jcode.py").read_text() == "# old\n"


def test_install_refuses_another_hooks_path(setup):
    package, jcode, audio = setup
    git("config", "core.hooksPath", "elsewhere", cwd=jcode)
    result = run_install(package, jcode, audio)
    assert result.returncode == 1 and "expected .githooks" in result.stdout
