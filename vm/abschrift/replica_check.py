#!/usr/bin/env python3
# Part of jcode adapted for mejorador-audio and Abschrift (c) 2026 Francis Garcia Reyes,
# MIT License (see LICENSE).
"""The start scripts and push.sh, tried on a throw-away copy of the layout.

Origin: jcode 0.10.0-ma.3 (mejorador-audio plan step 1.0.b1); Abschrift plan
v12, 0.1.g: "Startskripte holen den Stand per git pull --ff-only und brechen
bei uncommitteten Änderungen ab; --branch wechselt den Branch außerhalb der
Zelle". Used by the tests AND by vm/verify_abschrift.sh, so the VM runs the
very same scenarios with its own kernel, AppArmor and bubblewrap.

Every scenario builds a fresh fake home in a temporary folder (meeting repo,
both clones, jcode project folder), runs the REAL scripts with HOME pointing
there - the real cells included - and replaces only the two programs at the
end of the chain: jcode.py and gemini (tiny scripts that report how they
were started). The real repositories are never touched.

    python3 replica_check.py          one line per scenario; exit 0 = all OK
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

VM_DIR = Path(__file__).resolve().parents[1]
JCODE_DIR = VM_DIR.parent
SCRIPTS = VM_DIR / "abschrift"
TIMEOUT_S = 120

FAKE_JCODE = """import os, sys
print("JCODE STARTED", " ".join(sys.argv[1:]))
print("BASHRC", os.path.exists(os.path.expanduser("~/.bashrc")))
"""
FAKE_GEMINI = """#!/bin/sh
echo "GEMINI STARTED $*"
[ -e "$HOME/.bashrc" ] && echo "LEAK bashrc"
[ -e "$HOME/jcode-projekte" ] && echo "LEAK jcode-projekte"
( : > tests/acceptance/x ) 2>/dev/null && echo "LEAK acceptance writable"
echo "PWD $PWD"
"""


class Layout:
    """A fake home like the VM's, with git identities set per repo."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.home = root / "home" / "fran"
        self.repo = self.home / "repos" / "abschrift.git"
        self.project = self.home / "jcode-projekte" / "abschrift"
        self.verifier = self.project / "code"
        self.gemini = self.home / "abschrift-gemini"
        self.seed = root / "seed"
        self.home.mkdir(parents=True)
        (self.home / ".bashrc").write_text("export GEMINI_API_KEY=not-a-real-key-at-all\n")
        self._git("init", "-q", "--bare", "-b", "main", str(self.repo))
        self._git("clone", "-q", str(self.repo), str(self.seed))
        self._identity(self.seed)
        self.commit(self.seed, "README.md", "Abschrift\n")
        self._git("push", "-q", "origin", "main", cwd=self.seed)
        for clone in (self.verifier, self.gemini):
            self._git("clone", "-q", str(self.repo), str(clone))
            self._identity(clone)
        settings = json.loads((JCODE_DIR / "examples/abschrift/settings.json").read_text())
        (self.project / "settings.json").write_text(json.dumps(settings))
        (self.root / "fake_jcode.py").write_text(FAKE_JCODE)
        gemini = self.home / ".local/opt/gemini-cli/node_modules/.bin/gemini"
        gemini.parent.mkdir(parents=True)
        gemini.write_text(FAKE_GEMINI)
        gemini.chmod(0o755)

    def env(self) -> dict[str, str]:
        keep = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "TZ")}
        return {**keep, "HOME": str(self.home), "JCODE_PYTHON": sys.executable,
                "JCODE_ENTRY": str(self.root / "fake_jcode.py"), "GIT_TERMINAL_PROMPT": "0",
                "GEMINI_API_KEY": "not-a-real-key-at-all"}

    def run(self, script: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(SCRIPTS / script), *args], capture_output=True,
                              text=True, env=self.env(), timeout=TIMEOUT_S, check=False,
                              stdin=subprocess.DEVNULL)

    def commit(self, clone: Path, name: str, content: str, branch: str | None = None) -> str:
        if branch:
            self._git("checkout", "-q", "-B", branch, cwd=clone)
        path = clone / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        self._git("add", name, cwd=clone)
        self._git("commit", "-q", "--no-verify", "-m", f"add {name}", cwd=clone)
        return self.head(clone)

    def head(self, repo: Path, ref: str = "HEAD") -> str:
        return self._git("rev-parse", ref, cwd=repo).strip()

    def branch(self, repo: Path) -> str:
        return self._git("symbolic-ref", "--short", "HEAD", cwd=repo).strip()

    def _identity(self, clone: Path) -> None:
        self._git("config", "user.email", "t@example.invalid", cwd=clone)
        self._git("config", "user.name", "t", cwd=clone)

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                              env={**os.environ, "HOME": str(self.home)}).stdout


