#Requires -Version 7.6
#Requires -PSEdition Core
<#
.SYNOPSIS
    RAGRAF installer: environment, dependencies, verified readiness.

.DESCRIPTION
    Installs RAGRAF into a dedicated virtual environment and VERIFIES that the
    result actually works. Every step ends with a check; the final state is
    printed as a summary table and (optionally) written as JSON for CI.

    Pipeline:
      1. environment checks (pwsh 7.6+, python >= 3.13, venv module, disk space);
      2. virtual environment creation OUTSIDE the repository (by default);
      3. package install (editable) or dependencies only;
      4. optional truststore (required under TLS inspection);
      5. launch + version verification (console command and wrapper);
      6. optional corpus preparation (ensure) and indexing;
      7. component verification via `selftest --json` (machine-readable).

    What this script deliberately does NOT do:
      * index a corpus without an explicit -Project/-Index: the first index run
        downloads the embedding model (~240 MB) and may build the code property
        graph (tens of seconds to minutes). That is a separate decision;
      * write into the repository except the venv (if you point it inside) and
        `.ragraf/` inside the indexed project;
      * install Java/Joern: without them the product still works, but `slice`
        refuses with a reason (see scripts/install-joern.ps1).

    Improvements over the previous revision (2026-10-12 refactor):
      * no more pipe deadlock: child stdout/stderr are drained concurrently
        (the old ReadToEnd-then-ReadToEnd pattern could hang pip installs);
      * every external process runs under a timeout (defaults: pip 30 min,
        ensure 10 min, index 60 min, quick checks 2 min) — a hung proxy no
        longer freezes the installer forever;
      * free disk space is checked before venv/pip/model steps;
      * Microsoft Store Python stub (App Execution Alias) is detected and
        explained instead of failing with a cryptic message;
      * selftest is parsed from JSON (`--json`), not from localized text —
        wording changes can no longer break verification silently;
      * full transcript is written to ~/.ragraf/logs/install-<ts>.log so any
        failure can be attached to an issue;
      * -Force venv removal is guarded (locked files produce an actionable
        FAIL row instead of a raw Win32 exception);
      * "venv inside repository" warning fires for newly created venvs too;
      * cross-platform "what's next" hints (Scripts/ vs bin/);
      * exit codes follow the documented contract strictly:
        0 success · 1 install error · 2 bad arguments · 3 unsuitable environment.

.PARAMETER Source
    Directory containing the package sources (pyproject.toml). Default: the
    repository root (one level above this script).

.PARAMETER Venv
    Where to create the virtual environment. Default: ~/.ragraf/venv.
    Creating it inside the repository is discouraged: the environment is
    machine-local and would become part of the indexed corpus.

.PARAMETER Python
    Interpreter used to create the environment. Default: `python` from PATH
    (then `python3`). Must be 3.13+ with the `venv` module.

.PARAMETER DependenciesOnly
    Install requirements.txt only, not the package itself. Wrappers
    (tools/ragraf.py, scripts/ragraf.ps1) keep working; the `ragraf` console
    command is not created. This path does not create src/ragraf.egg-info.

.PARAMETER WithTruststore
    Additionally install `truststore` (system certificate store). Required in
    networks with TLS inspection; unnecessary at home.

.PARAMETER Project
    Project directory to prepare a corpus for. When set, the script runs
    `ensure` against it.

.PARAMETER Index
    Index the project immediately (may download the ~240 MB embedding model).

.PARAMETER NoVectors
    Index without the vector layer: no model download; lexical (FTS5/BM25)
    search still works.

.PARAMETER Force
    Recreate the virtual environment if it already exists.

.PARAMETER MinFreeGb
    Minimum free disk space (GB) required on the venv target drive. Default 3.
    With -Project -Index the effective requirement is raised by 1 GB (model).

.PARAMETER NoTranscript
    Do not write the log file.

.PARAMETER SummaryJson
    Optional path: write the machine-readable summary (steps, states, exit
    verdict) for CI pipelines.

.PARAMETER TimeoutScale
    Multiplier for all built-in timeouts (slow networks/VMs: use 2-4).

.EXAMPLE
    pwsh -NoLogo -NoProfile -File scripts/install.ps1
    Install into ~/.ragraf/venv without corpus preparation.

.EXAMPLE
    pwsh -NoLogo -NoProfile -File scripts/install.ps1 -Project C:\work\myproject -Index
    Install, prepare the corpus and run full indexing.

.NOTES
    Exit codes: 0 — success; 1 — install error; 2 — invalid arguments;
    3 — unsuitable environment (no/old python, no venv module, no disk space).
