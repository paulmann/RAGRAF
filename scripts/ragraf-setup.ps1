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
    # Собрать сведения об окружении одним блоком (для письма в поддержку) и выйти.
    [switch]$Diagnose,
    # Первый запуск: каталог проекта, который надо проиндексировать и показать в работе.
    [string]$FirstRun = '',
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

#: Граница версии Python — ОДНО объявление на установщик. Её же читает
#: `tools/check_python_floor.py` и сверяет с `pyproject.toml` и документом.
$script:MinPython = [version]'3.13'

$script:Results = [System.Collections.Generic.List[object]]::new()

#: Предел файлов для мастера первого запуска — ИЗМЕРЕН, а не на глаз:
#: 500 файлов → 48 с, 2000 → 501 с (23.11.2026), кривая ≈ n^1,7, то есть
#: 20 000 файлов — это часы работы. 10-минутный бюджет даёт ≈2 000 файлов.
#: Предел называет число и причину в отказе; обойти — `ragraf ensure`.
$script:FirstRunFileLimit = 2000

function Get-ProjectFileCount {
    <# Файлов в проекте, с ранним остановом на пределе: полный обход огромного
       дерева — сам по себе работа, которой мастер первого запуска не обещает. #>
    param([string]$Project, [int]$Limit)
    $count = 0
    $stack = [System.Collections.Generic.Stack[string]]::new()
    $stack.Push($Project)
    while ($stack.Count -gt 0) {
        $dir = $stack.Pop()
        try { $entries = [IO.Directory]::EnumerateFileSystemEntries($dir) } catch { continue }
        foreach ($entry in $entries) {
            if ([IO.Directory]::Exists($entry)) {
                $name = [IO.Path]::GetFileName($entry)
                if ($name -in @('.git', '.ragraf', 'node_modules', '.venv', 'vendor', '__pycache__')) { continue }
                $stack.Push($entry)
            } else {
                $count++
                if ($count -gt $Limit) { return $count }
            }
        }
    }
    return $count
}

function Get-ForeignStateNote {
    <# Причина, по которой каталог проекта нельзя индексировать этой установкой. #>
    param([string]$Project)
    $marker = Join-Path $Project '.ragraf\install.json'
    if (-not (Test-Path -LiteralPath $marker)) { return '' }
    try { $doc = [IO.File]::ReadAllText($marker) | ConvertFrom-Json } catch { return '' }
    $owner = [string]$doc.install_path
    if ($owner -and $owner -ne $script:InstallDir) {
        return "в проекте индекс установки $owner, а это $($script:InstallDir): индексируйте своим установщиком"
    }
    return ''
}