# ----------------------------------------------------------------- scenarios
def scenario_jcode_pulls_and_starts(lay: Layout) -> tuple[bool, str]:
    new = lay.commit(lay.seed, "SPEC.md", "1. criterion\n")
    lay._git("push", "-q", "origin", "main", cwd=lay.seed)
    result = lay.run("start_jcode.sh", "task", "words")
    good = (result.returncode == 0 and lay.head(lay.verifier) == new
            and f"JCODE STARTED --project {lay.project} task words" in result.stdout)
    return good, "start_jcode.sh: git pull --ff-only, then jcode --project with the task"


def scenario_jcode_stops_on_changes(lay: Layout) -> tuple[bool, str]:
    stray = lay.verifier / "tests/acceptance/new.spec.js"
    stray.parent.mkdir(parents=True)
    stray.write_text("// written by the verifier, not reviewed yet\n")
    result = lay.run("start_jcode.sh")
    good = (result.returncode == 1 and "new.spec.js" in result.stderr
            and "JCODE STARTED" not in result.stdout and stray.exists())
    return good, "start_jcode.sh: uncommitted changes -> stop, shown, kept"


def scenario_jcode_branch(lay: Layout) -> tuple[bool, str]:
    new = lay.commit(lay.seed, "phase.txt", "phase 2\n", branch="phase-2")
    lay._git("push", "-q", "origin", "phase-2", cwd=lay.seed)
    result = lay.run("start_jcode.sh", "--branch", "phase-2")
    good = (result.returncode == 0 and lay.branch(lay.verifier) == "phase-2"
            and lay.head(lay.verifier) == new)
    return good, "start_jcode.sh --branch: switches outside the cell and pulls"


def scenario_gemini_pulls_in_sync_cell(lay: Layout) -> tuple[bool, str]:
    new = lay.commit(lay.seed, "SPEC.md", "1. criterion\n")
    lay._git("push", "-q", "origin", "main", cwd=lay.seed)
    result = lay.run("start_gemini.sh", "--yolo")
    out = result.stdout
    good = (result.returncode == 0 and lay.head(lay.gemini) == new and "GEMINI STARTED --yolo" in out
            and "LEAK" not in out and f"PWD {lay.gemini}" in out)
    return good, "start_gemini.sh: pull in the sync cell, Gemini CLI in its cell (no ~/.bashrc, tests/acceptance read-only)"


def scenario_gemini_stops_on_changes(lay: Layout) -> tuple[bool, str]:
    (lay.gemini / "half-done.js").write_text("// not committed\n")
    result = lay.run("start_gemini.sh")
    good = (result.returncode == 1 and "half-done.js" in result.stdout + result.stderr
            and "GEMINI STARTED" not in result.stdout and (lay.gemini / "half-done.js").exists())
    return good, "start_gemini.sh: uncommitted changes -> stop, shown, kept"


def scenario_gemini_branch(lay: Layout) -> tuple[bool, str]:
    lay.commit(lay.seed, "phase.txt", "phase 3\n", branch="phase-3")
    lay._git("push", "-q", "origin", "phase-3", cwd=lay.seed)
    result = lay.run("start_gemini.sh", "--branch", "phase-3")
    good = result.returncode == 0 and lay.branch(lay.gemini) == "phase-3"
    return good, "start_gemini.sh --branch: switches in the sync cell, never in Gemini's cell"


