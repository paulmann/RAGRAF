#Requires -Version 7.6
#Requires -PSEdition Core
<#
.SYNOPSIS
    Установка RAGRAF: скачивание пакета, развёртывание, подключение клиентов.

.DESCRIPTION
    Скрипт делает то, что нужно сделать перед первым запуском продукта:

      1. проверяет окружение (PowerShell 7.6+, Python 3.13+, место на диске,
         доступность github.com);
      2. спрашивает каталог установки (по умолчанию C:\RAGRAF);
      3. получает пакет: git clone с https://github.com/<Repo>, а если git
         недоступен — архив ветки; при -SourceDir разворачивает из локальной
         копии и в сеть не идёт вообще;
      4. ставит виртуальное окружение и зависимости штатным scripts/install.ps1 —
         тем же путём, которым пользуется CI продукта, а не своей копией;
      5. спрашивает, искать ли AutoClaw и DeepSeek Harness, и что с ними делать;
      6. печатает инструкцию по запуску панели управления и предупреждение о
         доступе к ней.

    Чего скрипт НЕ делает — и почему это сказано прямо:

      * НЕ записывает настройки в AutoClaw. AutoClaw принимает MCP-серверы только
        через свой интерфейс: файла конфигурации для них нет, а единственное
        хранилище (runtime.sqlite) принадлежит продукту — запись в него в обход
        интерфейса обошла бы учёт прав и артефактов. Поэтому установщик находит
        AutoClaw и показывает ТОЧНЫЕ значения для формы «Add tool», полученные у
        самого RAGRAF (client_setup.state()), а не пересказанные по памяти.
      * НЕ индексирует корпуса: первая индексация скачивает модель эмбеддингов
        (~240 МБ) и строит граф свойств кода. Это отдельное решение владельца;
        запускается позже — `ragraf ensure <каталог>` или из панели.
      * НЕ ставит Java и Joern: без них продукт работает, а операция `slice`
        отказывает с причиной. Отдельный путь — scripts/install-joern.ps1.
      * НЕ переписывает найденную установку молча: обновление выполняется только
        с согласия, каталог `.ragraf` внутри неё не трогается никогда.

.PARAMETER InstallDir
    Каталог установки. По умолчанию спрашивается в диалоге, значение по
    умолчанию — C:\RAGRAF.

.PARAMETER Repo
    Репозиторий GitHub в виде «владелец/имя». По умолчанию paulmann/RAGRAF.

.PARAMETER Ref
    Ветка или тег. По умолчанию main.

.PARAMETER SourceDir
    Локальная копия пакета: развернуть из неё, а не скачивать. Нужна, когда сеть
    недоступна или когда публикуется проверенная локальная версия.

.PARAMETER VenvDir
    Каталог виртуального окружения. По умолчанию `<InstallDir>\.venv`.

.PARAMETER Python
    Интерпретатор для создания окружения. По умолчанию `python` из PATH.

.PARAMETER Yes
    Не задавать вопросов: принять значения по умолчанию. Нужен там, где консоли
    для диалога нет (автоматическая установка, запуск из скрипта).

.PARAMETER DependenciesOnly
    Поставить только зависимости, без самого пакета: тогда работают обёртки
    (tools/ragraf.py), но консольной команды `ragraf` не будет, и в дереве не
    появится каталог src/ragraf.egg-info.

.PARAMETER WithTruststore
    Дополнительно поставить truststore — нужно в сетях с TLS-инспекцией, иначе
    загрузка модели эмбеддингов падает на проверке сертификата.

.PARAMETER SkipDeps
    Развернуть пакет без создания окружения. Это режим репетиции: он проверяет
    скачивание, развёртывание и диалог о клиентах, но не ставит зависимости.

.PARAMETER SkipClients
    Не спрашивать про AutoClaw и Harness.

.PARAMETER NoTranscript
    Не вести журнал установки.

.PARAMETER SummaryJson
    Записать итог таблицей в этот файл — для конвейера.

.EXAMPLE
    # Обычная установка: диалог, каталог по умолчанию, подключение клиентов
    pwsh -NoLogo -NoProfile -File .\install.ps1

.EXAMPLE
    # Без вопросов, в указанный каталог, из локальной копии
    pwsh -NoLogo -NoProfile -File .\install.ps1 -Yes -InstallDir D:\RAGRAF -SourceDir C:\AutoClaw\RAGRAF

.EXAMPLE
    # Репетиция: развернуть и показать инструкции, не ставя зависимости
    pwsh -NoLogo -NoProfile -File .\install.ps1 -SkipDeps -InstallDir "$env:TEMP\ragraf-check"

.NOTES
    Коды возврата: 0 — установка завершена; 1 — ошибка установки; 2 — неверные
    аргументы; 3 — неподходящее окружение. Журнал установки:
    <домашний каталог>\.ragraf\logs\install-<дата-время>.log
#>