#>
[CmdletBinding()]
param(
    [string]$Source,
    [string]$Venv,
    [string]$Python,
    [switch]$DependenciesOnly,
    [switch]$WithTruststore,
    [string]$Project,
    [switch]$Index,
    [switch]$NoVectors,
    [switch]$Force,
    [double]$MinFreeGb = 3.0,
    [switch]$NoTranscript,
    [string]$Offline,
    [string]$SummaryJson,
    [double]$TimeoutScale = 1.0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Encodings: UTF-8 without BOM for both output and child-process pipes.
# Otherwise localized selftest/index output arrives mangled — and we parse it.
$OutputEncoding = [Console]::InputEncoding = [Console]::OutputEncoding =
    [System.Text.UTF8Encoding]::new($false)

$script:ExitOk = 0
$script:ExitError = 1
$script:ExitUsage = 2
$script:ExitEnvironment = 3

# Offline (air-gapped) installation is a DIFFERENT pipeline and lives in its own
# script: it must not touch the network at all, while this installer downloads
# dependencies and the embedding model. The switch exists so the documented form
# `install.ps1 -Offline <set>` works; all work is delegated unchanged.
if ($Offline) {
    $offlineScript = Join-Path $PSScriptRoot 'install-airgap.ps1'
    if (-not (Test-Path -LiteralPath $offlineScript -PathType Leaf)) {
        Write-Host "offline installer not found: $offlineScript" -ForegroundColor Red
        exit $script:ExitUsage
    }
    Write-Host "offline set: $Offline"
    $offlineArgs = @{ Set = $Offline }
    if ($Venv) { $offlineArgs['Prefix'] = $Venv }
    & $offlineScript @offlineArgs
    exit $LASTEXITCODE
}

# Timeouts (seconds), scaled by -TimeoutScale. Rationale: pip may sit on a slow
# proxy for many minutes; indexing a large corpus legitimately takes tens of
# minutes; quick probes must not hang at all.
$script:TO = @{
    Probe   = [int](120  * $TimeoutScale)   # python -c, --version, selftest
    Pip     = [int](1800 * $TimeoutScale)   # dependency installation
    Ensure  = [int](600  * $TimeoutScale)   # corpus ensure (may reindex)
    Index   = [int](3600 * $TimeoutScale)   # full indexing incl. model download
}

$script:Results = [System.Collections.Generic.List[object]]::new()
$script:TranscriptPath = $null

function Add-Result {
    param([string]$Step, [ValidateSet('OK', 'WARN', 'FAIL', 'SKIP')][string]$State, [string]$Detail = '')
    $script:Results.Add([pscustomobject]@{ Step = $Step; State = $State; Detail = $Detail })
    $color = switch ($State) {
        'OK'    { 'Green' }
        'WARN'  { 'Yellow' }
        'FAIL'  { 'Red' }
        'SKIP'  { 'DarkGray' }
        default { 'Gray' }
    }
    Write-Host ("  {0,-6} {1,-42} {2}" -f $State, $Step, $Detail) -ForegroundColor $color
}

function Write-Section {
    param([string]$Title)
    Write-Host ''
    Write-Host $Title -ForegroundColor Cyan
}

function Stop-Install {
    param([int]$Code, [string]$Message)
    Write-Host ''
    Write-Host "STOP: $Message" -ForegroundColor Red
    Show-Summary
    Write-SummaryJson
    Close-InstallTranscript
    exit $Code
}

function Close-InstallTranscript {
    if ($script:TranscriptPath) {
        try { Stop-Transcript | Out-Null } catch { Write-Verbose "transcript stop: $($_.Exception.Message)" }
    }
}

function Write-SummaryJson {
    # $SummaryJson is the script-level parameter (PSReviewUnusedParameter false positive:
    # the analyzer does not track script-scope parameter usage inside functions).
    if (-not $SummaryJson) { return }
    try {
        $failed = @($script:Results | Where-Object State -eq 'FAIL').Count
        $warned = @($script:Results | Where-Object State -eq 'WARN').Count
        $payload = [ordered]@{
            tool       = 'ragraf-install'
            version    = 2
            started_at = $script:StartedAt.ToString('o')
            finished_at= (Get-Date).ToString('o')
            host_os    = if ($IsWindows) { 'windows' } elseif ($IsMacOS) { 'macos' } else { 'linux' }
            pwsh       = $PSVersionTable.PSVersion.ToString()
            venv       = $Venv
            project    = $Project
            steps      = $script:Results
            failed     = $failed
            warned     = $warned
            verdict    = if ($failed -gt 0) { 'fail' } elseif ($warned -gt 0) { 'warn' } else { 'ok' }
            transcript = $script:TranscriptPath
        }
        $dir = Split-Path -Parent $SummaryJson
        if ($dir -and -not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Force -Path $dir | Out-Null
        }
        $payload | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $SummaryJson -Encoding utf8NoBOM
    }
    catch {
        Write-Warning "could not write summary JSON: $($_.Exception.Message)"
    }
}

function Show-Summary {
    Write-Section 'SUMMARY'
    if ($script:Results.Count -eq 0) {
        Write-Host '  (no checks executed)'
        return
    }
    foreach ($r in $script:Results) {
        $mark = switch ($r.State) { 'OK' { 'OK  ' } 'WARN' { 'WARN' } 'SKIP' { 'SKIP' } default { 'FAIL' } }
        Write-Host ("  {0}  {1,-42} {2}" -f $mark, $r.Step, $r.Detail)
    }
    $failed = @($script:Results | Where-Object State -eq 'FAIL').Count
    $warned = @($script:Results | Where-Object State -eq 'WARN').Count
    Write-Host ''
    if ($failed -gt 0) {
        Write-Host "  errors: $failed, warnings: $warned" -ForegroundColor Red
    }
    elseif ($warned -gt 0) {
        Write-Host "  errors: 0, warnings: $warned" -ForegroundColor Yellow
    }
    else {
        Write-Host '  errors: 0, warnings: 0' -ForegroundColor Green
    }
    if ($script:TranscriptPath) {
        Write-Host "  log: $($script:TranscriptPath)" -ForegroundColor DarkGray
    }
}

function Invoke-Checked {
    <#
    Run an external command with exit-code checking, output capture AND a timeout.

    Why a dedicated function:
      * PowerShell does not turn a non-zero native exit code into an exception
        even with $ErrorActionPreference='Stop' — without an explicit check a
        failed command looks like success;
      * reading stdout to EOF before stderr deadlocks when the child fills the
        stderr pipe buffer (~4 KB) while we are still draining stdout — pip does
        exactly that on slow networks. Both streams are drained CONCURRENTLY
        here via ReadToEndAsync tasks.
    #>
    param(
        [Parameter(Mandatory)][string]$File,
        [AllowEmptyCollection()][string[]]$Arguments = @(),
        [int[]]$AllowedExitCodes = @(0),
        [int]$TimeoutSec = 120
    )
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $File
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $psi.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $psi.StandardErrorEncoding = [System.Text.UTF8Encoding]::new($false)
    foreach ($a in $Arguments) { $psi.ArgumentList.Add($a) }

    $proc = [System.Diagnostics.Process]::Start($psi)
    # Concurrent drain — fixes the ReadToEnd deadlock of the previous revision.
    $stdoutTask = $proc.StandardOutput.ReadToEndAsync()
    $stderrTask = $proc.StandardError.ReadToEndAsync()

    $exited = $proc.WaitForExit($TimeoutSec * 1000)
    if (-not $exited) {
        try { $proc.Kill($true) }
        catch {
            # Tree-kill unsupported on this runtime: fall back to single-process kill.
            try { $proc.Kill() } catch { Write-Verbose "kill: $($_.Exception.Message)" }
        }
        $null = $proc.WaitForExit(5000)
        return [pscustomobject]@{
            ExitCode = -1
            StdOut   = $stdoutTask.IsCompleted ? $stdoutTask.Result : '(killed: partial output lost)'
            StdErr   = "TIMEOUT after ${TimeoutSec}s — process killed"
            Ok       = $false
            TimedOut = $true
        }
    }
    # Streams may lag process exit slightly; bounded wait keeps it safe.
    $null = $stdoutTask.Wait(5000)
    $null = $stderrTask.Wait(5000)
    return [pscustomobject]@{
        ExitCode = $proc.ExitCode
        StdOut   = $stdoutTask.IsCompleted ? $stdoutTask.Result : ''
        StdErr   = $stderrTask.IsCompleted ? $stderrTask.Result : ''
        Ok       = ($AllowedExitCodes -contains $proc.ExitCode)
        TimedOut = $false
    }
}

function Get-FreeGb {
    param([Parameter(Mandatory)][string]$Path)
    try {
        $full = [System.IO.Path]::GetFullPath($Path)
        if ($IsWindows) {
            $drive = ([System.IO.DriveInfo]::new($full)).Name
            $info = [System.IO.DriveInfo]::new($drive)
        }
        else {
            $info = [System.IO.DriveInfo]::new($full)
        }
        return [math]::Round($info.AvailableFreeSpace / 1GB, 2)
    }
    catch { return $null }
}

function Get-LastErrorLine {
    param([string]$Text, [int]$Count = 3)
    if ([string]::IsNullOrWhiteSpace($Text)) { return '' }
    return (($Text.Trim() -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -Last $Count) -join ' | ')
}

$script:StartedAt = Get-Date

# --------------------------------------------------------------------------
# Step 0. Transcript (so failures can be reported)
# --------------------------------------------------------------------------
if (-not $NoTranscript) {
    try {
        $logRoot = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.ragraf/logs'
        New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
        $script:TranscriptPath = Join-Path $logRoot ("install-{0:yyyyMMdd-HHmmss}.log" -f $script:StartedAt)
        Start-Transcript -LiteralPath $script:TranscriptPath -Append | Out-Null
    }
    catch {
        Write-Warning "transcript unavailable: $($_.Exception.Message)"
        $script:TranscriptPath = $null
    }
}

Write-Host 'RAGRAF — installation' -ForegroundColor White

# --------------------------------------------------------------------------
# Step 1. Environment
# --------------------------------------------------------------------------
Write-Section '1. Environment checks'

$v = $PSVersionTable.PSVersion
if ($PSVersionTable.PSEdition -ne 'Core') {
    Add-Result -Step 'PowerShell Core (pwsh)' -State 'FAIL' -Detail "edition: $($PSVersionTable.PSEdition)"
    Stop-Install -Code $script:ExitEnvironment -Message 'PowerShell Core (pwsh) is required, not Windows PowerShell.'
}
if ($v -lt [version]'7.6') {
    Add-Result -Step 'PowerShell 7.6+' -State 'FAIL' -Detail "found $v"
    Stop-Install -Code $script:ExitEnvironment -Message 'PowerShell 7.6 or newer is required.'
}
Add-Result -Step 'PowerShell 7.6+' -State 'OK' -Detail "$v ($($PSVersionTable.PSEdition))"

if (-not $Source) { $Source = Split-Path -Parent $PSScriptRoot }
if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
    Add-Result -Step 'Sources found' -State 'FAIL' -Detail "no such directory: $Source"
    # Contract fix: a bad -Source is a USAGE error (2), not environment (3).
    Stop-Install -Code $script:ExitUsage -Message "source directory does not exist: $Source"
}
if (-not (Test-Path -LiteralPath (Join-Path $Source 'pyproject.toml'))) {
    Add-Result -Step 'Sources found' -State 'FAIL' -Detail "no pyproject.toml in $Source"
    Stop-Install -Code $script:ExitUsage -Message "point -Source at the directory containing pyproject.toml (not found in: $Source)."
}
$Source = (Resolve-Path -LiteralPath $Source).Path
Add-Result -Step 'Sources found' -State 'OK' -Detail $Source

