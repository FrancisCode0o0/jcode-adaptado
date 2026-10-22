<#
.SYNOPSIS
  Windows part of installing jcode 0.10.0-ma.3: copy the package into both
  working copies, check them for secrets, commit and push.

.DESCRIPTION
  Origin: mejorador-audio plan step 1.0.b1 (automation of 2026-10-20).
  Run it from the UNPACKED package (it lives in
  <package>\jcode-adaptado\vm\windows\). In order, for each repository
  (C:\Users\Francis\jcode-adaptado and C:\Users\Francis\mejorador-audio):
    1. it is a git working copy on 'main' with no local changes, and
       'git pull --ff-only' brings it up to date;
    2. core.hooksPath is .githooks (set if empty; another value -> stop);
    3. every file of the package matches MANIFEST.sha256 (checked once,
       before anything is copied);
    4. the package's files are copied over the working copy;
    5. tools\check_secrets.py --all finds nothing;
    6. the hook files get the executable bit in git (a commit from Windows
       drops it, and git silently ignores a hook that is not executable);
    7. commit (the secret hook runs once more) and push - never forced.
  It stops at the first problem, says which step and changes nothing more.
  It never deletes, resets or forces anything.

.EXAMPLE
  Expand-Archive "$env:USERPROFILE\Downloads\jcode-adaptado-0.10.0-ma.3.zip" "$env:USERPROFILE\Downloads\ma3"
  powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\Downloads\ma3\jcode-adaptado\vm\windows\install_ma3.ps1"
#>
param(
    [string]$Package = "",                                   # default: the unpacked package this script is in
    [string]$JcodeRepo = "$env:USERPROFILE\jcode-adaptado",
    [string]$AudioRepo = "$env:USERPROFILE\mejorador-audio",
    [switch]$NoPush                                          # tests: commit only
)

$ErrorActionPreference = "Stop"
$ExpectedVersion = "0.10.0-ma.3"
$CommitMessage = "jcode ${ExpectedVersion}: cells (bubblewrap), merged secret check and session check, Abschrift setup"
# Hooks run directly by git: they must be executable in the repository.
$ExecutableFiles = @{
    "jcode-adaptado"  = @(".githooks/pre-commit", "vm/agent-hooks/pre-commit")
    "mejorador-audio" = @(".githooks/pre-commit")
}
$script:Step = "start"

function Stop-Install([string]$Message) {
    throw "STOPPED at step '$script:Step': $Message"
}

function Invoke-Git([string]$Repo, [string[]]$Arguments) {
    # Windows PowerShell 5.1 turns native stderr into errors under "Stop";
    # judge git by its exit code instead.
    $ErrorActionPreference = "Continue"
    $output = & git -C $Repo @Arguments 2>&1
    $code = $LASTEXITCODE
    $text = (@($output | ForEach-Object { "$_" }) -join "`n")
    if ($code -ne 0) { Stop-Install "git $($Arguments -join ' ') failed in ${Repo}:`n$text" }
    return $text
}

function Find-Python {
    foreach ($candidate in @(@("py", "-3"), @("python3"), @("python"))) {
        $exe = $candidate[0]
        $rest = @($candidate | Select-Object -Skip 1)
        if (Get-Command $exe -ErrorAction SilentlyContinue) {
            $ErrorActionPreference = "Continue"
            & $exe @rest -c "import sys" 2>$null
            if ($LASTEXITCODE -eq 0) { return , $candidate }
        }
    }
    Stop-Install "no Python found (needed for tools\check_secrets.py)."
}

