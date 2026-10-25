#Requires -Version 7.6
#Requires -PSEdition Core
<#
.SYNOPSIS
    Заменить рабочее дерево клона публикации собранным деревом поставки.

.DESCRIPTION
    Сборщик отказывается писать внутрь живого дерева (код 2), поэтому снимок
    собирается вне дерева, а затем его содержимое заменяет рабочее дерево клона
    `.staging/release-v0.2/RAGRAF`. Каталог `.git` клона сохраняется: история
    публикации живёт в нём.

    Операция НИЧЕГО не удаляет: прежнее дерево целиком переносится рядом как
    `<Target>.bak-<метка>`, затем создаётся новое и в него возвращается `.git`.
    Поэтому откат — это обратный перенос, а не восстановление из ниоткуда.

    Порядок (нарушение любого шага = стоп):
      1. проверить источник: есть checksums.sha256 и он не пуст;
      2. проверить приёмник: это каталог с .git и именем RAGRAF;
      3. перенести приёмник в <Target>.bak-<метка>;
      4. создать приёмник, вернуть в него .git, скопировать источник;
      5. сверить число файлов и sha256 каждого файла с checksums.sha256.

.PARAMETER Source
    Каталог собранного дерева (результат build_release.py). Обязателен.

.PARAMETER Target
    Рабочее дерево клона. Обязателен.

.PARAMETER Stamp
    Метка резервной копии. По умолчанию — текущее время.

.PARAMETER DryRun
    Ничего не менять: только проверить условия и напечатать план.

.EXAMPLE
    pwsh -NoLogo -NoProfile -NonInteractive -File tools/release/replace_staging_tree.ps1 `
        -Source $env:TEMP\ragraf-release-v0.2 -Target .staging\release-v0.2\RAGRAF -DryRun
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Source,
    [Parameter(Mandatory)][string]$Target,
    [string]$Stamp = (Get-Date -Format 'yyyyMMdd-HHmmss'),
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Stop-With([string]$Message, [int]$Code) {
    Write-Host "STOP: $Message" -ForegroundColor Red
    exit $Code
}

$sourcePath = [IO.Path]::GetFullPath($Source)
$targetPath = [IO.Path]::GetFullPath($Target)
$backupPath = "$targetPath.bak-$Stamp"

# --- 1. Источник -----------------------------------------------------------
if (-not (Test-Path -LiteralPath $sourcePath -PathType Container)) {
    Stop-With "нет каталога сборки: $sourcePath" 2
}
$checksums = Join-Path $sourcePath 'checksums.sha256'
if (-not (Test-Path -LiteralPath $checksums -PathType Leaf)) {
    Stop-With "в сборке нет checksums.sha256 — это не результат конвейера: $sourcePath" 2
}
$expected = @{}
foreach ($line in Get-Content -LiteralPath $checksums -Encoding utf8) {
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    $parts = $line -split '  ', 2
    if ($parts.Count -ne 2) { Stop-With "строка checksums.sha256 не разбирается: $line" 2 }
    $expected[$parts[1].Trim()] = $parts[0].Trim()
}
if ($expected.Count -eq 0) { Stop-With 'checksums.sha256 пуст — сверять нечего' 2 }

# --- 2. Приёмник -----------------------------------------------------------
if (-not (Test-Path -LiteralPath $targetPath -PathType Container)) {
    Stop-With "нет рабочего дерева клона: $targetPath" 2
}
if ((Split-Path -Leaf $targetPath) -ne 'RAGRAF') {
    Stop-With "приёмник должен называться RAGRAF, получено «$(Split-Path -Leaf $targetPath)» — вероятно, указан не тот каталог" 2
}
if (-not (Test-Path -LiteralPath (Join-Path $targetPath '.git') -PathType Container)) {
    Stop-With "в приёмнике нет .git — это не клон публикации: $targetPath" 2
}
if (Test-Path -LiteralPath $backupPath) {
    Stop-With "резервная копия уже существует: $backupPath (возьмите другую метку)" 2
}

$oldFiles = @(Get-ChildItem -LiteralPath $targetPath -Recurse -File -Force |
    Where-Object { $_.FullName -notlike "$targetPath\.git\*" })
Write-Host "источник: $sourcePath ($($expected.Count) файлов по checksums)"
Write-Host "приёмник: $targetPath ($($oldFiles.Count) файлов сейчас, .git сохраняется)"
Write-Host "рядом останется: $backupPath"

if ($DryRun) {
    Write-Host 'DRY RUN: изменений нет.' -ForegroundColor Yellow
    exit 0
}

# --- 3. Прежнее дерево — в резервную копию ---------------------------------
Move-Item -LiteralPath $targetPath -Destination $backupPath
if (-not (Test-Path -LiteralPath (Join-Path $backupPath '.git') -PathType Container)) {
    Stop-With "резервная копия без .git — остановка до копирования: $backupPath" 3
}

# --- 4. Новое дерево + сохранённый .git ------------------------------------
New-Item -ItemType Directory -Path $targetPath | Out-Null
Move-Item -LiteralPath (Join-Path $backupPath '.git') -Destination (Join-Path $targetPath '.git')
Get-ChildItem -LiteralPath $sourcePath -Force | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $targetPath -Recurse -Force
}

# --- 5. Сверка -------------------------------------------------------------
$missing = @()
$wrong = @()
foreach ($relative in $expected.Keys) {
    $file = Join-Path $targetPath $relative
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { $missing += $relative; continue }
    $actual = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected[$relative]) { $wrong += $relative }
}
$extra = @(Get-ChildItem -LiteralPath $targetPath -Recurse -File -Force |
    Where-Object { $_.FullName -notlike "$targetPath\.git\*" } |
    ForEach-Object { [IO.Path]::GetRelativePath($targetPath, $_.FullName).Replace('\', '/') } |
    Where-Object { -not $expected.ContainsKey($_) -and $_ -ne 'checksums.sha256' })

Write-Host "файлов в приёмнике: $(@(Get-ChildItem -LiteralPath $targetPath -Recurse -File -Force | Where-Object { $_.FullName -notlike "$targetPath\.git\*" }).Count)"
Write-Host "нет в приёмнике: $($missing.Count); хэш не совпал: $($wrong.Count); лишних: $($extra.Count)"
if ($missing.Count -gt 0) { $missing | Select-Object -First 10 | ForEach-Object { Write-Host "  нет: $_" } }
if ($wrong.Count -gt 0) { $wrong | Select-Object -First 10 | ForEach-Object { Write-Host "  другой хэш: $_" } }
if ($extra.Count -gt 0) { $extra | Select-Object -First 10 | ForEach-Object { Write-Host "  лишний: $_" } }
if ($missing.Count -gt 0 -or $wrong.Count -gt 0 -or $extra.Count -gt 0) {
    Write-Host "прежнее дерево осталось здесь: $backupPath" -ForegroundColor Yellow
    exit 1
}
if (-not (Test-Path -LiteralPath (Join-Path $targetPath '.git') -PathType Container)) {
    Write-Host 'STOP: .git пропал при замене' -ForegroundColor Red
    exit 3
}
Write-Host 'замена завершена, сверка сошлась' -ForegroundColor Green
exit 0