# --- Python discovery: explicit -Python, then python, then python3. ---
if (-not $Python) {
    foreach ($name in @('python', 'python3')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { $Python = $cmd.Source; break }
    }
}
if (-not $Python) {
    Add-Result -Step 'Python interpreter' -State 'FAIL' -Detail 'not found in PATH'
    Stop-Install -Code $script:ExitEnvironment -Message 'python not found in PATH. Install Python 3.13+ or pass -Python <path>.'
}
# Microsoft Store App Execution Alias: prints a banner and exits 9009.
# Detect by path — the failure message below would otherwise be cryptic.
if ($IsWindows -and $Python -match '\\WindowsApps\\') {
    Add-Result -Step 'Python interpreter' -State 'FAIL' -Detail 'Microsoft Store alias detected'
    Stop-Install -Code $script:ExitEnvironment -Message @(
        "'$Python' is the Microsoft Store App Execution Alias, not a real interpreter."
        'Install Python 3.13+ from python.org (or your vendor), or disable the alias:'
        'Settings > Apps > Advanced app settings > App execution aliases.'
    ) -join ' '
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf) -and -not (Get-Command $Python -ErrorAction SilentlyContinue)) {
    Add-Result -Step 'Python interpreter' -State 'FAIL' -Detail "not runnable: $Python"
    Stop-Install -Code $script:ExitEnvironment -Message "interpreter is not runnable: $Python"
}