[CmdletBinding()]
param(
    [string]$InstallDir = '',
    [string]$Repo = 'paulmann/RAGRAF',
    [string]$Ref = 'main',
    [string]$SourceDir = '',
    [string]$VenvDir = '',
    [string]$Python = '',
    [switch]$Yes,
    [switch]$DependenciesOnly,
    [switch]$WithTruststore,
    [switch]$SkipDeps,
    [switch]$SkipClients,
    [switch]$NoTranscript,
    [string]$SummaryJson = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Кодировки: без BOM, иначе кириллица в дочерних процессах приходит искажённой,
# а её разбирает разбор итога установки.
$OutputEncoding = [Console]::InputEncoding = [Console]::OutputEncoding =
    [System.Text.UTF8Encoding]::new($false)

$script:ExitOk = 0
$script:ExitError = 1
$script:ExitUsage = 2
$script:ExitEnvironment = 3

$script:Results = [System.Collections.Generic.List[object]]::new()
$script:Failed = 0
$script:Warned = 0
$script:WorkDir = Join-Path ([IO.Path]::GetTempPath()) ("ragraf-install-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$script:TranscriptPath = ''
$script:Interactive = $false

# ---------------------------------------------------------------------------
# Вывод: шаги, результаты и итоговая таблица
# ---------------------------------------------------------------------------

function Write-Head {
    param([string]$Title)
    Write-Host ''
    Write-Host ('=' * 74) -ForegroundColor DarkCyan
    Write-Host " $Title" -ForegroundColor Cyan
    Write-Host ('=' * 74) -ForegroundColor DarkCyan
}

function Add-Result {
    param(
        [string]$Step,
        [ValidateSet('OK', 'WARN', 'FAIL', 'SKIP')][string]$State,
        [string]$Detail = ''
    )
    $script:Results.Add([pscustomobject]@{ Step = $Step; State = $State; Detail = $Detail })
    if ($State -eq 'FAIL') { $script:Failed++ }
    if ($State -eq 'WARN') { $script:Warned++ }
    $color = switch ($State) {
        'OK' { 'Green' } 'WARN' { 'Yellow' } 'FAIL' { 'Red' } default { 'DarkGray' }
    }
    Write-Host ("  {0,-6} {1,-38} {2}" -f $State, $Step, $Detail) -ForegroundColor $color
}

function Write-Note {
    param([string]$Text)
    Write-Host "  $Text" -ForegroundColor Gray
}

function Fail-Install {
    param([string]$Reason, [int]$Code = 1)
    Add-Result -Step 'Установка' -State 'FAIL' -Detail $Reason
    Write-Summary
    Stop-Journal
    exit $Code
}

function Write-Summary {
    if ($SummaryJson) {
        $payload = [ordered]@{
            verdict = if ($script:Failed -gt 0) { 'fail' } elseif ($script:Warned -gt 0) { 'warn' } else { 'ok' }
            failed = $script:Failed
            warned = $script:Warned
            install_dir = $InstallDir
            results = $script:Results
        }
        $dir = Split-Path -Parent $SummaryJson
        if ($dir -and -not (Test-Path -LiteralPath $dir)) {
            $null = New-Item -ItemType Directory -Force -Path $dir
        }
        $payload | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $SummaryJson -Encoding utf8NoBOM
    }
    Write-Host ''
    Write-Host ("  итог: проверок {0}, ошибок {1}, предупреждений {2}" -f `
                $script:Results.Count, $script:Failed, $script:Warned) -ForegroundColor Gray
    if ($script:TranscriptPath) {
        Write-Host "  журнал: $script:TranscriptPath" -ForegroundColor Gray
    }
}

function Start-Journal {
    if ($NoTranscript) { return }
    try {
        $logDir = Join-Path (Join-Path ([Environment]::GetFolderPath('UserProfile')) '.ragraf') 'logs'
        $null = New-Item -ItemType Directory -Force -Path $logDir
        $script:TranscriptPath = Join-Path $logDir ("install-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
        Start-Transcript -Path $script:TranscriptPath -Force | Out-Null
    } catch {
        $script:TranscriptPath = ''
        Write-Note "журнал не заведён (причина: $($_.Exception.GetType().Name))"
    }
}

function Stop-Journal {
    if ($script:TranscriptPath) {
        try { Stop-Transcript | Out-Null } catch { }
    }
}

# ---------------------------------------------------------------------------
# Диалог. Вопрос задаётся только тогда, когда консоль для него действительно
# есть: признак проверяется, а не предполагается.
# ---------------------------------------------------------------------------

function Test-CanPrompt {
    if ($Yes) { return $false }
    try {
        if (-not [Environment]::UserInteractive) { return $false }
        if ([Console]::IsInputRedirected) { return $false }
    } catch {
        return $false
    }
    return $true
}

function Read-Answer {
    param([string]$Question, [string]$Default)
    if (-not $script:Interactive) {
        Write-Host ("  {0} -> {1} (вопрос не задан: консоли для диалога нет)" -f $Question, $Default) -ForegroundColor Gray
        return $Default
    }
    while ($true) {
        $answer = Read-Host ("  {0} [{1}]" -f $Question, $Default)
        if ([string]::IsNullOrWhiteSpace($answer)) { return $Default }
        return $answer.Trim()
    }
}

function Read-Choice {
    param(
        [string]$Question,
        [string[]]$Choices,
        [string]$Default
    )
    if (-not $script:Interactive) {
        Write-Host ("  {0} -> {1} (вопрос не задан: консоли для диалога нет)" -f $Question, $Default) -ForegroundColor Gray
        return $Default
    }
    while ($true) {
        $raw = Read-Host ("  {0} ({1}) [{2}]" -f $Question, ($Choices -join '/'), $Default)
        if ([string]::IsNullOrWhiteSpace($raw)) { return $Default }
        $value = $raw.Trim().ToLowerInvariant()
        foreach ($c in $Choices) {
            if ($value -eq $c.ToLowerInvariant() -or $value -eq $c.Substring(0, 1).ToLowerInvariant()) {
                return $c
            }
        }
        Write-Host ("    не понял: ожидается одно из {0}" -f ($Choices -join ', ')) -ForegroundColor Yellow
    }
}

# ---------------------------------------------------------------------------
# Запуск внешних процессов
# ---------------------------------------------------------------------------

function Invoke-Capture {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$Arguments = @()
    )
    $text = & $FilePath @Arguments 2>&1 | Out-String
    return [pscustomobject]@{ Code = $LASTEXITCODE; Text = $text.Trim() }
}

function Invoke-Streamed {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$Arguments = @()
    )
    # Вывод не перехватывается: у дочернего процесса свои таймауты, а перехват
    # большого объёма через пайп как раз и приводит к взаимной блокировке.
    & $FilePath @Arguments
    return $LASTEXITCODE
}

# ---------------------------------------------------------------------------
# 1. Окружение
# ---------------------------------------------------------------------------

function Test-Environment {
    Write-Head '1. Проверка окружения'

    $v = $PSVersionTable.PSVersion
    if ($v -ge [version]'7.6') {
        Add-Result -Step 'PowerShell' -State 'OK' -Detail "$v / $($PSVersionTable.PSEdition)"
    } else {
        Add-Result -Step 'PowerShell' -State 'FAIL' -Detail "нужен 7.6+, найден $v"
    }

    if (-not $Python) {
        $cmd = Get-Command python -ErrorAction SilentlyContinue
        if (-not $cmd) {
            Add-Result -Step 'Python' -State 'FAIL' -Detail 'python не найден в PATH'
            return $false
        }
        $script:Python = $cmd.Source
    } else {
        $script:Python = $Python
    }

    if ($script:Python -match 'WindowsApps') {
        Add-Result -Step 'Python' -State 'FAIL' -Detail 'найден псевдоним Microsoft Store, а не интерпретатор'
        Write-Note 'Отключите псевдоним python в «Параметры → Приложения → Псевдонимы выполнения приложений»'
        Write-Note 'или укажите настоящий интерпретатор: -Python C:\Python313\python.exe'
        return $false
    }

    $probe = Invoke-Capture -FilePath $script:Python -Arguments @('-c', 'import sys;print("%d.%d.%d"%sys.version_info[:3]);print(sys.executable)')
    if ($probe.Code -ne 0) {
        Add-Result -Step 'Python' -State 'FAIL' -Detail "не запускается (код $($probe.Code))"
        return $false
    }
    $lines = @($probe.Text -split "`r?`n" | Where-Object { $_ })
    $version = $lines[0]
    if ([version]($version -replace '^(\d+\.\d+).*$', '$1.0') -lt [version]'3.13') {
        Add-Result -Step 'Python' -State 'FAIL' -Detail "нужен 3.13+, найден $version"
        return $false
    }
    Add-Result -Step 'Python' -State 'OK' -Detail "$version ($($lines[-1]))"

    if (-not (Test-Path -LiteralPath $script:WorkDir)) {
        $null = New-Item -ItemType Directory -Force -Path $script:WorkDir
    }

    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) {
        $script:Git = $git.Source
        Add-Result -Step 'git' -State 'OK' -Detail $script:Git
    } else {
        $script:Git = ''
        Add-Result -Step 'git' -State 'WARN' -Detail 'нет — пакет будет взят архивом ветки'
    }

    return $true
}

function Test-TargetDisk {
    param([string]$Path)
    try {
        $root = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Path))
        $drive = [System.IO.DriveInfo]::new($root)
        $freeGb = [math]::Round($drive.AvailableFreeSpace / 1GB, 1)
        if ($freeGb -lt 3.0) {
            Add-Result -Step 'Место на диске' -State 'FAIL' -Detail "свободно $freeGb ГБ, нужно не меньше 3"
            return $false
        }
        Add-Result -Step 'Место на диске' -State 'OK' -Detail "$freeGb ГБ свободно на $root"
        return $true
    } catch {
        Add-Result -Step 'Место на диске' -State 'WARN' -Detail "не удалось определить (причина: $($_.Exception.GetType().Name))"
        return $true
    }
}

function Test-GitHubReachable {
    if ($SourceDir) {
        Add-Result -Step 'Сеть' -State 'SKIP' -Detail 'развёртывание из локальной копии, сеть не нужна'
        return $true
    }
    try {
        $client = [System.Net.Sockets.TcpClient]::new()
        $task = $client.ConnectAsync('github.com', 443)
        if (-not $task.Wait(5000)) {
            $client.Dispose()
            Add-Result -Step 'Сеть' -State 'FAIL' -Detail 'github.com:443 не отвечает за 5 с'
            return $false
        }
        $client.Dispose()
        Add-Result -Step 'Сеть' -State 'OK' -Detail 'github.com:443 доступен'
        return $true
    } catch {
        Add-Result -Step 'Сеть' -State 'FAIL' -Detail "github.com недоступен: $($_.Exception.GetType().Name)"
        return $false
    }
}

# ---------------------------------------------------------------------------
# 2. Развёртывание пакета
# ---------------------------------------------------------------------------

function Test-ExistingInstall {
    param([string]$Dir)
    $marker = Join-Path $Dir 'pyproject.toml'
    if (-not (Test-Path -LiteralPath $marker)) { return $false }
    $text = Get-Content -LiteralPath $marker -Raw
    return ($text -match '(?m)^\s*name\s*=\s*"ragraf"')
}

function Deploy-FromGitHub {
    param([string]$Dir, [string]$RepoArg, [string]$RefArg)
    $url = "https://github.com/$RepoArg.git"
    Write-Note "git clone --depth 1 --branch $RefArg $url"
    $code = Invoke-Streamed -FilePath $script:Git -Arguments @('clone', '--depth', '1', '--branch', $RefArg, $url, $Dir)
    if ($code -ne 0) {
        Add-Result -Step 'Развёртывание (git)' -State 'FAIL' -Detail "git clone вышел с кодом $code"
        return $false
    }
    Add-Result -Step 'Развёртывание (git)' -State 'OK' -Detail "ветка $RefArg -> $Dir"
    return $true
}

function Deploy-FromArchive {
    param([string]$Dir, [string]$RepoArg, [string]$RefArg)
    $zip = Join-Path $script:WorkDir 'ragraf.zip'
    $urls = @(
        "https://codeload.github.com/$RepoArg/zip/refs/heads/$RefArg",
        "https://codeload.github.com/$RepoArg/zip/refs/tags/$RefArg"
    )
    $ok = $false
    foreach ($url in $urls) {
        try {
            Write-Note "скачиваю $url"
            # Параметра -TimeoutSec у Invoke-WebRequest в PowerShell 7.6 нет.
            # Проверено вызовом: (Get-Command Invoke-WebRequest).Parameters —
            # есть ConnectionTimeoutSeconds и OperationTimeoutSeconds.
            Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing `
                -ConnectionTimeoutSeconds 30 -OperationTimeoutSeconds 300
            $ok = $true
            break
        } catch {
            Write-Note "не вышло ($($_.Exception.GetType().Name)); пробую следующую форму адреса"
        }
    }
    if (-not $ok) {
        Add-Result -Step 'Развёртывание (архив)' -State 'FAIL' -Detail 'архив ветки и тега не скачался'
        return $false
    }
    $unpack = Join-Path $script:WorkDir 'unpack'
    if (Test-Path -LiteralPath $unpack) { Remove-Item -LiteralPath $unpack -Recurse -Force }
    Expand-Archive -LiteralPath $zip -DestinationPath $unpack -Force
    $inner = @(Get-ChildItem -LiteralPath $unpack -Directory)
    if ($inner.Count -ne 1) {
        Add-Result -Step 'Развёртывание (архив)' -State 'FAIL' -Detail "в архиве ожидался один корневой каталог, найдено $($inner.Count)"
        return $false
    }
    $null = New-Item -ItemType Directory -Force -Path $Dir
    Copy-Item -Path (Join-Path $inner[0].FullName '*') -Destination $Dir -Recurse -Force
    Add-Result -Step 'Развёртывание (архив)' -State 'OK' -Detail "ветка $RefArg -> $Dir"
    return $true
}

# Служебное и машинно-локальное при развёртывании не переносится: оно принадлежит
# машине-источнику, а не установке. Список закрытый и один на все проверки ниже.
$script:SkipDirNames = @('.git', '.ragraf', '.venv', 'venv', 'node_modules',
                         '__pycache__', '.pytest_cache', '.zwork', '.staging',
                         '.dev_agent', '.agents', '.tools', '.ort_gpu_probe',
                         'catalog', 'config', 'workspace')
$script:SkipFilePattern = '\.(pyc|pyo|log|tmp|out\.txt)$|\.bak($|\.|-)|^\.interpreter$'

function Test-DeployCandidate {
    param([string]$Relative, [string]$Leaf)
    foreach ($part in ($Relative -split '\\')) {
        if ($part -in $script:SkipDirNames) { return $false }
    }
    return ($Leaf -notmatch $script:SkipFilePattern)
}

function Deploy-FromLocal {
    param([string]$Dir, [string]$Src)
    if (-not (Test-Path -LiteralPath (Join-Path $Src 'pyproject.toml'))) {
        Add-Result -Step 'Развёртывание (локально)' -State 'FAIL' -Detail "в $Src нет pyproject.toml"
        return $false
    }
    $copied = 0
    foreach ($file in Get-ChildItem -LiteralPath $Src -Recurse -File -Force) {
        $rel = $file.FullName.Substring($Src.Length).TrimStart('\')
        if (-not (Test-DeployCandidate -Relative $rel -Leaf $file.Name)) { continue }
        $out = Join-Path $Dir $rel
        $null = New-Item -ItemType Directory -Force -Path (Split-Path $out -Parent)
        Copy-Item -LiteralPath $file.FullName -Destination $out -Force
        $copied++
    }
    if ($copied -eq 0) {
        Add-Result -Step 'Развёртывание (локально)' -State 'FAIL' -Detail 'нечего копировать'
        return $false
    }
    Add-Result -Step 'Развёртывание (локально)' -State 'OK' -Detail "из $Src, файлов $copied"
    return $true
}

function Test-Deployed {
    param([string]$Dir)
    $problems = @()
    if (-not (Test-Path -LiteralPath (Join-Path $Dir 'pyproject.toml'))) { $problems += 'нет pyproject.toml' }
    if (-not (Test-Path -LiteralPath (Join-Path $Dir 'src\ragraf\cli.py'))) { $problems += 'нет src/ragraf/cli.py' }
    if (-not (Test-Path -LiteralPath (Join-Path $Dir 'tools\ragraf.py'))) { $problems += 'нет tools/ragraf.py' }
    if (-not (Test-Path -LiteralPath (Join-Path $Dir 'tools\ragraf-admin.py'))) { $problems += 'нет tools/ragraf-admin.py' }
    if (-not (Test-Path -LiteralPath (Join-Path $Dir 'scripts\install.ps1'))) { $problems += 'нет scripts/install.ps1' }
    if (-not (Test-Path -LiteralPath (Join-Path $Dir 'LICENSE'))) { $problems += 'нет LICENSE' }
    if ($problems.Count -gt 0) {
        Add-Result -Step 'Состав установки' -State 'FAIL' -Detail ($problems -join '; ')
        return $false
    }
    $license = (Get-Content -LiteralPath (Join-Path $Dir 'LICENSE') -Raw)
    $licenseName = if ($license -match 'PolyForm Noncommercial') { 'PolyForm Noncommercial 1.0.0' }
                   elseif ($license -match 'MIT License') { 'MIT' } else { 'не опознана' }
    Add-Result -Step 'Состав установки' -State 'OK' -Detail "обязательные файлы на месте, лицензия: $licenseName"
    return $true
}

# ---------------------------------------------------------------------------
# 3. Окружение Python и зависимости
# ---------------------------------------------------------------------------

function Install-Dependencies {
    param([string]$Dir, [string]$Venv)
    $script = Join-Path $Dir 'scripts\install.ps1'
    $summary = Join-Path $script:WorkDir 'install-summary.json'
    $arguments = @('-NoLogo', '-NoProfile', '-File', $script,
                   '-Source', $Dir, '-Venv', $Venv, '-SummaryJson', $summary)
    if ($DependenciesOnly) { $arguments += '-DependenciesOnly' }
    if ($WithTruststore) { $arguments += '-WithTruststore' }
    Write-Note "запускаю штатный установщик продукта: $script"
    $code = Invoke-Streamed -FilePath 'pwsh' -Arguments $arguments
    if (Test-Path -LiteralPath $summary) {
        try {
            $s = Get-Content -LiteralPath $summary -Raw | ConvertFrom-Json
            if ($s.verdict) {
                Write-Note ("итог установщика: verdict=$($s.verdict), ошибок $($s.failed), предупреждений $($s.warned)")
            }
        } catch {
            Write-Note "итог установщика не разобран ($($_.Exception.GetType().Name)); смотрите журнал"
        }
    }
    if ($code -ne 0) {
        Add-Result -Step 'Окружение и зависимости' -State 'FAIL' -Detail "scripts/install.ps1 вышел с кодом $code"
        return $false
    }
    Add-Result -Step 'Окружение и зависимости' -State 'OK' -Detail "окружение: $Venv"
    return $true
}

function Test-Smoke {
    param([string]$Dir, [string]$Venv)
    $py = Join-Path $Venv 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $py)) {
        Add-Result -Step 'Контрольный запуск' -State 'FAIL' -Detail "в $Venv нет Scripts\python.exe"
        return $false
    }
    $probe = Invoke-Capture -FilePath $py -Arguments @((Join-Path $Dir 'tools\ragraf.py'), '--version')
    if ($probe.Code -ne 0) {
        Add-Result -Step 'Контрольный запуск' -State 'FAIL' -Detail "tools/ragraf.py --version, код $($probe.Code)"
        return $false
    }
    $first = @($probe.Text -split "`r?`n" | Where-Object { $_ })[0]
    Add-Result -Step 'Контрольный запуск' -State 'OK' -Detail $first
    return $true
}

