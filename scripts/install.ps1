#Requires -Version 7.6
#Requires -PSEdition Core
<#
.SYNOPSIS
    Установка RAGRAF: окружение, зависимости, проверка готовности.

.DESCRIPTION
    Скрипт ставит RAGRAF в отдельное виртуальное окружение и ПРОВЕРЯЕТ, что
    получилось работоспособное окружение. Он не «делает вид, что установил»:
    каждый шаг заканчивается проверкой, а итог печатается таблицей.

    Порядок работы:
      1. проверки окружения (pwsh 7.6+, python >=3.13, модуль venv);
      2. создание виртуального окружения ВНЕ репозитория;
      3. установка пакета (editable) или только зависимостей;
      4. необязательный truststore (нужен под TLS-инспекцией);
      5. проверка запуска и версии;
      6. необязательная подготовка корпуса (init или ensure);
      7. разбор отчёта selftest по девяти обязательным компонентам.

    Чего скрипт СОЗНАТЕЛЬНО НЕ делает:
      * не индексирует корпус без явного -Project/-Index: первая индексация
        скачивает модель эмбеддингов (~240 МБ) и строит граф свойств кода
        (десятки секунд — минуты). Это отдельное решение, а не побочный
        эффект установки;
      * не пишет в репозиторий ничего, кроме виртуального окружения (если вы
        сами указали его внутри) и файла `.ragraf/` при индексации;
      * не ставит Java и Joern: без них продукт работает, но `slice`
        отказывает с причиной (см. scripts/install-joern.ps1).

    Измеренные ловушки, учтённые здесь (12.10.2026):
      * переменная PIP_USER=1 ломает установку в venv
        («Can not perform a '--user' install»). Скрипт снимает её для pip;
      * установка editable создаёт каталог `src/ragraf.egg-info/` в проекте —
        его надо исключать из индекса и из git. Скрипт сообщает об этом;
      * без truststore загрузка модели падает на проверке сертификата в сетях
        с TLS-инспекцией. Скрипт предупреждает, а с -WithTruststore ставит пакет.

.PARAMETER Source
    Каталог с исходниками пакета (где pyproject.toml). По умолчанию — корень
    репозитория, то есть каталог двумя уровнями выше этого скрипта.

.PARAMETER Venv
    Куда создать виртуальное окружение. По умолчанию — каталог `.venv` в
    домашнем каталоге пользователя. Внутри репозитория создавать не
    рекомендуется: окружение машинно-локальное и в поставку не входит.

.PARAMETER Python
    Интерпретатор для создания окружения. По умолчанию берётся `python` из PATH.

.PARAMETER DependenciesOnly
    Не устанавливать сам пакет, а поставить только зависимости из
    requirements.txt. Тогда работают обёртки (tools/ragraf.py, scripts/ragraf.ps1),
    но консольной команды `ragraf` не будет. Этот путь не создаёт
    `src/ragraf.egg-info/` в репозитории — то есть не меняет корпус.

.PARAMETER WithTruststore
    Дополнительно поставить `truststore` (системное хранилище сертификатов).
    Нужно в сетях с TLS-инспекцией; в домашней сети не требуется.

.PARAMETER Project
    Каталог проекта, для которого готовится корпус. Если указан, скрипт
    выполнит `ensure` по этому каталогу и подготовит его к работе.

.PARAMETER Index
    Выполнить индексацию указанного проекта сразу (скачивание модели ~240 МБ).
    Без этого флага корпус только подготавливается.

.PARAMETER NoVectors
    Индексация без векторного слоя: модель не скачивается, лексический поиск
    (FTS5/BM25) работает. Быстрее, но семантика недоступна.

.PARAMETER Force
    Пересоздать виртуальное окружение, если оно уже существует.

.EXAMPLE
    pwsh -NoLogo -NoProfile -File scripts/install.ps1
    Установка в ~/.ragraf/venv без подготовки корпуса.

.EXAMPLE
    pwsh -NoLogo -NoProfile -File scripts/install.ps1 -Project C:\work\myproject
    Установка и подготовка корпуса для проекта (регистрация, создание индекса
    при необходимости — но без скачивания модели).

.EXAMPLE
    pwsh -NoLogo -NoProfile -File scripts/install.ps1 -Project C:\work\myproject -Index
    То же плюс полная индексация с векторами и графом свойств кода.

