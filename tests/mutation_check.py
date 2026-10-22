#!/usr/bin/env python3
"""Mutation check for the fork's changes: each mutation below breaks one
protection on purpose; the test suite must then FAIL. A mutation the tests
do not notice ("SURVIVED") means a protection is not really tested.

Origin: written by the Abschrift chat for 0.10.0-ma.2 (E1-E4, E6); extended
in 0.10.0-ma.3 (mejorador-audio plan step 1.0.b1): merged secret check and
session check, cells, search_files fix, Abschrift scripts, installers.

Run from the repository root:  python3 tests/mutation_check.py [-j N]
Needs pytest, git and bubblewrap (pwsh for the Windows mutations); no
network, no API key. Each mutation runs the whole suite on a copy of the
repository (without .git), stopping at the first failure.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# .git is copied: the Windows installer test packs the tracked files with git.
SKIP = shutil.ignore_patterns("code", "logs", "__pycache__", ".pytest_cache")

# (change, file, original text, mutated text) - the original must occur exactly once.
MUTATIONS = [
    # ------------------------------------------------ E1-E4 (from ma.2, unchanged)
    ("E1 --project ignored", "jcode_extras.py",
     "if not words or not words[0].startswith(PROJECT_OPTION):", "if True:"),
    ("E1 paths always next to jcode.py", "jcode.py",
     "BASE_DIR = PROJECT_DIR or APP_DIR", "BASE_DIR = APP_DIR"),
    ("E1 project without settings.json accepted", "jcode_extras.py",
     "if not (folder / SETTINGS_FILE_NAME).is_file():", "if False:"),
    ("E1 wrong --project does not stop jcode", "jcode.py",
     "    sys.exit(2)\nBASE_DIR", "    pass\nBASE_DIR"),
    ("E2 verifier exists without settings", "jcode_extras.py",
     "modes = (EXECUTOR, BUILDER) + ((VERIFIER,) if write_paths else ())",
     "modes = (EXECUTOR, BUILDER, VERIFIER)"),
    ("E2 verifier may write anywhere", "jcode_extras.py",
     "        if mode == VERIFIER:\n            return self.verifier_write_paths",
     "        if mode == VERIFIER:\n            return None"),
    ("E2 verifier gets the edit tools", "jcode.py",
     "        return VERIFIER_TOOL_NAMES", "        return tuple(TOOL_FUNCS)"),
    ("E2 '..' allowed in write_paths", "jcode_extras.py",
     'if not parts or ".." in parts:', "if not parts:"),
    ("E2 exit answer changed", "jcode_extras.py",
     'VERIFIER_NO_FINDINGS = "KEINE FEHLER"', 'VERIFIER_NO_FINDINGS = "NO ERRORS"'),
    ("E2 git check ignores write_paths", "jcode.py",
     "changed = git_changes(SANDBOX, allowed)", "changed = git_changes(SANDBOX)"),
    ("E2 prefix check too loose", "jcode_extras.py",
     "(sandbox / folder).resolve() in path.parents",
     "str(path).startswith(str((sandbox / folder).resolve()))"),
    ("E3 'stop' ignored at start", "jcode.py",
     "    if CONFIG.fallback_exhausted == FALLBACK_STOP:\n        print(",
     "    if False:\n        print("),
    ("E3 main ignores a failed start", "jcode.py",
     "if not _start_backend():", "if not _start_backend() and False:"),
    ("E3 'stop' accepted without chain", "jcode_extras.py",
     "if value == FALLBACK_STOP and not chain:", "if False:"),
    ("E4 run_command gets the full environment", "jcode.py",
     "        return command, True, _command_env()", "        return command, True, None"),
    ("E4 env_passthrough ignored", "jcode_extras.py",
     "secret = name not in keep and is_secret_name(name)", "secret = is_secret_name(name)"),
    ("E4 lower-case names not caught", "jcode_extras.py",
     "    upper = name.upper()\n    return any(part in upper for part in SECRET_NAME_PARTS)",
     "    upper = name\n    return any(part in upper for part in SECRET_NAME_PARTS)"),
    ("E4 audio project loses Kaggle passthrough", "settings.json",
     '"env_passthrough": ["KAGGLE_KEY", "KAGGLE_API_TOKEN"],', ""),
    ("E4 GIT_CONFIG group split up", "jcode_extras.py",
     "if _GIT_CONFIG_GROUP.match(name):", "if False:"),
    # ------------------------------------------------ search_files / list_files
    ("search_files follows '..' in glob", "jcode.py",
     'if ".." in Path(glob).parts:', "if False:"),
    ("search_files follows symlinks out of code/", "jcode.py",
     "        if not _inside_sandbox(p):\n            continue\n        try:",
     "        try:"),
    ("list_files shows symlinks out of code/", "jcode.py",
     "        if not _inside_sandbox(p):  # FORK (security): no peeking through symlinks\n            continue\n",
     ""),
    # ------------------------------------------------ E7 cell (jcode_cell.py, jcode.py)
    ("cell without own PID namespace", "jcode_cell.py",
     '"--die-with-parent", "--unshare-pid", "--unshare-ipc"', '"--die-with-parent", "--unshare-ipc"'),
    ("cell keeps /home, /tmp ... visible", "jcode_cell.py",
     "        if Path(hidden).is_dir():\n            argv += [\"--tmpfs\", hidden]",
     "        if False:\n            argv += [\"--tmpfs\", hidden]"),
    ("cell keeps the caller's environment", "jcode_cell.py",
     '    argv.append("--clearenv")\n', ""),
    ("cell lets protected folders be renamed away", "jcode_cell.py",
     "            if parent != Path(\".\"):\n                mounts.add(workdir / parent, \"rw\")",
     "            if False:\n                mounts.add(workdir / parent, \"rw\")"),
    ("cell leaves .git writable", "jcode_cell.py",
     '        mounts.add(git_dir, "ro")', "        pass"),
    ("protected folder not created before the cell", "jcode_cell.py",
     "        (workdir / relative).mkdir(parents=True, exist_ok=True)", "        pass"),
    ("bwrap started with the caller's environment (run)", "jcode_cell.py",
     "os.execve(bwrap, argv, LAUNCH_ENV)", "os.execve(bwrap, argv, dict(os.environ))"),
    ("bwrap started with jcode's environment (run_command)", "jcode.py",
     "    return argv, False, dict(LAUNCH_ENV)", "    return argv, False, _command_env()"),
    ("interactive cell shares the terminal (TIOCSTI)", "jcode_cell.py",
     "    own_terminal = config.interactive and tiocsti_allowed()",
     "    own_terminal = False"),
    ("check ignores visible paths", "jcode_cell.py",
     '    results += [("FAIL", f"VISIBLE inside the cell: {p}") for p in absent if p in visible]', ""),
    ("check accepts an X server without cookie", "jcode_cell.py",
     '        if answer == "1":', '        if False:'),
    ("check accepts a D-Bus bus on an abstract socket", "jcode_cell.py",
     "    if buses:\n", "    if False:\n"),
    ("check ignores a missing PID namespace", "jcode_cell.py",
     '    isolated = "marker=visible" not in lines', "    isolated = True"),
    ("run_command ignores an enabled cell", "jcode.py",
     "    if not CELL.enabled:\n        return command, True, _command_env()",
     "    if True:\n        return command, True, _command_env()"),
    ("required cell may be switched off", "jcode.py",
     "        if CELL.required:\n", "        if False:\n"),
    ("jcode starts although the cell fails", "jcode.py",
     "    if not _cell_ready():  # FORK (E7): no cell, no start (when configured)\n        sys.exit(2)",
     "    if not _cell_ready():  # FORK (E7): no cell, no start (when configured)\n        pass"),
    # ------------------------------------------------ E6 tools/check_secrets.py
    ("values of exported keys not checked", "tools/check_secrets.py",
     '    found += [f"value of ${name}" for value, name in (values or {}).items() if value in line]', ""),
    ("NVIDIA keys not recognised", "tools/check_secrets.py",
     'rf"{NOT_TOKEN_BEFORE}nvapi-[{TOKEN_CHARS}]{{20,}}"', 'r"nvapi-NEVER"'),
    ("--range lets options through to git", "tools/check_secrets.py",
     '    if revision_range.startswith("-"):', "    if False:"),
    ("--range scans only the final tree", "tools/check_secrets.py",
     '    patches = run_git(["log", "--format=", "-p", *DIFF_OPTIONS, revision_range, "--"])',
     '    patches = run_git(["diff", *DIFF_OPTIONS, revision_range, "--"])'),
    ("--install-hook replaces another hooksPath", "tools/check_secrets.py",
     "    if current:\n        print(", "    if False:\n        print("),
    ("checker exit code 0 on findings", "tools/check_secrets.py",
     '        "then \'git add\' the file again. Do NOT use --no-verify.",\n        file=sys.stderr,\n    )\n    return 1',
     '        "then \'git add\' the file again. Do NOT use --no-verify.",\n        file=sys.stderr,\n    )\n    return 0'),
    # ------------------------------------------------ session_check.sh
    ("session_check loses its error count", "vm/session_check.sh",
     '            *) fail "$text" ;;\n        esac\n    done\n    [ "$lines" -gt 0 ]',
     '            *) echo "ERROR $text" ;;\n        esac\n    done\n    [ "$lines" -gt 0 ]'),
    ("session_check accepts a silent helper", "vm/session_check.sh",
     '    [ "$lines" -gt 0 ] || fail', '    [ "$lines" -gt 0 ] || ok'),
    ("session_check: Google in the chain accepted", "vm/session_check.sh",
     '              if any(word in e.model.lower() for word in ("gemini", "gemma"))\n              or "googleapis" in',
     '              if False\n              and "googleapis" in'),
    ("session_check: 'stop' not required", "vm/session_check.sh",
     "if config.fallback_exhausted == FALLBACK_STOP else", "if True else"),
    ("session_check: cell not required", "vm/session_check.sh",
     "if cell.enabled and cell.required else", "if True else"),
    ("session_check: any hook accepted", "vm/session_check.sh",
     '        if [ "$hooks" = "$EXTRAS_DIR/vm/agent-hooks" ] && [ -x "$hooks/pre-commit" ]; then',
     "        if true; then"),
    ("session_check ignores a dirty sandbox", "vm/session_check.sh",
     '    if [ -n "$others" ]; then', "    if false; then"),
    # ------------------------------------------------ Abschrift scripts
    ("start_jcode.sh ignores uncommitted changes", "vm/abschrift/start_jcode.sh",
     'changes=$(git -C "$clone" status --porcelain --untracked-files=all)', 'changes=""'),
    ("start_gemini.sh runs git outside a cell", "vm/abschrift/start_gemini.sh",
     ('python3 -I "$CELL_TOOL" run --settings "$ABSCHRIFT_PROFILES/sync-cell.json" --workdir "$clone" \\\n'
      '    -- /bin/sh -c "$SYNC_SCRIPT" sync "$ABSCHRIFT_REPO" "$branch"'),
     '(cd "$clone" && /bin/sh -c "$SYNC_SCRIPT" sync "$ABSCHRIFT_REPO" "$branch")'),
    ("push.sh lets Gemini change tests/acceptance", "vm/abschrift/push.sh",
     'if [ "$agent" = "gemini" ]; then', "if false; then"),
    ("push.sh skips the secret check", "vm/abschrift/push.sh",
     '    | python3 -I "$ABSCHRIFT_JCODE_DIR/tools/check_secrets.py" --diff - \\',
     "    | cat >/dev/null \\"),
    ("push.sh forces a diverged branch", "vm/abschrift/push.sh",
     'if [ -n "$old" ] && ! git -C "$repo" merge-base --is-ancestor "$old" "$new"; then',
     "if false; then"),
    ("setup leaves the verifier clone without trusted hook", "vm/setup_abschrift.sh",
     '        git -C "$ABSCHRIFT_VERIFIER_CLONE" config core.hooksPath "$hooks"', "        :"),
    ("setup overwrites a changed settings.json", "vm/setup_abschrift.sh",
     '    elif cmp -s "$source" "$target"; then', "    elif true; then cp \"$source\" \"$target\""),
    ("install_ma3.sh reloads AppArmor although a profile exists", "vm/install_ma3.sh",
     '    if [ -e "$APPARMOR_TARGET" ]; then', "    if false; then"),
    # ------------------------------------------------ Windows installer
    ("install_ma3.ps1 ignores the MANIFEST", "vm/windows/install_ma3.ps1",
     '        if ($actual -ne $hash) { Stop-Install "file changed since packing: $relative" }', ""),
    ("install_ma3.ps1 copies over local changes", "vm/windows/install_ma3.ps1",
     '    if ($changes) { Stop-Install "$Repo has local changes (nothing copied):`n$changes" }', ""),
    ("install_ma3.ps1 drops the hooks' executable bit", "vm/windows/install_ma3.ps1",
     '            Invoke-Git $Repo @("update-index", "--chmod=+x", "--", $file) | Out-Null', ""),
]


def run_mutation(mutation: tuple[str, str, str, str]) -> tuple[str, bool]:
    """(name, caught): caught = the test suite fails with the mutation.
    The mutation ("baseline", "", "", "") changes nothing: it must pass."""
    name, file, original, mutated = mutation
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "repo"
        shutil.copytree(REPO, copy, ignore=SKIP, symlinks=True)
        if file:
            target = copy / file
            text = target.read_text(encoding="utf-8")
            target.write_text(text.replace(original, mutated), encoding="utf-8")
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"],
                              cwd=copy, capture_output=True, text=True, check=False)
        return name, proc.returncode != 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-j", "--jobs", type=int, default=2)
    parser.add_argument("-k", help="only mutations whose name contains this text")
    args = parser.parse_args()
    selected = [m for m in MUTATIONS if not args.k or args.k in m[0]]
    for name, file, original, _ in selected:  # check every text first: no silent no-ops
        count = (REPO / file).read_text(encoding="utf-8").count(original)
        if count != 1:
            raise SystemExit(f"mutation '{name}': original text found {count}x in {file}")
    if run_mutation(("baseline", "", "", ""))[1]:
        raise SystemExit("the unmutated copy fails its own tests - fix that first")
    print("baseline  unmutated copy passes", flush=True)
    survived = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for name, caught in pool.map(run_mutation, selected):
            survived += not caught
            print(f"{'caught  ' if caught else 'SURVIVED'}  {name}", flush=True)
    print(f"\n{len(selected) - survived}/{len(selected)} mutations caught")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