# ---------------------------------------------------------------------------
# 4. Клиенты: AutoClaw и DeepSeek Harness
# ---------------------------------------------------------------------------

function Get-RagrafClientState {
    param([string]$Dir, [string]$PythonExe)
    if (-not $PythonExe.StartsWith($Dir, [StringComparison]::OrdinalIgnoreCase)) {
        Write-Note "окружение установки ещё не создано: значения получены внешним интерпретатором"
    }
    # Значения берутся у самого продукта, а не пересказываются здесь: иначе
    # установщик и панель со временем разошлись бы, и прав был бы неизвестно кто.
    $code = @"
import json, sys
sys.path.insert(0, r'$Dir\src')
from ragraf import client_setup as cs
print(json.dumps(cs.state(), ensure_ascii=False))
"@
    $probe = Invoke-Capture -FilePath $PythonExe -Arguments @('-c', $code)
    if ($probe.Code -ne 0) {
        Write-Note "не удалось получить значения у самого продукта: код $($probe.Code)"
        return $null
    }
    try {
        # Invoke-Capture отдаёт текст одной строкой; JSON разбирается целиком.
        return ($probe.Text | ConvertFrom-Json)
    } catch {
        Write-Note "значения продукта не разобраны ($($_.Exception.GetType().Name))"
        return $null
    }
}