.NOTES
    Коды возврата: 0 — успех; 1 — ошибка установки; 2 — неверные аргументы;
    3 — неподходящее окружение (нет python, старый python, нет модуля venv).
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
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Кодировки: UTF-8 без BOM и на вывод, и на чтение. Иначе русский текст в
# отчёте selftest приезжает искажённым, а его ещё и разбирать скриптом.
$OutputEncoding = [Console]::InputEncoding = [Console]::OutputEncoding =
    [System.Text.UTF8Encoding]::new($false)

$script:ExitOk = 0
$script:ExitError = 1
$script:ExitUsage = 2
$script:ExitEnvironment = 3

$script:Results = [System.Collections.Generic.List[object]]::new()

function Add-Result {
    param([string]$Step, [string]$State, [string]$Detail = '')
    $script:Results.Add([pscustomobject]@{ Step = $Step; State = $State; Detail = $Detail })
    $color = switch ($State) {
        'OK'    { 'Green' }
        'WARN'  { 'Yellow' }
        'FAIL'  { 'Red' }
        default { 'Gray' }
    }
    Write-Host ("  {0,-6} {1,-42} {2}" -f $State, $Step, $Detail) -ForegroundColor $color
}

function Write-Section {
    param([string]$Title)
    Write-Host ''
    Write-Host $Title -ForegroundColor Cyan
}

function Fail-Out {
    param([int]$Code, [string]$Message)
    Write-Host ''
    Write-Host "ОСТАНОВ: $Message" -ForegroundColor Red
    Show-Summary
    exit $Code
}

function Show-Summary {
    Write-Section 'ИТОГ'
    if ($script:Results.Count -eq 0) {
        Write-Host '  (ни одной проверки не выполнено)'
        return
    }
    foreach ($r in $script:Results) {
        $mark = if ($r.State -eq 'OK') { 'OK  ' } elseif ($r.State -eq 'WARN') { 'ВНИМ' } else { 'ОШИБ' }
        Write-Host ("  {0}  {1,-42} {2}" -f $mark, $r.Step, $r.Detail)
    }
    $failed = @($script:Results | Where-Object { $_.State -eq 'FAIL' }).Count
    $warned = @($script:Results | Where-Object { $_.State -eq 'WARN' }).Count
    Write-Host ''
    if ($failed -gt 0) {
        Write-Host "  ошибок: $failed, предупреждений: $warned" -ForegroundColor Red
    }
    elseif ($warned -gt 0) {
        Write-Host "  ошибок: 0, предупреждений: $warned" -ForegroundColor Yellow
    }
    else {
        Write-Host '  ошибок: 0, предупреждений: 0' -ForegroundColor Green
    }
}

function Invoke-Checked {
    <#
    Запуск внешней команды с проверкой кода возврата и сохранением вывода.
    Отдельная функция нужна потому, что PowerShell не превращает ненулевой код
    внешнего процесса в исключение даже при $ErrorActionPreference='Stop':
    без явной проверки «команда упала» выглядит как успех.
    #>
    param(
        [Parameter(Mandatory)][string]$File,
        [Parameter(Mandatory)][string[]]$Arguments,
        [int[]]$AllowedExitCodes = @(0)
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
    $out = $proc.StandardOutput.ReadToEnd()
    $err = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()
    return [pscustomobject]@{
        ExitCode = $proc.ExitCode
        StdOut   = $out
        StdErr   = $err
        Ok       = ($AllowedExitCodes -contains $proc.ExitCode)
    }
}

# --------------------------------------------------------------------------
# Шаг 1. Окружение
# --------------------------------------------------------------------------
Write-Host 'RAGRAF — установка' -ForegroundColor White
Write-Section '1. Проверка окружения'

$v = $PSVersionTable.PSVersion
if ($v -lt [version]'7.6') {
    Add-Result -Step 'PowerShell 7.6+' -State 'FAIL' -Detail "найдено $v"
    Fail-Out -Code $script:ExitEnvironment -Message 'нужен PowerShell 7.6 или новее (pwsh, не Windows PowerShell).'
}
if ($PSVersionTable.PSEdition -ne 'Core') {
    Add-Result -Step 'PowerShell Core' -State 'FAIL' -Detail "редакция $($PSVersionTable.PSEdition)"
    Fail-Out -Code $script:ExitEnvironment -Message 'нужен PowerShell Core (pwsh), а не Windows PowerShell.'
}
Add-Result -Step 'PowerShell 7.6+' -State 'OK' -Detail "$v ($($PSVersionTable.PSEdition))"

if (-not $Source) {
    $Source = Split-Path -Parent $PSScriptRoot
}
if (-not (Test-Path -LiteralPath (Join-Path $Source 'pyproject.toml'))) {
    Add-Result -Step 'Исходники найдены' -State 'FAIL' -Detail "нет pyproject.toml в $Source"
    Fail-Out -Code $script:ExitEnvironment -Message "укажите каталог с исходниками: -Source <путь> (в нём нет pyproject.toml)."
}
$Source = (Resolve-Path -LiteralPath $Source).Path
Add-Result -Step 'Исходники найдены' -State 'OK' -Detail $Source

if (-not $Python) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) {
        Add-Result -Step 'Интерпретатор Python' -State 'FAIL' -Detail 'python не найден в PATH'
        Fail-Out -Code $script:ExitEnvironment -Message 'python не найден в PATH. Установите Python 3.13+ или укажите -Python <путь>.'
    }
    $Python = $cmd.Source
}

