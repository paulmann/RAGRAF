#Requires -Version 7.6
#Requires -PSEdition Core
<#
.SYNOPSIS
    Самопроверка установщика: четыре заведомые порчи, на каждой установщик обязан
    отказать и назвать причину.

.DESIGN
    Гейт, который никогда не падал, не стоит ничего (то же правило, что у
    tools/release/gate_release.py: пять порч, каждая обязана быть поймана).
    Здесь портится копия поставки или аргументы, и проверяется КОД возврата
    и названная причина — а не текст сообщения целиком.

    Порчи:
      1. в копии поставки нет src/ragraf/__init__.py   → отказ «не дерево RAGRAF»
      2. версия в pyproject.toml ≠ __version__          → отказ по RAGRAF_EXPECTED_VERSION
      3. нет интерпретатора с нужной версией            → код 3
      4. каталог установки внутри индексируемого проекта → код 2

    Песочница создаётся в каталоге вне индексируемых корней и убирается по списку
    созданного (инвариант: уборка только по списку, иначе стираются чужие файлы).

.EXAMPLE
    pwsh -NoLogo -NoProfile -File tools\installer\selftest.py-Equivalent.ps1
    pwsh -NoLogo -NoProfile -File tools\installer\selftest.ps1
#>
[CmdletBinding()]
param(
    # Корень дерева продукта (по умолчанию — два уровня выше tools\installer).
    [string]$Source = '',
    # Каталог для песочницы; по умолчанию — <TEMP>\ragraf-installer-selftest.
    [string]$Sandbox = '',
    # Оставить песочницу на диске для разбора.
    [switch]$Keep
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::InputEncoding = [Console]::OutputEncoding =
    [System.Text.UTF8Encoding]::new($false)

if (-not $Source) { $Source = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path }
if (-not $Sandbox) { $Sandbox = Join-Path ([IO.Path]::GetTempPath()) 'ragraf-installer-selftest' }
$setup = Join-Path $Source 'scripts\ragraf-setup.ps1'
if (-not (Test-Path -LiteralPath $setup)) {
    Write-Host "ОТКАЗ: нет $setup"
    exit 2
}

$created = [System.Collections.Generic.List[string]]::new()
$passed = 0
$failed = 0

function New-Sandbox {
    param([string]$Name)
    $path = Join-Path $Sandbox $Name
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
    New-Item -ItemType Directory -Path $path -Force | Out-Null
    $script:created.Add($path)
    return $path
}

function Copy-Tree {
    param([string]$From, [string]$To)
    New-Item -ItemType Directory -Path $To -Force | Out-Null
    foreach ($item in Get-ChildItem -LiteralPath $From -Force) {
        if ($item.Name -in @('.git', '.ragraf', '.work', '.zwork', '.venv', '.staging', '.tools', 'node_modules')) { continue }
        Copy-Item -LiteralPath $item.FullName -Destination $To -Recurse -Force
    }
}

function Invoke-Setup {
    param([string[]]$Arguments)
    $out = & pwsh -NoLogo -NoProfile -NonInteractive -File $setup @Arguments 2>&1 | Out-String
    return @{ Code = $LASTEXITCODE; Output = $out }
}

function Assert-Failure {
    param([string]$Name, [hashtable]$Result, [int[]]$ExpectedCodes, [string]$ReasonLike)
    $codeOk = $ExpectedCodes -contains $Result.Code
    $reasonOk = $Result.Output -match [regex]::Escape($ReasonLike)
    if ($codeOk -and $reasonOk) {
        Write-Host ("  [ ok ] {0,-46} код {1}, причина названа" -f $Name, $Result.Code)
        $script:passed++
    } else {
        Write-Host ("  [FAIL] {0,-46} код {1} (ждали {2}), причина '{3}': {4}" -f `
            $Name, $Result.Code, ($ExpectedCodes -join '/'), $ReasonLike, $(if ($reasonOk) { 'есть' } else { 'НЕ названа' }))
        $script:failed++
        ($Result.Output -split "`n" | Select-Object -Last 6) | ForEach-Object { Write-Host "         $_" }
    }
}

Write-Host '=== Самопроверка установщика: порчи ==='
New-Item -ItemType Directory -Path $Sandbox -Force | Out-Null

# --- порча 1: нет пакета ---------------------------------------------------
$tree1 = New-Sandbox 'port1-no-package'
Copy-Tree -From $Source -To $tree1
Remove-Item -LiteralPath (Join-Path $tree1 'src') -Recurse -Force
$result = Invoke-Setup -Arguments @('-DryRun', '-Prefix', (Join-Path $Sandbox 'target1'), '-Source', $tree1)
Assert-Failure -Name '1. нет src/ragraf/__init__.py' -Result $result -ExpectedCodes @(1) `
    -ReasonLike 'это не дерево RAGRAF'

# --- порча 2: версия расходится с заявленной --------------------------------
$tree2 = New-Sandbox 'port2-version-mismatch'
Copy-Tree -From $Source -To $tree2
$env:RAGRAF_EXPECTED_VERSION = '9.9.9'
$result = Invoke-Setup -Arguments @('-DryRun', '-Prefix', (Join-Path $Sandbox 'target2'), '-Source', $tree2)
Remove-Item Env:\RAGRAF_EXPECTED_VERSION
Assert-Failure -Name '2. версия дерева ≠ заявленной' -Result $result -ExpectedCodes @(1) `
    -ReasonLike 'не совпадает с заявленной 9.9.9'

# --- порча 3: нет интерпретатора нужной версии ------------------------------
$result = Invoke-Setup -Arguments @('-DryRun', '-Prefix', (Join-Path $Sandbox 'target3'),
    '-PythonPath', (Join-Path $Sandbox 'no-such-python\python.exe'))
Assert-Failure -Name '3. нет Python нужной версии' -Result $result -ExpectedCodes @(3) `
    -ReasonLike 'не найден или не подходит'

# --- порча 4: каталог внутри индексируемого проекта -------------------------
$project = New-Sandbox 'port4-project'
Copy-Tree -From $Source -To $project
New-Item -ItemType Directory -Path (Join-Path $project '.ragraf') -Force | Out-Null
Set-Content -LiteralPath (Join-Path $project 'main.py') -Value "def x():`n    return 1`n" -Encoding utf8NoBOM
$result = Invoke-Setup -Arguments @('-DryRun', '-Prefix', (Join-Path $project 'installed'), '-Source', $project)
Assert-Failure -Name '4. каталог внутри проекта' -Result $result -ExpectedCodes @(2) `
    -ReasonLike 'внутри индексируемого проекта'

# --- контрольный прогон: на целом дереве установщик не отказывает ----------
$result = Invoke-Setup -Arguments @('-DryRun', '-Prefix', (Join-Path $Sandbox 'target-ok'), '-Source', $Source)
if ($result.Code -eq 0) {
    Write-Host ("  [ ok ] {0,-46} код 0 (контроль)" -f '5. целое дерево не отвергается')
    $script:passed++
} else {
    Write-Host ("  [FAIL] {0,-46} код {1}" -f '5. целое дерево не отвергается', $result.Code)
    $script:failed++
    ($result.Output -split "`n" | Select-Object -Last 6) | ForEach-Object { Write-Host "         $_" }
}

# --- проверка свойства: обновление сохраняет состояние ----------------------
# Своё же поведение, которое ломалось: `-Upgrade` уносил catalog/ и лицензию в .bak.
$upgradeTarget = Join-Path $Sandbox 'upgrade-target'
$first = Invoke-Setup -Arguments @('-Prefix', $upgradeTarget, '-NoPath', '-SkipDeps', '-SkipVerify', '-Source', $Source)
if ($first.Code -eq 0) {
    New-Item -ItemType Directory -Path (Join-Path $upgradeTarget 'catalog') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $upgradeTarget 'catalog\marker.json') -Value '{"registry":"live"}' -Encoding utf8NoBOM
    Set-Content -LiteralPath (Join-Path $upgradeTarget 'edition.license') -Value 'license' -Encoding utf8NoBOM
    $marker = (Get-FileHash (Join-Path $upgradeTarget 'catalog\marker.json')).Hash
    $initPath = Join-Path $upgradeTarget 'src\ragraf\__init__.py'
    $text = [IO.File]::ReadAllText($initPath)
    $current = [regex]::Match($text, '__version__\s*=\s*"([^"]+)"').Groups[1].Value
    [IO.File]::WriteAllText($initPath, $text.Replace($current, '0.0.1-probe'), [Text.UTF8Encoding]::new($false))
    $second = Invoke-Setup -Arguments @('-Prefix', $upgradeTarget, '-NoPath', '-SkipDeps', '-SkipVerify', '-Upgrade', '-Source', $Source)
    $markerKept = (Test-Path (Join-Path $upgradeTarget 'catalog\marker.json')) -and
                  ((Get-FileHash (Join-Path $upgradeTarget 'catalog\marker.json')).Hash -eq $marker)
    $licenseKept = Test-Path (Join-Path $upgradeTarget 'edition.license')
    $backupMade = @(Get-ChildItem -Directory -Path $Sandbox -Filter 'upgrade-target.bak-*').Count -ge 1
    if ($second.Code -eq 0 -and $markerKept -and $licenseKept -and $backupMade) {
        Write-Host ("  [ ok ] {0,-46} код 0, состояние и лицензия на месте, .bak создан" -f '6. обновление сохраняет состояние')
        $script:passed++
    } else {
        Write-Host ("  [FAIL] {0,-46} код {1}, реестр {2}, лицензия {3}, .bak {4}" -f `
            '6. обновление сохраняет состояние', $second.Code, $markerKept, $licenseKept, $backupMade)
        $script:failed++
        ($second.Output -split "`n" | Select-Object -Last 8) | ForEach-Object { Write-Host "         $_" }
    }
} else {
    Write-Host ("  [FAIL] {0,-46} подготовка не удалась (код {1})" -f '6. обновление сохраняет состояние', $first.Code)
    $script:failed++
}

# --- порча 7: диагностика обязана назвать причину -------------------------
# Блок диагностики бесполезен, если он говорит «всё хорошо» на сломанном окружении.
# Ломаем сами: ставим дерево без окружения и требуем, чтобы диагностика это сказала.
$diagTarget = Join-Path $Sandbox 'diag-target'
$diagInstall = Invoke-Setup -Arguments @('-Prefix', $diagTarget, '-NoPath', '-SkipDeps', '-SkipVerify', '-Source', $Source)
if ($diagInstall.Code -eq 0) {
    $venvDir = Join-Path $diagTarget '.venv'
    if (Test-Path -LiteralPath $venvDir) { Remove-Item -LiteralPath $venvDir -Recurse -Force }
    $diag = Invoke-Setup -Arguments @('-Prefix', $diagTarget, '-Diagnose', '-Source', $Source)
    $diagText = $diag.Output
    $saysMissing = ($diagText -match 'ragraf\.exe нет') -or ($diagText -match 'зависимости не встали')
    $saysVersion = $diagText -match 'версия:'
    if ($diag.Code -eq 0 -and $saysMissing -and $saysVersion) {
        Write-Host ("  [ ok ] {0,-46} код 0, сбой окружения назван" -f '7. диагностика называет причину')
        $script:passed++
    } else {
        Write-Host ("  [FAIL] {0,-46} код {1}, сбой назван: {2}, версия: {3}" -f `
            '7. диагностика называет причину', $diag.Code, $saysMissing, $saysVersion)
        $script:failed++
        ($diagText -split "`n" | Select-Object -Last 8) | ForEach-Object { Write-Host "         $_" }
    }
} else {
    Write-Host ("  [FAIL] {0,-46} подготовка не удалась (код {1})" -f '7. диагностика называет причину', $diagInstall.Code)
    $script:failed++
}

Write-Host ''
Write-Host ("ИТОГ: проверок {0}, пройдено {1}, провалено {2}" -f ($passed + $failed), $passed, $failed)

if (-not $Keep) {
    # Уборка ТОЛЬКО по списку созданного этим прогоном.
    foreach ($path in $created) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
    }
    if (Test-Path -LiteralPath $Sandbox) {
        $rest = Get-ChildItem -LiteralPath $Sandbox -Force
        if (-not $rest) { Remove-Item -LiteralPath $Sandbox -Force }
    }
}

exit $(if ($failed -eq 0) { 0 } else { 1 })
