"""Tests for the VM shell scripts: notify_run.sh, set_ntfy_topic.sh and
session_check.sh.

Origin: plan step 1.0 (automations of 2026-10-20).
Each test runs the real script with bash, against temporary git repos and a
fake ntfy server on 127.0.0.1, so no network, key or VM is needed.
Run: python -m pytest vm/tests -q
"""

from __future__ import annotations

import http.server
import os
import shutil
import stat
import subprocess
import sys
import threading
from pathlib import Path
from typing import ClassVar

import pytest

VM_DIR = Path(__file__).resolve().parents[1]
FAKE_KEY = "AQ" + "." + "k" * 50  # 53 characters, like the real format

pytestmark = pytest.mark.skipif(
    not all(shutil.which(tool) for tool in ("bash", "curl", "git")),
    reason="needs bash, curl and git",
)


class NtfyRecorder(http.server.BaseHTTPRequestHandler):
    """Stores every POST it receives, like ntfy.sh would."""

    received: ClassVar[list[dict[str, str]]] = []

    def do_POST(self) -> None:  # name required by http.server
        length = int(self.headers.get("Content-Length", 0))
        NtfyRecorder.received.append(
            {
                "path": self.path,
                "title": self.headers.get("Title", ""),
                "priority": self.headers.get("Priority", ""),
                "body": self.rfile.read(length).decode(),
            }
        )
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def ntfy_server():
    NtfyRecorder.received = []
    server = http.server.HTTPServer(("127.0.0.1", 0), NtfyRecorder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", NtfyRecorder.received
    server.shutdown()


def run_script(name: str, *args: str, env: dict[str, str], cwd: Path | None = None):
    return subprocess.run(
        ["bash", str(VM_DIR / name), *args],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, **env},
        cwd=cwd,
        timeout=120,
    )


# ---------------------------------------------------------------- notify_run


def test_notify_success_sends_ok_and_keeps_exit_code(ntfy_server) -> None:
    """A finished job sends 'OK' to the topic and exits 0."""
    url, received = ntfy_server
    result = run_script(
        "notify_run.sh", "F3 sweep", "true", env={"NTFY_SERVER": url, "NTFY_TOPIC": "ma-test"}
    )
    assert result.returncode == 0
    assert received[0]["path"] == "/ma-test"
    assert received[0]["title"] == "F3 sweep: OK"


def test_notify_failure_is_high_priority_and_passes_exit_code(ntfy_server) -> None:
    """A failed job is reported as FAILED with its code; the script exits with it."""
    url, received = ntfy_server
    result = run_script(
        "notify_run.sh",
        "battery",
        "bash",
        "-c",
        "exit 3",
        env={"NTFY_SERVER": url, "NTFY_TOPIC": "ma-test"},
    )
    assert result.returncode == 3
    assert received[0]["title"] == "battery: FAILED"
    assert received[0]["priority"] == "high"
    assert "Exit code 3" in received[0]["body"]


def test_notify_never_sends_command_output(ntfy_server) -> None:
    """Privacy: what the job prints stays on the VM."""
    url, received = ntfy_server
    run_script(
        "notify_run.sh",
        "job",
        "echo",
        "PRIVATE-OUTPUT",
        env={"NTFY_SERVER": url, "NTFY_TOPIC": "ma-test"},
    )
    assert "PRIVATE-OUTPUT" not in str(received)


def test_notify_job_name_is_sanitised(ntfy_server) -> None:
    """Only [A-Za-z0-9 ._-] and at most 60 characters reach the server."""
    url, received = ntfy_server
    run_script(
        "notify_run.sh",
        "a/b;$(x)" + "z" * 100,
        "true",
        env={"NTFY_SERVER": url, "NTFY_TOPIC": "ma-test"},
    )
    name = received[0]["title"].removesuffix(": OK")
    assert set(name) <= set("abxz") and len(name) <= 60