$pyVer = Invoke-Checked -File $Python -Arguments @('-c', 'import sys; print("%d.%d.%d" % sys.version_info[:3])')
if (-not $pyVer.Ok) {
    Add-Result -Step 'Интерпретатор Python' -State 'FAIL' -Detail "не запускается: $Python"
    Fail-Out -Code $script:ExitEnvironment -Message "указанный интерпретатор не запускается: $Python"
}
$versionText = $pyVer.StdOut.Trim()
$dyVersion = $null
if (-not [version]::TryParse(($versionText -replace '^(\d+\.\d+).*$', '$1.0'), [ref]$dyVersion)) {
    $dyVersion = [version]'0.0'
}
# Сравнение по первым двум числам: нужно >=3.13 (объявлено в pyproject.toml).
$parts = $versionText.Split('.')
$majorMinor = [version]("$($parts[0]).$($parts[1])")
if ($majorMinor -lt [version]'3.13') {
    Add-Result -Step 'Версия Python >= 3.13' -State 'FAIL' -Detail "найдено $versionText"
    Fail-Out -Code $script:ExitEnvironment -Message "нужен Python 3.13 или новее; найден $versionText ($Python)."
}
Add-Result -Step 'Версия Python >= 3.13' -State 'OK' -Detail "$versionText ($Python)"

# Модуль venv: у некоторых сборок Python его нет вовсе (проверено на вендоренном
# интерпретаторе, где venv отсутствует). Без модуля установка невозможна.
$venvProbe = Invoke-Checked -File $Python -Arguments @('-c', 'import venv; print("venv ok")')
if (-not $venvProbe.Ok) {
    Add-Result -Step 'Модуль venv доступен' -State 'FAIL' -Detail 'модуль venv отсутствует'
    Fail-Out -Code $script:ExitEnvironment -Message "у интерпретатора $Python нет модуля venv. Нужен полноценный Python 3.13+, а не встроенный в другое приложение."
}
Add-Result -Step 'Модуль venv доступен' -State 'OK' -Detail $venvProbe.StdOut.Trim()

if (-not $Venv) {
    $userHome = [Environment]::GetFolderPath('UserProfile')
    $Venv = Join-Path $userHome '.ragraf/venv'
}
if (Test-Path -LiteralPath $Venv) {
    $insideSource = $Venv.StartsWith($Source, [System.StringComparison]::OrdinalIgnoreCase)
    if ($Force) {
        Add-Result -Step 'Виртуальное окружение' -State 'WARN' -Detail "-Force: пересоздаю $Venv"
        Remove-Item -LiteralPath $Venv -Recurse -Force
    }
    else {
        Add-Result -Step 'Виртуальное окружение' -State 'OK' -Detail "использую существующее: $Venv"
    }
    if ($insideSource) {
        Add-Result -Step 'Окружение вне репозитория' -State 'WARN' `
            -Detail 'venv внутри проекта попадёт в корпус: добавьте его в exclude'
    }
}

# --------------------------------------------------------------------------
# Шаг 2. Виртуальное окружение
# --------------------------------------------------------------------------
Write-Section '2. Виртуальное окружение'

$pythonInVenv = if ($IsWindows) { Join-Path $Venv 'Scripts/python.exe' } else { Join-Path $Venv 'bin/python' }
if (Test-Path -LiteralPath $pythonInVenv) {
    Add-Result -Step 'Создание окружения' -State 'OK' -Detail 'уже существует'
}
else {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Venv) | Out-Null
    $mk = Invoke-Checked -File $Python -Arguments @('-m', 'venv', $Venv)
    if (-not $mk.Ok -or -not (Test-Path -LiteralPath $pythonInVenv)) {
        Add-Result -Step 'Создание окружения' -State 'FAIL' -Detail ($mk.StdErr.Trim() -split "`n" | Select-Object -First 1)
        Fail-Out -Code $script:ExitError -Message "не удалось создать виртуальное окружение в $Venv"
    }
    Add-Result -Step 'Создание окружения' -State 'OK' -Detail $Venv
}

