#!/usr/bin/env python3
# Part of jcode adapted for mejorador-audio and Abschrift (c) 2026 Francis Garcia Reyes,
# MIT License (see LICENSE).
"""Cells for the agents of the VM: run a command inside bubblewrap (bwrap).

Origin: mejorador-audio plan step 1.0.b1 (jcode 0.10.0-ma.3), decision of
2026-10-20 "one Linux user, one CELL per agent"; Abschrift plan v12,
requirements E5, E7, A1, A5, A8.

What a cell is, mechanically: bwrap builds a private view of the file system
for one command. Each mount below is applied in this order:
    /                 read-only (the system: /usr, /etc, ...)
    /dev, /proc       fresh; /proc only shows the cell's own processes
                      (own PID namespace), so /proc/<jcode>/environ is gone
    /home, /mnt, /media, /run/user, /srv, /tmp, /var/tmp
                      replaced by EMPTY in-memory folders: ~/.bashrc,
                      other projects and /mnt/datos simply do not exist
    $HOME             an empty, writable, throw-away folder
    read_only / read_write paths from the configuration, the work folder
                      (writable) and protected sub-folders (read-only)
The environment starts EMPTY; only a short list of harmless names (PATH,
LANG, ...), the env_passthrough names and the configured values are set.
The network stays open (the agents need their provider; the Abschrift plan
documents that). One cell per command: nothing survives in $HOME or /tmp.

Why sub-folders of the work folder get "self-binds": a read-only mount at
code/tests/acceptance alone can be bypassed with 'mv tests tests_old' (the
mount moves along, a new tests/acceptance is writable). A mount point cannot
be renamed, so every folder between the work folder and a protected path is
mounted onto itself first (tested in the cell tests).

Configuration ("sandbox" in a jcode settings.json or a cell profile file):
    enabled               false/true - jcode's run_command uses the cell
    required              true = jcode refuses to start without a cell
    read_only, read_write absolute paths or ~/...; missing ones are skipped
    read_only_in_workdir  relative paths inside the work folder (Gemini CLI:
                          tests/acceptance); created empty if missing
    git_read_only         true (default) = <work folder>/.git read-only, so
                          git commands run later OUTSIDE the cell can trust
                          the repository configuration and hooks
    env                   {"NAME": "value"} set inside (~ expanded)
    env_keep              names copied from the caller (default: BASE_ENV)
    expect_absent         paths the check must NOT see inside the cell
    interactive           true = a terminal for a screen program (Gemini CLI);
                          false = new session, no access to the terminal.
                          Where the kernel still allows TIOCSTI (keystroke
                          injection, dev.tty.legacy_tiocsti=1), an interactive
                          command gets its OWN pseudo-terminal (util-linux
                          'script') inside a new session: injected keys land
                          there, never in Fran's shell after the cell ends.
    network               true (default) = network open; false = no network
                          at all (the git sync cell needs none)

Command line (used by the start scripts and session_check.sh):
    python3 jcode_cell.py check --settings FILE [--workdir DIR]
    python3 jcode_cell.py run   --settings FILE [--workdir DIR] -- CMD [ARGS...]
    python3 jcode_cell.py argv  --settings FILE [--workdir DIR] -- CMD   (debug)
Exit codes: check 0 = all OK, 1 = a check failed, 2 = usage/config error;
run: the command's own exit code (bwrap replaces this process).
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shlex
import shutil
import subprocess
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

# 'python3 -I jcode_cell.py' (isolated mode, as the scripts call it) does not
# put this folder on sys.path; jcode_extras lives next to this file.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from jcode_extras import is_secret_name  # after the sys.path line above

SETTINGS_KEY = "sandbox"
BWRAP = "bwrap"
# Replaced by empty in-memory folders in every cell (see module docstring).
HIDDEN_DIRS = ("/home", "/mnt", "/media", "/run/user", "/srv", "/tmp", "/var/tmp")
# Harmless names copied into the cell when the caller has them set.
BASE_ENV = ("PATH", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "TZ",
            "TERM", "COLORTERM", "NO_COLOR", "FORCE_COLOR", "VIRTUAL_ENV", "USER", "LOGNAME")
PROBE_TIMEOUT_S = 30
PROBE_FILE_PREFIX = ".jcode-cell-probe-"
MARKER_HALVES = ("CELL_MARKER_A", "CELL_MARKER_B")
LEGACY_TIOCSTI = Path("/proc/sys/dev/tty/legacy_tiocsti")
SCRIPT_TOOL = "/usr/bin/script"  # util-linux (bsdutils on Ubuntu, always installed)
# bwrap itself becomes process 1 inside the cell and /proc/1/environ shows
# the environment it was STARTED with. So bwrap is always started with this
# minimal environment, never with the caller's (which may hold API keys).
LAUNCH_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"}
# Messages bwrap prints when the kernel or AppArmor refuses a user namespace.
NAMESPACE_DENIED_HINTS = ("setting up uid map", "No permissions to creat", "Operation not permitted",
                          "Permission denied", "Creating new namespace failed")


class CellError(Exception):
    """The cell cannot be built as configured; nothing was run."""


@dataclass(frozen=True)
class CellConfig:
    """Parsed "sandbox" section. Paths stay as written (~ expanded later)."""

    enabled: bool = False
    required: bool = False
    read_only: tuple[str, ...] = ()
    read_write: tuple[str, ...] = ()
    read_only_in_workdir: tuple[str, ...] = ()
    git_read_only: bool = True
    env: tuple[tuple[str, str], ...] = ()
    env_keep: tuple[str, ...] = BASE_ENV
    expect_absent: tuple[str, ...] = ()
    interactive: bool = False
    network: bool = True


# ------------------------------------------------------------- configuration
def parse_cell_config(raw, warnings: list[str]) -> CellConfig:
    """Reads the "sandbox" section; unknown or wrong values become warnings
    and fall back to "no cell" only where that is safe (see required)."""
    if raw is None:
        return CellConfig()
    if not isinstance(raw, dict):
        warnings.append("sandbox must be an object like {\"enabled\": false} - cell disabled")
        return CellConfig()
    known = {f for f in CellConfig.__dataclass_fields__} | {"_comment"}
    for name in sorted(set(raw) - known):
        warnings.append(f"sandbox.{name} is unknown - ignored")
    return CellConfig(
        enabled=_flag(raw, "enabled", False, warnings),
        required=_flag(raw, "required", False, warnings),
        read_only=_path_list(raw, "read_only", absolute=True, warnings=warnings),
        read_write=_path_list(raw, "read_write", absolute=True, warnings=warnings),
        read_only_in_workdir=_path_list(raw, "read_only_in_workdir", absolute=False,
                                        warnings=warnings),
        git_read_only=_flag(raw, "git_read_only", True, warnings),
        env=_env_values(raw.get("env"), warnings),
        env_keep=_names(raw, "env_keep", BASE_ENV, warnings),
        expect_absent=_path_list(raw, "expect_absent", absolute=True, warnings=warnings),
        interactive=_flag(raw, "interactive", False, warnings),
        network=_flag(raw, "network", True, warnings),
    )


def _flag(raw: dict, name: str, default: bool, warnings: list[str]) -> bool:
    value = raw.get(name, default)
    if isinstance(value, bool):
        return value
    warnings.append(f"sandbox.{name} must be true or false - using {str(default).lower()}")
    return default


def _path_list(raw: dict, name: str, absolute: bool, warnings: list[str]) -> tuple[str, ...]:
    values = raw.get(name, [])
    if not isinstance(values, list):
        warnings.append(f"sandbox.{name} must be a list of paths - ignored")
        return ()
    paths = []
    for value in values:
        if _valid_path(value, absolute):
            paths.append(value.rstrip("/") if len(value) > 1 else value)
        else:
            kind = "an absolute path or ~/..." if absolute else "a relative path inside the work folder"
            warnings.append(f"sandbox.{name}: {value!r} is not {kind} - skipped")
    return tuple(paths)


def _valid_path(value, absolute: bool) -> bool:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        return False
    parts = PurePosixPath(value).parts
    if ".." in parts:
        return False
    if absolute:
        return (value == "~" or value.startswith(("/", "~/"))) and value != "/"
    return not value.startswith(("/", "~")) and value.strip("./") != ""


def _names(raw: dict, name: str, default: tuple[str, ...], warnings: list[str]) -> tuple[str, ...]:
    values = raw.get(name)
    if values is None:
        return default
    if not isinstance(values, list) or not all(isinstance(v, str) and v for v in values):
        warnings.append(f"sandbox.{name} must be a list of variable names - using the default")
        return default
    secret = [v for v in values if is_secret_name(v)]
    if secret:
        warnings.append(f"sandbox.{name}: {', '.join(secret)} look like secrets - "
                        "use env_passthrough on purpose; skipped")
    return tuple(v for v in values if not is_secret_name(v))


def _env_values(raw, warnings: list[str]) -> tuple[tuple[str, str], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        warnings.append("sandbox.env must be an object {\"NAME\": \"value\"} - ignored")
        return ()
    pairs = []
    for name, value in raw.items():
        if not isinstance(value, str) or not name or "=" in name:
            warnings.append(f"sandbox.env.{name}: must be a text value - skipped")
        elif is_secret_name(name):
            warnings.append(f"sandbox.env.{name}: no secrets in settings.json - skipped")
        else:
            pairs.append((name, value))
    return tuple(pairs)


def load_cell_settings(path: Path) -> tuple[CellConfig, tuple[str, ...], list[str]]:
    """(cell configuration, env_passthrough, warnings) from a settings file."""
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CellError(f"cannot read {path}: {error}") from error
    if not isinstance(settings, dict):
        raise CellError(f"{path} must contain a JSON object")
    warnings: list[str] = []
    config = parse_cell_config(settings.get(SETTINGS_KEY), warnings)
    passthrough = settings.get("env_passthrough") or []
    if not isinstance(passthrough, list):
        warnings.append("env_passthrough must be a list - ignored")
        passthrough = []
    return config, tuple(p for p in passthrough if isinstance(p, str)), warnings


# -------------------------------------------------------------- building
@dataclass
class _Mounts:
    """Mount plan: (path, 'ro'|'rw') in the order bwrap must apply them."""

    entries: dict[Path, str] = field(default_factory=dict)
    symlinks: list[tuple[Path, Path]] = field(default_factory=list)  # (target, link)
    notes: list[str] = field(default_factory=list)

    def add(self, path: Path, kind: str) -> None:
        previous = self.entries.get(path)
        if previous is not None and previous != kind:
            raise CellError(f"{path} is configured both read-only and writable")
        self.entries[path] = kind

    def ordered(self) -> list[tuple[Path, str]]:
        # Parents before children: a later mount covers what an earlier one
        # put at the same place, so depth order gives "children win".
        return sorted(self.entries.items(), key=lambda item: len(item[0].parts))


def expand(path: str, home: Path) -> Path:
    """'~/x' -> <home>/x. Only the given home counts, not the caller's."""
    if path == "~":
        return home
    if path.startswith("~/"):
        return home / path[2:]
    return Path(path)