def test_notify_unreachable_server_does_not_change_result() -> None:
    """A dead server only prints a warning; the job's exit code wins."""
    result = run_script(
        "notify_run.sh",
        "job",
        "bash",
        "-c",
        "exit 0",
        env={"NTFY_SERVER": "http://127.0.0.1:9", "NTFY_TOPIC": "ma-test"},
    )
    assert result.returncode == 0
    assert "could not be sent" in result.stderr


def test_notify_without_topic_still_runs_the_job(tmp_path: Path) -> None:
    """No topic configured: the job runs and nothing is sent."""
    marker = tmp_path / "ran"
    env = {"NTFY_TOPIC": "", "NTFY_SERVER": "http://127.0.0.1:9"}
    result = run_script("notify_run.sh", "job", "touch", str(marker), env=env)
    assert result.returncode == 0 and marker.exists()


# ------------------------------------------------------------ set_ntfy_topic


def test_topic_created_once_and_replaced_only_with_new(tmp_path: Path, ntfy_server) -> None:
    """Creates one random topic line; refuses to overwrite it without --new."""
    url, received = ntfy_server
    bashrc = tmp_path / ".bashrc"
    bashrc.write_text("source ~/venv-audio/bin/activate\n")
    env = {"BASHRC": str(bashrc), "NTFY_SERVER": url}

    first = run_script("set_ntfy_topic.sh", env=env)
    lines = [line for line in bashrc.read_text().splitlines() if "NTFY_TOPIC" in line]
    assert first.returncode == 0 and len(lines) == 1
    topic = lines[0].split('"')[1]
    assert topic.startswith("ma-") and len(topic) == 3 + 24
    assert received[0]["path"] == "/" + topic  # test notification sent

    assert run_script("set_ntfy_topic.sh", env=env).returncode == 1
    run_script("set_ntfy_topic.sh", "--new", env=env)
    lines = [line for line in bashrc.read_text().splitlines() if "NTFY_TOPIC" in line]
    assert len(lines) == 1 and topic not in lines[0]
    assert bashrc.read_text().startswith("source ~/venv-audio/bin/activate")


# ------------------------------------------------------------- session_check


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def make_repo_with_origin(root: Path, name: str, files: dict[str, str]) -> Path:
    """A bare 'origin' plus a clone of it, like the repos on the VM."""
    seed = root / f"{name}-seed"
    seed.mkdir()
    git("init", "-q", "-b", "main", cwd=seed)
    for file_name, content in files.items():
        (seed / file_name).write_text(content)
    git("add", ".", cwd=seed)
    git("-c", "user.email=t@e.invalid", "-c", "user.name=t", "commit", "-q", "-m", "init", cwd=seed)
    origin = root / f"{name}.git"
    git("clone", "-q", "--bare", str(seed), str(origin), cwd=root)
    clone = root / name
    git("clone", "-q", str(origin), str(clone), cwd=root)
    return clone


@pytest.fixture
def vm_layout(tmp_path: Path) -> dict[str, str]:
    """jcode-adaptado with code/ inside, a venv path, a read-only datos/."""
    jcode = make_repo_with_origin(
        tmp_path,
        "jcode-adaptado",
        {
            "test_ok.py": "def test_ok():\n    assert True\n",
            ".gitignore": "code/\nlogs/\n",
            # ma.3: session_check reads settings.json with jcode's own parser
            "settings.json": (VM_DIR.parent / "settings.json").read_text(encoding="utf-8"),
        },
    )
    code = tmp_path / "code-clone"
    shutil.move(str(make_repo_with_origin(tmp_path, "code", {"README.md": "x\n"})), code)
    shutil.move(str(code), jcode / "code")
    data = tmp_path / "datos"
    data.mkdir()
    (data / "DEMAND").mkdir()
    data.chmod(stat.S_IRUSR | stat.S_IXUSR)
    venv = tmp_path / "venv-audio"
    yield {
        "JCODE_DIR": str(jcode),
        "CODE_DIR": str(jcode / "code"),
        "DATA_DIR": str(data),
        "VENV_DIR": str(venv),
        "VIRTUAL_ENV": str(venv),
        "GEMINI_API_KEY": FAKE_KEY,
        "NTFY_TOPIC": "ma-test",
        # As on the VM: the active venv's python (with pytest) comes first.
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
    }
    data.chmod(stat.S_IRWXU)  # let pytest clean up