$venvPython = $pythonInVenv

# --------------------------------------------------------------------------
# Шаг 3. Зависимости
# --------------------------------------------------------------------------
Write-Section '3. Установка зависимостей'

# PIP_USER=1 ломает установку в venv: pip отказывается
# («Can not perform a '--user' install»). Это переменная окружения сессии,
# и она не должна влиять на установку в изолированное окружение.
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
    Write-Host '  (установка может занять несколько минут: скачиваются tree-sitter, ONNX Runtime и другие пакеты)' -ForegroundColor DarkGray
    $pip = Invoke-Checked -File $venvPython -Arguments $pipArgs
    if (-not $pip.Ok) {
        $tail = ($pip.StdErr.Trim() -split "`n" | Select-Object -Last 6) -join ' | '
        Add-Result -Step 'Установка зависимостей' -State 'FAIL' -Detail $tail
        Fail-Out -Code $script:ExitError -Message 'pip завершился с ошибкой. Полный вывод выше.'
    }
    $mode = if ($DependenciesOnly) { 'только зависимости (requirements.txt)' } else { 'пакет editable (-e .)' }
    Add-Result -Step 'Установка зависимостей' -State 'OK' -Detail $mode

    # Editable-установка создаёт каталог с метаданными внутри репозитория.
    # Это меняет состав корпуса (замер: 484 -> 489 файлов), поэтому о факте
    # нужно сказать, а не оставить сюрпризом.
    if (-not $DependenciesOnly) {
        $eggInfo = Join-Path $Source 'src/ragraf.egg-info'
        if (Test-Path -LiteralPath $eggInfo) {
            Add-Result -Step 'Метаданные сборки' -State 'WARN' `
                -Detail 'создан src/ragraf.egg-info/ — исключён в .gitignore, но меняет состав корпуса'
        }
    }

    if ($WithTruststore) {
        $ts = Invoke-Checked -File $venvPython -Arguments @('-m', 'pip', 'install', '--disable-pip-version-check', '--quiet', 'truststore')
        if ($ts.Ok) {
            Add-Result -Step 'truststore (сертификаты)' -State 'OK' -Detail 'установлен'
        }
        else {
            Add-Result -Step 'truststore (сертификаты)' -State 'WARN' -Detail 'не установлен: ' + (($ts.StdErr.Trim() -split "`n" | Select-Object -Last 1))
        }
    }
    else {
        Add-Result -Step 'truststore (сертификаты)' -State 'OK' -Detail 'не запрошен (нужен только под TLS-инспекцией)'
    }
}
finally {
    if ($null -eq $savedPipUser) { Remove-Item Env:\PIP_USER -ErrorAction SilentlyContinue }
    else { $env:PIP_USER = $savedPipUser }
}

# --------------------------------------------------------------------------
# Шаг 4. Проверка запуска
# --------------------------------------------------------------------------
Write-Section '4. Проверка запуска'