$pyVer = Invoke-Checked -File $Python -TimeoutSec $script:TO.Probe `
    -Arguments @('-c', 'import sys; print("%d.%d.%d" % sys.version_info[:3])')
if (-not $pyVer.Ok) {
    Add-Result -Step 'Python interpreter' -State 'FAIL' -Detail "does not run: $Python ($($pyVer.ExitCode))"
    Stop-Install -Code $script:ExitEnvironment -Message "the given interpreter does not run: $Python"
}
$versionText = $pyVer.StdOut.Trim()
# Single robust parse (previous revision parsed twice and kept dead $dyVersion).
$parts = $versionText -split '\.'
if ($parts.Count -lt 2) {
    Add-Result -Step 'Python version' -State 'FAIL' -Detail "unparsable: '$versionText'"
    Stop-Install -Code $script:ExitEnvironment -Message "cannot parse python version output: '$versionText'"
}
$majorMinor = [version]"$($parts[0]).$($parts[1])"
if ($majorMinor -lt [version]'3.13') {
    Add-Result -Step 'Python >= 3.13' -State 'FAIL' -Detail "found $versionText"
    Stop-Install -Code $script:ExitEnvironment -Message "Python 3.13+ required; found $versionText ($Python)."
}
Add-Result -Step 'Python >= 3.13' -State 'OK' -Detail "$versionText ($Python)"

# venv module: some vendored/embeddable builds ship without it.
$venvProbe = Invoke-Checked -File $Python -TimeoutSec $script:TO.Probe `
    -Arguments @('-c', 'import venv; print("venv ok")')
if (-not $venvProbe.Ok) {
    Add-Result -Step 'venv module' -State 'FAIL' -Detail 'module missing'
    Stop-Install -Code $script:ExitEnvironment -Message "interpreter $Python has no venv module. A full Python 3.13+ distribution is required (not an app-bundled one)."
}
Add-Result -Step 'venv module' -State 'OK' -Detail $venvProbe.StdOut.Trim()