def _add_outside_path(mounts: _Mounts, raw: str, kind: str, home: Path) -> None:
    path = expand(raw, home)
    if not path.exists():
        mounts.notes.append(f"{raw}: missing - not mounted")
        return
    real = path.resolve()
    mounts.add(real, kind)
    if real != path:  # e.g. ~/.local/opt/node -> node-v24.x-linux-x64
        mounts.symlinks.append((real, path))


def _add_workdir(mounts: _Mounts, config: CellConfig, workdir: Path) -> None:
    mounts.add(workdir, "rw")
    for relative in config.read_only_in_workdir:
        target = workdir / relative
        if target.is_symlink() or not target.is_dir():
            raise CellError(f"{target} must be a real folder before it can be protected "
                            "(prepare_workdir creates it)")
        # Self-binds of every folder in between: a mount point cannot be
        # renamed, so 'mv tests other' cannot carry the protection away.
        for parent in reversed(target.relative_to(workdir).parents):
            if parent != Path("."):
                mounts.add(workdir / parent, "rw")
        mounts.add(target, "ro")
    git_dir = workdir / ".git"
    if config.git_read_only and git_dir.exists() and not git_dir.is_symlink():
        mounts.add(git_dir, "ro")


def prepare_workdir(config: CellConfig, workdir: Path) -> None:
    """Creates the protected sub-folders if they do not exist yet (an empty
    folder is invisible to git). Without it the agent could create them."""
    for relative in config.read_only_in_workdir:
        (workdir / relative).mkdir(parents=True, exist_ok=True)