if ($DependenciesOnly) {
    $launcher = Join-Path $Source 'tools/ragraf.py'
    $run = Invoke-Checked -File $venvPython -Arguments @($launcher, '--version')
    if (-not $run.Ok) {
        Add-Result -Step 'Запуск через tools/ragraf.py' -State 'FAIL' -Detail (($run.StdErr.Trim() -split "`n" | Select-Object -Last 1))
        Fail-Out -Code $script:ExitError -Message 'обёртка tools/ragraf.py не работает в созданном окружении.'
    }
    Add-Result -Step 'Запуск через tools/ragraf.py' -State 'OK' -Detail $run.StdOut.Trim()
    $ragrafCmd = $null
    Add-Result -Step 'Консольная команда ragraf' -State 'WARN' -Detail 'не создана (-DependenciesOnly): запускайте через tools/ragraf.py'
}
else {
    $ragrafExe = if ($IsWindows) { Join-Path $Venv 'Scripts/ragraf.exe' } else { Join-Path $Venv 'bin/ragraf' }
    if (-not (Test-Path -LiteralPath $ragrafExe)) {
        # Windows создаёт .exe-обёртку не всегда: у не-Windows её нет по определению,
        # а на Windows бывает только скрипт без расширения. Ищем оба варианта.
        $alt = Join-Path $Venv 'Scripts/ragraf'
        if (Test-Path -LiteralPath $alt) { $ragrafExe = $alt }
    }
    if (Test-Path -LiteralPath $ragrafExe) {
        $run = Invoke-Checked -File $ragrafExe -Arguments @('--version')
    }
    else {
        $run = Invoke-Checked -File $venvPython -Arguments @('-m', 'ragraf.cli', '--version')
    }
    if (-not $run.Ok) {
        Add-Result -Step 'Консольная команда ragraf' -State 'FAIL' -Detail (($run.StdErr.Trim() -split "`n" | Select-Object -Last 1))
        Fail-Out -Code $script:ExitError -Message 'установленный пакет не запускается. Полный вывод выше.'
    }
    Add-Result -Step 'Консольная команда ragraf' -State 'OK' -Detail $run.StdOut.Trim()
    $ragrafCmd = if (Test-Path -LiteralPath $ragrafExe) { $ragrafExe } else { $null }

    # Обёртка тоже должна работать: её имя совпадает с именем пакета, и до
    # правки 12.10.2026 она падала после установки ('ragraf' is not a package).
    $launcher = Join-Path $Source 'tools/ragraf.py'
    $wrap = Invoke-Checked -File $venvPython -Arguments @($launcher, '--version')
    if ($wrap.Ok) {
        Add-Result -Step 'Обёртка tools/ragraf.py' -State 'OK' -Detail $wrap.StdOut.Trim()
    }
    else {
        Add-Result -Step 'Обёртка tools/ragraf.py' -State 'WARN' `
            -Detail 'не работает при установленном пакете — используйте команду ragraf'
    }
}

# --------------------------------------------------------------------------
# Шаг 5. Корпус (необязательно)
# --------------------------------------------------------------------------
Write-Section '5. Корпус проекта'

# Переменные объявлены ДО ветвлений: под Set-StrictMode обращение к
# неинициализированной переменной — терминальная ошибка, а не $null.
$cfgPath = $null
$corpusCfg = $null

if (-not $Project) {
    Add-Result -Step 'Подготовка корпуса' -State 'OK' -Detail 'пропущена: не указан -Project'
}
else {
    if (-not (Test-Path -LiteralPath $Project -PathType Container)) {
        Add-Result -Step 'Каталог проекта' -State 'FAIL' -Detail "не найден: $Project"
        Fail-Out -Code $script:ExitUsage -Message "каталог проекта не существует: $Project"
    }
    $Project = (Resolve-Path -LiteralPath $Project).Path
    Add-Result -Step 'Каталог проекта' -State 'OK' -Detail $Project

    # ensure сам находит корпус, пересобирает отставший индекс и создаёт
    # корпус для незнакомого каталога. Нужен каталог, похожий на проект:
    # у домашнего каталога и корня диска корпус не создаётся (статус
    # not_a_project), и это НЕ ошибка среды, а сознательное ограничение.
    # --json ставится ПОСЛЕ подкоманды: это описано в справке самого `ensure`
    # (у него свой ключ) — в отличие от глобальных --json/--project, которые
    # обязаны стоять ДО подкоманды. Перепутанный порядок даёт не ошибку,
    # а текстовый вывод вместо JSON — проверено запуском 12.10.2026.
    $ensureArgs = if ($ragrafCmd) {
        @('ensure', $Project, '--json')
    }
    else {
        @($launcher, 'ensure', $Project, '--json')
    }
    $ens = Invoke-Checked -File $(if ($ragrafCmd) { $ragrafCmd } else { $venvPython }) -Arguments $ensureArgs
    $status = $null
    $parsed = $null
    try {
        $parsed = $ens.StdOut | ConvertFrom-Json -AsHashtable
        $status = $parsed['status']
    }
    catch {
        $status = $null
    }
    if (-not $status) {
        # Резервный путь: разбор текстового вывода. JSON может отсутствовать
        # (другая версия продукта, изменился формат), и установка НЕ должна
        # от этого ломаться — читаем строку статуса словами.
        $raw = ($ens.StdOut + $ens.StdErr)
        if ($raw -match 'индекс актуален|сумма корпуса совпадает') { $status = 'actual' }
        elseif ($raw -match 'отставал|пересобран') { $status = 'reindexed' }
        elseif ($raw -match 'корпус создан|создан и проиндексирован') { $status = 'created' }
        elseif ($raw -match 'не является проектом|не похож на проект') { $status = 'not_a_project' }
    }
    switch ($status) {
        'actual'       { Add-Result -Step 'Актуализация корпуса' -State 'OK' -Detail 'индекс актуален' }
        'reindexed'    { Add-Result -Step 'Актуализация корпуса' -State 'OK' -Detail 'индекс был отставшим и пересобран' }
        'created'      { Add-Result -Step 'Актуализация корпуса' -State 'OK' -Detail 'корпус создан и проиндексирован' }
        'not_a_project' {
            Add-Result -Step 'Актуализация корпуса' -State 'WARN' `
                -Detail 'каталог не похож на проект (нет маркеров: .git, pyproject.toml и др.) — корпус не создан'
        }
        default {
            Add-Result -Step 'Актуализация корпуса' -State 'WARN' `
                -Detail "статус не распознан; вывод: $(($ens.StdOut.Trim() -split "`n" | Select-Object -First 1))"
        }
    }
    # Путь к конфигу корпуса берём из ответа ensure: он его печатает.
    # Искать его в реестре — лишний вызов и лишний повод ошибиться.
    if ($parsed -and $parsed['config']) {
        $cfgPath = [string]$parsed['config']
    }

    if ($Index -and $status -in @('actual', 'reindexed', 'created')) {
        if (-not $cfgPath) {
            # Резервный путь: ensure не отдал конфиг (нет JSON) — берём из реестра.
            $cat = Invoke-Checked -File $(if ($ragrafCmd) { $ragrafCmd } else { $venvPython }) `
                -Arguments $(if ($ragrafCmd) { @('catalog') } else { @($launcher, 'catalog') })
            foreach ($line in ($cat.StdOut -split "`n")) {
                if ($line -match 'конфиг:\s*(.+\.yaml)\s*$') { $cfgPath = $Matches[1].Trim() }
            }
        }
        if (-not $cfgPath) {
            Add-Result -Step 'Индексация' -State 'WARN' -Detail 'не удалось определить конфиг корпуса'
        }
        else {
            $idxArgs = if ($ragrafCmd) { @('-p', $cfgPath, 'index') } else { @($launcher, '-p', $cfgPath, 'index') }
            if ($NoVectors) { $idxArgs += '--no-vectors' }
            Write-Host '  (индексация: первая может скачать модель эмбеддингов ~240 МБ)' -ForegroundColor DarkGray
            $idx = Invoke-Checked -File $(if ($ragrafCmd) { $ragrafCmd } else { $venvPython }) -Arguments $idxArgs
            if ($idx.Ok) {
                $idxLine = ($idx.StdOut -split "`n" | Where-Object { $_ -match 'файлов=' } | Select-Object -First 1)
                if (-not $idxLine) { $idxLine = (($idx.StdOut -split "`n" | Select-Object -First 2) -join ' ') }
                Add-Result -Step 'Индексация' -State 'OK' -Detail $idxLine.Trim()
            }
            else {
                Add-Result -Step 'Индексация' -State 'FAIL' -Detail (($idx.StdErr.Trim() -split "`n" | Select-Object -Last 1))
            }
        }
    }
    elseif ($Index) {
        Add-Result -Step 'Индексация' -State 'WARN' -Detail 'пропущена: корпус не подготовлен'
    }
    else {
        Add-Result -Step 'Индексация' -State 'OK' -Detail 'не запрошена (добавьте -Index, когда будете готовы к скачиванию модели)'
    }
}