function Get-GitignoreNote {
    <# Проект под git без правила `.ragraf/`: индекс попадёт в git status. #>
    param([string]$Project)
    if (-not (Test-Path -LiteralPath (Join-Path $Project '.git'))) { return '' }
    $ignore = Join-Path $Project '.gitignore'
    if (-not (Test-Path -LiteralPath $ignore)) {
        return 'проект под git без .gitignore: добавьте строку .ragraf/ (иначе индекс попадёт в git status)'
    }
    $lines = [IO.File]::ReadAllLines($ignore)
    foreach ($line in $lines) {
        $clean = $line.Trim()
        if ($clean -eq '.ragraf' -or $clean -eq '.ragraf/' -or $clean -eq '/.ragraf/' -or $clean -eq '**/.ragraf/') { return '' }
    }
    return 'в .gitignore нет правила .ragraf/: индекс попадёт в git status (добавьте .ragraf/)'
}

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
    $marker = Join-Path $full '.ragraf\install.json'
    if (Test-Path -LiteralPath $marker) {
        # Маркер пишет наш же установщик. Другой путь в нём — чужое состояние:
        # там лицензия и реестр другой установки (правило введено 23.11.2026).
        $marked = ''
        try {
            $doc = [IO.File]::ReadAllText($marker) | ConvertFrom-Json
            $marked = [string]$doc.install_path
        } catch { }
        if ($marked -and $marked -ne $full) {
            return "состояние в $full\.ragraf принадлежит установке $marked`: выберите другой каталог"
        }
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

if ($Diagnose) {
    # Диагностика ничего не меняет на диске и не читает секретоносные пути.
    # Один блок — чтобы его можно было скопировать в письмо целиком.
    Write-Host ''
    Write-Host '--- сведения об окружении (для поддержки) ---'
    Write-Host ("RAGRAF-установщик: {0}" -f $PSScriptRoot)
    Write-Host ("PowerShell: {0} {1}" -f $PSVersionTable.PSVersion, $PSVersionTable.PSEdition)
    Write-Host ("ОС: {0}" -f [System.Runtime.InteropServices.RuntimeInformation]::OSDescription)
    Write-Host ("Архитектура: {0}" -f [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture)
    if ($Prefix) { Write-Host ("Каталог установки (задан): {0}" -f $Prefix) }

    Write-Host ''
    Write-Host '--- интерпретаторы ---'
    $probe = @()
    $probe += Get-Command python -ErrorAction SilentlyContinue | ForEach-Object { $_.Source }
    $probe += (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Python\bin\python.exe')
    $probe += Get-ChildItem 'C:\Program Files\Python3*\python.exe' -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }
    foreach ($candidate in ($probe | Where-Object { $_ } | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $candidate)) {
            Write-Host ("  · {0} — нет файла" -f $candidate)
            continue
        }
        $version = (& $candidate -c "import sys; print('%d.%d.%d' % sys.version_info[:3])" 2>&1 | Out-String).Trim()
        if ($version -notmatch '^\d+\.\d+\.\d+$') {
            # Заглушка Microsoft Store отдаёт текст вместо версии: в письме нужна
            # короткая правда, а не весь абзац (измерено 23.11.2026).
            $short = ($version -split "`n" | Select-Object -First 1)
            if ($short.Length -gt 70) { $short = $short.Substring(0, 70) + '…' }
            Write-Host ("  · {0} — НЕ ЗАПУСКАЕТСЯ: {1}" -f $candidate, $short)
            continue
        }
        $venvOk = (& $candidate -c "import venv; print('venv')" 2>&1 | Out-String).Trim()
        Write-Host ("  · {0} — версия {1}, venv: {2}" -f $candidate, $version, $(if ($venvOk -eq 'venv') { 'есть' } else { 'НЕТ' }))
    }

    Write-Host ''
    Write-Host '--- установка, если уже есть ---'
    $target = if ($Prefix) { $Prefix } else { Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'RAGRAF' }
    if (Test-Path -LiteralPath (Join-Path $target 'src\ragraf\__init__.py')) {
        Write-Host ("каталог: {0}" -f $target)
        Write-Host ("версия: {0}" -f (Get-TreeVersion -Root $target))
        $diagExe = Join-Path $target '.venv\Scripts\ragraf.exe'
        if (Test-Path -LiteralPath $diagExe) {
            Write-Host ("ragraf --version: {0}" -f ((& $diagExe --version 2>&1 | Out-String).Trim()))
            Write-Host '--- состояние индекса ---'
            $ownConfig = Join-Path $target 'config\ragraf.yaml'
            if (Test-Path -LiteralPath $ownConfig) {
                (& $diagExe -p $ownConfig status 2>&1 | Out-String).Trim() -split "`n" |
                    Select-Object -First 6 | ForEach-Object { Write-Host ("  {0}" -f $_.Trim()) }
            } else {
                Write-Host '  · своего конфига нет (установка без корпуса)'
            }
        } else {
            Write-Host '  · .venv\Scripts\ragraf.exe нет — зависимости не встали'
        }
    } else {
        Write-Host ("  · не установлено в {0}" -f $target)
    }

    Write-Host ''
    Write-Host '--- свободное место ---'
    foreach ($drive in (Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Free -ne $null })) {
        Write-Host ("  · {0}: свободно {1:N1} ГБ" -f $drive.Name, ($drive.Free / 1GB))
    }
    Write-Host ''
    Write-Host 'Конец блока диагностики.'
    exit $script:ExitOk
}

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
    $okVersion = $false
    try { $okVersion = ([version]$probeVersion -ge $script:MinPython) } catch { $okVersion = $false }
    if (-not $okVersion) {
        $rejected += "$candidate ($probeVersion) — версия ниже $($script:MinPython)"
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
$venvCarried = $false
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
    $stage = "$($script:InstallDir).new-$PID"
    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
    New-Item -ItemType Directory -Path $stage -Force | Out-Null

    # Служебные каталоги живого дерева в установку НЕ переносятся: состояние (.ragraf),
    # песочницы (.staging, .work, .zwork), окружение (.venv) принадлежат разработке, а не
    # продукту (измерено 23.11.2026: копия с .ragraf делала каталог «чужим корпусом»).
    $skipNames = @('.git', '.ragraf', '.venv', '.work', '.zwork', '.staging', '.tools',
                   'node_modules', '__pycache__', '.pytest_cache', '.mypy_cache')
    foreach ($item in Get-ChildItem -LiteralPath $Source -Force) {
        if ($skipNames -contains $item.Name) { continue }
        Copy-Item -LiteralPath $item.FullName -Destination $stage -Recurse -Force
    }
    $copied = Get-TreeVersion -Root $stage
    if ($copied -ne $script:Version) {
        Remove-Item -LiteralPath $stage -Recurse -Force
        Stop-WithReason -Step 'Распаковка' -Reason "в новом каталоге версия $copied вместо $($script:Version)"
    }

    if ($installedVersion) {
        $moved = @()
        foreach ($name in @('catalog', '.ragraf')) {
            $from = Join-Path $script:InstallDir $name
            if (Test-Path -LiteralPath $from) {
                # В поставке лежит пустая заготовка с тем же именем — снимаем её,
                # иначе Move-Item падает «already exists» (измерено 23.11.2026).
                $staleTarget = Join-Path $stage $name
                if (Test-Path -LiteralPath $staleTarget) {
                    Remove-Item -LiteralPath $staleTarget -Recurse -Force
                }
                Move-Item -LiteralPath $from -Destination $stage -Force
                $moved += $name
            }
        }
        foreach ($pattern in @('edition.license', 'edition.license.sig', 'edition.pub')) {
            $file = Join-Path $script:InstallDir $pattern
            if (Test-Path -LiteralPath $file) {
                Move-Item -LiteralPath $file -Destination $stage -Force
                $moved += $pattern
            }
        }
        # Конфиги корпусов, созданные ensure/init, лежат в config\ установки: новый
        # набор выигрывает по совпадающим именам, остальные переносятся как есть.
        $oldConfig = Join-Path $script:InstallDir 'config'
        if (Test-Path -LiteralPath $oldConfig) {
            $newConfig = Join-Path $stage 'config'
            if (-not (Test-Path -LiteralPath $newConfig)) { New-Item -ItemType Directory -Path $newConfig -Force | Out-Null }
            foreach ($file in Get-ChildItem -LiteralPath $oldConfig -File -Force) {
                $target = Join-Path $newConfig $file.Name
                if (-not (Test-Path -LiteralPath $target)) { Copy-Item -LiteralPath $file.FullName -Destination $target }
            }
            $moved += 'config'
        }
        $oldVenv = Join-Path $script:InstallDir '.venv'
        # Окружение НЕ уносим до подмены: пока идёт сборка (десятки секунд),
        # установка обязана работать. Переезжает из резервной копии сразу после
        # второго переименования — путями внутри venv это не двигает.
        $venvCarried = (-not $SkipDeps) -and (Test-Path -LiteralPath $oldVenv)
        if ($moved.Count) {
            Add-Step -Name 'Состояние' -State 'OK' -Detail ('перенесено: ' + ($moved -join ', '))
        } else {
            Add-Step -Name 'Состояние' -State 'SKIP' -Detail 'переносить было нечего (свежая установка)'
        }

        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $backup = "$($script:InstallDir).bak-$stamp"
        Move-Item -LiteralPath $script:InstallDir -Destination $backup
        Move-Item -LiteralPath $stage -Destination $script:InstallDir
        Add-Step -Name 'Прежняя версия' -State 'OK' -Detail "$installedVersion → $backup"
        if ($venvCarried) {
            # Третье переименование: каталог тот же, поэтому editable-пакет уже
            # указывает верно и `pip install -e` в окне недоступности не нужен.
            try {
                Move-Item -LiteralPath (Join-Path $backup '.venv') -Destination $script:InstallDir -Force
                $moved += '.venv'
            } catch {
                $venvCarried = $false
                Add-Step -Name 'Зависимости' -State 'WARN' -Detail 'прежнее окружение не переехало — запустите установщик без -SkipDeps'
            }
        }
    } else {
        if (Test-Path -LiteralPath $script:InstallDir) { Remove-Item -LiteralPath $script:InstallDir -Recurse -Force }
        Move-Item -LiteralPath $stage -Destination $script:InstallDir
    }
    Add-Step -Name 'Распаковка' -State 'OK' -Detail "версия $copied"
    $verify = -not $SkipVerify
}

