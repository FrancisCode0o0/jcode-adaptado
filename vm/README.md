# vm/ - setup and routine scripts for the jcode VM

Origin: plan step 1.0 of *mejorador-audio* (automations of 2026-10-20).
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
| `session_check.sh` | VM | start of every session | Traffic light: venv, key length, clean repos then `git pull --ff-only`, jcode tests, disk, tools, ntfy, read-only data. Never restores or discards anything. |
| `notify_run.sh` | VM | wrap long jobs | `notify_run.sh "<job>" <command...>`: runs the job, sends OK/FAILED + duration to the phone. Never sends output. |

## Rules these scripts keep

- The API key is never printed: only its length is checked.
- Nothing is restored, discarded or deleted automatically. Unexpected changes
  are shown; the decision stays with a person.
- Downloads are verified (Node: SHA-256 list; Python: `--require-hashes`).
- A failed notification never changes a job's result.

## Tests

```
python -m pytest vm/tests tools/tests -q
```

`tools/check_secrets.py` and `.githooks/pre-commit` block commits that add an
API key or token. Activate once per clone: `git config core.hooksPath .githooks`.