function Find-AutoClaw {
    $hits = [System.Collections.Generic.List[string]]::new()
    foreach ($base in @($env:ProgramFiles, ${env:ProgramW6432})) {
        if (-not $base) { continue }
        $candidate = Join-Path $base 'AutoClaw'
        if (Test-Path -LiteralPath $candidate) { $hits.Add($candidate) }
    }
    foreach ($folder in 'LocalApplicationData', 'ApplicationData') {
        $base = [Environment]::GetFolderPath($folder)
        if (-not $base -or -not (Test-Path -LiteralPath $base)) { continue }
        foreach ($d in @(Get-ChildItem -LiteralPath $base -Directory -Force -ErrorAction SilentlyContinue |
                         Where-Object { $_.Name -like 'AutoClaw*' -or $_.Name -eq 'autoclaw' })) {
            $hits.Add($d.FullName)
        }
    }
    return @($hits | Sort-Object -Unique)
}

function Find-Harness {
    $dshHome = if ($env:DSH_HOME) { $env:DSH_HOME }
               else { Join-Path ([Environment]::GetFolderPath('UserProfile')) '.dsh' }
    $patch = Join-Path $dshHome 'profiles\desktop\cordis.patch.yml'
    return [pscustomobject]@{
        Home = $dshHome
        Patch = $patch
        HomeExists = (Test-Path -LiteralPath $dshHome)
        PatchExists = (Test-Path -LiteralPath $patch)
    }
}