# --- 5. зависимости -------------------------------------------------------
if ($venvCarried -and (Test-Path -LiteralPath $exePath)) {
    # Путь каталога не менялся, поэтому перепривязка не нужна: она осталась
    # страховкой на случай, если окружение всё же оказалось из другого пути.
    $pyExe = Join-Path $script:InstallDir '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $pyExe) {
        $rebind = $false
        try {
            $inner = & $pyExe -c 'import ragraf, pathlib; print(pathlib.Path(ragraf.__file__).resolve())' 2>$null
            if (-not $inner -or -not ([string]$inner).StartsWith($script:InstallDir)) { $rebind = $true }
        } catch { $rebind = $true }
        if ($rebind) { & $pyExe -m pip install -e $script:InstallDir --no-deps --quiet *> $null }
        if ($rebind -and $LASTEXITCODE -ne 0) {
            Add-Step -Name 'Зависимости' -State 'WARN' -Detail 'перепривязка editable-пакета не удалась'
        } else {
            Add-Step -Name 'Зависимости' -State 'OK' -Detail 'окружение перенесено (пути совпадают)'
        }
    } else {
        Add-Step -Name 'Зависимости' -State 'OK' -Detail 'окружение перенесено, установка не нужна'
    }
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

# --- 8. первый запуск ------------------------------------------------------
# Мастер показывает, что продукт делает, на реальном каталоге владельца: ensure →
# status → первая выдача с адресом файл:строка. Честное «capability=degraded»
# произносится вслух, а не замалчивается.
if ($FirstRun) {
    $probeProject = [IO.Path]::GetFullPath($FirstRun)
    $exe = Join-Path $script:InstallDir '.venv\Scripts\ragraf.exe'
    if (-not (Test-Path -LiteralPath $probeProject)) {
        Add-Step -Name 'Первый запуск' -State 'FAIL' -Detail "каталога $probeProject нет"
    } else {
        # Проверки ДАННЫХ идут до проверки окружения: чужий индекс в проекте и
        # огромный каталог — факты о файлах, и они не должны зависеть от того,
        # встали ли зависимости.
        $foreign = Get-ForeignStateNote -Project $probeProject
        $size = Get-ProjectFileCount -Project $probeProject -Limit $script:FirstRunFileLimit
        if ($foreign) {
            Add-Step -Name 'Первый запуск' -State 'FAIL' -Detail $foreign
        } elseif ($size -gt $script:FirstRunFileLimit) {
            # Предел назван числом: молчаливое ожидание на 200 000 файлов — не помощь.
            Add-Step -Name 'Первый запуск' -State 'SKIP' `
                -Detail ("файлов $size, предел $($script:FirstRunFileLimit): мастер не индексирует такой проект — это часы работы. Запустите отдельно: ragraf ensure `"$probeProject`"")
        } elseif ($note = Get-GitignoreNote -Project $probeProject) {
            Add-Step -Name 'Первый запуск git' -State 'WARN' -Detail $note
        } elseif (-not (Test-Path -LiteralPath $exe)) {
            Add-Step -Name 'Первый запуск' -State 'SKIP' -Detail 'без окружения первый запуск невозможен'
        } else {
            if ($size -gt 1500) {
                Add-Step -Name 'Первый запуск размер' -State 'WARN' `
                    -Detail "файлов ${size}: индексация займёт минуты, это ожидаемо"
            }
            $queryOut = (& $exe -p $ensureDoc.config query search 'def ' --limit 3 2>&1 | Out-String)
            $refs = ([regex]::Matches($queryOut, '[\w./\\-]+\.\w+:\d+')).Count
            if ($refs -gt 0) {
                Add-Step -Name 'Первый запуск поиск' -State 'OK' -Detail "адресов файл:строка $refs"
            } else {
                Add-Step -Name 'Первый запуск поиск' -State 'SKIP' -Detail 'поиск не дал адресов (маленький корпус)'
            }
        }
    }
}


# --- маркер установки -----------------------------------------------------
# Свой маркер, а не догадка о чужом индексе: другой запуск по нему увидит, что
# состояние принадлежит иной установке (проверка в Test-DangerousTarget).
$markerDir = Join-Path $script:InstallDir '.ragraf'
New-Item -ItemType Directory -Path $markerDir -Force | Out-Null
$markerDoc = [ordered]@{
    installer    = 'ragraf-setup.ps1'
    install_path = $script:InstallDir
    version      = $script:Version
    written_at   = (Get-Date).ToString('o')
}
[IO.File]::WriteAllText((Join-Path $markerDir 'install.json'),
    ($markerDoc | ConvertTo-Json -Depth 3), [Text.UTF8Encoding]::new($false))

Write-Host ''
Write-Host "Готово: RAGRAF $($script:Version) в $($script:InstallDir)" -ForegroundColor Green
Write-Host 'Что осталось настроить по желанию:'
Write-Host '  · Joern для срезов свойств кода: scripts\install-joern.ps1 (без него capability=degraded)'
Write-Host '  · платное издание: положите edition.license, edition.license.sig и edition.pub рядом с catalog\'
Write-Summary -Verdict 'installed'
exit $script:ExitOk
