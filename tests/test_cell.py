"""Tests for the agent cells (jcode 0.10.0-ma.3, plan step 1.0.b1; Abschrift
plan v12: E5, E7, A1, A5, A8) and for the search_files/list_files fix.

Part 1 needs nothing: configuration and the bwrap command line.
Part 2 builds real cells and tries to break out of them; it is skipped when
bubblewrap is missing or the kernel refuses user namespaces (then
vm/install_ma3.sh step 3 is the place to look).
Run: python -m pytest tests/test_cell.py -q
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from conftest import REPO

import jcode_cell as cell
import jcode_extras as extras

ABSCHRIFT_SETTINGS = REPO / "examples" / "abschrift" / "settings.json"
GEMINI_PROFILE = REPO / "examples" / "abschrift" / "gemini-cell.json"
OUR_SETTINGS = REPO / "settings.json"
FAKE_SECRET = "fake-secret-" + "x" * 20


def bwrap_works() -> bool:
    if shutil.which("bwrap") is None:
        return False
    probe = subprocess.run(["bwrap", "--ro-bind", "/", "/", "--unshare-pid", "--proc", "/proc",
                            "--dev", "/dev", "true"], capture_output=True, check=False)
    return probe.returncode == 0


needs_bwrap = pytest.mark.skipif(not bwrap_works(), reason="bubblewrap missing or user "
                                 "namespaces refused (see vm/install_ma3.sh, step 3)")


def config_of(**sandbox) -> cell.CellConfig:
    warnings: list[str] = []
    config = cell.parse_cell_config({"enabled": True, **sandbox}, warnings)
    assert warnings == []
    return config


def mount_pairs(argv: list[str]) -> list[tuple[str, str]]:
    """[(option, path)] for every mount option in a bwrap command line."""
    options = {"--ro-bind", "--bind", "--tmpfs", "--dir"}
    pairs = []
    for index, word in enumerate(argv):
        if word in options:
            pairs.append((word, argv[index + 1]))
        if word == "--":
            break
    return pairs


def cell_env(argv: list[str]) -> dict[str, str]:
    env = {}
    for index, word in enumerate(argv):
        if word == "--setenv":
            env[argv[index + 1]] = argv[index + 2]
        if word == "--":
            break
    return env


# ===================================================== Part 1 · configuration
def test_no_section_means_no_cell():
    """Compatibility: mejorador-audio without "sandbox" works as before."""
    warnings: list[str] = []
    assert cell.parse_cell_config(None, warnings) == cell.CellConfig()
    assert cell.CellConfig().enabled is False and warnings == []


@pytest.mark.parametrize("raw, fragment", [
    ({"enabled": "yes"}, "sandbox.enabled must be true or false"),
    ({"read_only": "~/x"}, "sandbox.read_only must be a list"),
    ({"read_only": ["relative/path"]}, "is not an absolute path"),
    ({"read_only": ["/"]}, "is not an absolute path"),
    ({"read_write": ["~/../etc"]}, "is not an absolute path"),
    ({"read_only_in_workdir": ["/abs"]}, "is not a relative path"),
    ({"read_only_in_workdir": ["../up"]}, "is not a relative path"),
    ({"env": {"OPENCODE_API_KEY": "x"}}, "no secrets in settings.json"),
    ({"env_keep": ["PATH", "GH_TOKEN"]}, "look like secrets"),
    ({"unknown_option": 1}, "sandbox.unknown_option is unknown"),
    ("on", "sandbox must be an object"),
])
def test_configuration_errors_are_warnings(raw, fragment):
    warnings: list[str] = []
    config = cell.parse_cell_config(raw, warnings)
    assert any(fragment in w for w in warnings), warnings
    assert "GH_TOKEN" not in config.env_keep


def test_shipped_settings():
    """Ours: cell prepared but OFF until step 1.0.e; Abschrift and Gemini CLI:
    on and required; Gemini CLI cannot write tests/acceptance/."""
    ours = cell.load_cell_settings(OUR_SETTINGS)
    abschrift = cell.load_cell_settings(ABSCHRIFT_SETTINGS)
    gemini = cell.load_cell_settings(GEMINI_PROFILE)
    for config, passthrough, warnings in (ours, abschrift, gemini):
        assert warnings == []
    assert ours[0].enabled is False and ours[0].required is False
    assert ours[1] == ("KAGGLE_KEY", "KAGGLE_API_TOKEN")
    assert "/mnt/datos" in ours[0].read_only
    for config in (abschrift[0], gemini[0]):
        assert config.enabled and config.required
        assert "~/.bashrc" in config.expect_absent and "/mnt/datos" in config.expect_absent
    assert abschrift[0].git_read_only is True and abschrift[1] == ()
    assert dict(abschrift[0].env)["PLAYWRIGHT_BROWSERS_PATH"] == "~/.cache/ms-playwright-abschrift"
    assert gemini[0].read_only_in_workdir == ("tests/acceptance",)
    assert gemini[0].read_write == ("~/.gemini",) and gemini[0].interactive is True
    assert dict(gemini[0].env)["NO_BROWSER"] == "true"


def test_launch_env_holds_no_secret():
    assert not any(extras.is_secret_name(name) for name in cell.LAUNCH_ENV)


# ======================================================== Part 1 · argv
@pytest.fixture
def layout(tmp_path):
    """A fake home with the folders of the VM and a work folder with .git."""
    home = tmp_path / "home" / "fran"
    work = home / "jcode-projekte" / "abschrift" / "code"
    (work / ".git").mkdir(parents=True)
    (work / "tests" / "acceptance").mkdir(parents=True)
    (work / "tests" / "acceptance" / "a.spec.js").write_text("// acceptance test\n")
    node_real = home / ".local" / "opt" / "node-v24-linux-x64"
    (node_real / "bin").mkdir(parents=True)
    (home / ".local" / "opt" / "node").symlink_to(node_real)
    (home / ".bashrc").write_text(f"export GEMINI_API_KEY={FAKE_SECRET}\n")
    (home / ".gemini").mkdir()
    (home / "repos").mkdir()
    (tmp_path / "outside-home.txt").write_text("host /tmp is not shared with cells\n")
    return home, work, node_real


def test_argv_hides_home_and_orders_mounts(layout):
    home, work, node_real = layout
    config = config_of(read_only=["~/.local/opt/node"], read_only_in_workdir=["tests/acceptance"])
    argv, notes = cell.build_argv(config, command=["true"], workdir=work, home=home,
                                  environ={"PATH": "/usr/bin"})
    pairs = mount_pairs(argv)
    assert argv[argv.index("--ro-bind") + 1: argv.index("--ro-bind") + 3] == ["/", "/"]
    assert ("--tmpfs", "/home") in pairs and ("--tmpfs", str(home)) in pairs
    assert ("--ro-bind", str(node_real)) in pairs  # symlink resolved ...
    assert "--symlink" in argv and str(home / ".local/opt/node") in argv  # ... and recreated
    positions = {pair: i for i, pair in enumerate(pairs)}
    assert positions[("--tmpfs", str(home))] < positions[("--bind", str(work))]
    # every folder between the work folder and the protected one is a mount
    # point of its own (cannot be renamed), then the protected one read-only
    assert positions[("--bind", str(work))] < positions[("--bind", str(work / "tests"))] \
        < positions[("--ro-bind", str(work / "tests" / "acceptance"))]
    assert ("--ro-bind", str(work / ".git")) in pairs
    assert "--new-session" in argv and argv[-1] == "true" and notes == []


def test_argv_environment_is_a_short_list(layout):
    home, work, _ = layout
    environ = {"PATH": "/usr/bin", "LANG": "C.UTF-8", "GEMINI_API_KEY": FAKE_SECRET,
               "SSH_AUTH_SOCK": "/run/user/1/ssh", "DISPLAY": ":0", "KAGGLE_KEY": "k",
               "VIRTUAL_ENV": "/v", "NTFY_TOPIC": "t"}
    config = config_of(env={"PLAYWRIGHT_BROWSERS_PATH": "~/.cache/pw"})
    argv, _ = cell.build_argv(config, command=["true"], workdir=work, home=home,
                              environ=environ, passthrough=["KAGGLE_KEY"])
    env = cell_env(argv)
    assert env == {"PATH": "/usr/bin", "LANG": "C.UTF-8", "VIRTUAL_ENV": "/v", "KAGGLE_KEY": "k",
                   "PLAYWRIGHT_BROWSERS_PATH": str(home / ".cache/pw"), "HOME": str(home)}
    assert "--clearenv" in argv


def test_argv_interactive_keeps_the_terminal(layout, monkeypatch):
    """Kernel without TIOCSTI (dev.tty.legacy_tiocsti=0): the terminal is kept as it is."""
    home, work, _ = layout
    monkeypatch.setattr(cell, "tiocsti_allowed", lambda: False)
    argv, _ = cell.build_argv(config_of(interactive=True), command=["true"], workdir=work,
                              home=home, environ={})
    assert "--new-session" not in argv


def test_argv_errors_and_notes(layout):
    home, work, _ = layout
    with pytest.raises(cell.CellError, match="both read-only and writable"):
        cell.build_argv(config_of(read_only=["~/repos"], read_write=["~/repos"]),
                        command=["true"], workdir=work, home=home, environ={})
    with pytest.raises(cell.CellError, match="must be a real folder"):
        cell.build_argv(config_of(read_only_in_workdir=["missing/dir"]), command=["true"],
                        workdir=work, home=home, environ={})
    _, notes = cell.build_argv(config_of(read_only=["~/not-there"]), command=["true"],
                               workdir=work, home=home, environ={})
    assert notes == ["~/not-there: missing - not mounted"]


def test_prepare_workdir_creates_protected_folder(tmp_path):
    config = config_of(read_only_in_workdir=["tests/acceptance"])
    cell.prepare_workdir(config, tmp_path)
    assert (tmp_path / "tests" / "acceptance").is_dir()


# ===================================================== Part 2 · real cells
def run_in_cell(config: cell.CellConfig, home: Path, work: Path | None, script: str,
                environ: dict | None = None, passthrough=()) -> subprocess.CompletedProcess:
    argv, _ = cell.build_argv(config, command=["/bin/sh", "-c", script], workdir=work, home=home,
                              environ=environ or {"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
                              passthrough=passthrough)
    return subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False,
                          env=cell.LAUNCH_ENV)


@needs_bwrap
def test_check_cell_all_ok(layout):
    home, work, _ = layout
    outside = str(home.parent.parent / "outside-home.txt")  # host /tmp, outside home
    config = config_of(read_only=["~/.local/opt/node"], read_only_in_workdir=["tests/acceptance"],
                       expect_absent=["~/.bashrc", "~/.gemini", "~/repos", "/mnt/datos", outside])
    results = cell.check_cell(config, workdir=work, environ={"PATH": "/usr/bin:/bin",
                              "GEMINI_API_KEY": FAKE_SECRET}, home=home)
    assert [level for level, _ in results if level != "OK"] == [], results
    text = "\n".join(t for _, t in results)
    assert str(home / ".bashrc") in text and "read-only inside" in text


@needs_bwrap
def test_check_cell_reports_what_is_wrong(layout):
    """The check must notice a hole: here a configured path that should
    have been hidden is mounted on purpose."""
    home, work, _ = layout
    config = config_of(read_only=["~/repos"], expect_absent=["~/repos"])
    results = cell.check_cell(config, workdir=work, environ={"PATH": "/usr/bin:/bin"}, home=home)
    assert ("FAIL", f"VISIBLE inside the cell: {home / 'repos'}") in results


@needs_bwrap
def test_check_cell_without_bwrap(layout):
    home, work, _ = layout
    results = cell.check_cell(config_of(), workdir=work, environ={}, home=home,
                              bwrap="/nonexistent/bwrap")
    assert results[0][0] == "FAIL" and "not installed" in results[0][1]


@needs_bwrap
def test_escape_attempts_fail(layout):
    """What an agent could try from run_command; every attempt must fail."""
    home, work, _ = layout
    config = config_of(read_only_in_workdir=["tests/acceptance"])
    script = (
        "cat ~/.bashrc 2>/dev/null && echo LEAK-bashrc; "
        "ls ~/repos 2>/dev/null && echo LEAK-repos; "
        "mv tests tests_old 2>/dev/null && echo ESCAPE-mv-parent; "
        "mv tests/acceptance x 2>/dev/null && echo ESCAPE-mv; "
        "echo x > tests/acceptance/new.js 2>/dev/null && echo ESCAPE-write; "
        "rm -f tests/acceptance/a.spec.js 2>/dev/null; "
        "echo '[core] fsmonitor = evil' >> .git/config 2>/dev/null && echo ESCAPE-git; "
        "mv .git g 2>/dev/null && echo ESCAPE-mv-git; "
        "tr '\\0' '\\n' < /proc/1/environ | grep -q GEMINI && echo LEAK-pid1; "
        "touch made-in-cell && echo work-ok"
    )
    result = run_in_cell(config, home, work, script)
    assert result.stdout.split() == ["work-ok"], result.stdout + result.stderr
    assert (work / "tests" / "acceptance" / "a.spec.js").exists()
    assert (work / "made-in-cell").exists()  # the work folder itself is writable
    assert not (work / "tests_old").exists()


@needs_bwrap
def test_background_processes_die_with_the_cell(layout):
    """Own PID namespace: what a command leaves running in the background
    ends with the cell (the original run_command left such processes)."""
    home, work, _ = layout
    tag = f"cell-test-{uuid.uuid4().hex}"
    started = run_in_cell(config_of(), home, work,
                          f"bash -c 'exec -a {tag} sleep 300' & sleep 0.5; pgrep -f {tag} >/dev/null && echo started")
    assert started.stdout.strip() == "started", started.stderr  # the witness really ran
    alive = subprocess.run(["pgrep", "-f", tag], capture_output=True, check=False)
    assert alive.returncode == 1, "a background process survived the cell"


@needs_bwrap
def test_gemini_profile_in_a_real_cell(layout):
    """The shipped Gemini CLI profile against the VM layout: tests/acceptance
    and the other projects are out of reach, ~/.gemini is writable."""
    home, _, _ = layout
    clone = home / "abschrift-gemini"
    (clone / ".git").mkdir(parents=True)
    config, passthrough, _ = cell.load_cell_settings(GEMINI_PROFILE)
    results = cell.check_cell(config, workdir=clone, environ={"PATH": "/usr/bin:/bin",
                              "GEMINI_API_KEY": FAKE_SECRET}, passthrough=passthrough, home=home)
    assert [r for r in results if r[0] != "OK"] == []
    script = "touch ~/.gemini/oauth && echo gemini-ok; touch .git/x && echo git-ok; " \
             "ls ~/jcode-projekte 2>/dev/null && echo LEAK"
    result = run_in_cell(config, home, clone, script)
    assert result.stdout.split() == ["gemini-ok", "git-ok"], result.stdout + result.stderr


@needs_bwrap
def test_cli_run_starts_bwrap_with_minimal_environment(layout, tmp_path):
    """'jcode_cell.py run' replaces itself with bwrap: bwrap must not inherit
    the caller's environment (it is process 1 and readable inside)."""
    home, work, _ = layout
    settings = tmp_path / "cell.json"
    settings.write_text(json.dumps({"sandbox": {"enabled": True}}))
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "GEMINI_API_KEY": FAKE_SECRET}
    proc = subprocess.run(
        [sys.executable, str(REPO / "jcode_cell.py"), "run", "--settings", str(settings),
         "--workdir", str(work), "--", "/bin/sh", "-c", "tr '\\0' '\\n' < /proc/1/environ; env"],
        capture_output=True, text=True, env=env, check=False, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert FAKE_SECRET not in proc.stdout and "GEMINI" not in proc.stdout


def test_cli_run_refuses_a_disabled_cell(tmp_path):
    settings = tmp_path / "cell.json"
    settings.write_text(json.dumps({"sandbox": {"enabled": False}}))
    proc = subprocess.run([sys.executable, str(REPO / "jcode_cell.py"), "run", "--settings",
                           str(settings), "--", "true"], capture_output=True, text=True,
                          check=False, timeout=60)
    assert proc.returncode == 2 and "disabled" in proc.stdout


# ================================================ jcode with a cell (E7)
@pytest.fixture
def jcell(jc, monkeypatch, layout):
    """The jc fixture with a cell switched on and HOME = the fake home."""
    home, _, _ = layout
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(jc.mod, "CELL", config_of(expect_absent=["~/.bashrc"]))
    return jc


def test_compat_our_settings_run_without_cell(jc, monkeypatch):
    """mejorador-audio today: no cell, /bin/sh -c in code/ as before."""
    args, shell, env = jc.mod._command_process("echo hi")
    assert (args, shell) == ("echo hi", True) and "PATH" in env
    assert jc.mod.CELL.enabled is False and jc.mod._cell_ready() is True


@needs_bwrap
def test_run_command_in_cell(jcell, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_SECRET)
    out = jcell.mod.run_command("cat ~/.bashrc; touch ok.txt; env | grep -c fake-secret")
    assert "exit_code=1" in out and "No such file" in out  # ~/.bashrc does not exist
    assert "stdout:\n0\n" in out and (jcell.sandbox / "ok.txt").exists()
    args, shell, env = jcell.mod._command_process("true")
    assert shell is False and args[0].endswith("bwrap") and env == cell.LAUNCH_ENV


@needs_bwrap
def test_run_command_timeout_ends_background_work(jcell):
    tag = f"cell-timeout-{uuid.uuid4().hex}"
    out = jcell.mod.run_command(f"bash -c 'exec -a {tag} sleep 300' & wait", timeout=2)
    assert out.startswith("TIMEOUT")
    alive = subprocess.run(["pgrep", "-f", tag], capture_output=True, check=False)
    assert alive.returncode == 1


def test_cell_ready_refuses_required_but_disabled(jc, monkeypatch, capsys):
    monkeypatch.setattr(jc.mod, "CELL", cell.CellConfig(enabled=False, required=True))
    assert jc.mod._cell_ready() is False
    assert "does not start without its cell" in capsys.readouterr().err


def test_cell_ready_refuses_a_broken_cell(jc, monkeypatch, capsys):
    monkeypatch.setattr(jc.mod, "CELL", config_of())
    monkeypatch.setattr(jc.mod, "_BWRAP", "/nonexistent/bwrap")
    assert jc.mod._cell_ready() is False
    assert "jcode does not start" in capsys.readouterr().err


@needs_bwrap
def test_cell_ready_accepts_a_working_cell(jcell):
    assert jcell.mod._cell_ready() is True


def test_abschrift_jcode_does_not_start_without_cell(tmp_path):
    """End to end: Abschrift settings with the cell switched off -> exit 2,
    before any model is contacted (no key needed to see it)."""
    project = tmp_path / "abschrift"
    project.mkdir()
    settings = json.loads(ABSCHRIFT_SETTINGS.read_text(encoding="utf-8"))
    settings["sandbox"]["enabled"] = False
    (project / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    env = extras.sanitized_env(os.environ)[0]
    proc = subprocess.run([sys.executable, str(REPO / "jcode.py"), "--project", str(project), "x"],
                          capture_output=True, text=True, env=env, timeout=60, check=False,
                          stdin=subprocess.DEVNULL)
    assert proc.returncode == 2
    assert "does not start without its cell" in proc.stderr


# ===================================== search_files / list_files (security)
def test_search_files_stays_inside_the_sandbox(jc, tmp_path):
    """The original followed '..' in glob and symlinks out of code/: one call
    could send ~/.bashrc (with the API key) to the model."""
    secret_file = tmp_path / "bashrc"
    secret_file.write_text(f"export GEMINI_API_KEY={FAKE_SECRET}\n")
    (jc.sandbox / "leak").symlink_to(secret_file)
    (jc.sandbox / "ok.txt").write_text("GEMINI is fine here\n")
    assert "not allowed" in jc.mod.search_files("GEMINI", "../bashrc")
    assert "not allowed" in jc.mod.search_files("GEMINI", "**/../../bashrc")
    hits = jc.mod.search_files("GEMINI")
    assert FAKE_SECRET not in hits and "ok.txt:1" in hits
    assert "leak" not in jc.mod.list_files()


# ============================== interactive cells: no keystroke injection
INJECT = ("import fcntl, termios\n"
          "for ch in b'echo INJECTED\\n':\n"
          "    fcntl.ioctl(0, termios.TIOCSTI, bytes([ch]))\n")
PTY_DRIVER = r'''
import os, pty, select, sys, time
pid, fd = pty.fork()
if pid == 0:
    os.execvp("bash", ["bash", "-c", sys.argv[1] + '; read -t 3 line; echo "GOT:[$line]"'])
out, end = b"", time.time() + 20
while time.time() < end:
    if select.select([fd], [], [], 0.5)[0]:
        try:
            data = os.read(fd, 4096)
        except OSError:
            break
        if not data:
            break
        out += data
os.waitpid(pid, 0)
sys.stdout.write(out.decode(errors="replace"))
'''


@needs_bwrap
@pytest.mark.skipif(shutil.which("script") is None, reason="util-linux 'script' missing")
def test_interactive_cell_cannot_type_into_the_terminal(tmp_path):
    """Gemini CLI's cell keeps a terminal. A process inside must not push
    keystrokes (TIOCSTI) into Fran's shell: they would run OUTSIDE any cell
    once Gemini CLI ends. The shell after the cell must read nothing."""
    settings = tmp_path / "interactive.json"
    settings.write_text(json.dumps({"sandbox": {"enabled": True, "interactive": True}}))
    work = tmp_path / "work"
    work.mkdir()
    (work / "inject.py").write_text(INJECT)
    command = (f"{sys.executable} {REPO / 'jcode_cell.py'} run --settings {settings} "
               f"--workdir {work} -- python3 inject.py")
    proc = subprocess.run([sys.executable, "-c", PTY_DRIVER, command], capture_output=True,
                          text=True, timeout=60, check=False)
    assert "GOT:[]" in proc.stdout, proc.stdout


def test_interactive_cell_gets_its_own_terminal_only_when_needed(layout, monkeypatch):
    home, work, _ = layout
    config = config_of(interactive=True)
    monkeypatch.setattr(cell, "tiocsti_allowed", lambda: True)
    argv, _ = cell.build_argv(config, command=["gemini", "--yolo"], workdir=work, home=home,
                              environ={})
    assert "--new-session" in argv
    assert argv[-7:] == [cell.SCRIPT_TOOL, "--quiet", "--return", "--flush", "--command",
                         "gemini --yolo", "/dev/null"]
    monkeypatch.setattr(cell, "tiocsti_allowed", lambda: False)
    argv, _ = cell.build_argv(config, command=["gemini"], workdir=work, home=home, environ={})
    assert "--new-session" not in argv and argv[-1] == "gemini"


# ================================= abstract unix sockets (X11, D-Bus)
FAKE_X_SERVER = r'''
import socket, sys, time
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.bind("\0/tmp/.X11-unix/X%s" % sys.argv[1])
s.listen(1)
print("ready", flush=True)
s.settimeout(30)
try:
    c, _ = s.accept()
    c.recv(12)
    c.sendall(bytes([int(sys.argv[2])]) + b"\0" * 7)
    time.sleep(0.5)
except OSError:
    pass
'''


def with_fake_x(display: int, answer: int):
    server = subprocess.Popen([sys.executable, "-c", FAKE_X_SERVER, str(display), str(answer)],
                              stdout=subprocess.PIPE, text=True)
    assert server.stdout.readline().strip() == "ready"
    return server


@needs_bwrap
@pytest.mark.parametrize("answer, level", [(1, "FAIL"), (0, "OK")])
def test_check_reports_an_x_server_without_cookie(layout, answer, level):
    """Abstract sockets are not files: an X server reachable without its
    cookie must make the check fail (a process could type into windows)."""
    home, work, _ = layout
    display = 4000 + answer * 1000 + os.getpid() % 997  # unused display number
    server = with_fake_x(display, answer)
    try:
        results = cell.check_cell(config_of(), workdir=work, environ={"PATH": "/usr/bin:/bin"},
                                  home=home)
    finally:
        server.kill()
        server.wait()
    x11 = [r for r in results if f"X server :{display}" in r[1]]
    assert x11 and x11[0][0] == level, results


@needs_bwrap
def test_cell_without_network_sees_no_abstract_socket(layout):
    home, work, _ = layout
    server = with_fake_x(6000 + os.getpid() % 997, 1)
    try:
        results = cell.check_cell(config_of(network=False), workdir=work,
                                  environ={"PATH": "/usr/bin:/bin"}, home=home)
    finally:
        server.kill()
        server.wait()
    assert all(level == "OK" for level, _ in results), results
    assert not any("X server" in text for _, text in results)


@needs_bwrap
def test_check_reports_a_missing_pid_namespace(layout, monkeypatch):
    """If bwrap ran without its own PID namespace, the witness process outside
    would be visible in /proc: the check must say so."""
    home, work, _ = layout
    real_build = cell.build_argv

    def without_pid_namespace(*args, **kwargs):
        argv, notes = real_build(*args, **kwargs)
        return [word for word in argv if word != "--unshare-pid"], notes

    monkeypatch.setattr(cell, "build_argv", without_pid_namespace)
    results = cell.check_cell(config_of(), workdir=work, environ={"PATH": "/usr/bin:/bin"},
                              home=home)
    assert ("FAIL", "/proc shows processes outside the cell (PID namespace missing)") in results


@needs_bwrap
def test_check_reports_a_dbus_bus_on_an_abstract_socket(layout):
    """Old-style session buses (unix:abstract=/tmp/dbus-...) accept any
    process of the same user: reachable from the cell = FAIL."""
    home, work, _ = layout
    name = f"\0/tmp/dbus-celltest{os.getpid()}"
    listener = subprocess.Popen([sys.executable, "-c", (
        "import socket, sys, time\n"
        "s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
        f"s.bind({name!r}); s.listen(1); print('ready', flush=True); time.sleep(30)\n")],
        stdout=subprocess.PIPE, text=True)
    assert listener.stdout.readline().strip() == "ready"
    try:
        results = cell.check_cell(config_of(), workdir=work, environ={"PATH": "/usr/bin:/bin"},
                                  home=home)
    finally:
        listener.kill()
        listener.wait()
    assert any(level == "FAIL" and "D-Bus bus on an abstract socket" in text
               for level, text in results), results