function Resolve-Package {
    if ($Package) { return (Resolve-Path $Package).Path }
    return (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
}

function Test-PackageManifest([string]$Root) {
    $script:Step = "check package"
    $manifest = Join-Path $Root "MANIFEST.sha256"
    if (-not (Test-Path $manifest)) { Stop-Install "MANIFEST.sha256 missing in $Root (unpack the whole ZIP)." }
    $count = 0
    foreach ($line in Get-Content $manifest) {
        if (-not $line.Trim()) { continue }
        $hash, $relative = $line -split "  ", 2
        $file = Join-Path $Root ($relative -replace "/", "\")
        if (-not (Test-Path -LiteralPath $file)) { Stop-Install "file missing from the package: $relative" }
        $actual = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLower()
        if ($actual -ne $hash) { Stop-Install "file changed since packing: $relative" }
        $count++
    }
    $versionLine = Select-String -Path (Join-Path $Root "jcode-adaptado\jcode.py") -Pattern '^APP_VERSION = "([^"]+)"'
    if (-not $versionLine -or $versionLine.Matches[0].Groups[1].Value -ne $ExpectedVersion) {
        Stop-Install "the package is not jcode $ExpectedVersion."
    }
    Write-Host "Package OK: $count files match MANIFEST.sha256, jcode $ExpectedVersion."
}

function Assert-CleanAndCurrent([string]$Repo) {
    $script:Step = "check $Repo"
    if (-not (Test-Path (Join-Path $Repo ".git"))) { Stop-Install "$Repo is not a git working copy." }
    $branch = Invoke-Git $Repo @("rev-parse", "--abbrev-ref", "HEAD")
    if ($branch -ne "main") { Stop-Install "$Repo is on branch '$branch', expected 'main'." }
    $changes = Invoke-Git $Repo @("status", "--porcelain")
    if ($changes) { Stop-Install "$Repo has local changes (nothing copied):`n$changes" }
    Invoke-Git $Repo @("pull", "--ff-only", "--quiet") | Out-Null
    $hooks = (& git -C $Repo config --get core.hooksPath) 2>$null
    if (-not $hooks) {
        Invoke-Git $Repo @("config", "core.hooksPath", ".githooks") | Out-Null
        Write-Host "  core.hooksPath set to .githooks"
    } elseif ($hooks -ne ".githooks") {
        Stop-Install "core.hooksPath in $Repo is '$hooks', expected .githooks; nothing changed."
    }
}

function Install-Into([string]$Source, [string]$Repo, [string]$Name, [string[]]$Python) {
    $script:Step = "copy into $Name"
    Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $Repo -Recurse -Force
    }
    $script:Step = "secret check in $Name"
    $exe = $Python[0]
    $rest = @($Python | Select-Object -Skip 1)
    Push-Location $Repo
    try {
        $ErrorActionPreference = "Continue"
        & $exe @rest "tools/check_secrets.py" "--all" | Out-Host
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($code -ne 0) { Stop-Install "check_secrets.py --all found something in $Name (above); nothing committed." }
    $script:Step = "commit in $Name"
    Invoke-Git $Repo @("add", "-A") | Out-Null
    foreach ($file in $ExecutableFiles[$Name]) {
        if (Test-Path (Join-Path $Repo $file)) {
            Invoke-Git $Repo @("update-index", "--chmod=+x", "--", $file) | Out-Null
        }
    }
    $staged = Invoke-Git $Repo @("diff", "--cached", "--name-status")
    if (-not $staged) {
        Write-Host "  $Name already has these files - nothing to commit."
        return $false
    }
    Write-Host "  Changes in ${Name}:"
    $staged -split "`n" | ForEach-Object { Write-Host "    $_" }
    Invoke-Git $Repo @("commit", "--quiet", "-m", $CommitMessage) | Out-Null
    Write-Host "  Commit $(Invoke-Git $Repo @('rev-parse', '--short', 'HEAD')) created (secret hook passed)."
    return $true
}

function Push-Repo([string]$Repo, [string]$Name) {
    $script:Step = "push $Name"
    if ($NoPush) { Write-Host "  (no push: -NoPush)"; return }
    Invoke-Git $Repo @("push", "origin", "main") | Out-Null
    Write-Host "  Pushed to GitHub."
}

try {
    $root = Resolve-Package
    $python = Find-Python
    Test-PackageManifest $root
    $targets = @(
        @{ Name = "jcode-adaptado"; Repo = $JcodeRepo },
        @{ Name = "mejorador-audio"; Repo = $AudioRepo }
    )
    foreach ($target in $targets) { Assert-CleanAndCurrent $target.Repo }
    foreach ($target in $targets) {
        Write-Host "== $($target.Name) ($($target.Repo))"
        $source = Join-Path $root $target.Name
        if (Install-Into $source $target.Repo $target.Name $python) { Push-Repo $target.Repo $target.Name }
    }
    Write-Host ""
    Write-Host "WINDOWS DONE. Next, in the VM: bash ~/jcode-adaptado/vm/install_ma3.sh"
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