def _cell_environment(config: CellConfig, environ: Mapping[str, str],
                      passthrough: Iterable[str], home: Path) -> list[tuple[str, str]]:
    allowed = set(passthrough)
    pairs = [(n, environ[n]) for n in config.env_keep if n in environ and not is_secret_name(n)]
    pairs += [(n, environ[n]) for n in sorted(allowed) if n in environ]
    pairs += [(n, str(expand(v, home)) if v.startswith("~") else v) for n, v in config.env]
    pairs.append(("HOME", str(home)))
    final: dict[str, str] = {}
    for name, value in pairs:  # later entries win (configured values, HOME)
        final[name] = value
    return list(final.items())


def build_argv(config: CellConfig, *, command: list[str], workdir: Path | None,
               environ: Mapping[str, str], passthrough: Iterable[str] = (),
               home: Path | None = None, bwrap: str = BWRAP,
               extra_read_only: Iterable[str] = ()) -> tuple[list[str], list[str]]:
    """bwrap command line for one command; returns (argv, notes).
    Raises CellError if the configuration cannot be honoured."""
    home = Path(home or Path.home())
    mounts = _Mounts()
    for raw in (*config.read_only, *extra_read_only):
        _add_outside_path(mounts, raw, "ro", home)
    for raw in config.read_write:
        _add_outside_path(mounts, raw, "rw", home)
    if workdir is not None:
        _add_workdir(mounts, config, workdir.resolve())
    argv = [bwrap, "--die-with-parent", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
            "--unshare-cgroup-try"]
    own_terminal = config.interactive and tiocsti_allowed()
    if not config.interactive or own_terminal:
        argv.append("--new-session")  # no TIOCSTI into the person's terminal
    if own_terminal:
        command = [SCRIPT_TOOL, "--quiet", "--return", "--flush", "--command",
                   shlex.join(command), "/dev/null"]
    if not config.network:
        argv.append("--unshare-net")  # only a loopback device inside
    argv += ["--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc"]
    for hidden in HIDDEN_DIRS:
        if Path(hidden).is_dir():
            argv += ["--tmpfs", hidden]
    argv += ["--tmpfs", str(home)]
    for path, kind in mounts.ordered():
        argv += ["--ro-bind" if kind == "ro" else "--bind", str(path), str(path)]
    for target, link in mounts.symlinks:
        if not _visible_without_mount(link):
            argv += ["--dir", str(link.parent), "--symlink", str(target), str(link)]
    argv.append("--clearenv")
    for name, value in _cell_environment(config, environ, passthrough, home):
        argv += ["--setenv", name, value]
    argv += ["--chdir", str(workdir.resolve() if workdir else home), "--", *command]
    return argv, mounts.notes