RUNNING_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


@pytest.mark.skipif(RUNNING_AS_ROOT, reason="root can write to a chmod 555 folder")
def test_session_green_when_everything_is_in_place(vm_layout) -> None:
    """Clean repos, key, read-only data, passing tests -> GREEN and exit 0."""
    result = run_script("session_check.sh", env=vm_layout)
    assert result.returncode == 0, result.stdout
    assert "GREEN" in result.stdout
    assert "read-only" in result.stdout
    assert FAKE_KEY not in result.stdout + result.stderr  # never shows the key


def test_session_green_with_data_not_mounted_yet(vm_layout, tmp_path: Path) -> None:
    """Before mount_data.sh runs, a missing datos/ is only a warning."""
    env = {**vm_layout, "DATA_DIR": str(tmp_path / "not-mounted")}
    result = run_script("session_check.sh", env=env)
    assert result.returncode == 0, result.stdout
    assert "datos/ not mounted" in result.stdout
    assert FAKE_KEY not in result.stdout + result.stderr  # never shows the key


def test_session_refuses_to_pull_dirty_repo_and_keeps_changes(vm_layout) -> None:
    """Unexpected file in code/: RED, path listed, file left untouched."""
    stray = Path(vm_layout["CODE_DIR"]) / "edited_by_agent.js"
    stray.write_text("// change nobody asked for\n")
    result = run_script("session_check.sh", "--quick", env=vm_layout)
    assert result.returncode == 1
    assert "edited_by_agent.js" in result.stdout
    assert stray.exists()  # never restored automatically (plan decision)


def test_session_pulls_new_commits(vm_layout, tmp_path: Path) -> None:
    """A commit pushed from Windows arrives through 'git pull --ff-only'."""
    origin = tmp_path / "code.git"
    other = tmp_path / "windows-copy"
    git("clone", "-q", str(origin), str(other), cwd=tmp_path)
    (other / "new.txt").write_text("from Windows\n")
    git("add", ".", cwd=other)
    git("-c", "user.email=t@e.invalid", "-c", "user.name=t", "commit", "-q", "-m", "w", cwd=other)
    git("push", "-q", cwd=other)
    run_script("session_check.sh", "--quick", env=vm_layout)
    assert (Path(vm_layout["CODE_DIR"]) / "new.txt").exists()


def test_session_detects_writable_data_and_removes_probe(vm_layout) -> None:
    """datos/ writable -> RED, and the probe file does not stay behind."""
    data = Path(vm_layout["DATA_DIR"])
    data.chmod(stat.S_IRWXU)
    result = run_script("session_check.sh", "--quick", env=vm_layout)
    assert result.returncode == 1
    assert "WRITABLE" in result.stdout
    assert [entry.name for entry in data.iterdir()] == ["DEMAND"]


def test_session_red_without_key(vm_layout) -> None:
    """Missing key -> RED."""
    result = run_script("session_check.sh", "--quick", env={**vm_layout, "GEMINI_API_KEY": ""})
    assert result.returncode == 1 and "GEMINI_API_KEY is empty" in result.stdout


def test_session_red_when_jcode_tests_fail(vm_layout) -> None:
    """A failing jcode test turns the light red; the repo stays clean after."""
    jcode = Path(vm_layout["JCODE_DIR"])
    (jcode / "test_ok.py").write_text("def test_ok():\n    assert False\n")
    git("-c", "user.email=t@e.invalid", "-c", "user.name=t", "commit", "-qam", "break", cwd=jcode)
    result = run_script("session_check.sh", env=vm_layout)
    assert result.returncode == 1 and "jcode tests: " in result.stdout
    assert git("status", "--porcelain", cwd=jcode) == ""  # no cache files left


def test_session_red_when_venv_not_active(vm_layout) -> None:
    result = run_script("session_check.sh", "--quick", env={**vm_layout, "VIRTUAL_ENV": ""})
    assert result.returncode == 1 and "virtual environment not active" in result.stdout


# ------------------------------------------------- setup_vm.sh: Node install


