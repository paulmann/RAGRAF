#Requires -Version 7.6
#Requires -PSEdition Core
<#
.SYNOPSIS
    Установка RAGRAF на Windows 10/11: распаковать поставку, поставить окружение,
    зарегистрировать в PATH, проверить тремя командами.

.DESCRIPTION
    Тонкая обёртка над уже существующими установщиками, а не второй установщик:
    зависимости ставит `scripts\install.ps1`, развёртывание — `community\install.ps1`.
    Здесь только то, чего у них нет: выбор каталога, отказ от опасных каталогов,
    перенос прежней установки в `.bak-<метка>`, PATH пользователя, три проверки после
    установки и итоговая печать отпечатка установки.

    Опасные каталоги отвергаются ДО записи: инсталлятор внутри индексируемого проекта
    попал бы в собственный индекс, а установка в C:\Windows — это уже не наша машина.

.EXAMPLE
    pwsh -NoLogo -NoProfile -File .\ragraf-setup.ps1 -DryRun

.EXAMPLE
    pwsh -NoLogo -NoProfile -File .\ragraf-setup.ps1 -Prefix "$env:TEMP\ragraf-test"

.EXAMPLE
    pwsh -NoLogo -NoProfile -File .\ragraf-setup.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    # Каталог установки. По умолчанию %LOCALAPPDATA%\RAGRAF.
    [string]$Prefix = '',
    # Распакованное дерево поставки. По умолчанию — каталог этого скрипта.
    [string]$Source = '',
    # Только показать план, ничего не менять.
    [switch]$DryRun,
    # Перенести прежнюю установку в .bak-<метка> перед распаковкой.
    [switch]$Upgrade,
    # Убрать программу, сохранив каталог состояния (catalog/, индекс).
    [switch]$Uninstall,
    # Убрать вместе с каталогом состояния.
    [switch]$Purge,
    # Не трогать PATH пользователя.
    [switch]$NoPath,
    # Не ставить зависимости (проверка самого установщика в песочнице).
    [switch]$SkipDeps,
    # Явный интерпретатор. Без него установщик ищет сам (PATH, %LOCALAPPDATA%\Python,
    # C:\Program Files\Python3*) и берёт первый с модулем venv.
    [string]$PythonPath = '',
    # Не запускать проверки после установки.
    [switch]$SkipVerify,
    # Машиночитаемый итог.
    [string]$SummaryJson = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::InputEncoding = [Console]::OutputEncoding =
    [System.Text.UTF8Encoding]::new($false)

$script:ExitOk = 0
$script:ExitError = 1
$script:ExitUsage = 2
$script:ExitEnvironment = 3

$script:Results = [System.Collections.Generic.List[object]]::new()

function Add-Step {
    param([string]$Name, [string]$State, [string]$Detail = '')
    $script:Results.Add([pscustomobject]@{ step = $Name; state = $State; detail = $Detail })
    $mark = switch ($State) { 'OK' { ' ok ' } 'FAIL' { 'FAIL' } 'SKIP' { 'skip' } 'WARN' { 'warn' } default { ' -- ' } }
    Write-Host ("  [{0}] {1,-22} {2}" -f $mark, $Name, $Detail)
}

function Stop-WithReason {
    param([string]$Step, [string]$Reason, [int]$Code = 1)
    Add-Step -Name $Step -State 'FAIL' -Detail $Reason
    Write-Host ''
    Write-Host "ОТКАЗ: $Reason" -ForegroundColor Red
    Write-Summary -Verdict 'failed' -Message $Reason
    exit $Code
}