# --------------------------------------------------------------------------
# Шаг 6. Подробная проверка окружения (selftest)
# --------------------------------------------------------------------------
Write-Section '6. Проверка компонентов (selftest)'

if (-not $Project -or -not $cfgPath) {
    Add-Result -Step 'selftest' -State 'OK' -Detail 'пропущен: нужен подготовленный корпус'
}
else {
    # selftest требует --project, хотя его докстрока называет команду работающей
    # без конфига (расхождение найдено и исправлено в этой версии). Отчёт он
    # печатает ВСЕГДА с кодом 0, поэтому разбираем вывод, а не код возврата.
    $stArgs = if ($ragrafCmd) { @('-p', $cfgPath, 'selftest') } else { @($launcher, '-p', $cfgPath, 'selftest') }
    $st = Invoke-Checked -File $(if ($ragrafCmd) { $ragrafCmd } else { $venvPython }) -Arguments $stArgs
    $text = ($st.StdOut + "`n" + $st.StdErr)
    $required = @('python', 'tree_sitter', 'php_grammar', 'sqlite_vec', 'fastembed', 'tiktoken', 'fts5', 'vec0_loadable', 'embedding_model')
    $missing = [System.Collections.Generic.List[string]]::new()
    foreach ($name in $required) {
        # Строка отчёта: имя компонента и признак готовности. Готовым считаем
        # строку, где имя есть и НЕТ пометок MISSING/НЕТ/отсутствует/не загружен.
        $line = ($text -split "`r?`n" | Where-Object { $_ -match [regex]::Escape($name) } | Select-Object -First 1)
        if (-not $line) { $missing.Add($name); continue }
        if ($line -match 'MISSING|\[НЕТ\]|отсутствует|не загружен|недоступ') { $missing.Add($name) }
    }
    if ($missing.Count -eq 0) {
        Add-Result -Step 'selftest: обязательные компоненты' -State 'OK' -Detail 'все девять на месте'
    }
    else {
        Add-Result -Step 'selftest: обязательные компоненты' -State 'WARN' `
            -Detail ('отсутствуют или не готовы: ' + ($missing -join ', '))
    }

    # Java и Joern — необязательны: без них продукт работает, но срез недоступен.
    $joernLine = ($text -split "`r?`n" | Where-Object { $_ -match 'joern' } | Select-Object -First 1)
    if ($joernLine -and $joernLine -match 'MISSING|\[НЕТ\]') {
        Add-Result -Step 'Граф свойств кода (Joern)' -State 'WARN' `
            -Detail 'Joern не найден: capability=degraded, операция slice откажет. Установка: scripts/install-joern.ps1'
    }
    elseif ($joernLine) {
        Add-Result -Step 'Граф свойств кода (Joern)' -State 'OK' -Detail $joernLine.Trim()
    }
}