function Set-AutoClawClient {
    param([string]$Dir, [string]$PythonExe, [string[]]$Found)
    Write-Host ''
    Write-Host '  AutoClaw' -ForegroundColor Cyan
    if ($Found.Count -eq 0) {
        Add-Result -Step 'AutoClaw' -State 'SKIP' -Detail 'установка не найдена'
        Write-Note 'Если AutoClaw поставлен в нестандартный каталог — настройте подключение вручную:'
    } else {
        Add-Result -Step 'AutoClaw' -State 'OK' -Detail ("найден: " + ($Found -join '; '))
    }

    $state = Get-RagrafClientState -Dir $Dir -PythonExe $PythonExe
    if (-not $state) {
        Write-Note 'Точные значения не получены: откройте панель, раздел «Клиенты», там они выдаются продуктом.'
        return $false
    }

    Write-Host '  Значения для формы «Add tool» (Settings → Tools & connections → Add tool):' -ForegroundColor Gray
    foreach ($field in $state.autoclaw.fields) {
        Write-Host ("    {0,-18} {1}" -f ($field.label + ':'), $field.value)
    }
    Write-Host ("    {0,-18} {1}" -f 'Значение доступа:', 'выдаётся в панели, раздел «Доступ» (показывается один раз)')
    Write-Host '    Альтернатива — тип «Local program»:' -ForegroundColor Gray
    $local = @($state.autoclaw.variants | Where-Object { $_.type -eq 'Local program' })[0]
    if ($local) {
        foreach ($field in $local.fields) {
            Write-Host ("      {0,-20} {1}" -f ($field.label + ':'), $field.value)
        }
        Write-Note 'Переменная с конфигом корпуса указывает на файл, которого в свежей установке ещё'
        Write-Note 'нет: он появится, когда вы подключите свой проект (панель → «Индексация»),'
        Write-Note 'и в форму надо подставить именно его путь.'
    }
    Write-Host '  Почему установщик не прописывает это сам:' -ForegroundColor Yellow
    Write-Note 'AutoClaw принимает MCP-серверы только через свой интерфейс — файла конфигурации'
    Write-Note 'для них нет, а хранилище (runtime.sqlite) принадлежит продукту: запись в него в'
    Write-Note 'обход интерфейса обошла бы его учёт прав. Значения выше — верные, вводятся вручную.'
    Write-Note 'Установщик не выдумывает значения: они получены у самого RAGRAF (client_setup.state()).'
    # Возвращается «значения показаны», а не «настройки записаны»: записать их
    # нельзя, и выдавать одно за другое нельзя тем более.
    return $true
}

