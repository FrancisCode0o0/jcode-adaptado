#!/usr/bin/env python3
# Part of jcode adapted for mejorador-audio and Abschrift (c) 2026 Francis Garcia Reyes,
# MIT License (see LICENSE).
"""jcode-level probes for vm/verify_abschrift.sh (Abschrift plan v12, 0.1.g).

Origin: jcode 0.10.0-ma.3 (mejorador-audio plan step 1.0.b1).
Each probe runs jcode in a CHILD process (jcode reads its command line at
import time) and prints one line "OK|FAIL|PENDING <text>". Probes never
print a key, only whether its variable is set.

    python3 verify_jcode.py <probe> <project-folder>
Probes:
    start        cell check + model start as jcode does it, no request sent
    nokey        jcode.py without the chain's keys -> must exit 2
    nocell       copy of settings.json with the cell off -> must exit 2
    write-guard  verifier write_file: tests/acceptance/ yes, public/ no
                 (in a temporary copy of the project - the clone is untouched)
    env-count    run_command in the real cell: variables named *key*,
                 *token*, *secret* -> 0
    deepseek     one short real task: the model must call list_files
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

JCODE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(JCODE_DIR))

from jcode_extras import sanitized_env  # after the sys.path line above

TIMEOUT_S = 120
DEEPSEEK_TIMEOUT_S = 300
DEEPSEEK_TASK = ("Call the tool list_files exactly once on the folder '.', then reply "
                 "with the single word FERTIG. Do not write or change anything.")


def chain_variables(project: Path) -> list[str]:
    settings = json.loads((project / "settings.json").read_text(encoding="utf-8"))
    providers = settings.get("providers") or {}
    names = []
    for entry in settings.get("model_fallback") or []:
        name = providers.get(entry.get("provider"), {}).get("api_key_env")
        if name and name not in names:
            names.append(name)
    return names


def jcode_child(code: str, project: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    """Imports jcode with '--project <folder>' in a child and runs code there."""
    program = (f"import sys; sys.argv = ['jcode.py', '--project', {str(project)!r}]; "
               f"sys.path.insert(0, {str(JCODE_DIR)!r}); import jcode as j\n{code}")
    return subprocess.run([sys.executable, "-c", program], capture_output=True, text=True,
                          env=env or dict(os.environ), timeout=TIMEOUT_S, check=False,
                          stdin=subprocess.DEVNULL, cwd=str(project))


def probe_start(project: Path) -> str:
    missing = [n for n in chain_variables(project) if not os.environ.get(n)]
    if len(missing) == len(chain_variables(project)):
        return f"PENDING start: no key of the chain set ({', '.join(missing)})"
    child = jcode_child("ok = j._cell_ready() and j._start_backend()\n"
                        "print('RESULT', ok, j.MODE, j.MODEL, j.BACKEND, j.CELL.enabled)", project)
    words = (child.stdout.strip().splitlines() or [""])[-1].split()
    if words[:2] != ["RESULT", "True"]:
        return f"FAIL start: jcode would not start ({child.stderr.strip()[-200:]})"
    mode, model, backend, cell = words[2:6]
    good = mode == "verifier" and cell == "True" and "gemini" not in model.lower()
    return (f"{'OK' if good else 'FAIL'} start: mode {mode.upper()}, model {model} "
            f"({backend}), cell {'ON' if cell == 'True' else 'off'}")


def probe_nokey(project: Path) -> str:
    env = sanitized_env(os.environ)[0]  # every *KEY*/*TOKEN* variable removed
    child = subprocess.run([sys.executable, str(JCODE_DIR / "jcode.py"), "--project", str(project),
                            "probe"], capture_output=True, text=True, env=env, timeout=TIMEOUT_S,
                           check=False, stdin=subprocess.DEVNULL)
    stopped = child.returncode == 2 and "does not start" in child.stderr
    return f"{'OK' if stopped else 'FAIL'} without keys: exit {child.returncode}" + \
        (" (no start, no other model)" if stopped else f" - {child.stderr.strip()[-200:]}")


def temporary_project(project: Path, cell_enabled: bool) -> Path:
    """A copy of the project's settings.json with a fresh git repo as code/."""
    folder = Path(tempfile.mkdtemp(prefix="verify-abschrift-"))
    settings = json.loads((project / "settings.json").read_text(encoding="utf-8"))
    settings["sandbox"]["enabled"] = cell_enabled
    (folder / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    (folder / "code").mkdir()
    subprocess.run(["git", "init", "-q", str(folder / "code")], check=True)
    return folder


def probe_nocell(project: Path) -> str:
    folder = temporary_project(project, cell_enabled=False)
    try:
        child = subprocess.run([sys.executable, str(JCODE_DIR / "jcode.py"), "--project",
                                str(folder), "probe"], capture_output=True, text=True,
                               timeout=TIMEOUT_S, check=False, stdin=subprocess.DEVNULL)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    stopped = child.returncode == 2 and "without its cell" in child.stderr
    return f"{'OK' if stopped else 'FAIL'} without cell: exit {child.returncode}" + \
        ("" if stopped else f" - {child.stderr.strip()[-200:]}")


def probe_write_guard(project: Path) -> str:
    folder = temporary_project(project, cell_enabled=True)
    try:
        child = jcode_child(
            "print('A', j.write_file('tests/acceptance/probe.spec.js', '// probe').startswith('ERROR'))\n"
            "print('B', j.write_file('public/js/probe.js', '// probe').startswith('ERROR'))\n"
            "print('M', j.MODE)", folder)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    lines = dict(line.split(" ", 1) for line in child.stdout.splitlines() if line[:2] in ("A ", "B ", "M "))
    good = lines.get("A") == "False" and lines.get("B") == "True" and lines.get("M") == "verifier"
    return (f"{'OK' if good else 'FAIL'} write_file: tests/acceptance/ "
            f"{'allowed' if lines.get('A') == 'False' else 'REFUSED'}, public/ "
            f"{'refused' if lines.get('B') == 'True' else 'ALLOWED'} (mode {lines.get('M')})")


def probe_env_count(project: Path) -> str:
    child = jcode_child(
        "print('OUT', j.run_command(\"env | cut -d= -f1 | grep -ci -e key -e token -e secret\"))",
        project)
    out = child.stdout.split("OUT", 1)[-1].strip()
    count = out.splitlines()[-1] if out else "?"
    return (f"{'OK' if count == '0' else 'FAIL'} run_command in the cell: {count} variable(s) "
            f"named *key*/*token*/*secret*")


def probe_deepseek(project: Path) -> str:
    if not any(os.environ.get(n) for n in chain_variables(project)):
        return "PENDING DeepSeek request: no key set yet"
    child = subprocess.run([sys.executable, str(JCODE_DIR / "jcode.py"), "--project", str(project),
                            DEEPSEEK_TASK], capture_output=True, text=True,
                           timeout=DEEPSEEK_TIMEOUT_S, check=False, stdin=subprocess.DEVNULL,
                           env={**os.environ, "NO_COLOR": "1"})
    called = "Exploring" in child.stdout
    model = next((line.split("Model: ")[1].split("|")[0].strip() for line in child.stdout.splitlines()
                  if "Model: " in line), "?")
    good = child.returncode == 0 and called
    return (f"{'OK' if good else 'FAIL'} DeepSeek request: model {model}, tool call "
            f"{'list_files seen' if called else 'NOT seen'}, exit {child.returncode}")


PROBES = {"start": probe_start, "nokey": probe_nokey, "nocell": probe_nocell,
          "write-guard": probe_write_guard, "env-count": probe_env_count,
          "deepseek": probe_deepseek}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in PROBES:
        print(f"usage: verify_jcode.py {{{'|'.join(PROBES)}}} <project-folder>", file=sys.stderr)
        return 2
    try:
        line = PROBES[argv[0]](Path(argv[1]).expanduser().resolve())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        line = f"FAIL {argv[0]}: {type(error).__name__}: {error}"
    print(line)
    return 0 if line.startswith(("OK", "PENDING")) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
