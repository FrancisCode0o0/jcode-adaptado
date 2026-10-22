"""Tests for the Abschrift scripts of jcode 0.10.0-ma.3 (mejorador-audio plan
step 1.0.b1; Abschrift plan v12: E5, E6, E8, A1-A10, 0.1.g).

Everything runs in a fake home under tmp_path with the REAL scripts and
real cells; no network, no key, no VM. Tests that build cells are skipped
without a working bubblewrap.
Run: python -m pytest vm/tests -q
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

VM_DIR = Path(__file__).resolve().parents[1]
JCODE_DIR = VM_DIR.parent
sys.path.insert(0, str(VM_DIR / "abschrift"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import replica_check  # after the sys.path lines above
from test_vm_scripts import make_repo_with_origin

FAKE_ZEN_KEY = "zen-" + "q" * 30
FAKE_NVIDIA_KEY = "nv" + "api-" + "w" * 40
GEMINI_VERSION = "0.64.0"


def bwrap_works() -> bool:
    if shutil.which("bwrap") is None:
        return False
    return subprocess.run(["bwrap", "--ro-bind", "/", "/", "--unshare-pid", "--proc", "/proc",
                           "--dev", "/dev", "true"], capture_output=True, check=False).returncode == 0


needs_bwrap = pytest.mark.skipif(not bwrap_works(), reason="bubblewrap missing or refused")


# ------------------------------------------------------- replica scenarios
@needs_bwrap
@pytest.mark.parametrize("scenario", replica_check.SCENARIOS, ids=lambda s: s.__name__)
def test_replica_scenario(scenario):
    """Start scripts and push.sh on a throw-away layout (same code that
    vm/verify_abschrift.sh runs in the VM)."""
    good, text = replica_check.run_scenario(scenario)
    assert good, text


# -------------------------------------------------------- setup_abschrift
@pytest.fixture
def fake_home(tmp_path: Path) -> dict[str, str]:
    """A home like the VM's after block D: Node (fake), ~/.bashrc with a key,
    and Gemini CLI already present in the pinned version (no download)."""
    home = tmp_path / "home" / "fran"
    node_bin = home / ".local/opt/node-v24-linux-x64/bin"
    node_bin.mkdir(parents=True)
    (node_bin / "node").write_text("#!/bin/sh\necho v24.0.0\n")
    (node_bin / "node").chmod(0o755)
    (home / ".local/opt/node").symlink_to(node_bin.parent)
    (home / ".bashrc").write_text("export GEMINI_API_KEY=not-a-real-key-value\n")
    install_fake_gemini(home, GEMINI_VERSION)
    keep = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "TZ")}
    return {**keep, "HOME": str(home), "SKIP_DOWNLOADS": "1", "GIT_TERMINAL_PROMPT": "0",
            "NODE_BIN_DIR": str(home / ".local/opt/node/bin")}


def install_fake_gemini(home: Path, version: str) -> None:
    package = home / ".local/opt/gemini-cli/node_modules/@google/gemini-cli"
    package.mkdir(parents=True, exist_ok=True)
    (package / "package.json").write_text(json.dumps({"version": version}))
    binary = home / ".local/opt/gemini-cli/node_modules/.bin/gemini"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text(f"#!/bin/sh\necho {version}\n")
    binary.chmod(0o755)


def run(script: str, env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(VM_DIR / script), *args], capture_output=True, text=True,
                          env=env, timeout=180, check=False, stdin=subprocess.DEVNULL)


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


@needs_bwrap
def test_setup_creates_everything_and_is_idempotent(fake_home):
    home = Path(fake_home["HOME"])
    first = run("setup_abschrift.sh", fake_home)
    assert first.returncode == 0, first.stdout + first.stderr
    assert "ABSCHRIFT SETUP OK" in first.stdout
    repo = home / "repos/abschrift.git"
    verifier = home / "jcode-projekte/abschrift/code"
    gemini = home / "abschrift-gemini"
    assert git("rev-parse", "--is-bare-repository", cwd=repo) == "true"
    for clone in (verifier, gemini):
        assert git("config", "remote.origin.url", cwd=clone) == str(repo)
    assert git("config", "core.hooksPath", cwd=verifier) == str(VM_DIR / "agent-hooks")
    assert git("config", "user.name", cwd=gemini) == "Gemini CLI (Abschrift)"
    shipped = (JCODE_DIR / "examples/abschrift/settings.json").read_bytes()
    assert (home / "jcode-projekte/abschrift/settings.json").read_bytes() == shipped
    gemini_settings = json.loads((home / ".gemini/settings.json").read_text())
    assert gemini_settings["general"]["enableAutoUpdate"] is False
    assert gemini_settings["security"]["auth"]["selectedType"] == "oauth-personal"
    assert oct((home / ".gemini").stat().st_mode & 0o777) == "0o700"
    assert (gemini / "tests/acceptance").is_dir()  # prepared, so it can be read-only
    second = run("setup_abschrift.sh", fake_home)
    assert second.returncode == 0 and "Meeting repo exists" in second.stdout
    assert "already installed. Skipped." in second.stdout


@needs_bwrap
def test_setup_never_overwrites(fake_home):
    home = Path(fake_home["HOME"])
    assert run("setup_abschrift.sh", fake_home).returncode == 0
    settings = home / "jcode-projekte/abschrift/settings.json"
    settings.write_text(settings.read_text().replace('"max_steps": 30', '"max_steps": 25'))
    (home / ".gemini/settings.json").write_text("{}")
    again = run("setup_abschrift.sh", fake_home)
    assert again.returncode == 0 and "was NOT overwritten" in again.stdout
    assert '"max_steps": 25' in settings.read_text()
    assert (home / ".gemini/settings.json").read_text() == "{}"


def test_setup_refuses_a_clone_with_another_origin(fake_home, tmp_path):
    home = Path(fake_home["HOME"])
    elsewhere = tmp_path / "elsewhere.git"
    subprocess.run(["git", "init", "-q", "--bare", str(elsewhere)], check=True)
    subprocess.run(["git", "clone", "-q", str(elsewhere), str(home / "abschrift-gemini")],
                   check=True, capture_output=True)
    result = run("setup_abschrift.sh", fake_home)
    assert result.returncode == 1 and "expected" in result.stderr
    assert git("config", "remote.origin.url", cwd=home / "abschrift-gemini") == str(elsewhere)


def test_setup_refuses_another_gemini_version(fake_home):
    install_fake_gemini(Path(fake_home["HOME"]), "0.63.0")
    result = run("setup_abschrift.sh", fake_home)
    assert result.returncode == 1 and "pins 0.64.0" in result.stderr


def test_setup_refuses_root_like_preconditions(fake_home):
    env = {**fake_home, "NODE_BIN_DIR": "/nonexistent"}
    result = run("setup_abschrift.sh", env)
    assert result.returncode == 1 and "Node missing" in result.stderr


# ----------------------------------------------- session_check (project mode)
@pytest.fixture
def abschrift_ready(fake_home, tmp_path):
    """fake_home after setup_abschrift.sh, keys set, a fake jcode-adaptado
    (session_check pulls it - never the real one in a test)."""
    setup = run("setup_abschrift.sh", fake_home)
    assert setup.returncode == 0, setup.stdout + setup.stderr
    fake_jcode = make_repo_with_origin(tmp_path, "jcode-fake",
                                       {"test_ok.py": "def test_ok():\n    assert True\n",
                                        ".gitignore": "code/\nlogs/\n"})
    env = {**fake_home, "JCODE_DIR": str(fake_jcode), "OPENCODE_API_KEY": FAKE_ZEN_KEY,
           "NVIDIA_API_KEY": FAKE_NVIDIA_KEY}
    project = Path(fake_home["HOME"]) / "jcode-projekte/abschrift"
    return env, project


@needs_bwrap
def test_session_check_project_green(abschrift_ready):
    env, project = abschrift_ready
    result = run("session_check.sh", env, "--quick", str(project))
    assert result.returncode == 0, result.stdout
    for text in ("model chain without Google", "fallback_exhausted is 'stop'",
                 "cell switched on and required", "cell: own /proc", "commit hook of the clone"):
        assert text in result.stdout
    assert FAKE_ZEN_KEY not in result.stdout + result.stderr


@needs_bwrap
@pytest.mark.parametrize("change, message", [
    (lambda s: s["providers"]["nvidia"].update(base_url="https://generativelanguage.googleapis.com/v1beta/openai/"),
     "Google model in the chain"),
    (lambda s: s.update(fallback_exhausted="default_provider"), "must be 'stop'"),
    (lambda s: s["sandbox"].update(enabled=False), "must both be true"),
    (lambda s: s["sandbox"].update(read_write=["~/.bashrc"]), "VISIBLE inside the cell"),
])
def test_session_check_project_red(abschrift_ready, change, message):
    env, project = abschrift_ready
    settings_file = project / "settings.json"
    settings = json.loads(settings_file.read_text())
    change(settings)
    settings_file.write_text(json.dumps(settings))
    result = run("session_check.sh", env, "--quick", str(project))
    assert result.returncode == 1 and message in result.stdout, result.stdout


@needs_bwrap
def test_session_check_project_without_key_and_with_changes(abschrift_ready):
    env, project = abschrift_ready
    no_key = {k: v for k, v in env.items() if k != "OPENCODE_API_KEY"}
    result = run("session_check.sh", no_key, "--quick", str(project))
    assert result.returncode == 1 and "$OPENCODE_API_KEY NOT set" in result.stdout
    test_file = project / "code/tests/acceptance/a.spec.js"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("// written by the verifier\n")
    review = run("session_check.sh", env, "--quick", str(project))
    assert review.returncode == 0 and "waiting for your review" in review.stdout
    (project / "code/public.js").write_text("// nobody asked for this\n")
    stray = run("session_check.sh", env, "--quick", str(project))
    assert stray.returncode == 1 and "public.js" in stray.stdout


@needs_bwrap
def test_session_check_project_needs_the_trusted_hook(abschrift_ready):
    env, project = abschrift_ready
    subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=project / "code", check=True)
    result = run("session_check.sh", env, "--quick", str(project))
    assert result.returncode == 1 and "expected" in result.stdout


def test_session_check_usage_errors(tmp_path):
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path)}
    assert run("session_check.sh", env, "--bogus").returncode == 2
    assert run("session_check.sh", env, "a", "b").returncode == 2
    missing = run("session_check.sh", env, str(tmp_path / "nope"))
    assert missing.returncode == 1 and "does not exist" in missing.stdout


# ------------------------------------------------------------- verify_jcode
@needs_bwrap
@pytest.mark.parametrize("probe, expected", [
    ("nocell", "OK without cell: exit 2"),
    ("nokey", "OK without keys: exit 2"),
    ("write-guard", "OK write_file: tests/acceptance/ allowed, public/ refused (mode verifier)"),
    ("env-count", "OK run_command in the cell: 0 variable(s)"),
])
def test_verify_jcode_probes(abschrift_ready, probe, expected):
    env, project = abschrift_ready
    result = subprocess.run([sys.executable, "-I", str(VM_DIR / "abschrift/verify_jcode.py"), probe,
                             str(project)], capture_output=True, text=True, env=env, timeout=180,
                            check=False)
    assert result.stdout.startswith(expected), result.stdout + result.stderr
    assert FAKE_ZEN_KEY not in result.stdout


def test_verify_jcode_pending_without_keys(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    shutil.copy(JCODE_DIR / "examples/abschrift/settings.json", project / "settings.json")
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path)}
    for probe in ("start", "deepseek"):
        result = subprocess.run([sys.executable, "-I", str(VM_DIR / "abschrift/verify_jcode.py"),
                                 probe, str(project)], capture_output=True, text=True, env=env,
                                timeout=60, check=False)
        assert result.returncode == 0 and result.stdout.startswith("PENDING"), result.stdout


# ------------------------------------------------------- trusted agent hook
@needs_bwrap
def test_agent_hook_blocks_a_key_in_the_verifier_clone(abschrift_ready):
    env, project = abschrift_ready
    clone = project / "code"
    (clone / "notes.txt").write_text("key " + "AQ" + "." + "Kk9_" * 12 + "xyz\n")
    subprocess.run(["git", "add", "notes.txt"], cwd=clone, check=True)
    result = subprocess.run(["git", "commit", "-q", "-m", "x"], cwd=clone, capture_output=True,
                            text=True, env=env, check=False)
    assert result.returncode != 0 and "notes.txt:1" in result.stderr


# --------------------------------------------- install_ma3.sh: AppArmor step
def stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def run_enable_userns(tmp_path: Path, bwrap_body: str, extra: bool, target: bool):
    stubs = tmp_path / "stubs"
    stubs.mkdir(exist_ok=True)
    log = tmp_path / "sudo.log"
    stub(stubs, "bwrap", bwrap_body)
    stub(stubs, "sudo", f'echo "$*" >> {log}\nexec "$@"\n')
    stub(stubs, "apparmor_parser", "touch \"$HOME/parsed\"\n")
    stub(stubs, "apt-get", "exit 0\n")
    extra_file, target_file = tmp_path / "extra-profile", tmp_path / "etc-profile"
    if extra:
        extra_file.write_text("profile bwrap /usr/bin/bwrap flags=(unconfined) { userns, }\n")
    if target:
        target_file.write_text("already here\n")
    env = {"PATH": f"{stubs}:{os.environ['PATH']}", "HOME": str(tmp_path),
           "APPARMOR_EXTRA": str(extra_file), "APPARMOR_TARGET": str(target_file)}
    proc = subprocess.run(["bash", "-c", f"source {VM_DIR / 'install_ma3.sh'}; enable_user_namespaces"],
                          capture_output=True, text=True, env=env, timeout=60, check=False)
    return proc, (log.read_text() if log.exists() else ""), target_file


def test_userns_working_needs_no_sudo(tmp_path):
    proc, sudo_log, _ = run_enable_userns(tmp_path, "exit 0\n", extra=True, target=False)
    assert proc.returncode == 0 and "no AppArmor change needed" in proc.stdout and sudo_log == ""


def test_userns_refused_loads_ubuntus_profile_once(tmp_path):
    # First call fails, later calls work (the profile is loaded in between).
    body = f'if [ -e "{tmp_path}/parsed" ]; then exit 0; fi\necho "bwrap: setting up uid map: Permission denied" >&2\nexit 1\n'
    proc, sudo_log, target = run_enable_userns(tmp_path, body, extra=True, target=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "bwrap works now" in proc.stdout and target.exists()
    assert "apparmor_parser -r" in sudo_log


def test_userns_refused_with_profile_present_stops_without_changes(tmp_path):
    proc, sudo_log, target = run_enable_userns(tmp_path, "echo denied >&2\nexit 1\n",
                                               extra=True, target=True)
    assert proc.returncode == 1 and "Nothing changed automatically" in proc.stderr
    assert sudo_log == "" and target.read_text() == "already here\n"