def tiocsti_allowed() -> bool:
    """True if a process may push keystrokes into its terminal (TIOCSTI).
    Kernels before 6.2 have no switch and always allow it."""
    try:
        return LEGACY_TIOCSTI.read_text().strip() != "0"
    except OSError:
        return True


def _visible_without_mount(path: Path) -> bool:
    """True if the path lies outside the folders a cell replaces (then the
    read-only system copy already shows the symlink)."""
    text = str(path)
    return not any(text == d or text.startswith(d + "/") for d in HIDDEN_DIRS)


# ------------------------------------------------------------------ check
# Runs inside the cell; prints one "key=value" per finding. Paths come as
# arguments ($1 = work folder or "", then groups separated by "--").
PROBE_SCRIPT = r"""
work="$1"; shift
group=absent
for item in "$@"; do
  if [ "$item" = "--" ]; then
    case "$group" in absent) group=readonly ;; readonly) group=end ;; esac
    continue
  fi
  case "$group" in
    absent) if [ -e "$item" ] || [ -L "$item" ]; then echo "visible=$item"; fi ;;
    readonly)
      probe="$item/PROBE_PREFIX$$"
      if ( : > "$probe" ) 2>/dev/null; then rm -f "$probe"; echo "writable=$item"; fi ;;
  esac
done
if [ -n "$work" ]; then
  probe="$work/PROBE_PREFIX$$"
  if ( : > "$probe" ) 2>/dev/null; then rm -f "$probe"; echo "work=writable"; else echo "work=readonly"; fi
fi
for name in $(env | cut -d= -f1); do echo "env=$name"; done
# Process 1 is bwrap itself: its start environment must be harmless too.
for name in $(tr '\0' '\n' < /proc/1/environ | cut -d= -f1); do echo "env1=$name"; done
# A witness process OUTSIDE the cell carries the marker in its command line.
# The marker arrives in two halves and is joined only in this shell, so no
# command line inside the cell holds it in one piece.
for f in /proc/[0-9]*/cmdline; do
  c=$(tr '\0' ' ' < "$f" 2>/dev/null) || continue
  case "$c" in *"$CELL_MARKER_A$CELL_MARKER_B"*) echo "marker=visible"; break ;; esac
done
# Abstract unix sockets live in the network namespace, not in the file
# system: the hidden folders do not hide them. An X server that answers
# "Success" to a connection without its cookie could be typed into.
awk 'NR > 1 && $8 ~ /^@/ {print "abstract=" $8}' /proc/net/unix 2>/dev/null | sort -u
if command -v python3 >/dev/null; then
  python3 -c '
import re, socket
names = set(re.findall(r"@/tmp/\.X11-unix/X(\d+)", open("/proc/net/unix").read()))
for n in sorted(names):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(2)
    try:
        s.connect("\0/tmp/.X11-unix/X" + n)
        s.sendall(b"l\0\x0b\0\0\0\0\0\0\0\0\0")
        print("x11=%s:%d" % (n, (s.recv(1) or b"\xff")[0]))
    except OSError:
        pass
'
fi
echo "processes=$(ls -d /proc/[0-9]* | wc -l)"
echo "done=1"
""".replace("PROBE_PREFIX", PROBE_FILE_PREFIX)


