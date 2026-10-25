#Requires -Version 7.6
#Requires -PSEdition Core
<#
.SYNOPSIS
    Установка RAGRAF из офлайн-набора: без сети, с проверкой целостности.

.DESCRIPTION
    Набор собирает `tools/release/airgap.py`. Установка состоит из четырёх шагов,
    и каждый проверяем:

      1. проверка целостности: каждый файл набора сверяется с `checksums.sha256`;
         расхождение — остановка (код 1), а не «поставим как есть»;
      2. дерево поставки копируется в `<Prefix>/tree` (существующее не
         перезаписывается: старая установка не должна исчезнуть молча);
      3. колёса ставятся с `--no-index --find-links`: pip НЕ имеет права выйти в
         сеть, поэтому отсутствующее колесо — ошибка, а не тихая докачка;
      4. модель эмбеддингов и Joern копируются, если они есть в наборе.

    Что НЕ обещается: если в наборе нет модели эмбеддингов, векторный поиск
    работать не будет — скрипт говорит это прямо, а не делает вид, что всё
    установлено. Наличие Joern влияет на `capability` (граф свойств кода).

.PARAMETER Set
    Каталог набора (там лежит `airgap.json`).

.PARAMETER Prefix
    Куда ставить. По умолчанию `<домашний каталог>/ragraf-offline`.

.PARAMETER SelfTest
    После установки прогнать цикл «создать корпус → проиндексировать → найти»
    на временном каталоге с двумя файлами и напечатать итог.

.EXAMPLE
    pwsh -NoLogo -NoProfile -NonInteractive -File scripts/install-airgap.ps1 -Set D:\airgap -SelfTest
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Set,
    [string]$Prefix = '',
    [switch]$SelfTest,
    [switch]$SkipVerify
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Step([string]$Text) { Write-Host "== $Text" }
function Fail([string]$Text, [int]$Code = 1) {
    Write-Host "ОТКАЗ: $Text" -ForegroundColor Red
    exit $Code
}

$setDir = [System.IO.Path]::GetFullPath($Set)
if (-not (Test-Path -LiteralPath $setDir -PathType Container)) {
    Fail "нет каталога набора: $setDir" 2
}
$manifestPath = Join-Path $setDir 'airgap.json'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    Fail "в наборе нет airgap.json: $setDir" 2
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable

if (-not $Prefix) {
    $Prefix = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'ragraf-offline'
}
$prefixDir = [System.IO.Path]::GetFullPath($Prefix)
Write-Step "набор:   $setDir"
Write-Step "приёмник: $prefixDir"
Write-Host "  продукт: $($manifest.product.name) $($manifest.product.version)"
Write-Host "  состав полон:  $($manifest.complete)"
if ($manifest.ContainsKey('full_capability')) {
    Write-Host "  полные возможности: $($manifest.full_capability)"
}
if ($manifest.ContainsKey('degraded')) {
    foreach ($line in $manifest.degraded) { Write-Host "  ! $line" -ForegroundColor Yellow }
}
foreach ($note in $manifest.notes) { Write-Host "  · $note" }

# --- 1. целостность ---------------------------------------------------------
if (-not $SkipVerify) {
    Write-Step 'проверка целостности набора'
    $checksums = Join-Path $setDir 'checksums.sha256'
    if (-not (Test-Path -LiteralPath $checksums -PathType Leaf)) {
        Fail "в наборе нет checksums.sha256: проверить нечего" 2
    }
    $checked = 0
    $bad = @()
    foreach ($line in Get-Content -LiteralPath $checksums -Encoding utf8) {
        if (-not $line.Trim()) { continue }
        $parts = $line -split '  ', 2
        if ($parts.Count -ne 2) { $bad += "строка без хэша: $line"; continue }
        $expected = $parts[0]
        $relative = $parts[1]
        $path = Join-Path $setDir $relative
        $checked++
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            $bad += "нет файла: $relative"
            continue
        }
        $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected.ToLowerInvariant()) {
            $bad += "хэш не совпал: $relative"
        }
    }
    Write-Host "  проверено объектов: $checked"
    if ($bad.Count -gt 0) {
        $bad | ForEach-Object { Write-Host "    · $_" -ForegroundColor Red }
        Fail "набор повреждён: установка не выполняется" 1
    }
}

# --- 2. дерево поставки -----------------------------------------------------
$treeSource = Join-Path $setDir 'tree'
$treeTarget = Join-Path $prefixDir 'tree'
if (-not (Test-Path -LiteralPath $treeSource -PathType Container)) {
    Fail "в наборе нет дерева поставки (tree/)" 2
}
Write-Step 'раскладка дерева поставки'
if (Test-Path -LiteralPath $treeTarget) {
    Fail "приёмник уже существует: $treeTarget. Старая установка не перезаписывается — укажите другой -Prefix" 2
}
New-Item -ItemType Directory -Force -Path $prefixDir | Out-Null
Copy-Item -LiteralPath $treeSource -Destination $treeTarget -Recurse
Write-Host "  дерево: $treeTarget"

# --- 3. колёса без сети -----------------------------------------------------
$wheels = Join-Path $setDir 'wheels'
$lib = Join-Path $prefixDir 'lib'
if (-not (Test-Path -LiteralPath $wheels -PathType Container)) {
    Fail "в наборе нет колёс (wheels/)" 2
}
Write-Step 'установка колёс с --no-index (сеть недоступна по замыслу)'
$python = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $python) { Fail 'python не найден в PATH' 2 }
New-Item -ItemType Directory -Force -Path $lib | Out-Null
# PIP_USER задан в окружении этой машины и несовместим с `--target`: pip отвечает
# «Can not combine '--user' and '--target'» (замерено 24.10.2026). Установка идёт
# в приёмник, а не в профиль пользователя, поэтому переменная снимается.
$env:PIP_USER = '0'
& $python.Source -m pip install --no-index --find-links $wheels --target $lib --no-warn-script-location ragraf 2>&1 |
    ForEach-Object { Write-Host "  $_" }