function Set-HarnessClient {
    param([string]$Dir, [string]$PythonExe)
    Write-Host ''
    Write-Host '  DeepSeek Harness' -ForegroundColor Cyan
    $harness = Find-Harness
    if (-not $harness.PatchExists) {
        $detail = if ($harness.HomeExists) { "профиль $($harness.Home) есть, файла cordis.patch.yml нет" }
                  else { "каталог $($harness.Home) не найден" }
        Add-Result -Step 'DeepSeek Harness' -State 'SKIP' -Detail $detail
        return $false
    }
    Add-Result -Step 'DeepSeek Harness' -State 'OK' -Detail "профиль: $($harness.Patch)"

    $transport = Read-Choice -Question 'Какой транспорт подключить?' `
                             -Choices @('stdio', 'streamable-http') -Default 'stdio'
    $answer = Read-Choice -Question 'Добавить (или заменить) блок RAGRAF в профиле Harness?' `
                          -Choices @('да', 'нет') -Default 'нет'
    if ($answer -ne 'да') {
        Add-Result -Step 'Harness: блок профиля' -State 'SKIP' -Detail 'пользователь отказался'
        Write-Note 'Блок можно добавить позже из панели: раздел «Клиенты» → «Настроить Harness».'
        return $false
    }

    $code = @"
import json, sys
sys.path.insert(0, r'$Dir\src')
from ragraf import client_setup as cs
print(json.dumps(cs.harness_apply('$transport'), ensure_ascii=False))
"@
    $probe = Invoke-Capture -FilePath $PythonExe -Arguments @('-c', $code)
    if ($probe.Code -ne 0) {
        Add-Result -Step 'Harness: блок профиля' -State 'FAIL' -Detail "вызов вышел с кодом $($probe.Code)"
        return $false
    }
    $result = $null
    try { $result = $probe.Text | ConvertFrom-Json } catch {
        Add-Result -Step 'Harness: блок профиля' -State 'FAIL' -Detail "ответ не разобран ($($_.Exception.GetType().Name))"
        return $false
    }
    if ($result.ok) {
        Add-Result -Step 'Harness: блок профиля' -State 'OK' -Detail "транспорт $transport, резервная копия: $(Split-Path $result.backup -Leaf)"
        Write-Note 'Профиль проверен разбором после записи. Приложение увидит блок после перезапуска Harness.'
        return $true
    }
    $reverted = if ($result.reverted) { '; профиль возвращён к прежнему виду' } else { '' }
    Add-Result -Step 'Harness: блок профиля' -State 'FAIL' -Detail "$($result.error)$reverted"
    return $false
}

# ---------------------------------------------------------------------------
# 5. Что делать дальше
# ---------------------------------------------------------------------------