# --- venv location & disk space ---
if (-not $Venv) {
    $Venv = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.ragraf/venv'
}
$requiredGb = $MinFreeGb
if ($Project -and $Index -and -not $NoVectors) { $requiredGb += 1.0 }  # model ~240 MB + index growth
$freeGb = Get-FreeGb -Path (Split-Path -Parent $Venv -ErrorAction SilentlyContinue)
if (-not $freeGb) { $freeGb = Get-FreeGb -Path ([Environment]::GetFolderPath('UserProfile')) }
if ($null -ne $freeGb -and $freeGb -lt $requiredGb) {
    Add-Result -Step 'Free disk space' -State 'FAIL' -Detail "$freeGb GB free, need >= $requiredGb GB"
    Stop-Install -Code $script:ExitEnvironment -Message "not enough disk space for venv + dependencies (ONNX Runtime and tree-sitter wheels take 1.5-2.5 GB): have $freeGb GB, need $requiredGb GB."
}
Add-Result -Step 'Free disk space' -State 'OK' -Detail "$freeGb GB free (need >= $requiredGb GB)"

# "venv inside repository" check — now unconditional (previous revision only
# warned for pre-existing venvs and stayed silent when creating one inside).
$insideSource = (Resolve-Path -LiteralPath $Source).Path.TrimEnd([IO.Path]::DirectorySeparatorChar)
$venvFull = [System.IO.Path]::GetFullPath($Venv)
$insideSource = $venvFull.StartsWith($Source + [IO.Path]::DirectorySeparatorChar,
    [System.StringComparison]::OrdinalIgnoreCase)
if ($insideSource) {
    Add-Result -Step 'Venv outside repository' -State 'WARN' `
        -Detail 'venv inside the project will be indexed: add it to exclude or move it out'
}

if (Test-Path -LiteralPath $Venv) {
    if ($Force) {
        Add-Result -Step 'Virtual environment' -State 'WARN' -Detail "-Force: recreating $Venv"
        try {
            Remove-Item -LiteralPath $Venv -Recurse -Force
        }
        catch {
            # Locked files (a shell still using the venv, AV) must produce an
            # actionable FAIL row, not a raw Win32 exception that kills the run.
            Add-Result -Step 'Virtual environment' -State 'FAIL' `
                -Detail "cannot remove existing venv: $($_.Exception.Message)"
            Stop-Install -Code $script:ExitError -Message "close terminals/IDEs using $Venv (or delete it manually), then re-run with -Force."
        }
    }
    else {
        Add-Result -Step 'Virtual environment' -State 'OK' -Detail "reusing existing: $Venv"
    }
}

# --------------------------------------------------------------------------
# Step 2. Virtual environment
# --------------------------------------------------------------------------
Write-Section '2. Virtual environment'

$pythonInVenv = if ($IsWindows) { Join-Path $Venv 'Scripts/python.exe' } else { Join-Path $Venv 'bin/python' }
if (Test-Path -LiteralPath $pythonInVenv) {
    Add-Result -Step 'Create environment' -State 'OK' -Detail 'already exists'
}
else {
    $venvParent = Split-Path -Parent $Venv
    if ($venvParent -and -not (Test-Path -LiteralPath $venvParent)) {
        New-Item -ItemType Directory -Force -Path $venvParent | Out-Null
    }
    $mk = Invoke-Checked -File $Python -TimeoutSec ($script:TO.Probe * 3) -Arguments @('-m', 'venv', $Venv)
    if (-not $mk.Ok -or -not (Test-Path -LiteralPath $pythonInVenv)) {
        Add-Result -Step 'Create environment' -State 'FAIL' -Detail (Get-LastErrorLine $mk.StdErr 1)
        Stop-Install -Code $script:ExitError -Message "failed to create the virtual environment in $Venv"
    }
    Add-Result -Step 'Create environment' -State 'OK' -Detail $Venv
}
$venvPython = $pythonInVenv

# --------------------------------------------------------------------------
# Step 3. Dependencies
# --------------------------------------------------------------------------
Write-Section '3. Dependencies'