if ($LASTEXITCODE -ne 0) { Fail "pip не смог поставить ragraf из набора (код $LASTEXITCODE)" 1 }

# --- 4. модель и Joern ------------------------------------------------------
$modelSource = Join-Path $setDir 'model'
if (Test-Path -LiteralPath $modelSource -PathType Container) {
    Write-Step 'модель эмбеддингов'
    $modelTarget = Join-Path $prefixDir 'model'
    Copy-Item -LiteralPath $modelSource -Destination $modelTarget -Recurse
    Write-Host "  модель: $modelTarget"
    # Переменная ставится в этом процессе: иначе проверка ниже пошла бы в сеть за
    # моделью, а офлайн-набор именно тем и проверяется, что сеть не нужна.
    $env:RAGRAF_MODEL_CACHE = $modelTarget
    Write-Host "  RAGRAF_MODEL_CACHE=$modelTarget (выставлено для этого процесса)"
} else {
    Write-Host '  модель эмбеддингов в наборе ОТСУТСТВУЕТ: векторный поиск будет недоступен, лексический останется' -ForegroundColor Yellow
}

$joernSource = Join-Path $setDir 'tools\joern'
if (Test-Path -LiteralPath $joernSource -PathType Container) {
    Write-Step 'инструменты Joern'
    $toolsTarget = Join-Path $prefixDir 'tools'
    New-Item -ItemType Directory -Force -Path $toolsTarget | Out-Null
    Copy-Item -LiteralPath $joernSource -Destination (Join-Path $toolsTarget 'joern') -Recurse
    Write-Host "  Joern: $(Join-Path $toolsTarget 'joern')"
} else {
    Write-Host '  Joern в наборе ОТСУТСТВУЕТ: граф свойств кода не будет строиться, capability будет ниже full' -ForegroundColor Yellow
}

$env:PYTHONPATH = "$lib;$treeTarget;$treeTarget\src"
$cli = Join-Path $treeTarget 'tools\ragraf.py'
if (-not (Test-Path -LiteralPath $cli -PathType Leaf)) {
    Fail "в дереве поставки нет tools/ragraf.py: $cli" 1
}

Write-Step 'проверка, что продукт запускается из установленного дерева'
& $python.Source $cli --version 2>&1 | ForEach-Object { Write-Host "  $_" }
if ($LASTEXITCODE -ne 0) { Fail "установленный продукт не запускается (код $LASTEXITCODE)" 1 }

# --- 5. цикл «создать → проиндексировать → найти» ---------------------------
if ($SelfTest) {
    Write-Step 'самопроверка: создать корпус → проиндексировать → найти'
    $sandbox = Join-Path ([System.IO.Path]::GetTempPath()) ("ragraf-airgap-selftest-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Force -Path $sandbox | Out-Null
    # init требует хотя бы 3 файла с кодом и отказывает на меньшем корпусе —
    # проверено вызовом 24.10.2026 («найдено файлов с кодом: 1 — слишком мало»).
    Set-Content -LiteralPath (Join-Path $sandbox 'alpha.py') -Encoding utf8NoBOM -Value @'
def unique_marker_function(value):
    """Функция с узнаваемым именем: по нему проверяется поиск."""
    return value * 2
'@
    Set-Content -LiteralPath (Join-Path $sandbox 'beta.py') -Encoding utf8NoBOM -Value @'
def helper_two():
    return 2
'@
    Set-Content -LiteralPath (Join-Path $sandbox 'gamma.py') -Encoding utf8NoBOM -Value @'
def helper_three():
    return 3
'@
    Set-Content -LiteralPath (Join-Path $sandbox 'notes.md') -Encoding utf8NoBOM -Value @'
# Песочница офлайн-проверки

Здесь лежит описание, по которому ищется слово sandbox_marker_word.
'@
    $initOut = & $python.Source $cli init $sandbox 2>&1
    $initText = ($initOut | Out-String)
    $initText.Trim().Split("`n") | Select-Object -First 4 | ForEach-Object { Write-Host "  $_" }
    $made = [regex]::Match($initText, 'конфигурация создана:\s*(\S+\.yaml)')
    if (-not $made.Success) { Fail "init не создал конфигурацию корпуса: $initText" 1 }
    $config = $made.Groups[1].Value
    & $python.Source $cli -p $config index 2>&1 | Select-Object -Last 6 |
        ForEach-Object { Write-Host "  $_" }
    if ($LASTEXITCODE -ne 0) { Fail "индексация в песочнице не прошла (код $LASTEXITCODE)" 1 }
    $found = & $python.Source $cli -p $config query search unique_marker_function 2>&1
    $foundText = ($found | Out-String)
    Write-Host '  результат поиска:' -NoNewline
    Write-Host ''
    $foundText.Trim().Split("`n") | Select-Object -First 6 | ForEach-Object { Write-Host "    $_" }
    if ($foundText -notmatch 'alpha\.py') {
        Fail 'поиск не нашёл проиндексированный файл: цикл не замкнут' 1
    }
    Write-Host '  цикл замкнут: корпус создан, проиндексирован, файл найден'
    Write-Host "  каталог проверки оставлен: $sandbox"
}

Write-Step 'ГОТОВО'
Write-Host "  дерево:  $treeTarget"
Write-Host "  модули:  $lib"
Write-Host "  запуск:  `$env:PYTHONPATH='$lib;$treeTarget;$treeTarget\src'; python $cli -p <конфиг> status"
exit 0
