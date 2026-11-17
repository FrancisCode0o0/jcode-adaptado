<#
.SYNOPSIS
  Take a dated snapshot of the jcode VM (uni-linux) with the VM shut down,
  or just list its snapshots.

.DESCRIPTION
  Origin: plan step 1.0.b (automation "snapshots with vmrun", 2026-10-20);
  ma.4: VM folder on E: by default, clear message when it is missing, and
  -Start returns the prompt.
  Plan decisions it follows:
    - snapshots are taken with the VM OFF (no RAM saved, ~6 GB smaller);
    - the VM is shut down only by asking the guest (soft stop). A forced
      stop could corrupt its file system, so this script never forces one:
      if the guest does not shut down in time, it stops and asks you.
    - it never deletes snapshots (irreversible; needed only before a disk
      enlargement, which stays a manual step).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\vm\windows\snapshot_vm.ps1 -ListOnly
  powershell -ExecutionPolicy Bypass -File .\vm\windows\snapshot_vm.ps1
  powershell -ExecutionPolicy Bypass -File .\vm\windows\snapshot_vm.ps1 -Name "block-d-done" -Start
#>
param(
    [string]$Name = "",                     # default: snap-YYYY-MM-DD
    [switch]$ListOnly,                      # only show the existing snapshots
    [switch]$Start,                         # start the VM again afterwards
    [string]$VmFolder = "E:\VMs\uni-linux", # plan: the VM lives on E: (WD) since 2026-10-21
    [string]$VmrunPath = "",                # override for tests
    [int]$ShutdownTimeoutSeconds = 180,
    [int]$StartTimeoutSeconds = 120         # vmrun start returns once the VM is powered on
)

$ErrorActionPreference = "Stop"
$MaxSnapshotsBeforeWarning = 3   # long chains cost disk space and speed
$PollIntervalSeconds = 5

function Find-Vmrun {
    if ($VmrunPath) { return $VmrunPath }
    $candidates = @(
        "${env:ProgramFiles(x86)}\VMware\VMware Workstation\vmrun.exe",
        "$env:ProgramFiles\VMware\VMware Workstation\vmrun.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    $command = Get-Command vmrun -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    throw "vmrun.exe not found (it comes with VMware Workstation)."
}

function Find-Vmx {
    # ma.4: a missing drive gave a confusing PowerShell error (2026-10-22).
    if (-not (Test-Path -LiteralPath $VmFolder)) {
        throw "VM folder not found: $VmFolder. Is the drive connected (E: = WD disk)? If the VM lives elsewhere, pass -VmFolder <folder>."
    }
    $files = @(Get-ChildItem -Path $VmFolder -Filter *.vmx -File)
    if ($files.Count -ne 1) { throw "Expected exactly one .vmx in $VmFolder, found $($files.Count)." }
    return $files[0].FullName
}

function Invoke-Vmrun([string[]]$Arguments) {
    # Windows PowerShell 5.1 turns native stderr into terminating errors when
    # the preference is "Stop"; judge vmrun by its exit code instead.
    $ErrorActionPreference = "Continue"
    $output = & $script:Vmrun -T ws @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "vmrun $($Arguments[0]) failed: $output" }
    return @($output | ForEach-Object { "$_" })
}

# "Total snapshots: 2" followed by one name per line.
function Get-Snapshots {
    $lines = Invoke-Vmrun @("listSnapshots", $script:Vmx)
    return @($lines | Select-Object -Skip 1 | Where-Object { $_.Trim() -ne "" })
}

# "Total running VMs: 1" followed by one .vmx path per line.
function Test-VmRunning {
    $lines = Invoke-Vmrun @("list")
    return [bool]($lines | Select-Object -Skip 1 | Where-Object { $_.Trim() -ieq $script:Vmx })
}

function Stop-VmSoftly {
    Write-Host "Asking the VM to shut down (soft stop)..."
    Invoke-Vmrun @("stop", $script:Vmx, "soft") | Out-Null
    $deadline = (Get-Date).AddSeconds($ShutdownTimeoutSeconds)
    while (Test-VmRunning) {
        if ((Get-Date) -gt $deadline) {
            throw "The VM did not shut down in $ShutdownTimeoutSeconds s. Shut it down from inside (save your work) and run this again. Nothing was forced."
        }
        Start-Sleep -Seconds $PollIntervalSeconds
    }
    Write-Host "VM is off."
}

# ma.4: 'vmrun start' leaves the VMware process running with vmrun's output
# handles; capturing them (Invoke-Vmrun) waited until that process ended, so
# the prompt never came back (2026-10-22). Here vmrun writes straight to this
# console and only vmrun itself is waited for (with a time limit).
function Start-VmDetached {
    $process = Start-Process -FilePath $script:Vmrun -ArgumentList @("-T", "ws", "start", "`"$($script:Vmx)`"") `
        -NoNewWindow -PassThru
    if (-not $process.WaitForExit($StartTimeoutSeconds * 1000)) {
        throw "vmrun start did not return in $StartTimeoutSeconds s. Check the VMware window; nothing else was done."
    }
    if ($process.ExitCode -ne 0) { throw "vmrun start failed (exit $($process.ExitCode)); its message is above." }
    Write-Host "VM started."
}

function Get-FreeSnapshotName([string[]]$Existing) {
    $base = if ($Name) { $Name } else { "snap-$(Get-Date -Format 'yyyy-MM-dd')" }
    $candidate = $base
    $suffix = 2
    while ($Existing -contains $candidate) { $candidate = "$base-$suffix"; $suffix++ }
    return $candidate
}

function Show-Snapshots([string[]]$Snapshots) {
    Write-Host "Snapshots of $($script:Vmx) ($($Snapshots.Count)):"
    $Snapshots | ForEach-Object { Write-Host "  - $_" }
}

$script:Vmrun = Find-Vmrun
$script:Vmx = Find-Vmx
$existing = Get-Snapshots
Show-Snapshots $existing
if ($ListOnly) { exit 0 }

$snapshotName = Get-FreeSnapshotName $existing
if (Test-VmRunning) { Stop-VmSoftly }
Write-Host "Taking snapshot '$snapshotName'..."
Invoke-Vmrun @("snapshot", $script:Vmx, $snapshotName) | Out-Null

$after = Get-Snapshots
if ($after -notcontains $snapshotName) { throw "Snapshot '$snapshotName' does not appear in the list." }
Write-Host "OK: snapshot '$snapshotName' confirmed."
Show-Snapshots $after
if ($after.Count -gt $MaxSnapshotsBeforeWarning) {
    Write-Warning "$($after.Count) snapshots. Long chains use disk space and slow the VM; deleting old ones is a manual decision (VMware -> Snapshot Manager)."
}
if ($Start) { Start-VmDetached }