function Write-Summary {
    param([string]$Verdict, [string]$Message = '')
    if (-not $SummaryJson) { return }
    $doc = [ordered]@{
        installer = 'ragraf-setup.ps1'
        platform  = 'windows'
        verdict   = $Verdict
        message   = $Message
        version   = $script:Version
        prefix    = $script:InstallDir
        steps     = @($script:Results)
    }
    $path = [IO.Path]::GetFullPath($SummaryJson)
    $dir = Split-Path -Parent $path
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    [IO.File]::WriteAllText($path, ($doc | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
}

function Test-DangerousTarget {
    <#
        Каталог установки не должен быть системным корнем и не должен лежать внутри
        ЧУЖОГО индексируемого проекта. Домашний каталог — исключение с предупреждением:
        там стоит умолчание (%LOCALAPPDATA%\RAGRAF), и домашний корпус — это индекс
        рабочей области, а не проект. Причина исключения называется в шаге.
    #>
    param([string]$Path)

    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $system = @(
        $env:SystemRoot,
        ${env:ProgramFiles},
        ${env:ProgramFiles(x86)},
        [Environment]::GetFolderPath('UserProfile')
    ) | Where-Object { $_ }
    foreach ($root in $system) {
        if ($full -eq ([IO.Path]::GetFullPath($root).TrimEnd('\'))) {
            return "каталог $full — системный или домашний корень целиком; выберите подкаталог"
        }
    }
    if ($full -match '^[A-Za-z]:\\?$') {
        return "каталог $full — корень диска"
    }
    if (Test-Path -LiteralPath (Join-Path $full '.ragraf')) {
        # Своя же установка содержит .ragraf: собственный check после установки создаёт
        # индекс в корне поставки (измерено 23.11.2026 — повторный запуск отказывал на
        # своей же установке). Чужой корпус — тот, где пакета RAGRAF нет.
        if (-not (Test-Path -LiteralPath (Join-Path $full 'src\ragraf\__init__.py'))) {
            return "каталог $full уже содержит индекс .ragraf: установка перезапишет чужой корпус"
        }
    }
    $homeDir = [IO.Path]::GetFullPath([Environment]::GetFolderPath('UserProfile')).TrimEnd('\')
    $probe = Split-Path -Parent $full
    while ($probe -and $probe -match '^[A-Za-z]:\\') {
        if (Test-Path -LiteralPath (Join-Path $probe '.ragraf')) {
            if ($probe.TrimEnd('\') -ne $homeDir) {
                return "каталог $full лежит внутри индексируемого проекта ($probe): установка попадёт в свой же индекс"
            }
            return ''  # домашний корпус: умолчание установщика, предупреждение отдельным шагом
        }
        $parent = Split-Path -Parent $probe
        if (-not $parent -or $parent -eq $probe) { break }
        $probe = $parent.TrimEnd('\')
    }
    return ''
}

function Get-TreeVersion {
    param([string]$Root)
    $init = Join-Path $Root 'src\ragraf\__init__.py'
    if (-not (Test-Path -LiteralPath $init)) { return '' }
    $text = [IO.File]::ReadAllText($init)
    $m = [regex]::Match($text, '__version__\s*=\s*"([^"]+)"')
    if ($m.Success) { return $m.Groups[1].Value }
    return ''
}

Write-Host '=== RAGRAF: установка на Windows ===' -ForegroundColor Cyan

# --- 1. окружение ---------------------------------------------------------
if ($PSVersionTable.PSVersion -lt [version]'7.6' -or $PSVersionTable.PSEdition -ne 'Core') {
    Stop-WithReason -Step 'PowerShell' -Code $script:ExitEnvironment `
        -Reason "нужен PowerShell 7.6 Core, а запущено $($PSVersionTable.PSVersion) $($PSVersionTable.PSEdition)"
}
Add-Step -Name 'PowerShell' -State 'OK' -Detail "$($PSVersionTable.PSVersion) $($PSVersionTable.PSEdition)"

$python = $null
$pythonVersion = ''
$rejected = @()
$candidates = @()
# Удалению интерпретатор не нужен: без этой развилки `-Uninstall` отказывал
# «нужен Python 3.13» (измерено 23.11.2026 на Linux; в PowerShell-версии
# поведение приведено к тому же).
if (-not $Uninstall) {
if ($PythonPath) {
    # Явный интерпретатор: без проверки venv нельзя — именно этот случай даровал
    # ложную надежду (app-bundled python 3.13 без venv).
    $candidates += $PythonPath
} else {
    $pathPython = Get-Command python -ErrorAction SilentlyContinue | ForEach-Object { $_.Source }
    $candidates += @($pathPython)
    $candidates += @(
        (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Python\bin\python.exe')
    )
    $candidates += @(Get-ChildItem 'C:\Program Files\Python3*\python.exe' -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
}
foreach ($candidate in ($candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -Unique)) {
    $probeVersion = (& $candidate -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>&1 | Out-String).Trim()
    if ($probeVersion -notmatch '^3\.(1[3-9]|[2-9][0-9])$') {
        $rejected += "$candidate ($probeVersion) — версия ниже 3.13"
        continue
    }
    # venv проверяется ДО записи: с app-bundled интерпретатором без venv установка
    # падала уже после распаковки (измерено 23.11.2026 на bundled python 3.13.15).
    $venvProbe = (& $candidate -c "import venv; print('ok')" 2>&1 | Out-String).Trim()
    if ($venvProbe -ne 'ok') {
        $rejected += "$candidate ($probeVersion) — нет модуля venv"
        continue
    }
    $python = $candidate
    $pythonVersion = $probeVersion
    break
}
if (-not $python) {
    $detail = if ($rejected.Count) {
        'проверены: ' + ($rejected -join '; ')
    } elseif ($PythonPath) {
        "указан $PythonPath — не найден или не подходит"
    } else {
        'python не найден ни в PATH, ни в %LOCALAPPDATA%\Python, ни в C:\Program Files\Python3*'
    }
    Stop-WithReason -Step 'Python' -Code $script:ExitEnvironment `
        -Reason "нужен Python 3.13+ с модулем venv; $detail"
}
Add-Step -Name 'Python' -State 'OK' -Detail "$pythonVersion ($python)"
if ($rejected.Count) {
    Add-Step -Name 'Прочие Python' -State 'SKIP' -Detail ($rejected -join '; ')
}
}

# --- 2. источник и каталог ------------------------------------------------
if (-not $Source) {
    # Поставка кладёт скрипт в scripts\, а дерево — на уровень выше; если рядом с
    # самим скриптом пакета нет, берём родительский каталог (и наоборот).
    if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'src\ragraf\__init__.py')) {
        $Source = $PSScriptRoot
    } else {
        $Source = Split-Path -Parent $PSScriptRoot
    }
}
$Source = [IO.Path]::GetFullPath($Source)
$script:Version = Get-TreeVersion -Root $Source
if (-not $script:Version) {
    Stop-WithReason -Step 'Поставка' -Reason "в $Source нет src\ragraf\__init__.py — это не дерево RAGRAF"
}
$expected = $env:RAGRAF_EXPECTED_VERSION
if ($expected -and $expected -ne $script:Version) {
    Stop-WithReason -Step 'Поставка' -Reason "версия дерева $($script:Version) не совпадает с заявленной $expected"
}
Add-Step -Name 'Поставка' -State 'OK' -Detail "версия $($script:Version), $Source"

if (-not $Prefix) {
    $Prefix = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'RAGRAF'
}
$script:InstallDir = [IO.Path]::GetFullPath($Prefix)
$danger = Test-DangerousTarget -Path $script:InstallDir
if ($danger) { Stop-WithReason -Step 'Каталог' -Code $script:ExitUsage -Reason $danger }
Add-Step -Name 'Каталог' -State 'OK' -Detail $script:InstallDir

# Домашний каталог проиндексирован (рабочая область), и умолчание лежит внутри него:
# это допустимо, но назвать причину надо вслух, а не молча.
$homeProbe = Split-Path -Parent $script:InstallDir
$homeIndexed = $false
while ($homeProbe -and $homeProbe -match '^[A-Za-z]:\\') {
    if (Test-Path -LiteralPath (Join-Path $homeProbe '.ragraf')) { $homeIndexed = $true; break }
    $parent = Split-Path -Parent $homeProbe
    if (-not $parent -or $parent -eq $homeProbe) { break }
    $homeProbe = $parent.TrimEnd('\')
}
if ($homeIndexed) {
    Add-Step -Name 'Домашний корпус' -State 'WARN' `
        -Detail 'каталог лежит внутри индексируемой рабочей области; при желании укажите -Prefix вне неё'
}

$exePath = Join-Path $script:InstallDir '.venv\Scripts\ragraf.exe'
$installedInit = Join-Path $script:InstallDir 'src\ragraf\__init__.py'
$installedVersion = if (Test-Path -LiteralPath $installedInit) { Get-TreeVersion -Root $script:InstallDir } else { '' }

# --- 3. удаление ----------------------------------------------------------
if ($Uninstall) {
    if (-not (Test-Path -LiteralPath $script:InstallDir)) {
        Stop-WithReason -Step 'Удаление' -Reason "каталога $($script:InstallDir) нет"
    }
    if ($DryRun) {
        Add-Step -Name 'Удаление' -State 'SKIP' -Detail "план: убрать программу из $($script:InstallDir)$(if ($Purge) { ' вместе с состоянием' } else { ', состояние сохранить' })"
        Write-Summary -Verdict 'dry-run'
        exit $script:ExitOk
    }
    $stateDirs = @('catalog', '.ragraf')
    $keep = @()
    foreach ($name in $stateDirs) {
        $candidate = Join-Path $script:InstallDir $name
        if ((Test-Path -LiteralPath $candidate) -and -not $Purge) { $keep += $candidate }
    }
    if ($keep.Count -gt 0) {
        $stash = Join-Path ([IO.Path]::GetTempPath()) ("ragraf-state-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
        New-Item -ItemType Directory -Path $stash -Force | Out-Null
        foreach ($path in $keep) { Move-Item -LiteralPath $path -Destination $stash -Force }
        Add-Step -Name 'Состояние' -State 'OK' -Detail "сохранено в $stash (перенесите обратно, если ставите снова)"
    }
    Remove-Item -LiteralPath $script:InstallDir -Recurse -Force
    Add-Step -Name 'Удаление' -State 'OK' -Detail "$($script:InstallDir) удалён$(if ($Purge) { ' с состоянием' } else { '' })"
    if (-not $NoPath) {
        $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        $entries = @($userPath -split ';' | Where-Object { $_ -and $_ -ne (Join-Path $script:InstallDir '.venv\Scripts') })
        [Environment]::SetEnvironmentVariable('Path', ($entries -join ';'), 'User')
        Add-Step -Name 'PATH' -State 'OK' -Detail 'запись установщика убрана'
    }
    Write-Summary -Verdict 'uninstalled'
    exit $script:ExitOk
}

# --- 4. та же версия: ничего не делаем ------------------------------------
$sameVersion = ($installedVersion -and $installedVersion -eq $script:Version)
if ($sameVersion -and -not $Upgrade) {
    Add-Step -Name 'Установка' -State 'SKIP' -Detail "уже установлено: версия $installedVersion в $($script:InstallDir)"
    $verify = -not $SkipVerify
} else {
    $verify = -not $SkipVerify
    if ($DryRun) {
        Add-Step -Name 'Распаковка' -State 'SKIP' -Detail "план: $Source → $($script:InstallDir)"
        Add-Step -Name 'Зависимости' -State 'SKIP' -Detail $(if ($SkipDeps) { 'план: пропустить -SkipDeps' } else { 'план: scripts\install.ps1' })
        Add-Step -Name 'PATH' -State 'SKIP' -Detail $(if ($NoPath) { 'план: не трогать' } else { 'план: добавить .venv\Scripts в PATH пользователя' })
        Write-Summary -Verdict 'dry-run'
        exit $script:ExitOk
    }

    $backup = ''
    $venvCarried = $false
    if ($installedVersion) {
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $backup = "$($script:InstallDir).bak-$stamp"
        Move-Item -LiteralPath $script:InstallDir -Destination $backup
        Add-Step -Name 'Прежняя версия' -State 'OK' -Detail "$installedVersion → $backup"
    }

    New-Item -ItemType Directory -Path $script:InstallDir -Force | Out-Null
    foreach ($item in Get-ChildItem -LiteralPath $Source -Force) {
        Copy-Item -LiteralPath $item.FullName -Destination $script:InstallDir -Recurse -Force
    }

    # Состояние переезжает из бэкапа в новую установку: реестр, хранилище секретов,
    # лицензия, отпечаток и индекс — это НЕ часть программы. Обновление, которое
    # уносит их в .bak, теряет лицензию и заставляет пересобирать индекс заново
    # (измерено 23.11.2026: требование «обновление при живом индексе»).
    if ($backup) {
        $moved = @()
        foreach ($name in @('catalog', '.ragraf')) {
            $from = Join-Path $backup $name
            if (Test-Path -LiteralPath $from) {
                # Поставка может содержать ПУСТОЙ заготовок с тем же именем — иначе
                # Move-Item падает «already exists» (измерено 23.11.2026). Состояние
                # прежней установки старше заготовка, поэтому заготовок убираем.
                $target = Join-Path $script:InstallDir $name
                if (Test-Path -LiteralPath $target) {
                    Remove-Item -LiteralPath $target -Recurse -Force
                }
                Move-Item -LiteralPath $from -Destination $script:InstallDir -Force
                $moved += $name
            }
        }
        foreach ($pattern in @('edition.license', 'edition.license.sig', 'edition.pub')) {
            $file = Join-Path $backup $pattern
            if (Test-Path -LiteralPath $file) {
                Move-Item -LiteralPath $file -Destination $script:InstallDir -Force
                $moved += $pattern
            }
        }
        # Виртуальное окружение тоже переезжает: без этого pip ставит зависимости заново
        # и «обновление» занимает те же минуты, что установка с нуля.
        $oldVenv = Join-Path $backup '.venv'
        if ((Test-Path -LiteralPath $oldVenv) -and -not $SkipDeps) {
            # В поставке лежит пустой .venv (или его создал pip install -e):
            # без снятия Move-Item падает «already exists» (измерено 23.11.2026).
            $newVenv = Join-Path $script:InstallDir '.venv'
            if (Test-Path -LiteralPath $newVenv) {
                Remove-Item -LiteralPath $newVenv -Recurse -Force
            }
            Move-Item -LiteralPath $oldVenv -Destination $script:InstallDir -Force
            $moved += '.venv'
            $venvCarried = $true
        }
        if ($moved.Count) {
            Add-Step -Name 'Состояние' -State 'OK' -Detail ('перенесено: ' + ($moved -join ', '))
        } else {
            Add-Step -Name 'Состояние' -State 'SKIP' -Detail 'переносить было нечего (свежая установка)'
        }
    }
    $copied = Get-TreeVersion -Root $script:InstallDir
    if ($copied -ne $script:Version) {
        Stop-WithReason -Step 'Распаковка' -Reason "после копирования версия $copied вместо $($script:Version)"
    }
    Add-Step -Name 'Распаковка' -State 'OK' -Detail "версия $copied"
    $verify = -not $SkipVerify
}

# --- 5. зависимости -------------------------------------------------------
if ($venvCarried -and (Test-Path -LiteralPath $exePath)) {
    # Окружение переехало из прежней установки: ставить заново нечего. Пакет при этом
    # лежит в venv в режиме editable, поэтому новая версия видна сразу (проверяется ниже).
    Add-Step -Name 'Зависимости' -State 'OK' -Detail 'окружение перенесено, установка не нужна'
} elseif (-not $SkipDeps) {
    $installer = Join-Path $script:InstallDir 'scripts\install.ps1'
    if (-not (Test-Path -LiteralPath $installer)) {
        Stop-WithReason -Step 'Зависимости' -Reason "в поставке нет scripts\install.ps1"
    }
    $log = Join-Path ([IO.Path]::GetTempPath()) ("ragraf-deps-" + [guid]::NewGuid().ToString('N').Substring(0, 6) + '.log')
    & pwsh -NoLogo -NoProfile -NonInteractive -File $installer -Source $script:InstallDir -Venv (Join-Path $script:InstallDir '.venv') -Python $python *> $log
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        Stop-WithReason -Step 'Зависимости' -Reason "scripts\install.ps1 вернул код $code; журнал: $log"
    }
    Add-Step -Name 'Зависимости' -State 'OK' -Detail "окружение $($script:InstallDir)\.venv"
} elseif (-not $sameVersion) {
    Add-Step -Name 'Зависимости' -State 'SKIP' -Detail 'пропущено по -SkipDeps'
}

# --- 6. PATH --------------------------------------------------------------
if (-not $NoPath) {
    $binDir = Join-Path $script:InstallDir '.venv\Scripts'
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $entries = @($userPath -split ';' | Where-Object { $_ })
    if ($entries -contains $binDir) {
        Add-Step -Name 'PATH' -State 'OK' -Detail 'уже есть'
    } else {
        $newPath = (@($entries) + $binDir) -join ';'
        [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
        $readBack = [Environment]::GetEnvironmentVariable('Path', 'User')
        if ($readBack -notlike "*$binDir*") {
            Stop-WithReason -Step 'PATH' -Reason 'запись PATH не подтвердилась чтением'
        }
        Add-Step -Name 'PATH' -State 'OK' -Detail "$binDir добавлен (нужен новый сеанс)"
    }
    $env:Path = "$binDir;$env:Path"
} else {
    Add-Step -Name 'PATH' -State 'SKIP' -Detail 'не трогали по -NoPath'
}

# --- 7. проверки ----------------------------------------------------------
$ensureConfig = ''
if ($verify -and $sameVersion -and -not $Upgrade) {
    # Повторный запуск не должен ничего менять: проверка ограничивается версией,
    # иначе ensure пишет прогон в реестр и catalog/ перестаёт быть неизменным
    # (измерено 23.11.2026: 25 файлов catalog/ менялись на повторном запуске).
    $exe = Join-Path $script:InstallDir '.venv\Scripts\ragraf.exe'
    if (Test-Path -LiteralPath $exe) {
        $verOut = (& $exe --version 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            Stop-WithReason -Step 'Проверка' -Reason 'ragraf --version вернул ненулевой код'
        }
        Add-Step -Name 'Проверка' -State 'OK' -Detail "$verOut (повторный запуск: без записи в реестр)"
    }
} elseif ($verify) {
    $candidates = @($exePath, (Join-Path $script:InstallDir '.venv\Scripts\ragraf.exe'))
    $exe = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $exe) {
        Stop-WithReason -Step 'Проверка' -Reason "после установки нет $exePath — зависимости не поставились"
    }
    $verOut = (& $exe --version 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $verOut -notlike "*$($script:Version)*") {
        Stop-WithReason -Step 'Проверка' -Reason "ragraf --version вернул «$verOut» (ожидалась $($script:Version))"
    }
    Add-Step -Name 'Проверка' -State 'OK' -Detail "ragraf --version → $verOut"

    $probe = Join-Path ([IO.Path]::GetTempPath()) ("ragraf-probe-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Path $probe -Force | Out-Null
    try {
        Set-Content -LiteralPath (Join-Path $probe '.gitignore') -Value ".ragraf/`n" -Encoding utf8NoBOM
        # ensure требует минимум три файла с кодом и маркер проекта, иначе честно
        # отвечает not_a_project (src/ragraf/ensure.py:478) — в проверке установщика
        # это было бы ложным отказом (измерено 23.11.2026).
        foreach ($name in 'main.py', 'util.py', 'model.py') {
            Set-Content -LiteralPath (Join-Path $probe $name) -Value "def $($name -replace '\.py$','')():\n    return 1\n" -Encoding utf8NoBOM
        }
        $ensureOut = (& $exe ensure $probe --json 2>&1 | Out-String)
        if ($LASTEXITCODE -ne 0) {
            Stop-WithReason -Step 'Проверка ensure' -Reason "ensure на пробном проекте вернул код $LASTEXITCODE"
        }
        # Разбор JSON, а не регулярка: в выводе пути с экранированными обратными косыми
        # ("C:\\..."), и регулярка давала нечитаемый путь — check получал код 2
        # (измерено 23.11.2026: установка падала на своей же проверке).
        $ensureDoc = $ensureOut | ConvertFrom-Json
        $status = [string]$ensureDoc.status
        if ($status -notin @('actual', 'reindexed', 'created')) {
            Stop-WithReason -Step 'Проверка ensure' -Reason "ensure вернул статус «$status» ($($ensureDoc.reason))"
        }
        Add-Step -Name 'Проверка ensure' -State 'OK' -Detail "статус $status на пробном проекте"
        $ensureConfig = [string]$ensureDoc.config
        # Третий шаг проверки — внутри try: check читает корень пробы, и после её
        # удаления он получает код 2 (измерено 23.11.2026: установка падала на
        # собственной уборке, а не на установке).
        if ($ensureConfig) {
            $checkOut = (& $exe -p $ensureConfig check 2>&1 | Out-String).Trim()
            if ($LASTEXITCODE -ne 0) {
                Stop-WithReason -Step 'Проверка check' -Reason "check пробного корпуса вернул код $LASTEXITCODE"
            }
            Add-Step -Name 'Проверка check' -State 'OK' -Detail (($checkOut -split "`n")[0])
        } else {
            Add-Step -Name 'Проверка check' -State 'SKIP' -Detail 'ensure не назвал конфиг корпуса'
        }
    } finally {
        Remove-Item -LiteralPath $probe -Recurse -Force -ErrorAction SilentlyContinue
    }

    $fingerprint = (& $exe edition --fingerprint 2>&1 | Out-String).Trim()
    $digest = ([regex]::Match($fingerprint, '([0-9a-f]{64})')).Groups[1].Value
    if ($digest) {
        Add-Step -Name 'Отпечаток' -State 'OK' -Detail "$digest"
    } else {
        Add-Step -Name 'Отпечаток' -State 'SKIP' -Detail 'edition --fingerprint не назвал отпечаток'
    }
} else {
    Add-Step -Name 'Проверка' -State 'SKIP' -Detail 'пропущено по -SkipVerify'
}

Write-Host ''
Write-Host "Готово: RAGRAF $($script:Version) в $($script:InstallDir)" -ForegroundColor Green
Write-Host 'Что осталось настроить по желанию:'
Write-Host '  · Joern для срезов свойств кода: scripts\install-joern.ps1 (без него capability=degraded)'
Write-Host '  · платное издание: положите edition.license, edition.license.sig и edition.pub рядом с catalog\'
Write-Summary -Verdict 'installed'
exit $script:ExitOk