FAKE_NODE_VERSION = "v99.1.0"
FAKE_NODE_DIR = f"node-{FAKE_NODE_VERSION}-linux-x64"


def build_fake_node_release(dist: Path, corrupt: bool) -> None:
    """A nodejs.org/dist look-alike: index.json, tarball and SHA-256 list."""
    import hashlib
    import json
    import tarfile

    (dist / FAKE_NODE_VERSION).mkdir(parents=True)
    (dist / "index.json").write_text(
        json.dumps(
            [
                {"version": "v100.0.0", "lts": False},  # newest, but not LTS
                {"version": FAKE_NODE_VERSION, "lts": "Fake"},
            ]
        )
    )
    staging = dist / "staging" / FAKE_NODE_DIR / "bin"
    staging.mkdir(parents=True)
    for tool, output in (("node", FAKE_NODE_VERSION), ("npm", "99.0.0")):
        script = staging / tool
        script.write_text(f"#!/bin/sh\necho {output}\n")
        script.chmod(0o755)
    archive = dist / FAKE_NODE_VERSION / f"{FAKE_NODE_DIR}.tar.xz"
    with tarfile.open(archive, "w:xz") as tar:
        tar.add(staging.parent, arcname=FAKE_NODE_DIR)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if corrupt:
        digest = "0" * 64
    (dist / FAKE_NODE_VERSION / "SHASUMS256.txt").write_text(f"{digest}  {archive.name}\n")


@pytest.fixture
def node_dist_server(tmp_path: Path):
    def start(corrupt: bool):
        dist = tmp_path / "dist"
        build_fake_node_release(dist, corrupt)
        handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(dist), **k)
        handler.log_message = lambda *a: None
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_port}"

    servers: list[http.server.ThreadingHTTPServer] = []
    yield start
    for server in servers:
        server.shutdown()


def run_install_node(home: Path, dist_url: str):
    return subprocess.run(
        ["bash", "-c", f'source "{VM_DIR / "setup_vm.sh"}" && install_node'],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home), "NODE_DIST_URL": dist_url},
        timeout=60,
    )


def test_node_lts_installed_verified_and_added_to_path_once(
    tmp_path: Path, node_dist_server
) -> None:
    """Picks the newest LTS (not the newest release), installs it, PATH line once."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bashrc").write_text("source ~/venv-audio/bin/activate\n")
    url = node_dist_server(corrupt=False)
    first = run_install_node(home, url)
    assert first.returncode == 0, first.stderr
    assert "SHA-256 verified" in first.stdout
    assert (home / ".local/opt/node/bin/node").exists()
    assert (home / ".local/opt/node").resolve().name == FAKE_NODE_DIR
    second = run_install_node(home, url)
    assert "Skipped" in second.stdout  # never upgrades Node silently
    path_lines = [
        line for line in (home / ".bashrc").read_text().splitlines() if "node/bin" in line
    ]
    assert path_lines == ['export PATH="$HOME/.local/opt/node/bin:$PATH"']


def test_node_with_wrong_checksum_is_not_installed(tmp_path: Path, node_dist_server) -> None:
    """A tampered or broken download stops the script before unpacking."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bashrc").write_text("")
    result = run_install_node(home, node_dist_server(corrupt=True))
    assert result.returncode != 0
    assert "does not match" in result.stderr
    assert not (home / ".local/opt").exists() or not any((home / ".local/opt").iterdir())


def test_session_red_when_a_helper_prints_nothing(vm_layout, tmp_path: Path) -> None:
    """A crashed helper must never look like a passed check (fail closed):
    here every python3 call silently succeeds without printing anything."""
    stubs = tmp_path / "silent-python"
    stubs.mkdir()
    (stubs / "python3").write_text("#!/bin/sh\nexit 0\n")
    (stubs / "python3").chmod(0o755)
    env = {**vm_layout, "PATH": f"{stubs}{os.pathsep}{vm_layout['PATH']}"}
    result = run_script("session_check.sh", "--quick", env=env)
    assert result.returncode == 1
    assert "the check printed nothing" in result.stdout