def scenario_gemini_hook_never_runs_outside(lay: Layout) -> tuple[bool, str]:
    """An agent-planted hook in the Gemini clone must not run outside a cell."""
    marker = lay.root / "HOOK-RAN-OUTSIDE"
    hook = lay.gemini / ".git/hooks/post-merge"
    hook.write_text(f"#!/bin/sh\ntouch {marker} 2>/dev/null\n")
    hook.chmod(0o755)
    lay.commit(lay.seed, "more.txt", "x\n", branch="main")
    lay._git("push", "-q", "origin", "main", cwd=lay.seed)
    result = lay.run("start_gemini.sh")
    lay.commit(lay.gemini, "work.js", "// gemini work\n")
    push = lay.run("push.sh", "gemini")
    good = result.returncode == 0 and push.returncode == 0 and not marker.exists()
    return good, "planted git hook in the Gemini clone: never runs outside a cell (pull, push.sh)"


def scenario_push_gemini(lay: Layout) -> tuple[bool, str]:
    new = lay.commit(lay.gemini, "public/js/main.js", "// app\n")
    result = lay.run("push.sh", "gemini")
    good = result.returncode == 0 and lay.head(lay.repo, "refs/heads/main") == new
    leftover = lay._git("for-each-ref", "refs/incoming", cwd=lay.repo).strip()
    return good and not leftover, "push.sh gemini: fast-forward of the meeting repo"


def scenario_push_refuses_acceptance(lay: Layout) -> tuple[bool, str]:
    before = lay.head(lay.repo, "refs/heads/main")
    lay.commit(lay.gemini, "tests/acceptance/upload.spec.js", "// weakened test\n")
    result = lay.run("push.sh", "gemini")
    good = (result.returncode == 1 and "taboo" in result.stderr
            and lay.head(lay.repo, "refs/heads/main") == before)
    return good, "push.sh gemini: refuses commits touching tests/acceptance/"


def scenario_push_refuses_secret(lay: Layout) -> tuple[bool, str]:
    before = lay.head(lay.repo, "refs/heads/main")
    key = "AQ" + "." + "Zz9_" * 12 + "abc"  # fake key, 53 characters
    lay.commit(lay.verifier, "notes.txt", f"key {key}\n")
    lay.commit(lay.verifier, "notes.txt", "key removed again\n")
    result = lay.run("push.sh", "verifier")
    good = (result.returncode == 1 and "notes.txt:1" in result.stdout + result.stderr
            and key not in result.stdout + result.stderr
            and lay.head(lay.repo, "refs/heads/main") == before)
    return good, "push.sh: a key in any new commit (even removed later) blocks the push"


def scenario_push_refuses_non_fast_forward(lay: Layout) -> tuple[bool, str]:
    lay.commit(lay.seed, "other.txt", "x\n", branch="main")
    lay._git("push", "-q", "origin", "main", cwd=lay.seed)
    before = lay.head(lay.repo, "refs/heads/main")
    lay.commit(lay.verifier, "acceptance.txt", "y\n")
    result = lay.run("push.sh", "verifier")
    good = (result.returncode == 1 and "not a fast-forward" in result.stderr
            and lay.head(lay.repo, "refs/heads/main") == before)
    return good, "push.sh: never forces (diverged clone -> stop)"


SCENARIOS: list[Callable[[Layout], tuple[bool, str]]] = [
    scenario_jcode_pulls_and_starts, scenario_jcode_stops_on_changes, scenario_jcode_branch,
    scenario_gemini_pulls_in_sync_cell, scenario_gemini_stops_on_changes, scenario_gemini_branch,
    scenario_gemini_hook_never_runs_outside, scenario_push_gemini,
    scenario_push_refuses_acceptance, scenario_push_refuses_secret,
    scenario_push_refuses_non_fast_forward,
]


def run_scenario(scenario: Callable[[Layout], tuple[bool, str]]) -> tuple[bool, str]:
    root = Path(tempfile.mkdtemp(prefix="replica-abschrift-"))
    try:
        return scenario(Layout(root))
    except (subprocess.SubprocessError, OSError) as error:
        return False, f"{scenario.__name__}: {type(error).__name__}: {error}"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    failed = 0
    for scenario in SCENARIOS:
        good, text = run_scenario(scenario)
        failed += not good
        print(f"{'OK' if good else 'FAIL'} {text}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