function Write-NextSteps {
    param([string]$Dir, [string]$Venv, [bool]$AutoClawValuesShown, [bool]$HarnessConfigured)
    $py = Join-Path $Venv 'Scripts\python.exe'
    $panel = Join-Path $Dir 'tools\ragraf-admin.py'

    Write-Head '6. Запуск панели управления'
    Write-Host '  Панель — это единственное место, где выставляются настройки продукта:'
    Write-Host '  пороги переиндексации, видеокарта, доступ, базы данных, подключение клиентов.'
    Write-Host ''
    Write-Host '  Запуск панели:' -ForegroundColor White
    Write-Host ("    `"{0}`" `"{1}`"" -f $py, $panel) -ForegroundColor Green
    Write-Host ''
    Write-Note 'Панель откроет браузер сама. В консоли печатается адрес и значение доступа.'
    Write-Note 'Адрес по умолчанию: http://127.0.0.1:8777 (если порт занят — берётся следующий свободный)'
    Write-Note ('Значение доступа хранится в файле: ' + (Join-Path $Dir '.ragraf\admin\session_key'))
    Write-Note 'Остановка панели: Ctrl+C в том окне, из которого она запущена.'

    Write-Host ''
    Write-Host '  ПРЕДУПРЕЖДЕНИЕ О ДОСТУПЕ' -ForegroundColor Yellow
    Write-Note 'Панель слушает только адрес 127.0.0.1: доступа из сети у неё нет по замыслу, и это'
    Write-Note 'не настройка, а устройство — сетевой порт не открывается вовсе.'
    Write-Note 'Но значение доступа печатается в консоль и лежит в файле, поэтому доступ к панели'
    Write-Note 'ограничивается внутри неё, раздел «Доступ»:'
    Write-Note '  • выпустите новое значение — повторный запуск с ключом --new-token отменяет прежнее;'
    Write-Note '  • задайте пароль, если за панелью должен работать человек;'
    Write-Note '  • посмотрите выданные значения и отзовите лишние.'
    Write-Note 'HTTP-сервис MCP (порт 8765) слушает тот же адрес и при обращении не с localhost'
    Write-Note 'требует значение доступа; без него он отказывает — это проверено в коде.'

    Write-Host ''
    Write-Host '  Дальнейшие шаги' -ForegroundColor White
    Write-Host '    1) Подключить свой проект — из панели, раздел «Индексация», либо командой:'
    Write-Host ("       `"{0}`" `"{1}`" ensure <каталог проекта>" -f $py, (Join-Path $Dir 'tools\ragraf.py')) -ForegroundColor Green
    Write-Host '    2) Проверить окружение продукта (отчёт по компонентам; команда всегда возвращает 0,'
    Write-Host '       это не гейт — читайте сам отчёт):'
    Write-Host ("       `"{0}`" `"{1}`" -p <конфиг> selftest" -f $py, (Join-Path $Dir 'tools\ragraf.py')) -ForegroundColor Green
    Write-Host '    3) В панели выставить остальные параметры: видеокарта, пороги, схлопывание дублей,'
    Write-Host '       исключения, подключение баз данных.'
    Write-Host '    4) Граф свойств кода (операция slice) требует Java и Joern — их ставит отдельный'
    Write-Host ('       скрипт: ' + (Join-Path $Dir 'scripts\install-joern.ps1')) -ForegroundColor Gray

    Write-Host ''
    Write-Host '  Документация' -ForegroundColor White
    Write-Note ('  ' + (Join-Path $Dir 'README.md') + ' — установка, первые шаги, требования')
    Write-Note ('  ' + (Join-Path $Dir 'docs\ru\02_architecture\ARCHITECTURE.md') + ' — устройство')
    Write-Note ('  ' + (Join-Path $Dir 'docs\ru\03_implementation\INTEGRATION.md') + ' — подключение к агентам')
    Write-Note ('  ' + (Join-Path $Dir 'CHANGELOG.md') + ' — что менялось')

    Write-Host ''
    Write-Host '  Удаление установки' -ForegroundColor White
    Write-Note ("Каталог $Dir и каталог окружения $Venv удаляются целиком.")
    Write-Note 'Индексы корпусов лежат не здесь, а в каталогах .ragraf проиндексированных проектов.'

    if ($AutoClawValuesShown) {
        Write-Host ''
        Write-Note 'AutoClaw: значения для подключения напечатаны выше — вводятся в его интерфейсе.'
    }
    if ($HarnessConfigured) {
        Write-Host ''
        Write-Note 'Harness: блок добавлен в профиль. Перезапустите Harness, иначе он его не прочитает.'
    }
}

# ---------------------------------------------------------------------------
# Ход установки
# ---------------------------------------------------------------------------

Start-Journal

Write-Head 'RAGRAF: установка'
Write-Host '  Продукт: графово-векторный индекс кода для LLM-агентов.'
Write-Host ('  Источник: ' + $(if ($SourceDir) { "локальная копия $SourceDir" } else { "https://github.com/$Repo (ветка $Ref)" }))
Write-Host '  Установщик ничего не делает молча: каждый шаг заканчивается проверкой.'

$script:Interactive = Test-CanPrompt
if (-not $script:Interactive) {
    Write-Host '  Диалог недоступен (нет консоли для ввода) — значения по умолчанию приняты автоматически.' -ForegroundColor Gray
}

if (-not (Test-Environment)) {
    Write-Summary
    Stop-Journal
    exit $script:ExitEnvironment
}

Write-Head '2. Каталог установки'
if (-not $InstallDir) {
    $InstallDir = Read-Answer -Question 'Куда развернуть RAGRAF?' -Default 'C:\RAGRAF'
}
try {
    $InstallDir = [IO.Path]::GetFullPath($InstallDir)
} catch {
    Write-Summary
    Stop-Journal
    exit $script:ExitUsage
}
if (-not $VenvDir) { $VenvDir = Join-Path $InstallDir '.venv' }
Write-Host "  Каталог установки: $InstallDir"
Write-Host "  Окружение:         $VenvDir"

if (-not (Test-TargetDisk -Path $InstallDir)) {
    Write-Summary
    Stop-Journal
    exit $script:ExitEnvironment
}
if (-not (Test-GitHubReachable)) {
    Add-Result -Step 'Установка' -State 'FAIL' -Detail 'нет доступа к источнику пакета'
    Write-Note 'Если есть локальная копия пакета, укажите её: -SourceDir <путь>'
    Write-Summary
    Stop-Journal
    exit $script:ExitEnvironment
}

Write-Head '3. Развёртывание пакета'
$existing = Test-ExistingInstall -Dir $InstallDir
$deployed = $false
if ($existing) {
    Add-Result -Step 'Найдена установка' -State 'WARN' -Detail $InstallDir
    $answer = Read-Choice -Question 'Обновить её поверх?' -Choices @('да', 'нет') -Default 'да'
    if ($answer -ne 'да') {
        Write-Note 'Установка остановлена по решению пользователя: каталог занят.'
        Write-Summary
        Stop-Journal
        exit $script:ExitOk
    }
    $dotGit = Test-Path -LiteralPath (Join-Path $InstallDir '.git')
    if ($dotGit -and $script:Git) {
        Write-Note 'обновляю рабочим деревом git (каталог .ragraf не затрагивается)'
        $code = Invoke-Streamed -FilePath $script:Git -Arguments @('-C', $InstallDir, 'pull', '--ff-only')
        if ($code -eq 0) {
            Add-Result -Step 'Обновление (git pull)' -State 'OK' -Detail 'ветка обновлена'
            $deployed = $true
        } else {
            Add-Result -Step 'Обновление (git pull)' -State 'WARN' -Detail "код $code — разворачиваю пакет заново"
        }
    }
} else {
    $null = New-Item -ItemType Directory -Force -Path $InstallDir
}

if (-not $deployed) {
    if ($SourceDir) {
        $deployed = Deploy-FromLocal -Dir $InstallDir -Src $SourceDir
    } elseif ($script:Git) {
        $deployed = Deploy-FromGitHub -Dir $InstallDir -RepoArg $Repo -RefArg $Ref
        if (-not $deployed) {
            Write-Note 'git не справился — перехожу на архив ветки'
            $deployed = Deploy-FromArchive -Dir $InstallDir -RepoArg $Repo -RefArg $Ref
        }
    } else {
        $deployed = Deploy-FromArchive -Dir $InstallDir -RepoArg $Repo -RefArg $Ref
    }
}
if (-not $deployed) { Fail-Install -Reason 'пакет не развёрнут' }
if (-not (Test-Deployed -Dir $InstallDir)) { Fail-Install -Reason 'состав установки неполный' }

Write-Head '4. Окружение и зависимости'
if ($SkipDeps) {
    Add-Result -Step 'Окружение и зависимости' -State 'SKIP' -Detail 'режим репетиции (-SkipDeps)'
    $py = $script:Python
} else {
    if (-not (Install-Dependencies -Dir $InstallDir -Venv $VenvDir)) {
        Fail-Install -Reason 'окружение не создано — смотрите журнал установки'
    }
    if (-not (Test-Smoke -Dir $InstallDir -Venv $VenvDir)) {
        Fail-Install -Reason 'пакет не запускается в созданном окружении'
    }
    $py = Join-Path $VenvDir 'Scripts\python.exe'
}

$autoClawValuesShown = $false
$harnessApplied = $false
if ($SkipClients) {
    Write-Head 'Клиенты'
    Add-Result -Step 'Клиенты' -State 'SKIP' -Detail 'пропущено по аргументу -SkipClients'
} else {
    Write-Head '5. Подключение клиентов'
    Write-Host '  Продукт работает и без клиентов. Ниже спрашивается, искать ли их и что с ними делать.'
    Write-Host ''
    $askAuto = Read-Choice -Question 'Искать AutoClaw и подготовить настройки подключения?' `
                           -Choices @('да', 'нет') -Default 'да'
    if ($askAuto -eq 'да') {
        $autoClawValuesShown = Set-AutoClawClient -Dir $InstallDir -PythonExe $py -Found (Find-AutoClaw)
    } else {
        Add-Result -Step 'AutoClaw' -State 'SKIP' -Detail 'по решению пользователя'
    }
    $askHarness = Read-Choice -Question 'Искать DeepSeek Harness и добавить в него блок RAGRAF?' `
                              -Choices @('да', 'нет') -Default 'да'
    if ($askHarness -eq 'да') {
        $harnessApplied = Set-HarnessClient -Dir $InstallDir -PythonExe $py
    } else {
        Add-Result -Step 'DeepSeek Harness' -State 'SKIP' -Detail 'по решению пользователя'
    }
}

Write-NextSteps -Dir $InstallDir -Venv $VenvDir `
                -AutoClawValuesShown $autoClawValuesShown -HarnessConfigured $harnessApplied

Write-Summary
Stop-Journal

if (Test-Path -LiteralPath $script:WorkDir) {
    Remove-Item -LiteralPath $script:WorkDir -Recurse -Force -ErrorAction SilentlyContinue
}

if ($script:Failed -gt 0) { exit $script:ExitError }
exit $script:ExitOk