# PIP_USER=1 breaks installs into a venv ("Can not perform a '--user' install").
# It is a session variable and must not leak into the isolated environment.
$savedPipUser = $env:PIP_USER
$env:PIP_USER = '0'
try {
    $pipArgs = @('-m', 'pip', 'install', '--disable-pip-version-check', '--quiet')
    if ($DependenciesOnly) {
        $pipArgs += @('-r', (Join-Path $Source 'requirements.txt'))
    }
    else {
        $pipArgs += @('-e', $Source)
    }
    Write-Host '  (this may take several minutes: tree-sitter, ONNX Runtime and friends are large)' -ForegroundColor DarkGray
    $pip = Invoke-Checked -File $venvPython -Arguments $pipArgs -TimeoutSec $script:TO.Pip
    if (-not $pip.Ok) {
        $detail = if ($pip.TimedOut) { "TIMEOUT after $($script:TO.Pip)s (slow network? retry with -TimeoutScale 3)" }
                  else { Get-LastErrorLine $pip.StdErr 4 }
        Add-Result -Step 'Install dependencies' -State 'FAIL' -Detail $detail
        Stop-Install -Code $script:ExitError -Message 'pip exited with an error. Full output is in the transcript log.'
    }
    $mode = if ($DependenciesOnly) { 'dependencies only (requirements.txt)' } else { 'package editable (-e .)' }
    Add-Result -Step 'Install dependencies' -State 'OK' -Detail $mode

    # Editable installs create src/ragraf.egg-info inside the repository —
    # that changes the corpus composition; say it out loud, not as a surprise.
    if (-not $DependenciesOnly) {
        $eggInfo = Join-Path $Source 'src/ragraf.egg-info'
        if (Test-Path -LiteralPath $eggInfo) {
            Add-Result -Step 'Build metadata' -State 'WARN' `
                -Detail 'src/ragraf.egg-info/ created — gitignored, but it changes corpus composition'
        }
    }

    if ($WithTruststore) {
        $ts = Invoke-Checked -File $venvPython -TimeoutSec $script:TO.Pip `
            -Arguments @('-m', 'pip', 'install', '--disable-pip-version-check', '--quiet', 'truststore')
        if ($ts.Ok) {
            Add-Result -Step 'truststore (certificates)' -State 'OK' -Detail 'installed'
        }
        else {
            Add-Result -Step 'truststore (certificates)' -State 'WARN' -Detail "not installed: $(Get-LastErrorLine $ts.StdErr 1)"
        }
    }
    else {
        Add-Result -Step 'truststore (certificates)' -State 'SKIP' -Detail 'not requested (needed only under TLS inspection)'
    }
}
finally {
    if ($null -eq $savedPipUser) { Remove-Item Env:\PIP_USER -ErrorAction SilentlyContinue }
    else { $env:PIP_USER = $savedPipUser }
}

# --------------------------------------------------------------------------
# Step 4. Launch verification
# --------------------------------------------------------------------------
Write-Section '4. Launch verification'

$ragrafCmd = $null
$launcher = Join-Path $Source 'tools/ragraf.py'
if ($DependenciesOnly) {
    $run = Invoke-Checked -File $venvPython -TimeoutSec $script:TO.Probe -Arguments @($launcher, '--version')
    if (-not $run.Ok) {
        Add-Result -Step 'Run via tools/ragraf.py' -State 'FAIL' -Detail (Get-LastErrorLine $run.StdErr 1)
        Stop-Install -Code $script:ExitError -Message 'the tools/ragraf.py wrapper does not work in the new environment.'
    }
    Add-Result -Step 'Run via tools/ragraf.py' -State 'OK' -Detail $run.StdOut.Trim()
    Add-Result -Step 'Console command ragraf' -State 'WARN' -Detail 'not created (-DependenciesOnly): use tools/ragraf.py'
}
else {
    $ragrafExe = if ($IsWindows) { Join-Path $Venv 'Scripts/ragraf.exe' } else { Join-Path $Venv 'bin/ragraf' }
    if (-not (Test-Path -LiteralPath $ragrafExe)) {
        # Some Windows setups produce an extensionless script; POSIX has no .exe.
        $alt = Join-Path $Venv 'Scripts/ragraf'
        if (Test-Path -LiteralPath $alt) { $ragrafExe = $alt }
    }
    if (Test-Path -LiteralPath $ragrafExe) {
        $run = Invoke-Checked -File $ragrafExe -TimeoutSec $script:TO.Probe -Arguments @('--version')
    }
    else {
        $run = Invoke-Checked -File $venvPython -TimeoutSec $script:TO.Probe -Arguments @('-m', 'ragraf.cli', '--version')
    }
    if (-not $run.Ok) {
        Add-Result -Step 'Console command ragraf' -State 'FAIL' -Detail (Get-LastErrorLine $run.StdErr 1)
        Stop-Install -Code $script:ExitError -Message 'the installed package does not run. Full output is in the transcript log.'
    }
    Add-Result -Step 'Console command ragraf' -State 'OK' -Detail $run.StdOut.Trim()
    if (Test-Path -LiteralPath $ragrafExe) { $ragrafCmd = $ragrafExe }

    # The wrapper must work too: its filename matches the package name and it
    # used to shadow the installed package (fixed 2026-10-12). Keep verifying.
    $wrap = Invoke-Checked -File $venvPython -TimeoutSec $script:TO.Probe -Arguments @($launcher, '--version')
    if ($wrap.Ok) {
        Add-Result -Step 'Wrapper tools/ragraf.py' -State 'OK' -Detail $wrap.StdOut.Trim()
    }
    else {
        Add-Result -Step 'Wrapper tools/ragraf.py' -State 'WARN' `
            -Detail 'broken alongside the installed package — use the ragraf command'
    }
}

# Helper: invoke ragraf either via console command or via wrapper.
function Invoke-Ragraf {
    param([Parameter(Mandatory)][string[]]$Arguments, [int]$TimeoutSec = 120)
    if ($script:RagrafCmdValue) {
        return Invoke-Checked -File $script:RagrafCmdValue -Arguments $Arguments -TimeoutSec $TimeoutSec
    }
    return Invoke-Checked -File $script:VenvPythonValue -Arguments (@($script:LauncherValue) + $Arguments) -TimeoutSec $TimeoutSec
}
$script:RagrafCmdValue = $ragrafCmd
$script:VenvPythonValue = $venvPython
$script:LauncherValue = $launcher

# --------------------------------------------------------------------------
# Step 5. Project corpus (optional)
# --------------------------------------------------------------------------
Write-Section '5. Project corpus'

# Declared BEFORE branching: under Set-StrictMode touching an uninitialized
# variable is a terminating error, not $null.
$cfgPath = $null

if (-not $Project) {
    Add-Result -Step 'Corpus preparation' -State 'SKIP' -Detail 'skipped: no -Project given'
}
else {
    if (-not (Test-Path -LiteralPath $Project -PathType Container)) {
        Add-Result -Step 'Project directory' -State 'FAIL' -Detail "not found: $Project"
        Stop-Install -Code $script:ExitUsage -Message "project directory does not exist: $Project"
    }
    $Project = (Resolve-Path -LiteralPath $Project).Path
    Add-Result -Step 'Project directory' -State 'OK' -Detail $Project

    # `ensure` finds the corpus, rebuilds a stale index and creates a corpus for
    # an unknown directory. NOTE: ensure's own --json flag goes AFTER the
    # subcommand (unlike the global --json, which goes BEFORE it).
    $ens = Invoke-Ragraf -TimeoutSec $script:TO.Ensure -Arguments @('ensure', $Project, '--json')
    $status = $null
    $parsed = $null
    if ($ens.StdOut) {
        try {
            $parsed = $ens.StdOut | ConvertFrom-Json -AsHashtable
            $status = $parsed['status']
        }
        catch { $status = $null }
    }
    if (-not $status) {
        # Text fallback (bilingual for the RU→EN transition). JSON may be absent
        # on older product versions; installation must not break because of it.
        $raw = ($ens.StdOut + $ens.StdErr)
        if ($raw -match 'index is up to date|индекс актуален|corpus hash matches|сумма корпуса совпадает') { $status = 'actual' }
        elseif ($raw -match 'reindexed|отставал|пересобран|rebuilt') { $status = 'reindexed' }
        elseif ($raw -match 'corpus created|корпус создан|created and indexed|создан и проиндексирован') { $status = 'created' }
        elseif ($raw -match 'not a project|не является проектом|не похож на проект') { $status = 'not_a_project' }
        if (-not $status) {
            Add-Result -Step 'Corpus ensure' -State 'WARN' `
                -Detail "unrecognized status (exit $($ens.ExitCode)): $(Get-LastErrorLine ($ens.StdOut + $ens.StdErr) 1)"
        }
    }
    switch ($status) {
        'actual'        { Add-Result -Step 'Corpus ensure' -State 'OK' -Detail 'index is up to date' }
        'reindexed'     { Add-Result -Step 'Corpus ensure' -State 'OK' -Detail 'stale index rebuilt' }
        'created'       { Add-Result -Step 'Corpus ensure' -State 'OK' -Detail 'corpus created and indexed' }
        'not_a_project' {
            Add-Result -Step 'Corpus ensure' -State 'WARN' `
                -Detail 'directory does not look like a project (no .git, pyproject.toml, etc.) — corpus not created'
        }
    }
    if ($parsed -and $parsed['config']) { $cfgPath = [string]$parsed['config'] }

    if ($Index -and $status -in @('actual', 'reindexed', 'created')) {
        if (-not $cfgPath) {
            # Fallback: ensure did not return the config path (no JSON) — ask
            # the catalog. `catalog` has no --json yet (roadmap), so parse text;
            # the pattern is bilingual and tolerant to spaces in paths.
            $cat = Invoke-Ragraf -TimeoutSec $script:TO.Probe -Arguments @('catalog')
            foreach ($line in ($cat.StdOut -split "`r?`n")) {
                if ($line -match '(?:конфиг|config):\s*(.+\.ya?ml)\s*$') { $cfgPath = $Matches[1].Trim() }
            }
            if ($cfgPath) {
                Add-Result -Step 'Corpus config path' -State 'WARN' -Detail "recovered from catalog text output: $cfgPath"
            }
        }
        if (-not $cfgPath) {
            Add-Result -Step 'Indexing' -State 'WARN' -Detail 'could not determine the corpus config path'
        }
        else {
            # Global --json goes BEFORE the subcommand.
            $idxArgs = @('--json', '-p', $cfgPath, 'index')
            if ($NoVectors) { $idxArgs += '--no-vectors' }
            Write-Host '  (first index run may download the embedding model, ~240 MB)' -ForegroundColor DarkGray
            $idx = Invoke-Ragraf -TimeoutSec $script:TO.Index -Arguments $idxArgs
            if ($idx.Ok) {
                $idxData = $null
                try { $idxData = $idx.StdOut | ConvertFrom-Json -AsHashtable }
                catch { Write-Verbose "index JSON not parsable: $($_.Exception.Message)" }
                if ($idxData) {
                    Add-Result -Step 'Indexing' -State 'OK' `
                        -Detail ("rev {0}, files {1}, {2}s" -f $idxData['rev_id'], $idxData['files'], $idxData['seconds'])
                }
                else {
                    Add-Result -Step 'Indexing' -State 'OK' -Detail (Get-LastErrorLine $idx.StdOut 1)
                }
            }
            else {
                $detail = if ($idx.TimedOut) { "TIMEOUT after $($script:TO.Index)s (retry with -TimeoutScale)" }
                          else { Get-LastErrorLine $idx.StdErr 2 }
                # FAIL without Stop-Install: installation itself succeeded; the
                # summary and the final exit code reflect the failure.
                Add-Result -Step 'Indexing' -State 'FAIL' -Detail $detail
            }
        }
    }
    elseif ($Index) {
        Add-Result -Step 'Indexing' -State 'WARN' -Detail 'skipped: corpus not prepared'
    }
    else {
        Add-Result -Step 'Indexing' -State 'SKIP' -Detail 'not requested (add -Index when ready for the model download)'
    }
}

