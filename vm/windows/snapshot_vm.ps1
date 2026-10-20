<#
.SYNOPSIS
  Take a dated snapshot of the jcode VM (uni-linux) with the VM shut down,
  or just list its snapshots.

.DESCRIPTION
  Origin: plan step 1.0.b (automation "snapshots with vmrun", 2026-10-20).
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
    [string]$VmFolder = "D:\VMs\uni-linux", # plan: location of the VM
    [string]$VmrunPath = "",                # override for tests
    [int]$ShutdownTimeoutSeconds = 180
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
if ($Start) { Invoke-Vmrun @("start", $script:Vmx) | Out-Null; Write-Host "VM started." }