def check_cell(config: CellConfig, *, workdir: Path | None, environ: Mapping[str, str],
               passthrough: Iterable[str] = (), home: Path | None = None,
               bwrap: str = BWRAP) -> list[tuple[str, str]]:
    """Builds the cell for real and looks inside. Returns [(level, text)]
    with level OK or FAIL. Only creates (and removes) its own probe files."""
    home = Path(home or Path.home())
    if shutil.which(bwrap) is None:
        return [("FAIL", f"{bwrap} not installed (sudo apt-get install bubblewrap)")]
    marker = "JCODE_CELL_MARKER_" + secrets.token_hex(8)
    try:
        if workdir is not None:
            prepare_workdir(config, workdir)
        readonly = _readonly_targets(config, workdir, home)
        absent = [str(expand(p, home)) for p in config.expect_absent]
        command = ["/bin/sh", "-c", PROBE_SCRIPT, "probe", str(workdir or ""), *absent, "--",
                   *readonly, "--"]
        cell_env = {**environ, **dict(zip(MARKER_HALVES, (marker[:12], marker[12:]), strict=True))}
        argv, notes = build_argv(config, command=command, workdir=workdir, environ=cell_env,
                                 passthrough=(*passthrough, *MARKER_HALVES), home=home,
                                 bwrap=bwrap)
    except CellError as error:
        return [("FAIL", f"cell configuration: {error}")]
    # A witness process OUTSIDE the cell carries the marker in its command
    # line; with its own PID namespace the cell cannot see it in /proc.
    witness = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({PROBE_TIMEOUT_S})",
                                marker], env=LAUNCH_ENV)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=PROBE_TIMEOUT_S,
                              env=LAUNCH_ENV, check=False)
    except subprocess.TimeoutExpired:
        return [("FAIL", f"cell did not answer within {PROBE_TIMEOUT_S} s")]
    finally:
        witness.kill()
        witness.wait()
    results = [("OK", f"note: {note}") for note in notes]
    return results + _judge_probe(proc, config, passthrough, absent, readonly, workdir)


