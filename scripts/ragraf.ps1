#Requires -Version 7.6
#Requires -PSEdition Core
<#
.SYNOPSIS
    Обёртка запуска RAGRAF (PowerShell 7.6+).

.DESCRIPTION
    Передаёт аргументы в python -m ragraf.cli, предварительно добавляя src/ в PYTHONPATH.
    Профиль не загружается, кодировки принудительно UTF-8 без BOM.

    Правила проекта: только pwsh 7.6+, без powershell.exe, без WinPS-compat.

.EXAMPLE
    pwsh -NoLogo -NoProfile -NonInteractive -File .\scripts\ragraf.ps1 --project config/myproject.yaml selftest

.EXAMPLE
    .\scripts\ragraf.ps1 --project config/myproject.yaml query repo_map --budget 1500
#>
[CmdletBinding()]
param(
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
    throw "Не найден каталог исходников: $srcPath"
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    throw 'Python не найден в PATH. Требуется Python 3.13 (см. requirements.txt).'
}

$previousPath = $env:PYTHONPATH
$env:PYTHONPATH = if ($previousPath) { "$srcPath;$previousPath" } else { $srcPath }
$env:PYTHONIOENCODING = 'utf-8'

try {
    $pythonArgs = @('-m', 'ragraf.cli') + $Arguments
    & $python.Source @pythonArgs
    exit $LASTEXITCODE
}
finally {
    if ($null -eq $previousPath) {
        Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue
    }
    else {
        $env:PYTHONPATH = $previousPath
    }
}