# --------------------------------------------------------------------------
# Шаг 7. Что дальше
# --------------------------------------------------------------------------
Write-Section 'Что дальше'
$ragrafCall = if ($ragrafCmd) { $ragrafCmd } else { "python `"$(Join-Path $Source 'tools/ragraf.py')`"" }
Write-Host '  Активировать окружение (Windows):'
Write-Host "    $(Join-Path $Venv 'Scripts/Activate.ps1')" -ForegroundColor DarkGray
Write-Host '  Подготовить корпус и получить первый ответ:'
Write-Host "    $ragrafCall ensure <каталог проекта>" -ForegroundColor DarkGray
Write-Host "    $ragrafCall -p <конфиг> query search \"что ищем\"" -ForegroundColor DarkGray
Write-Host '  Подключить к агенту как MCP-сервер (stdio):'
Write-Host "    $venvPython `"$(Join-Path $Source 'tools/ragraf-mcp.py')`" --project <конфиг> --agent <имя агента>" -ForegroundColor DarkGray
Write-Host '  Панель настроек (localhost:8777):'
Write-Host "    python `"$(Join-Path $Source 'tools/ragraf-admin.py')`" --no-browser" -ForegroundColor DarkGray
Write-Host '  Без Java/Joern: продукт работает, но операция slice недоступна.'
Write-Host "    установка внешнего слоя: pwsh -File `"$(Join-Path $Source 'scripts/install-joern.ps1')`"" -ForegroundColor DarkGray

Show-Summary

$failedFinal = @($script:Results | Where-Object { $_.State -eq 'FAIL' }).Count
if ($failedFinal -gt 0) { exit $script:ExitError }
exit $script:ExitOk