# --------------------------------------------------------------------------
# Step 6. Component verification (selftest --json)
# --------------------------------------------------------------------------
Write-Section '6. Component check (selftest)'

if (-not $Project -or -not $cfgPath) {
    Add-Result -Step 'selftest' -State 'SKIP' -Detail 'skipped: needs a prepared corpus (-Project)'
}
else {
    # The previous revision regex-matched localized selftest TEXT even though
    # `selftest --json` exists (cli.py). JSON is the contract; wording is not.
    # NOTE: selftest always exits 0 — it is a report, not a gate. Parse checks[].
    $st = Invoke-Ragraf -TimeoutSec ($script:TO.Probe * 3) -Arguments @('--json', '-p', $cfgPath, 'selftest')
    $stData = $null
    if ($st.StdOut) {
        try { $stData = $st.StdOut | ConvertFrom-Json -AsHashtable }
        catch { Write-Verbose "selftest JSON not parsable: $($_.Exception.Message)" }
    }
    $required = @('python', 'tree_sitter', 'php_grammar', 'sqlite_vec', 'fastembed',
                  'tiktoken', 'fts5', 'vec0_loadable', 'embedding_model')
    $optional = @('joern', 'java')
    if ($stData -and $stData['checks']) {
        $byName = @{}
        foreach ($c in $stData['checks']) { $byName[[string]$c['check']] = $c }
        $missing = [System.Collections.Generic.List[string]]::new()
        foreach ($name in $required) {
            if (-not $byName.ContainsKey($name)) { $missing.Add("$name(no report)"); continue }
            if (-not $byName[$name]['ok']) { $missing.Add($name) }
        }
        if ($missing.Count -eq 0) {
            Add-Result -Step 'selftest: required components' -State 'OK' -Detail 'all nine present'
        }
        else {
            Add-Result -Step 'selftest: required components' -State 'WARN' `
                -Detail ('missing or not ready: ' + ($missing -join ', '))
        }
        foreach ($name in $optional) {
            if ($byName.ContainsKey($name) -and -not $byName[$name]['ok']) {
                Add-Result -Step "Optional layer ($name)" -State 'WARN' `
                    -Detail 'not available: capability=degraded, `slice` will refuse. Install: scripts/install-joern.ps1'
            }
        }
    }
    else {
        # JSON unavailable (older product build): degrade honestly, do not guess.
        Add-Result -Step 'selftest: required components' -State 'WARN' `
            -Detail 'selftest --json not parsable; run `ragraf -p <cfg> selftest` manually and read the report'
    }
}

# --------------------------------------------------------------------------
# Step 7. What's next
# --------------------------------------------------------------------------
Write-Section "What's next"
$ragrafCall = if ($ragrafCmd) { $ragrafCmd } else { "python `"$launcher`"" }
$activate = if ($IsWindows) { Join-Path $Venv 'Scripts/Activate.ps1' } else { Join-Path $Venv 'bin/activate' }
Write-Host '  Activate the environment:'
Write-Host "    $activate" -ForegroundColor DarkGray
Write-Host '  Prepare a corpus and get the first answer:'
Write-Host "    $ragrafCall ensure <project directory>" -ForegroundColor DarkGray
Write-Host "    $ragrafCall -p <config> query search `"what to look for`"" -ForegroundColor DarkGray
Write-Host '  Attach to an agent as an MCP server (stdio):'
Write-Host "    $venvPython `"$(Join-Path $Source 'tools/ragraf-mcp.py')`" --project <config> --agent <agent name>" -ForegroundColor DarkGray
Write-Host '  Admin panel (127.0.0.1:8777):'
Write-Host "    $venvPython `"$(Join-Path $Source 'tools/ragraf-admin.py')`" --no-browser" -ForegroundColor DarkGray
Write-Host '  Without Java/Joern the product works, but `slice` is unavailable.'
Write-Host "    optional external layer: pwsh -File `"$(Join-Path $Source 'scripts/install-joern.ps1')`"" -ForegroundColor DarkGray

Show-Summary
Write-SummaryJson
Close-InstallTranscript

$failedFinal = @($script:Results | Where-Object State -eq 'FAIL').Count
if ($failedFinal -gt 0) { exit $script:ExitError }
exit $script:ExitOk
