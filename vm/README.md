# vm/ - setup and routine scripts for the jcode VM

Origin: plan step 1.0 of *mejorador-audio* (automations of 2026-10-20);
Abschrift scripts and installers since jcode 0.10.0-ma.3 (step 1.0.b1).
These scripts prepare and check the Xubuntu VM where jcode runs. They are not
part of jcode itself and live outside `code/`, so the agent cannot read or
change them through its tools.

| Script | Where | When | What it does |
|---|---|---|---|
| `windows/snapshot_vm.ps1` | Windows | before risky changes | Soft-stops the VM, takes a dated snapshot, confirms it in the list. `-ListOnly` just lists. Never forces a stop, never deletes. |
| `setup_vm.sh` | VM | once (block D) | ffmpeg, Node.js LTS (SHA-256 checked), Python packages (pinned + hashed), Playwright browsers, jcode tests, `check_vm.py`. Safe to re-run. |
| `check_vm.py` | VM | after setup | Every tool does a small real job (FFT, WAV, LUFS, YIN, WPE, ffmpeg formats, Web Audio in 3 browsers). Prints `VM READY`. |
| `mount_data.sh` | VM | once, after sharing `datos` in VMware | Mounts the WD `datos/` folder read-only at `/mnt/datos` (VMware read-only + `ro` mount). Backs up `/etc/fstab`. |
| `set_ntfy_topic.sh` | VM | once | Creates a random ntfy.sh topic in `~/.bashrc` and sends a test notification. |
| `session_check.sh` | VM | start of every session | Traffic light: venv, key length, clean repos then `git pull --ff-only`, jcode tests, disk, tools, ntfy, read-only data; settings.json, chain keys, sandbox, cell. `session_check.sh ~/jcode-projekte/abschrift`: the same for Abschrift (chain without Google, `stop`, cell required). Never restores or discards anything. |
| `notify_run.sh` | VM | wrap long jobs | `notify_run.sh "<job>" <command...>`: runs the job, sends OK/FAILED + duration to the phone. Never sends output. |
| `windows/install_ma3.ps1` | Windows | jcode 0.10.0-ma.3 | Checks the unpacked package against `MANIFEST.sha256`, copies it into both working copies, `check_secrets.py --all`, hook files executable in git, commit, push. Stops at the first problem. |
| `install_ma3.sh` | VM | jcode 0.10.0-ma.3, after block D | pull, bubblewrap, AppArmor check (Ubuntu's own `bwrap-userns-restrict` only if bwrap is refused), SSH check, `setup_abschrift.sh`, tests, both session checks → `MA3 READY`. |
| `setup_abschrift.sh` | VM | once (by `install_ma3.sh`) | Abschrift: meeting repo, clones, project folder, Gemini CLI 0.64.0, its Playwright browsers, cells checked. Never overwrites; changes nothing of mejorador-audio. |
| `verify_abschrift.sh` | VM | after the installation | Abschrift plan 0.1.g, checked for real → report without keys for the Abschrift chat. |
| `abschrift/start_jcode.sh` | VM | every jcode session of Abschrift | Clean verifier clone or stop, `--branch`, `git pull --ff-only`, then `jcode --project ~/jcode-projekte/abschrift` (in its cell). |
| `abschrift/start_gemini.sh` | VM | every Gemini CLI session | Git of the Gemini clone in a sync cell, then Gemini CLI in its cell. The only way to start Gemini CLI. |
| `abschrift/push.sh` | VM | after an agent's work | `push.sh gemini\|verifier`: brings a clone's branch into the meeting repo (secret scan, no `tests/acceptance/` from Gemini, fast-forward only). |
| `abschrift/npm_install.sh` | VM | when package.json changes | npm packages + browsers of a clone, in an install cell. |
| `agent-hooks/pre-commit` | VM | (hook) | Trusted commit hook of the Abschrift verifier clone (outside every cell). |

## Rules these scripts keep

- The API key is never printed: only its length is checked.
- Nothing is restored, discarded or deleted automatically. Unexpected changes
  are shown; the decision stays with a person.
- Downloads are verified (Node: SHA-256 list; Python: `--require-hashes`).
- A failed notification never changes a job's result.

## Tests

```
python -m pytest -q          # everything; the cell tests need bubblewrap
```

`tools/check_secrets.py` and `.githooks/pre-commit` block commits that add an
API key or token. Activate once per clone: `git config core.hooksPath .githooks`.
