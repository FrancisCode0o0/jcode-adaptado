"""Tests for vm/windows/snapshot_vm.ps1 (ma.4) with a fake vmrun.

The fake vmrun keeps its state in files (running yes/no, snapshot list). Its
'start' leaves a child process holding vmrun's output handles, like VMware's
window does on Windows: the old script waited for that child, so the prompt
never came back (2026-10-22). Skipped when pwsh is not installed.
Run: python -m pytest vm/tests/test_snapshot_vm.py -q
"""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "windows" / "snapshot_vm.ps1"
CHILD_SECONDS = 20  # how long the fake "VMware window" keeps the handles open

pytestmark = pytest.mark.skipif(shutil.which("pwsh") is None, reason="needs pwsh")

FAKE_VMRUN = f"""#!/bin/bash
# fake vmrun: -T ws <command> [vmx] [args]
state="$(dirname "$0")"
shift 2
case "$1" in
  list)
    if [ -f "$state/running" ]; then echo "Total running VMs: 1"; cat "$state/running";
    else echo "Total running VMs: 0"; fi ;;
  listSnapshots)
    echo "Total snapshots: $(wc -l < "$state/snapshots")"; cat "$state/snapshots" ;;
  snapshot) echo "$3" >> "$state/snapshots" ;;
  stop) rm -f "$state/running" ;;
  start)
    echo "$2" > "$state/running"
    echo "$2" > "$state/started-with"
    sleep {CHILD_SECONDS} &   # inherits stdout/stderr, like the VMware window
    ;;
esac
"""


@pytest.fixture
def vm(tmp_path: Path):
    folder = tmp_path / "VMs" / "uni linux"  # a space, as Windows paths often have
    folder.mkdir(parents=True)
    vmx = folder / "uni-linux.vmx"
    vmx.write_text("")
    state = tmp_path / "vmrun"
    state.mkdir()
    (state / "snapshots").write_text("base\n")
    vmrun = state / "vmrun"
    vmrun.write_text(FAKE_VMRUN)
    vmrun.chmod(0o755)
    return folder, vmx, state, vmrun


def run_script(vmrun: Path, *args: str, timeout: int = 60):
    """Runs the script and returns when pwsh exits - as the prompt would come back.

    Output goes to a file, not a pipe: with a pipe, the fake VMware child that
    inherits it would keep this test waiting for end-of-file, not the script."""
    log = vmrun.parent / "output.txt"
    with log.open("w") as handle:
        process = subprocess.Popen(
            ["pwsh", "-NoProfile", "-File", str(SCRIPT), "-VmrunPath", str(vmrun),
             "-ShutdownTimeoutSeconds", "10", *args],
            stdout=handle, stderr=subprocess.STDOUT, text=True)
        returncode = process.wait(timeout=timeout)
    return subprocess.CompletedProcess(process.args, returncode, log.read_text(), "")


def test_start_returns_the_prompt_while_the_vm_window_runs(vm):
    folder, vmx, state, vmrun = vm
    started = time.monotonic()
    result = run_script(vmrun, "-VmFolder", str(folder), "-Name", "t1", "-Start")
    elapsed = time.monotonic() - started
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK: snapshot 't1' confirmed." in result.stdout and "VM started." in result.stdout
    assert elapsed < CHILD_SECONDS / 2, f"waited {elapsed:.0f} s for the VM window"
    assert (state / "started-with").read_text().strip() == str(vmx)  # path with a space kept whole


def test_missing_vm_folder_says_so(vm, tmp_path: Path):
    _, _, _, vmrun = vm
    result = run_script(vmrun, "-VmFolder", str(tmp_path / "no-such-drive"), "-ListOnly")
    assert result.returncode != 0
    assert "VM folder not found" in result.stdout + result.stderr


def test_default_folder_is_on_e():
    assert '[string]$VmFolder = "E:\\VMs\\uni-linux"' in SCRIPT.read_text()
