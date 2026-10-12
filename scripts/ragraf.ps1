#Requires -Version 7.6
#Requires -PSEdition Core
<#
.SYNOPSIS
    RAGRAF launcher wrapper (PowerShell 7.6+).

.DESCRIPTION
    Forwards arguments to `python -m ragraf.cli`, adding the repository's
    src/ to PYTHONPATH so the wrapper works without installing the package.
    The user profile is not loaded; encodings are forced to UTF-8 (no BOM).

    Interpreter resolution order (improved in the 2026-10-12 refactor):
      1. -Python parameter;
      2. the installation venv (~/.ragraf/venv) if present — the wrapper then
         runs against exactly the environment install.ps1 verified;
      3. a local .venv next to the repository;
      4. `python` / `python3` from PATH.
    The Microsoft Store App Execution Alias is detected and rejected with an
    actionable message, and the interpreter VERSION is checked (>= 3.13):
    the old wrapper checked presence only and produced import tracebacks.

    Project rules: pwsh 7.6+ only, no powershell.exe, no WinPS-compat.

.PARAMETER Python
    Explicit interpreter path. Overrides all discovery.

.EXAMPLE
    pwsh -NoLogo -NoProfile -NonInteractive -File .\scripts\ragraf.ps1 --project config/myproject.yaml selftest

.EXAMPLE
    .\scripts\ragraf.ps1 --project config/myproject.yaml query repo_map --budget 1500
#>
[CmdletBinding()]
param(
    [string]$Python,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$OutputEncoding = [Console]::InputEncoding = [Console]::OutputEncoding =
    [System.Text.UTF8Encoding]::new($false)

$repoRoot = Split-Path -Parent $PSScriptRoot
$srcPath = Join-Path $repoRoot 'src'

if (-not (Test-Path -LiteralPath $srcPath -PathType Container)) {
    throw "Source directory not found: $srcPath"
}

function Resolve-Interpreter {
    param([string]$Explicit)
    if ($Explicit) { return $Explicit }

    # 1) venv created by install.ps1 — verified environment, best default.
    $installVenv = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.ragraf/venv'
    # 2) local .venv of the repository checkout.
    $localVenv = Join-Path $repoRoot '.venv'
    foreach ($venv in @($installVenv, $localVenv)) {
        $py = if ($IsWindows) { Join-Path $venv 'Scripts/python.exe' } else { Join-Path $venv 'bin/python' }
        if (Test-Path -LiteralPath $py) { return $py }
    }
    # 3) PATH.
    foreach ($name in @('python', 'python3')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            if ($IsWindows -and $cmd.Source -match '\\WindowsApps\\') { continue }  # Store stub — skip
            return $cmd.Source
        }
    }
    # 4) nothing good: report the stub explicitly if that is all there is.
    $stub = Get-Command python -ErrorAction SilentlyContinue
    if ($stub -and $stub.Source -match '\\WindowsApps\\') {
        throw ("'python' resolves to the Microsoft Store App Execution Alias ($($stub.Source)). " +
               'Install Python 3.13+ from python.org or disable the alias ' +
               '(Settings > Apps > Advanced app settings > App execution aliases), ' +
               'or pass -Python <path>.')
    }
    throw 'Python not found in PATH. Python 3.13+ is required (see pyproject.toml requires-python).'
}

$pythonExe = Resolve-Interpreter -Explicit $Python

# Version gate (the old wrapper only checked presence and claimed "3.13 required").
$verOut = & $pythonExe -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>$null
if ($LASTEXITCODE -ne 0 -or -not $verOut) {
    throw "Interpreter does not run: $pythonExe"
}
$majorMinor = [version]($verOut.Trim())
if ($majorMinor -lt [version]'3.13') {
    throw "Python 3.13+ required; $($verOut.Trim()) found at $pythonExe. Pass -Python <path> to override."
}

# POSIX-correct separator (the old wrapper hardcoded ';' — Windows-only).
$previousPath = $env:PYTHONPATH
$sep = [IO.Path]::PathSeparator
$env:PYTHONPATH = if ($previousPath) { "$srcPath$sep$previousPath" } else { $srcPath }
$previousIoEncoding = $env:PYTHONIOENCODING
$env:PYTHONIOENCODING = 'utf-8'

try {
    $pythonArgs = @('-m', 'ragraf.cli') + $Arguments
    & $pythonExe @pythonArgs
    exit $LASTEXITCODE
}
finally {
    if ($null -eq $previousPath) { Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue }
    else { $env:PYTHONPATH = $previousPath }
    if ($null -eq $previousIoEncoding) { Remove-Item Env:\PYTHONIOENCODING -ErrorAction SilentlyContinue }
    else { $env:PYTHONIOENCODING = $previousIoEncoding }
}