def _readonly_targets(config: CellConfig, workdir: Path | None, home: Path) -> list[str]:
    targets = [str(expand(p, home).resolve()) for p in config.read_only if expand(p, home).exists()]
    if workdir is not None:
        targets += [str(workdir / p) for p in config.read_only_in_workdir]
        if config.git_read_only and (workdir / ".git").is_dir():
            targets.append(str(workdir / ".git"))
    return targets


def _judge_probe(proc: subprocess.CompletedProcess, config: CellConfig,
                 passthrough: Iterable[str], absent: list[str], readonly: list[str],
                 workdir: Path | None) -> list[tuple[str, str]]:
    lines = proc.stdout.splitlines()
    if "done=1" not in lines:
        stderr = proc.stderr.strip().splitlines()[-1:] or ["(no message)"]
        hint = ""
        if any(h in proc.stderr for h in NAMESPACE_DENIED_HINTS):
            hint = " - the kernel/AppArmor refuses user namespaces: see vm/install_ma3.sh, step 3"
        return [("FAIL", f"cell does not start: {stderr[0]}{hint}")]
    values: dict[str, list[str]] = {"visible": [], "writable": [], "env": [], "env1": [],
                                    "abstract": [], "x11": []}
    for line in lines:
        key, _, value = line.partition("=")
        if key in values:
            values[key].append(value)
    results = [("OK", "cell starts (bubblewrap)")]
    results += _judge_paths(values, absent, readonly)
    results += _judge_environment(values["env"], config, passthrough)
    leaked = sorted(n for n in values["env1"] if is_secret_name(n))
    results.append(("FAIL", f"bwrap was started with secrets: {', '.join(leaked)}") if leaked else
                   ("OK", f"process 1 (bwrap) started with: {', '.join(sorted(values['env1']))}"))
    if workdir is not None:
        writable = "work=writable" in lines
        results.append(("OK" if writable else "FAIL",
                        f"work folder {'writable' if writable else 'NOT writable'}: {workdir}"))
    results += _judge_abstract_sockets(values["abstract"], values["x11"])
    isolated = "marker=visible" not in lines
    results.append(("OK" if isolated else "FAIL",
                    "own /proc: processes outside the cell are invisible" if isolated else
                    "/proc shows processes outside the cell (PID namespace missing)"))
    return results


def _judge_abstract_sockets(names: list[str], x11: list[str]) -> list[tuple[str, str]]:
    """X11 servers that accept a connection without cookie and D-Bus buses on
    abstract sockets are reachable from the cell although the files are not."""
    results = []
    for entry in x11:
        display, _, answer = entry.partition(":")
        if answer == "1":
            text = (f"X server :{display} accepts connections from inside the cell without its "
                    "cookie (xhost +SI:localuser?) - a process could type into other windows")
            results.append(("FAIL", text))
        else:
            results.append(("OK", f"X server :{display} reachable but refuses without its cookie"))
    buses = sorted(n for n in names if "dbus" in n.lower())
    if buses:
        results.append(("FAIL", f"D-Bus bus on an abstract socket reachable: {', '.join(buses)}"))
    others = sorted(n for n in names if "dbus" not in n.lower() and ".X11-unix" not in n)
    shown = ", ".join(others[:5]) + (" ..." if len(others) > 5 else "") if others else "none other"
    results.append(("OK", f"abstract sockets reachable (network namespace shared): {shown}"))
    return results


def _judge_paths(values: dict[str, list[str]], absent: list[str],
                 readonly: list[str]) -> list[tuple[str, str]]:
    results = []
    visible = set(values["visible"])
    hidden = [p for p in absent if p not in visible]
    if hidden:
        results.append(("OK", f"not visible inside: {', '.join(hidden)}"))
    results += [("FAIL", f"VISIBLE inside the cell: {p}") for p in absent if p in visible]
    writable = set(values["writable"])
    protected = [p for p in readonly if p not in writable]
    if protected:
        results.append(("OK", f"read-only inside: {', '.join(protected)}"))
    results += [("FAIL", f"WRITABLE inside the cell: {p}") for p in readonly if p in writable]
    return results


def _judge_environment(names: list[str], config: CellConfig,
                       passthrough: Iterable[str]) -> list[tuple[str, str]]:
    allowed = set(passthrough) | set(MARKER_HALVES)
    secret = sorted(n for n in names if is_secret_name(n) and n not in allowed)
    shown = sorted(n for n in names if n not in MARKER_HALVES)
    if secret:
        return [("FAIL", f"secret-looking variables inside: {', '.join(secret)}")]
    summary = f"environment inside: {len(shown)} variable(s), none secret ({', '.join(shown)})"
    return [("OK", summary)]


# ----------------------------------------------------------- command line
def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run or check an agent cell (bubblewrap).")
    parser.add_argument("action", choices=("check", "run", "argv"))
    parser.add_argument("--settings", required=True, type=Path,
                        help="settings.json or cell profile with a \"sandbox\" section")
    parser.add_argument("--workdir", type=Path, help="the agent's writable work folder")
    parser.add_argument("--extra-ro", action="append", default=[],
                        help="one more read-only path for this call (diagnostics)")
    parser.add_argument("command", nargs="*", help="after '--': the command to run")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        config, passthrough, warnings = load_cell_settings(args.settings)
    except CellError as error:
        print(f"FAIL {error}")
        return 2
    for warning in warnings:
        print(f"WARN settings: {warning}", file=sys.stderr)
    workdir = args.workdir.expanduser().resolve() if args.workdir else None
    if workdir is not None and not workdir.is_dir():
        print(f"FAIL work folder {workdir} does not exist")
        return 2
    if args.action == "check":
        results = check_cell(config, workdir=workdir, environ=os.environ, passthrough=passthrough)
        for level, text in results:
            print(f"{level} {text}")
        return 0 if all(level == "OK" for level, _ in results) else 1
    return _run_or_print(args, config, passthrough, workdir)


def _run_or_print(args: argparse.Namespace, config: CellConfig, passthrough: tuple[str, ...],
                  workdir: Path | None) -> int:
    if not args.command:
        print("FAIL no command given (use: ... run --settings F -- CMD)")
        return 2
    if args.action == "run" and not config.enabled:
        print(f"FAIL the cell in {args.settings} is disabled - nothing run outside a cell")
        return 2
    bwrap = shutil.which(BWRAP)
    if bwrap is None:
        print("FAIL bwrap not installed (sudo apt-get install bubblewrap)")
        return 2
    try:
        if workdir is not None:
            prepare_workdir(config, workdir)
        argv, notes = build_argv(config, command=args.command, workdir=workdir,
                                 environ=os.environ, passthrough=passthrough, bwrap=bwrap,
                                 extra_read_only=args.extra_ro)
    except CellError as error:
        print(f"FAIL cell configuration: {error}")
        return 2
    for note in notes:
        print(f"note: {note}", file=sys.stderr)
    if args.action == "argv":
        print(json.dumps(argv, indent=1))
        return 0
    os.execve(bwrap, argv, LAUNCH_ENV)  # replaces this process; exit code = the command's
    return 127  # not reached


if __name__ == "__main__":
    sys.exit(main())
