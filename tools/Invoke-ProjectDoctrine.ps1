#Requires -Version 7.6
#Requires -PSEdition Core

<#
.SYNOPSIS
    Чекер конвенций проекта RAGRAF.

.DESCRIPTION
    Проверяет то, что в этом проекте поддаётся объективной проверке и что уже
    ломалось или почти сломалось. Правила выведены из кода; каждое опирается на
    конкретный файл и, где возможно, на измеренное значение.

    Только чтение: скрипт ничего не меняет в проекте и не запускает код проекта.
    Он читает файлы и сравнивает их с инвариантами.

    ВАЖНО про замысел. Самая опасная проверка — не та, что упала, а та, что
    позеленела, ничего не осмотрев. Поэтому у каждой проверки есть -Examined, и
    результат Ok=$true при нулевом осмотре считается ложным PASS и валит прогон.

    Ожидаемый baseline на момент передачи проекта: 0 ошибок, 0 предупреждений,
    14 проверок. Прежние две ошибки («запись прогона в реестр не покрывала путь
    ensure» и «документы называли 18 и 16 инструментов там, где в коде 23») закрыты
    9 октября 2026; на каждую есть регрессионная защита. Правило чтения прогона:
    любая ошибка или новое предупреждение — находка. Ожидаемого красного
    состояния у чекера больше нет: если он падает — это либо новый дефект,
    либо сломанное исправление.

.PARAMETER ProjectRoot
    Корень проекта. По умолчанию — каталог двумя уровнями выше скрипта, то есть
    корректно, когда скрипт лежит в каталоге инструментов проекта.

.PARAMETER MaxExamples
    Сколько примеров печатать на проваленную проверку. По умолчанию 10.

.PARAMETER Strict
    Считать предупреждения ошибками.

.EXAMPLE
    pwsh -NoLogo -NoProfile -NonInteractive -File .\Invoke-ProjectDoctrine.ps1

.EXAMPLE
    pwsh -NoLogo -NoProfile -NonInteractive -File .\Invoke-ProjectDoctrine.ps1 -Strict
#>

[CmdletBinding()]
param(
    [string] $ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [int] $MaxExamples = 10,
    [switch] $Strict
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
if (-not (Test-Path -LiteralPath $script:ProjectRoot -PathType Container)) {
    throw "Project root not found: $script:ProjectRoot"
}
$script:MaxExamples = $MaxExamples

# --------------------------------------------------------------- exclusions

# Деревья, которые не являются кодом проекта. `.zwork` и `.dev_agent` хранят
# снимки прежних прогонов агента, включая устаревшие копии самого этого файла:
# чтение их даёт находки о коде, которого уже нет.
$script:CodeExclusionPattern = '\\(?:\.git|\.zwork|\.dev_agent|\.venv|\.tools|\.ort_gpu_probe|\.staging|node_modules|vendor|bower_components|__pycache__|workspace)\\|\\site-packages\\'

# Секретоносные имена ищутся с УЗКИМ исключением: такой файл может лежать и в
# дереве зависимостей, поэтому широкое исключение ослепило бы проверку.
# Исключены только служебные каталоги агента и системы контроля версий.
$script:SecretExclusionPattern = '\\(?:\.git|\.zwork|\.dev_agent)\\'

# --------------------------------------------------------------- framework

function Test-RegexLiteral {
    <#
    .SYNOPSIS
        Собирается ли шаблон вообще.

    .DESCRIPTION
        Неверный шаблон падает не там, где это видно: внутри -notmatch он
        возбуждает исключение, и небрежно написанная проверка превращает его в
        PASS. Каждый шаблон проверяется до использования.
    #>
    param([Parameter(Mandatory)][string] $Pattern)

    try { $null = [regex]::new($Pattern); return $true }
    catch { return $false }
}

function Assert-RegexLiteral {
    param(
        [Parameter(Mandatory)][string] $Pattern,
        [Parameter(Mandatory)][string] $What
    )

    if (Test-RegexLiteral -Pattern $Pattern) { return }
    throw "invalid regex for ${What}: $Pattern"
}

function Get-RelativePath {
    param([Parameter(Mandatory)][string] $FullPath)
    return ([System.IO.Path]::GetRelativePath($script:ProjectRoot, $FullPath)) -replace '\\', '/'
}

function Get-RepositoryFile {
    <#
    .SYNOPSIS
        Файлы проекта с исключением служебных и третьесторонних деревьев.
    #>
    param(
        [string[]] $Extension = @(),
        [switch] $IncludeHidden,
        [switch] $SecretScan
    )

    $pattern = $script:CodeExclusionPattern
    if ($SecretScan) { $pattern = $script:SecretExclusionPattern }
    Assert-RegexLiteral -Pattern $pattern -What 'repository file exclusion'

    $items = Get-ChildItem -LiteralPath $script:ProjectRoot -Recurse -File -Force:$IncludeHidden -ErrorAction SilentlyContinue
    $result = [System.Collections.Generic.List[System.IO.FileInfo]]::new()

    foreach ($file in $items) {
        if ($file.FullName -match $pattern) { continue }
        if ($Extension.Count -gt 0 -and $Extension -notcontains $file.Extension.ToLowerInvariant()) { continue }
        $result.Add($file)
    }

    return $result
}

function Get-ProjectText {
    param([Parameter(Mandatory)][string] $Path)
    return [System.IO.File]::ReadAllText($Path)
}

function New-CheckResult {
    <#
    .SYNOPSIS
        Итог одной проверки вместе с размером её доказательства.

    .PARAMETER Examined
        Сколько наблюдений сделала проверка (прочитано файлов, сравнено записей).
        Обязателен для успешной проверки: зелёный результат при нулевом осмотре
        считается ложным PASS и валит прогон.

    .PARAMETER NotApplicable
        Причина, по которой проверка к проекту неприменима. Печатается как [N/A]
        и не считается ошибкой. Это информация, а не заработанный PASS.
    #>
    param(
        [Parameter(Mandatory)][string] $Name,
        [Parameter(Mandatory)][bool] $Ok,
        [int] $Examined = -1,
        [string[]] $Details = @(),
        [switch] $Warning,
        [string] $NotApplicable
    )

    $detailList = [System.Collections.Generic.List[string]]::new()
    foreach ($detail in $Details) { $detailList.Add($detail) }

    $kind = 'error'
    if ($NotApplicable) { $kind = 'na' }
    elseif ($Ok) {

        if ($Examined -le 0) {
            # Дефект, ради которого написан весь этот каркас.
            $Ok = $false
            $kind = 'void'
            $detailList.Insert(0, 'FALSE PASS: проверка сообщила Ok, но не осмотрела ни одного объекта (-Examined = ' + $Examined + ')')
        }
        else { $kind = 'pass' }
    }
    elseif ($Warning) { $kind = 'warn' }

    [pscustomobject]@{
        Name     = $Name
        Ok       = $Ok
        Examined = $Examined
        Details  = @($detailList)
        Warning  = [bool]$Warning
        Kind     = $kind
    }
}

function Write-CheckResult {
    param([Parameter(Mandatory)][pscustomobject] $Check)

    $label = switch ($Check.Kind) {
        'pass' { '[PASS]' }
        'na'   { '[N/A] ' }
        'warn' { '[WARN]' }
        default { '[FAIL]' }
    }
    $color = switch ($Check.Kind) {
        'pass' { 'Green' }
        'na'   { 'DarkGray' }
        'warn' { 'Yellow' }
        default { 'Red' }
    }

    $suffix = ''
    if ($Check.Examined -ge 0) { $suffix = "  (examined $($Check.Examined))" }

    $padding = 0
    foreach ($c in $script:CheckNames) { if ($c.Length -gt $padding) { $padding = $c.Length } }

    Write-Host ($label + ' ' + $Check.Name.PadRight($padding) + $suffix) -ForegroundColor $color

    $shown = @($Check.Details | Select-Object -First $script:MaxExamples)
    foreach ($detail in $shown) { Write-Host "         $detail" }
    if ($Check.Details.Count -gt $shown.Count) {
        Write-Host "         ... и ещё $($Check.Details.Count - $shown.Count)"
    }
}

# -------------------------------------------------- стартовая самопроверка

foreach ($guard in @(
        @{ Pattern = $script:CodeExclusionPattern; What = 'code exclusions' },
        @{ Pattern = $script:SecretExclusionPattern; What = 'secret exclusions' })) {
    Assert-RegexLiteral -Pattern $guard.Pattern -What $guard.What
}

# ------------------------------------------------------------------ проверки

function Test-ProjectLayout {
    <#
    .SYNOPSIS
        Репозиторий всё ещё тот проект, который описан в паспорте.

    .DESCRIPTION
        Защита от запуска старого чекера на переехавшем дереве: иначе прогон
        рапортует чисто, осмотрев пустоту.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0

    $expected = @(
        'pyproject.toml', 'requirements.txt', 'README.md',
        'src/ragraf/cli.py', 'src/ragraf/facade.py', 'src/ragraf/mcp_server.py',
        'src/ragraf/indexer.py', 'src/ragraf/corpus.py', 'src/ragraf/config.py',
        'src/ragraf/watch.py', 'src/ragraf/catalog.py', 'src/ragraf/store.py',
        'src/ragraf/dedupe.py', 'src/ragraf/ensure.py', 'src/ragraf/revisions.py',
        'src/ragraf/extract/generic.py',
        'tools/ragraf.py', 'tools/ragraf-mcp.py', 'tools/ragraf-admin.py',
        'config/ragraf.yaml',
        'scripts/ragraf.ps1', 'scripts/install-joern.ps1',
        'src/ragraf/formats.py', 'src/ragraf/documents.py',
        'src/ragraf/officebinary.py',
        'tests/test_formats.py', 'tests/test_new_languages.py',
        'tests/test_office_binary.py', 'tools/check_formats_panel.py',
        'tests/test_ensure.py', 'tests/test_dedupe.py', 'tests/test_lang_routing.py',
        'docs/02_architecture/TZ.md', 'docs/02_architecture/ACCEPTANCE.md'
    )

    foreach ($relative in $expected) {
        $examined++
        if (-not (Test-Path -LiteralPath (Join-Path $script:ProjectRoot $relative) -PathType Leaf)) {
            $problems.Add("missing: $relative")
        }
    }

    return New-CheckResult -Name 'repository layout matches the described project' -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Test-SecretHygiene {
    <#
    .SYNOPSIS
        Секретоносные имена не появились сверх записанного baseline.

    .DESCRIPTION
        Файлы не открываются: читается только имя. Классы, существовавшие при
        передаче проекта, записаны в baseline, иначе каждая известная запись
        печаталась бы как новая находка, и проверка стала бы шумом.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0

    $patterns = @(
        '^\.env$', '^\.env\.(?!example$|sample$|template$)',
        '\.key$', '^\.config\.php$', '^\.(htpasswd|httpauth)$',
        '^wp-config\.php$', '^id_(rsa|dsa|ecdsa|ed25519)',
        '\.(pem|pfx|p12|jks|keystore|ppk)$',
        '^\.(netrc|_netrc|git-credentials|npmrc|kdbx|ovpn)$',
        '\.sql\.php$'
    )
    $benign = @('cacert\.pem$', 'ca-bundle\.', '\.crt$', '\.min\.js$')

    foreach ($pattern in ($patterns + $benign)) {
        Assert-RegexLiteral -Pattern $pattern -What 'secret hygiene'
    }

    # Измерено при передаче: секретоносных имён внутри собственных деревьев
    # проекта (src, tools, tests, config, docs, scripts) нет. Найдены лишь
    # материалы прогонов и выгрузки в исключённых служебных каталогах, а они
    # этой проверкой не осматриваются.
    # Файл ключа хранилища секретов — ЕДИНСТВЕННОЕ законное исключение:
    # он обязан существовать, лежит вне корпуса (`catalog/**`), права
    # сужены до текущей учётной записи, в git не попадает. Значение ключа
    # не читается ни этой проверкой, ни индексатором.
    $baseline = @('catalog/secrets.key')

    $scan = Get-ProjectSourceFile
    $examined = $scan.Examined

    foreach ($file in $scan.Files) {
        $relative = Get-RelativePath $file.FullName

        $skip = $false
        foreach ($pattern in $benign) { if ($file.Name -match $pattern) { $skip = $true; break } }
        if ($skip) { continue }

        $matched = $false
        foreach ($pattern in $patterns) { if ($file.Name -match $pattern) { $matched = $true; break } }
        if (-not $matched) { continue }

        $known = $false
        foreach ($entry in $baseline) { if ($relative -like $entry) { $known = $true; break } }
        if ($known) { continue }

        $problems.Add("secret-shaped and not in the recorded baseline: $relative")
    }

    return New-CheckResult -Name 'secret-shaped files stayed within the baseline' -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems -Warning:($problems.Count -gt 0)
}

function Test-RegistryCoverage {
    <#
    .SYNOPSIS
        Каждый путь, запускающий индексатор, записывает результат в реестр.

    .DESCRIPTION
        Инвариант 11 из AGENTS.md. Измеренный дефект: ensure регистрировал корпус
        до индексации и после неё реестр не обновлял, поэтому запись оставалась с
        пустой ревизией и нулём узлов при фактически построенном индексе.

        Правило проверяет инвариант, а не написание вызова: запись считается
        выполненной, если модуль обращается к общей точке записи прогона —
        напрямую к `record_run` или через обёртку `record_index_run`. Первая
        редакция этого правила искала только буквальное `record_run`, и после
        вынесения общей точки записи она объявила исправленный код дефектным.
        Проверка, зависящая от написания, — ложная проверка.

        Второй измеренный дефект того же рода (13.10.2026): проверка считала
        запись по ФАЙЛУ `cli.py` целиком, а в нём две команды, запускающие
        индексатор — `index` и `reindex`. Первая записывала прогон, вторая —
        нет, и одного упоминания в файле хватало, чтобы правило позеленело.
        Замерено на собственном корпусе: в индексе rev-0046, в реестре rev-0045
        со старой суммой; `check` из-за этого честно реиндексировал корпус
        заново, а `catalog` показывал прошедшее состояние. Поэтому теперь
        просматриваются все функции-обработчики команд, и в каждой, где
        создаётся `Indexer`, требуется вызов общей точки записи.

        Дополнительно проверяется несущее свойство самой общей точки: пустая
        ревизия не должна затирать записанное состояние разработки.
        #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0

    $paths = @(
        @{ Relative = 'src/ragraf/watch.py';  What = 'сторож' },
        @{ Relative = 'src/ragraf/cli.py';    What = 'ручная индексация из CLI' },
        @{ Relative = 'src/ragraf/ensure.py'; What = 'заведение и актуализация корпуса' }
    )

    # Общая точка записи обязана существовать: без неё пути снова разойдутся.
    $watchPath = Join-Path $script:ProjectRoot 'src/ragraf/watch.py'
    if (Test-Path -LiteralPath $watchPath) {
        $examined++
        $watchText = Get-ProjectText $watchPath
        if ($watchText -notmatch 'def\s+record_index_run\s*\(') {
            $problems.Add('в src/ragraf/watch.py нет общей точки записи record_index_run: пути индексации снова начнут расходиться')
        }
        if ($watchText -notmatch 'record_run\s*\(') {
            $problems.Add('в src/ragraf/watch.py нет вызова catalog.record_run: реестр не получит результат прогона')
        }
        # Пустая ревизия не записывается: иначе неудачный прогон затёр бы
        # записанное состояние разработки.
        $helperBlock = [regex]::Match($watchText, '(?s)def\s+record_index_run\s*\(.*?(?=\ndef |\Z)')
        if ($helperBlock.Success -and $helperBlock.Value -notmatch 'if\s+not\s+rev_id') {
            $problems.Add('общая точка записи принимает пустую ревизию: неудачный прогон затрёт состояние разработки в реестре')
        }
    }

    foreach ($entry in $paths) {
        $path = Join-Path $script:ProjectRoot $entry.Relative
        if (-not (Test-Path -LiteralPath $path)) { continue }
        $examined++
        $text = Get-ProjectText $path
        if ($text -notmatch 'record_run\s*\(' -and $text -notmatch 'record_index_run\s*\(') {
            $problems.Add("$($entry.Relative) ($($entry.What)) не записывает прогон в реестр: реестр покажет пустую ревизию и нули")
        }
    }

    # Модуль, запускающий индексатор, обязан записывать прогон. Проверка идёт
    # ПО ФУНКЦИЯМ, а не по файлу: в cli.py таких функций две (`index` и
    # `reindex`), и упоминание в одной из них прикрывало отсутствие в другой.
    $cliPath = Join-Path $script:ProjectRoot 'src/ragraf/cli.py'
    if (Test-Path -LiteralPath $cliPath) {
        $examined++
        $cliText = Get-ProjectText $cliPath
        foreach ($match in [regex]::Matches($cliText, '(?ms)^def\s+([a-z_]+)\s*\(.*?(?=^def |\Z)')) {
            $funcName = $match.Groups[1].Value
            $body = $match.Value
            if ($body -notmatch 'Indexer\s*\(') { continue }
            $examined++
            $writesRun = ($body -match 'record_run\s*\(') -or ($body -match 'record_index_run\s*\(')
            if (-not $writesRun) {
                $problems.Add("src/ragraf/cli.py: функция $funcName запускает Indexer, но не записывает прогон в реестр — реестр расходится с индексом (измерено: в индексе rev-0046, в реестре rev-0045)")
            }
        }
    }

    $ensurePath = Join-Path $script:ProjectRoot 'src/ragraf/ensure.py'
    if (Test-Path -LiteralPath $ensurePath) {
        $examined++
        $ensureText = Get-ProjectText $ensurePath
        $runsIndexer = $ensureText -match 'Indexer\s*\('
        $writesRegistry = ($ensureText -match 'record_run\s*\(') -or ($ensureText -match 'record_index_run\s*\(')
        if ($runsIndexer -and -not $writesRegistry) {
            $problems.Add('src/ragraf/ensure.py запускает Indexer, но не записывает прогон в реестр (измеренный дефект: rev_id пустой, node_count=0 при 1855 узлах в индексе)')
        }
    }

    return New-CheckResult -Name 'every indexer path records the run in the registry' -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}
function Test-ExcludeInvariants {
    <#
    .SYNOPSIS
        Несущие инварианты исключений и сопоставления шаблонов на месте.

    .DESCRIPTION
        Три инварианта, каждый из которых уже был предметом разбора:
        самоисключение служебного каталога индекса, сложение базового списка с
        конфигом вместо замены, регистрозависимость сопоставления без флага
        игнорирования регистра.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0

    $configPath = Join-Path $script:ProjectRoot 'src/ragraf/config.py'
    $utilPath = Join-Path $script:ProjectRoot 'src/ragraf/util.py'

    if (Test-Path -LiteralPath $configPath) {
        $examined++
        $configText = Get-ProjectText $configPath
        if (-not $configText.Contains('"**/.ragraf/**"')) {
            $problems.Add('в src/ragraf/config.py нет шаблона самоисключения каталога индекса: индексация начнёт читать собственные базы')
        }
        if ($configText -notmatch 'list\(DEFAULT_EXCLUDES\)\s*\+') {
            $problems.Add('в src/ragraf/config.py исключения конфига больше не складываются с базовым списком: служебные защиты потеряны')
        }
    }

    if (Test-Path -LiteralPath $utilPath) {
        $examined++
        $utilText = Get-ProjectText $utilPath
        if (-not ($utilText -match 'def compile_glob' -or $utilText -match '_translate')) {
            $problems.Add('в src/ragraf/util.py нет транслятора шаблонов в регулярное выражение')
        }
        # Сопоставление обязано оставаться регистрозависимым: рабочий код в
        # каталогах с заглавной буквы иначе выпадет из индекса.
        $compileBlock = [regex]::Match($utilText, '(?s)def compile_glob.*?(?=\ndef |\Z)')
        if ($compileBlock.Success -and $compileBlock.Value -match 'IGNORECASE') {
            $problems.Add('сопоставление шаблонов в src/ragraf/util.py стало нечувствительным к регистру: код в каталогах вида src/Cache/ выпадет из индекса')
        }
    }

    return New-CheckResult -Name 'exclusion and glob invariants hold' -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Test-DedupeDeterminism {
    <#
    .SYNOPSIS
        Схлопывание дублей остаётся детерминированным и живёт в сканере.

    .DESCRIPTION
        Инварианты 4 и 5 из AGENTS.md. Недетерминированный выбор главной копии
        делает контрольную сумму корпуса дрожащей, и сторож свежести запускает
        переиндексацию бесконечно.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0

    $dedupePath = Join-Path $script:ProjectRoot 'src/ragraf/dedupe.py'
    $corpusPath = Join-Path $script:ProjectRoot 'src/ragraf/corpus.py'

    if (Test-Path -LiteralPath $dedupePath) {
        $examined++
        $dedupeText = Get-ProjectText $dedupePath
        if ($dedupeText -notmatch 'def choose_keeper') {
            $problems.Add('в src/ragraf/dedupe.py нет выбора главной копии: схлопывание дублей неопределено')
        }
        foreach ($rule in @('cleanest', 'shortest', 'longest', 'newest')) {
            if ($dedupeText -notmatch $rule) {
                $problems.Add("правило выбора копии '$rule' исчезло из src/ragraf/dedupe.py, но записано в паспорте и в настройках")
            }
        }
    }

    if (Test-Path -LiteralPath $corpusPath) {
        $examined++
        $corpusText = Get-ProjectText $corpusPath
        if ($corpusText -notmatch 'dedupe') {
            $problems.Add('src/ragraf/corpus.py больше не вызывает схлопывание дублей: контрольная сумма корпуса разойдётся с содержимым индекса и сторож зациклится')
        }
    }

    # Ни индексатор, ни сторож не должны схлопывать дубли самостоятельно:
    # файлы обязан отдавать только сканер.
    foreach ($relative in @('src/ragraf/indexer.py', 'src/ragraf/watch.py')) {
        $path = Join-Path $script:ProjectRoot $relative
        if (-not (Test-Path -LiteralPath $path)) { continue }
        $examined++
        if ((Get-ProjectText $path) -match 'dedupe') {
            $problems.Add("$relative стал схлопывать дубли сам: единственная точка, откуда файлы берут и индексатор, и сторож, — src/ragraf/corpus.py")
        }
    }

    return New-CheckResult -Name 'dedupe stays deterministic and lives in the scanner' -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Test-StoragePragmas {
    <#
    .SYNOPSIS
        Настройки соединения с хранилищем остались контрактом.

    .DESCRIPTION
        Инвариант 6 из AGENTS.md: без ожидания блокировки параллельные CLI и MCP
        начнут натыкаться на занятую базу; включённые внешние ключи сломают
        порядок вставок, на который рассчитан код.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0

    $storePath = Join-Path $script:ProjectRoot 'src/ragraf/store.py'
    if (Test-Path -LiteralPath $storePath) {
        $examined++
        $storeText = Get-ProjectText $storePath
        foreach ($needle in @('busy_timeout', 'WAL', 'foreign_keys')) {
            if ($storeText -notmatch [regex]::Escape($needle)) {
                $problems.Add("настройка '$needle' исчезла из src/ragraf/store.py: это контракт соединения, а не оптимизация")
            }
        }
        if ($storeText -notmatch 'foreign_keys\s*=\s*OFF') {
            $problems.Add('в src/ragraf/store.py внешние ключи больше не отключены: порядок вставок рассчитан на обратное')
        }
    }

    return New-CheckResult -Name 'storage connection pragmas are unchanged' -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Test-JvmHeapSource {
    <#
    .SYNOPSIS
        Лимит памяти JVM берётся из одного источника.

    .DESCRIPTION
        Измеренный дефект: сборка свойств кода передавала JVM зашитые 18 ГБ, а
        серверный режим — значение настройки корпуса (8 ГБ), то есть одна и та же
        JVM получала разные лимиты в зависимости от пути запуска. Решение
        владельца: 8 ГБ.

        Правило проверяет именно единство источника, а не конкретное число:
        лимит обязан выводиться из настройки `index.joern_heap_gb`, а переменная
        окружения — только перекрывать её. Зашитое значение без настройки снова
        разведёт пути.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0

    $cpgPath = Join-Path $script:ProjectRoot 'src/ragraf/cpg.py'
    $configPath = Join-Path $script:ProjectRoot 'src/ragraf/config.py'
    $serverPath = Join-Path $script:ProjectRoot 'src/ragraf/joern_server.py'

    if (Test-Path -LiteralPath $cpgPath) {
        $examined++
        $cpgText = Get-ProjectText $cpgPath
        if ($cpgText -notmatch 'def\s+_heap_arg\s*\(') {
            $problems.Add('в src/ragraf/cpg.py нет вывода аргумента памяти JVM')
        }
        # Зашитое значение рядом с переменной окружения — признак второго источника.
        if ($cpgText -match 'os\.environ\.get\(\s*"RAGRAF_JOERN_HEAP_GB"\s*,\s*"\d+"\s*\)') {
            $problems.Add('лимит памяти JVM в src/ragraf/cpg.py задан зашитым значением по умолчанию: источник обязан быть один — настройка корпуса')
        }
        if ($cpgText -notmatch 'joern_heap_gb') {
            $problems.Add('src/ragraf/cpg.py не читает настройку корпуса joern_heap_gb: лимит памяти перестал зависеть от конфигурации')
        }
        if ($cpgText -notmatch 'RAGRAF_JOERN_HEAP_GB') {
            $problems.Add('в src/ragraf/cpg.py пропала возможность перекрыть лимит переменной окружения')
        }
    }

    if (Test-Path -LiteralPath $configPath) {
        $examined++
        $configText = Get-ProjectText $configPath
        $match = [regex]::Match($configText, 'joern_heap_gb\s*:\s*int\s*=\s*(\d+)')
        if (-not $match.Success) {
            $problems.Add('в src/ragraf/config.py нет настройки joern_heap_gb')
        }
        elseif ([int]$match.Groups[1].Value -ne 8) {
            $problems.Add("настройка joern_heap_gb по умолчанию равна $($match.Groups[1].Value), решение владельца — 8 ГБ")
        }
    }

    if (Test-Path -LiteralPath $serverPath) {
        $examined++
        $serverText = Get-ProjectText $serverPath
        $match = [regex]::Match($serverText, 'heap:\s*str\s*=\s*"(\d+)g"')
        if ($match.Success -and [int]$match.Groups[1].Value -ne 8) {
            $problems.Add("значение памяти сервера Joern по умолчанию — $($match.Groups[1].Value) ГБ, решение владельца — 8 ГБ")
        }
    }

    return New-CheckResult -Name 'JVM heap comes from one source (8 GB)' -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Test-CapabilityHonesty {
    <#
    .SYNOPSIS
        Режим ревизии не завышен относительно содержимого графа.

    .DESCRIPTION
        `capability` — утверждение о возможностях, а не украшение ответа. `full`
        означает «доступны операции среза», то есть в графе есть рёбра от графа
        свойств кода (источник помечен `joern`).

        Измеренный дефект: обходной путь экспорта возвращал `full`, не добавив ни
        одного ребра (ревизия rev-0012: запросов 131 639, подтверждено 0). Клиент
        видел `full`, вызывал срез и получал ошибку вместо честного «недоступно».

        Проверка формулируется по коду, а не по документам: ищется зашитое
        присваивание `capability = "full"` в стадии свойств кода. Значение обязано
        браться из результата стадии — иначе честный `partial` будет затёрт.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0

    $indexerPath = Join-Path $script:ProjectRoot 'src/ragraf/indexer.py'
    if (-not (Test-Path -LiteralPath $indexerPath)) {
        return New-CheckResult -Name 'capability reflects the actual graph' -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: нет src/ragraf/indexer.py, проверять нечего')
    }

    $examined++
    $text = Get-ProjectText $indexerPath

    # 1. Ветка «стадия свойств кода отработала» обязана брать режим из результата.
    #
    # Правило проверяет ИНВАРИАНТ, а не написание присваивания: первая редакция
    # искала буквальное `capability = str(cpg_info...`, и реализация, где режим
    # сначала кладётся в локальную переменную, объявлялась дефектной. Проверка,
    # зависящая от того, как названа переменная, — ложная проверка.
    $okBranch = [regex]::Match($text, '(?s)if\s+cpg_info\[\s*"ok"\s*\]\s*:.*?(?=\n        [a-z#]|\n    def )')
    if ($okBranch.Success) {
        $branch = $okBranch.Value
        if ($branch -notmatch 'cpg_info\.get\(\s*"capability"') {
            $problems.Add('ветка успешной стадии свойств кода не читает cpg_info["capability"]: честный partial будет затёрт значением full')
        }
        if ($branch -match 'capability\s*=\s*"full"') {
            $problems.Add('ветка успешной стадии свойств кода жёстко выставляет capability="full": это ложное утверждение, если свойства кода построены не полностью')
        }
    }
    # 2. Обходной путь обязан связывать режим с ЧИСЛОМ добавленных связей.
    #
    # Правило проверяет ИНВАРИАНТ, а не написание присваивания. Первая редакция
    # искала буквальное `"capability": "full"` и объявила дефектом корректный
    # код, где режим выбирается тернарным выражением по `contributed`. Это
    # третий случай такой ошибки в проекте, поэтому правило записано так:
    # ищем в ветке обходного пути признаки того, что режим зависит от числа
    # связей (`contributed` или `added`), и отдельно — прямое присваивание full.
    $fallback = [regex]::Match($text, '(?s)def\s+_calls_from_file\s*\(.*?(?=\n    def |\nclass |\Z)')
    if ($fallback.Success) {
        $branch = $fallback.Value
        if ($branch -match '"capability"\s*:\s*"full"\s*,') {
            $problems.Add('обходной путь (_calls_from_file) объявляет режим full без условия: свойства кода при этом не построены, срез недоступен')
        }
        elseif ($branch -notmatch 'contributed|stats\["added"\]|stats\.get\("added"') {
            $problems.Add('обходной путь (_calls_from_file) не связывает режим с числом добавленных связей: режим может быть завышен')
        }
    }
    # 3. Операция среза обязана называть фактический режим, а не зашитый degraded.
    $facadePath = Join-Path $script:ProjectRoot 'src/ragraf/facade.py'
    if (Test-Path -LiteralPath $facadePath) {
        $examined++
        $facadeText = Get-ProjectText $facadePath
        if ($facadeText -match 'env\.error\s*=\s*"[^"]*режиме degraded') {
            $problems.Add('сообщение отказа среза называет режим degraded зашитым текстом: ревизия в режиме partial получит неверное объяснение')
        }
    }

    return New-CheckResult -Name 'capability reflects the actual graph' -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Test-RevisionRules {
    <#
    .SYNOPSIS
        Правила ревизий и журнала расхождений не изменены.

    .DESCRIPTION
        Инварианты 7, 8 и 9: активная ревизия выбирается по номеру, а не по
        времени; журнал расхождений не удаляется; ротация историю сохраняет.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0

    foreach ($relative in @('src/ragraf/watch.py', 'src/ragraf/revisions.py', 'src/ragraf/store.py', 'src/ragraf/drift.py')) {
        $path = Join-Path $script:ProjectRoot $relative
        if (-not (Test-Path -LiteralPath $path)) { continue }
        $examined++
        $text = Get-ProjectText $path

        if ($relative -eq 'src/ragraf/watch.py') {
            if ($text -notmatch 'ORDER BY rev_id DESC') {
                $problems.Add('src/ragraf/watch.py больше не выбирает ревизию по номеру: сортировка по времени уже была дефектом')
            }
            if ($text -match 'ORDER BY created_at') {
                $problems.Add('src/ragraf/watch.py сортирует ревизии по времени создания: активной станет не последняя по номеру')
            }
        }

        if ($relative -eq 'src/ragraf/revisions.py') {
            if ($text -notmatch 'rotated') {
                $problems.Add('src/ragraf/revisions.py больше не помечает вытесненные ревизии: история и нумерация зависят от этой пометки')
            }
        }

        if ($relative -eq 'src/ragraf/drift.py') {
            if ($text -match '(?i)DELETE\s+FROM\s+drift_entry') {
                $problems.Add('src/ragraf/drift.py удаляет записи журнала расхождений: журнал объявлен append-only')
            }
        }
    }

    # Требование append-only зафиксировано прямо в схеме хранилища.
    $storePath = Join-Path $script:ProjectRoot 'src/ragraf/store.py'
    if (Test-Path -LiteralPath $storePath) {
        $examined++
        if ((Get-ProjectText $storePath) -notmatch 'append-only') {
            $problems.Add('в src/ragraf/store.py исчезла пометка append-only у журнала расхождений')
        }
    }

    return New-CheckResult -Name 'revision and ledger rules unchanged' -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Test-LanguageRouting {
    <#
    .SYNOPSIS
        Языки разбора деревом совпадают с профилями извлечения.

    .DESCRIPTION
        Инвариант 12. Расхождение хоть на один язык даёт тихую потерю разбора:
        ошибки не будет, просто часть файлов перестанет давать узлы графа.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0

    $configPath = Join-Path $script:ProjectRoot 'src/ragraf/config.py'
    $genericPath = Join-Path $script:ProjectRoot 'src/ragraf/extract/generic.py'

    if ((Test-Path -LiteralPath $configPath) -and (Test-Path -LiteralPath $genericPath)) {
        $examined++
        $configText = Get-ProjectText $configPath
        $genericText = Get-ProjectText $genericPath

        $block = [regex]::Match($configText, 'TREE_LANGS\s*=\s*frozenset\(\{([^}]*)\}\)', 'Singleline')
        if (-not $block.Success) {
            $problems.Add('в src/ragraf/config.py не найден набор языков разбора: связь с профилями извлечения проверить нельзя')
        }
        else {
            $members = [regex]::Matches($block.Groups[1].Value, '"([a-z]+)"') | ForEach-Object { $_.Groups[1].Value }
            $profileMembers = [regex]::Matches($genericText, '"([a-z]+)":\s*LangProfile\(') | ForEach-Object { $_.Groups[1].Value }
            # Сравниваются МНОЖЕСТВА, а не число. Прежняя редакция требовала ровно
            # 16 языков и падала при каждом осознанном расширении списка, хотя
            # расхождения между списками не было: проверка ловила правку документа,
            # а не дефект. Настоящий инвариант: каждый язык разбора имеет профиль
            # (или свой извлекатель), и каждый профиль объявлен в наборе. Именно
            # расхождение даёт тихую потерю разбора без единой ошибки.
            $profiled = @($profileMembers) + @('php', 'css', 'html')
            $withoutProfile = @($members | Where-Object { $profiled -notcontains $_ })
            $withoutTree = @($profiled | Where-Object { $members -notcontains $_ })
            if ($withoutProfile.Count -gt 0) {
                $problems.Add("языки TREE_LANGS без профиля извлечения: $($withoutProfile -join ', ')")
            }
            if ($withoutTree.Count -gt 0) {
                $problems.Add("у языков с профилем нет записи в TREE_LANGS: $($withoutTree -join ', ')")
            }
            if ($genericText -notmatch 'PROFILES') {
                $problems.Add('в src/ragraf/extract/generic.py не найден набор профилей извлечения, с которым обязан совпадать список языков разбора')
            }
        }

        $testPath = Join-Path $script:ProjectRoot 'tests/test_lang_routing.py'
        $examined++
        if (-not (Test-Path -LiteralPath $testPath)) {
            $problems.Add('проверка tests/test_lang_routing.py, сверяющая языки разбора с профилями, исчезла')
        }
    }

    return New-CheckResult -Name 'tree-sitter languages match the extraction profiles' -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Test-LexvecSchemaContract {
    <#
    .SYNOPSIS
        Схема лексико-векторного хранилища не разошлась с кодом поиска.

    .DESCRIPTION
        Инвариант добавлен после замера 12 октября 2026. До него проверки схемы
        хранилища не было НИ ОДНОЙ: изменение DDL `ft_chunk` не замечали ни чекер,
        ни тесты, а разойтись там есть чему —

        * число колонок fts5 обязано равняться числу весов `bm25()`. Весов в
          `lexvec.py` ровно семь; восьмая колонка сдвинула бы все веса молча,
          и поиск перестал бы ранжировать по смыслу без единой ошибки;
        * `search_lexical` адресует колонки ПО НОМЕРАМ мест: `path` — 3.0,
          `symbol_tokens` — 4.0, `code_tokens` — 1.0. Порядок в DDL и порядок
          весов — один контракт, записанный в двух файлах;
        * сырой текст НЕ должен становиться колонкой `ft_chunk`: fts5 хранит
          только токены (замер: `sqlite3` удаляет колонки UNINDEXED, `snippet()`
          по номеру отдаёт токенизированную строку, а не код). Текст живёт
          в отдельной таблице `chunk_text`, и она обязана быть в схеме.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0

    $storePath = Join-Path $script:ProjectRoot 'src/ragraf/store.py'
    $lexvecPath = Join-Path $script:ProjectRoot 'src/ragraf/lexvec.py'
    if (-not (Test-Path -LiteralPath $storePath) -or -not (Test-Path -LiteralPath $lexvecPath)) {
        return New-CheckResult -Name 'lexvec schema matches the search code' -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: не найден store.py или lexvec.py, сверять схему не с чем')
    }

    $storeText = Get-ProjectText $storePath
    $lexvecText = Get-ProjectText $lexvecPath

    # 1. Состав колонок fts5 и число весов bm25.
    $fts = [regex]::Match($storeText, 'CREATE VIRTUAL TABLE IF NOT EXISTS ft_chunk USING fts5\((?<body>.*?)\)\s*;', 'Singleline')
    if (-not $fts.Success) {
        $problems.Add('в src/ragraf/store.py не найдено объявление ft_chunk: контракт схемы проверить нельзя')
    }
    else {
        $examined++
        # `tokenize = '...'` — не колонка, а опция таблицы: строка начинается
        # словом, как имя колонки, и без отсечения даёт лишний «восьмой» элемент.
        # Это дефект самого чекера был бы неотличим от дефекта схемы, поэтому
        # опции отсекаются явно.
        $body = $fts.Groups['body'].Value
        $body = [regex]::Replace($body, 'tokenize\s*=.*$', '', 'Singleline')
        $cols = [regex]::Matches($body, '^\s*(?<name>[a-z_]+)', 'Multiline') |
            ForEach-Object { $_.Groups['name'].Value }
        if ($cols.Count -ne 7) {
            $problems.Add("колонок ft_chunk $($cols.Count), а весов bm25 в коде рассчитано на 7: числа обязаны совпадать")
        }
        foreach ($expected in @('symbol_tokens', 'code_tokens', 'sql_tokens', 'text_tokens')) {
            if ($cols -notcontains $expected) {
                $problems.Add("колонка $expected исчезла из ft_chunk: номера колонок в snippet() и веса bm25 сместятся")
            }
        }
        if ($cols -contains 'text_raw' -or $cols -contains 'source_text') {
            $problems.Add('сырой текст добавлен колонкой ft_chunk: fts5 хранит токены, код оттуда не читается (нужна отдельная таблица)')
        }

        $weights = [regex]::Match($lexvecText, 'bm25\(ft_chunk,\s*(?<w>[0-9.,\s]+)\)')
        if ($weights.Success) {
            $numbers = [regex]::Matches($weights.Groups['w'].Value, '[0-9]+\.?[0-9]*') |
                ForEach-Object { $_.Value }
            if ($numbers.Count -ne $cols.Count) {
                $problems.Add("весов bm25 $($numbers.Count), колонок ft_chunk $($cols.Count): числа обязаны совпадать, иначе запрос падает или ранжирование смещается")
            }
            $examined++
        }
        else {
            $problems.Add('в src/ragraf/lexvec.py не найден вызов bm25(ft_chunk, ...): связь весов со схемой проверить нельзя')
        }
    }

    # 2. Хранилище сырого текста обязано быть в схеме и обязано вычищаться
    #    при ротации: иначе текст переживёт свою ревизию и база будет расти.
    $examined++
    if ($storeText -notmatch 'CREATE TABLE IF NOT EXISTS chunk_text') {
        $problems.Add('в схеме lexvec нет таблицы chunk_text: текст чанков читать неоткуда, выдача вернётся к чтению диска')
    }
    if ($storeText -notmatch 'init_lexvec[\s\S]{0,400}LEXVEC_DDL_CHUNK_TEXT' -and
        $storeText -notmatch 'executescript\(LEXVEC_DDL_CHUNK_TEXT\)') {
        $problems.Add('DDL таблицы текста не создаётся в init_lexvec: таблицы не будет в новых корпусах')
    }

    $revisionsPath = Join-Path $script:ProjectRoot 'src/ragraf/revisions.py'
    if (Test-Path -LiteralPath $revisionsPath) {
        $examined++
        $revText = Get-ProjectText $revisionsPath
        if ($revText -notmatch 'chunk_text') {
            $problems.Add('ротация ревизий не удаляет chunk_text: текст копится по ревизиям и не освобождается')
        }
    }

    # 3. Проверка на применение: текст чанков обязан быть доступен в живом корпусе.
    $testPath = Join-Path $script:ProjectRoot 'tests/test_text_from_index.py'
    $examined++
    if (-not (Test-Path -LiteralPath $testPath)) {
        $problems.Add('проверка tests/test_text_from_index.py исчезла: адресация хита и текст из индекса остались без регрессионной защиты')
    }

    return New-CheckResult -Name 'lexvec schema matches the search code' -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Test-AccessHygiene {
    <#
    .SYNOPSIS
        Значение доступа не отдаётся в ответах, файлы доступа лежат вне корпуса,
        а использование выданного доступа видно в статистике.

    .DESCRIPTION
        Три свойства, каждое — из измеренного состояния этого проекта.

        1. Панель раньше возвращала токен администратора в ответе `/api/token`.
           Это не оплошность в тексте, а рабочая утечка: значение получал любой
           локальный клиент, и проверка изменяющих запросов становилась
           формальностью. Поэтому правило ищет ВОЗВРАТ значения в ответе, а не
           конкретную строку: проверка через написание сломалась бы при первом
           переименовании переменной и ничего не поймала бы.

        2. Файлы доступа обязаны лежать в каталоге, исключённом из корпуса.
           Иначе значение попадёт в индекс и в поисковую выдачу агента.
           Проверяется связь: путь файла доступа и исключение каталога.

        3. Телеметрия обязана хранить и показывать доступ, которым пришёл вызов.
           Без подстановки доступа процесса метка терялась, и вся статистика
           выглядела как «без доступа» (измерено на собственном корпусе).
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0

    $adminPath = Join-Path $script:ProjectRoot 'src/ragraf/admin_server.py'
    $accessPath = Join-Path $script:ProjectRoot 'src/ragraf/access.py'
    $telemetryPath = Join-Path $script:ProjectRoot 'src/ragraf/telemetry.py'
    $configPath = Join-Path $script:ProjectRoot 'config/ragraf.yaml'

    if (-not (Test-Path -LiteralPath $adminPath) -or -not (Test-Path -LiteralPath $accessPath)) {
        return New-CheckResult -Name 'access hygiene: secrets out, usage attributed' -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: нет src/ragraf/admin_server.py или access.py — проверять нечего')
    }

    # 1. Значение доступа не возвращается в ответе панели.
    $examined++
    $adminText = Get-ProjectText $adminPath
    $leak = [regex]::Match($adminText,
        '"[a-z_]*token[a-z_]*"\s*:\s*ACCESS_TOKEN', 'IgnoreCase')
    if ($leak.Success) {
        $problems.Add('src/ragraf/admin_server.py возвращает значение доступа в ответе: локальный клиент получит его и защита станет формальностью')
    }
    if ($adminText -notmatch 'auth_state\s*\(') {
        $problems.Add('в src/ragraf/admin_server.py нет состояния доступа без значений: точка состояния обязана отдавать только факт «пароль задан» и число токенов')
    }

    # 2. Файлы доступа — вне корпуса.
    $examined++
    if (Test-Path -LiteralPath $accessPath) {
        $accessText = Get-ProjectText $accessPath
        if ($accessText -notmatch 'CATALOG_DIR') {
            $problems.Add('в src/ragraf/access.py не видно привязки файлов доступа к каталогу состояния')
        }
        if ((Test-Path -LiteralPath $configPath) -and
            ((Get-ProjectText $configPath) -notmatch '\*\*/catalog/\*\*')) {
            $problems.Add('config/ragraf.yaml больше не исключает catalog/**: файлы доступа попадут в корпус и в поисковую выдачу')
        }
    }

    # 3. Статистика знает про доступ.
    $examined++
    if (Test-Path -LiteralPath $telemetryPath) {
        $telemetryText = Get-ProjectText $telemetryPath
        if ($telemetryText -notmatch 'by_access') {
            $problems.Add('в src/ragraf/telemetry.py нет разреза by_access: использование выданного доступа не видно в статистике')
        }
        if ($telemetryText -notmatch 'token_label') {
            $problems.Add('в src/ragraf/telemetry.py нет колонки метки доступа: записи не отличат выданный доступ от вызова без него')
        }
        # Подстановка доступа процесса: вызов её не называет, её знает процесс.
        if ($telemetryText -notmatch 'or self\.') {
            $problems.Add('в src/ragraf/telemetry.py нет подстановки доступа процесса: метка теряется, и статистика выглядит как «без доступа» (измерено)')
        }
    }

    return New-CheckResult -Name 'access hygiene: secrets out, usage attributed' -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}


function Test-ModeHasProbe {
    <#
    .SYNOPSIS
        Заявленный канал работы RAGRAF подтверждается пробой, а не надписью.

    .DESCRIPTION
        Настройка `ragraf_mode` описывает ОЖИДАЕМЫЙ канал (cli, mcp-stdio,
        mcp-http, both). Сам сервер MCP поднимает клиент, поэтому «включить
        режим» панель не может — но обязана ПРОВЕРИТЬ факт и назвать
        расхождение. Без этого настройка становится надписью, не связанной
        с состоянием: именно так в этом проекте уже появлялся ложный `full`.

        Проверяется механизм, а не текст:
          * список режимов закрытый — опечатка не станет новым режимом;
          * проба каналов существует и возвращает вердикт по режиму;
          * проверка не подменяется осмотром файлов: для stdio проба поднимает
            сервер и считает инструменты;
          * второй канал остаётся доступен и показывается справочно, потому что
            CLI и MCP не конфликтуют.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0

    $probesPath = Join-Path $script:ProjectRoot 'src/ragraf/probes.py'
    $settingsPath = Join-Path $script:ProjectRoot 'src/ragraf/settings.py'
    $adminPath = Join-Path $script:ProjectRoot 'src/ragraf/admin_server.py'

    if (-not (Test-Path -LiteralPath $probesPath)) {
        return New-CheckResult -Name 'declared mode is confirmed by a probe' -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: нет src/ragraf/probes.py — режим нечем подтвердить')
    }

    # 1. Список режимов закрытый.
    $examined++
    if (Test-Path -LiteralPath $settingsPath) {
        $settingsText = Get-ProjectText $settingsPath
        if ($settingsText -notmatch 'ragraf_mode') {
            $problems.Add('в src/ragraf/settings.py нет настройки ragraf_mode: режим нельзя выбрать')
        }
        elseif ($settingsText -notmatch '"ragraf_mode":\s*\(') {
            $problems.Add('ragraf_mode объявлен без закрытого списка значений: опечатка станет новым режимом, который никто не проверяет')
        }
    }

    # 2. Проба каналов: запускает канал, а не смотрит на файлы.
    $examined++
    $probesText = Get-ProjectText $probesPath
    if ($probesText -notmatch 'def\s+probe_mcp_stdio') {
        $problems.Add('в src/ragraf/probes.py нет пробы stdio-канала: наличие файла сервера не доказывает, что он отвечает')
    }
    if ($probesText -notmatch '(subprocess\.(Popen|run)|tools/list)') {
        $problems.Add('проба stdio не запускает сервер: проверка обязана получить ответ, иначе это осмотр файлов, а не проба')
    }
    if ($probesText -notmatch 'def\s+probe_cli') {
        $problems.Add('в src/ragraf/probes.py нет пробы CLI-канала')
    }
    if ($probesText -notmatch 'mode_ok') {
        $problems.Add('проба не отдаёт вердикт по режиму (mode_ok): связь между настройкой и фактом отсутствует')
    }
    if ($probesText -notmatch 'reference') {
        $problems.Add('нет пометки «справочно»: второй канал обязан показываться, но не входить в вердикт')
    }

    # 3. Панель показывает вердикт.
    $examined++
    if (Test-Path -LiteralPath $adminPath) {
        $adminText = Get-ProjectText $adminPath
        if ($adminText -notmatch '/api/probe') {
            $problems.Add('в панели нет точки пробы (/api/probe): режим негде подтвердить')
        }
    }

    return New-CheckResult -Name 'declared mode is confirmed by a probe' -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}


function Test-DocumentedNumbers {
    <#
    .SYNOPSIS
        Числа, записанные в документах текущего состояния, совпадают с кодом.

    .DESCRIPTION
        Измеренный дефект: код объявляет 23 инструмента поверхности для агентов и
        22 операции фасада, а документы называют 18 и 16 инструментов и 21 операцию.
        Одно и то же число живёт в трёх редакциях, и читатель не может понять,
        какое верно.

        Под намеренно оставлено открытым: оно описывает уже существующее
        расхождение и валит прогон до тех пор, пока его не устранят.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0

    $mcpPath = Join-Path $script:ProjectRoot 'src/ragraf/mcp_server.py'
    $facadePath = Join-Path $script:ProjectRoot 'src/ragraf/facade.py'

    if (-not (Test-Path -LiteralPath $mcpPath)) {
        return New-CheckResult -Name 'documented operation counts match the code' -Ok $false -Examined 0 -Details @('NOT-IMPLEMENTED: нет src/ragraf/mcp_server.py, считать число операций нечем')
    }
    if (-not (Test-Path -LiteralPath $facadePath)) {
        return New-CheckResult -Name 'documented operation counts match the code' -Ok $false -Examined 0 -Details @('NOT-IMPLEMENTED: нет src/ragraf/facade.py, считать число операций нечем')
    }

    $examined++
    $mcpText = Get-ProjectText $mcpPath
    $toolCount = @([regex]::Matches($mcpText, '"name":\s*"ragraf_[a-z_]+"') | ForEach-Object { $_.Value } | Sort-Object -Unique).Count

    $examined++
    $facadeText = Get-ProjectText $facadePath
    $opCount = @([regex]::Matches($facadeText, '(?m)^\s*def op_[a-z_]+\(') | ForEach-Object { $_.Value } | Sort-Object -Unique).Count

    if ($toolCount -lt 1 -or $opCount -lt 1) {
        return New-CheckResult -Name 'documented operation counts match the code' -Ok $false -Examined $examined `
            -Details @("не удалось посчитать операции в коде: инструментов $toolCount, операций фасада $opCount — регулярное выражение разошлось с кодом")
    }

    # Проверяются ТОЛЬКО документы текущего состояния. Исторические документы
    # (требования релиза 1, отчёты прежних этапов) намеренно сохраняют свои
    # числа: 17 операций там — след этапа, а не действующий контракт. Смешать
    # эти два вида нельзя, переписав историю проекта, поэтому список явный.
    $documents = @(
        @{ Path = 'README.md'; Pattern = '\*\*(\d+)\*\*\s+операци'; Label = 'операций фасада' },
        @{ Path = 'README.md'; Pattern = '\*\*(\d+)\*\*\s+инструмент'; Label = 'инструментов поверхности для агентов' },
        @{ Path = 'docs/03_implementation/INTEGRATION.md'; Pattern = '\*\*(\d+)\s+инструмент'; Label = 'инструментов поверхности для агентов' },
        @{ Path = 'docs/05_article/RAGRAF_ARTICLE.md'; Pattern = '(\d+)\s+операци'; Label = 'операций фасада' }
    )

    foreach ($doc in $documents) {
        $full = Join-Path $script:ProjectRoot $doc.Path
        if (-not (Test-Path -LiteralPath $full)) { continue }
        $examined++
        $text = Get-ProjectText $full
        $match = [regex]::Match($text, $doc.Pattern)
        if (-not $match.Success) { continue }

        $stated = [int]$match.Groups[1].Value
        $actual = $opCount
        if ($doc.Label -like 'инструментов*') { $actual = $toolCount }

        if ($stated -ne $actual) {
            $problems.Add("$($doc.Path) называет $stated $($doc.Label), в коде их $actual")
        }
    }

    return New-CheckResult -Name 'documented operation counts match the code' -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Test-McpClientCompatibility {
    <#
    .SYNOPSIS
        MCP-сервер работает в УРЕЗАННОМ окружении внешнего клиента.

    .DESCRIPTION
        Измеренный дефект (13.10.2026): MCP-клиент запускает сервер как
        дочерний процесс, передавая ему только APPDATA, HOMEDRIVE, HOMEPATH,
        LOCALAPPDATA, PATH, PROCESSOR_ARCHITECTURE, SYSTEMDRIVE, SYSTEMROOT,
        TEMP, USERNAME, USERPROFILE, PROGRAMFILES (официальный SDK,
        `@modelcontextprotocol/client/dist/stdio.mjs`). Ни `PYTHONUTF8`,
        ни `PYTHONIOENCODING` туда не попадают, а DeepSeek Harness
        дополнительно вычищает имена с PASSWORD|SECRET|TOKEN|KEY и все
        `DSH_*`. Оболочка AutoClaw ставит обе переменные, поэтому дефект был
        невидим: сервер падал на ПЕРВОМ же ответе с русским текстом
        (`UnicodeEncodeError: 'charmap' codec` на cp1252 в `send()`), и клиент
        видел мёртвый процесс вместо инструментов.

        Проверяется СВОЙСТВО, а не текст: сервер обязан сам приводить потоки
        к UTF-8 в `main()` и не требовать обязательного `--project` (клиент
        может держать один сервер на несколько проектов и выбирать корпус
        операцией `use_project`).
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0

    $serverPath = Join-Path $script:ProjectRoot 'src/ragraf/mcp_server.py'
    if (-not (Test-Path -LiteralPath $serverPath)) {
        return New-CheckResult -Name 'MCP server survives a scrubbed client environment' -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: нет src/ragraf/mcp_server.py — совместимость проверить нечем')
    }

    $examined++
    $text = Get-ProjectText $serverPath

    # 1. Кодировка потоков ставится в самом модуле, а не в окружении.
    if ($text -notmatch 'def\s+force_utf8_stdio\s*\(') {
        $problems.Add('в src/ragraf/mcp_server.py нет настройки UTF-8 на потоках: при локальном окружении cp1252 сервер упадёт на первом русском ответе')
    }
    if ($text -notmatch 'encoding\s*=\s*["'']utf-8["'']') {
        $problems.Add('в src/ragraf/mcp_server.py потоки не перенастраиваются на UTF-8')
    }

    # 2. Корпус не может быть обязательным аргументом запуска.
    #    Ищем проверку на обязательность в описании ОПЦИИ, а не подстроку
    #    с именем аргумента: иначе проверка ловила бы обычные упоминания.
    $projectArg = [regex]::Match($text, 'add_argument\(\s*["'']--project["'']\s*,[^)]{0,400}?\)', 'Singleline')
    if (-not $projectArg.Success) {
        $problems.Add('в src/ragraf/mcp_server.py не найден разбор аргумента --project — форма запуска разошлась с проверкой')
    }
    elseif ($projectArg.Value -match 'required\s*=\s*True') {
        $problems.Add('src/ragraf/mcp_server.py требует --project: клиент, держащий один сервер на несколько проектов, не сможет запустить его без корпуса')
    }

    # 3. Выбор корпуса обязан быть доступен как операция.
    if ($text -notmatch '["'']ragraf_use_project["'']') {
        $problems.Add('в src/ragraf/mcp_server.py нет инструмента ragraf_use_project: один сервер не сможет обслужить несколько проектов')
    }

    return New-CheckResult -Name 'MCP server survives a scrubbed client environment' -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Test-ExternalOpsAreRecorded {
    <#
    .SYNOPSIS
        Операции, живущие вне фасада, тоже попадают в журнал статистики.

    .DESCRIPTION
        Измеренный дефект (13.10.2026): телеметрия навешивается обёрткой на
        методы `op_*` фасада, а `ensure` и `use_project` реализованы в
        MCP-сервере (первая умеет переиндексировать корпус, вторая меняет
        конфиг сервера). В сводке `usage` они не появлялись вообще — на
        собственном корпусе это было видно прямо: есть `index_status`
        и `catalog`, а `ensure` нет, хотя агент вызывает именно `ensure`
        первым шагом. Последствие не косметическое: нельзя ответить на вопрос
        «соблюдают ли агенты правило первого шага», потому что первый шаг
        невидим.

        Проверяется свойство: у обеих операций есть вызов общей точки записи
        со стороны MCP-сервера.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0

    $serverPath = Join-Path $script:ProjectRoot 'src/ragraf/mcp_server.py'
    $facadePath = Join-Path $script:ProjectRoot 'src/ragraf/facade.py'
    if (-not (Test-Path -LiteralPath $serverPath)) {
        return New-CheckResult -Name 'operations outside the facade are recorded' -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: нет src/ragraf/mcp_server.py')
    }

    $examined++
    $serverText = Get-ProjectText $serverPath
    if ($serverText -notmatch 'record_external_call\s*\(') {
        $problems.Add('в src/ragraf/mcp_server.py нет записи в журнал для операций вне фасада: ensure и use_project останутся невидимыми в статистике')
    }

    $examined++
    if (-not (Test-Path -LiteralPath $facadePath)) {
        $problems.Add('NOT-IMPLEMENTED: нет src/ragraf/facade.py — точку записи проверить нечем')
    }
    else {
        $facadeText = Get-ProjectText $facadePath
        if ($facadeText -notmatch 'def\s+record_external_call\s*\(') {
            $problems.Add('в src/ragraf/facade.py нет точки record_external_call: операции вне фасада не попадут в журнал')
        }
        # Точка обязана писать ТОЙ ЖЕ записью, что и фасадная обёртка:
        # отдельный формат расколол бы сводку на две несводимые части.
        $block = [regex]::Match($facadeText, '(?s)def\s+record_external_call\s*\(.*?(?=\n    def |\Z)')
        if ($block.Success -and $block.Value -notmatch 'self\._record\s*\(') {
            $problems.Add('record_external_call не использует общую запись self._record: сводка статистики станет несводимой')
        }
    }

    return New-CheckResult -Name 'operations outside the facade are recorded' -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Test-PowerShellVintage {
    <#
    .SYNOPSIS
        Локальный PowerShell-код соответствует среде PowerShell 7.6.

    .DESCRIPTION
        Проект содержит два своих скрипта. Проверяется то, что поддаётся
        объективной проверке без запуска: объявленная требуемая версия и
        отсутствие явного вызова Windows PowerShell 5.1.

        Отдельный случай — чужой инструмент в слое CPG, который сознательно
        запускает 5.1 для перечисления процессов. Это исключение записано в
        паспорте и здесь не считается находкой: проверка смотрит только
        собственные скрипты проекта.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0

    $scriptsRoot = Join-Path $script:ProjectRoot 'scripts'
    if (-not (Test-Path -LiteralPath $scriptsRoot -PathType Container)) {
        return New-CheckResult -Name 'project PowerShell code targets pwsh 7.6' -Ok $false -Examined 0 -Details @('NOT-IMPLEMENTED: нет каталога scripts, проверять нечего')
    }

    foreach ($file in (Get-ChildItem -LiteralPath $scriptsRoot -Recurse -File -Include *.ps1, *.psm1 -ErrorAction SilentlyContinue)) {
        $examined++
        $relative = Get-RelativePath $file.FullName
        $text = Get-ProjectText $file.FullName
        if ($text -notmatch '#Requires\s+-Version\s+7\.6') {
            $problems.Add("$relative не объявляет требуемую версию PowerShell 7.6")
        }
        # Комментарии и справочные блоки выбрасываются до проверки: упоминание
        # запрещённого интерпретатора внутри правила — это описание правила, а не
        # вызов. Иначе собственный комментарий о запрете валил бы прогон.
        $codeOnly = [regex]::Replace($text, '(?s)<#.*?#>', '')
        $codeLines = @($codeOnly -split "`n" | Where-Object { $_.TrimStart() -notmatch '^#' })
        $codeText = $codeLines -join "`n"
        if ($codeText -match 'powershell\.exe') {
            $problems.Add("$relative вызывает Windows PowerShell 5.1")
        }
    }

    return New-CheckResult -Name 'project PowerShell code targets pwsh 7.6' -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Get-ProjectSourceFile {
    <#
    .SYNOPSIS
        Файлы собственных деревьев проекта, без служебных и сторонних каталогов.

    .DESCRIPTION
        Отличие от общего обхода: осматриваются только деревья, которые проект
        ведёт сам, плюс файлы корня. `community` — опубликованная обвязка: без неё
        новые файлы поставки остались бы вне проверки секретоносных имён.
        Каталог временных файлов, распакованный
        комплект JDK и песочницы сторонних пакетов исключены явно: иначе проверка
        секретоносных имён читает чужие данные и печатает их как находки проекта.
    #>
    $ownTrees = @('src', 'tools', 'tests', 'config', 'docs', 'scripts', 'admin', 'bench', 'catalog', 'community')
    $result = [System.Collections.Generic.List[System.IO.FileInfo]]::new()
    $examined = 0

    foreach ($tree in $ownTrees) {
        $dir = Join-Path $script:ProjectRoot $tree
        if (-not (Test-Path -LiteralPath $dir -PathType Container)) { continue }
        foreach ($file in (Get-ChildItem -LiteralPath $dir -Recurse -File -Force -ErrorAction SilentlyContinue)) {
            if ($file.FullName -match '\\(?:__pycache__|site-packages|node_modules|vendor|bower_components)\\') { continue }
            $result.Add($file)
            $examined++
        }
    }

    foreach ($file in (Get-ChildItem -LiteralPath $script:ProjectRoot -File -Force -ErrorAction SilentlyContinue)) {
        $result.Add($file)
        $examined++
    }

    return @{ Files = $result; Examined = $examined }
}

function Test-SourceMaterialHygiene {
    <#
    .SYNOPSIS
        Резервные копии исходников не разрослись сверх baseline.

    .DESCRIPTION
        Измерено при передаче: три копии с суффиксом обслуживания лежат рядом с
        рабочими файлами в деревьях кода. Копия исходника — источник устаревших
        фактов: чтец по маске увидит обе версии и не отличит рабочую от прежней.
        Проверка не требует удалить существующие, но сообщает о новых.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0

    $baseline = @(
        'src/ragraf/cli.py.bak-ensure',
        'src/ragraf/mcp_server.py.bak-ensure',
        'tests/test_init_project.py.bak-ensure'
    )

    foreach ($root in @('src', 'tools', 'tests', 'scripts')) {
        $dir = Join-Path $script:ProjectRoot $root
        if (-not (Test-Path -LiteralPath $dir -PathType Container)) { continue }
        foreach ($file in (Get-ChildItem -LiteralPath $dir -Recurse -File -Force -ErrorAction SilentlyContinue)) {
            if ($file.FullName -match $script:CodeExclusionPattern) { continue }
            if (-not ($file.Name -match '\.(bak|orig|old|save|before|after)(\.|$)' -or $file.Name -match '\.bak-')) { continue }
            $examined++
            $relative = Get-RelativePath $file.FullName
            if ($baseline -contains $relative) { continue }
            $problems.Add("новая копия исходника рядом с рабочим файлом: $relative")
        }
    }

    if ($examined -eq 0) {
        return New-CheckResult -Name 'source backup copies stayed within the baseline' -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: ни одной копии не найдено, значит обход каталогов разошёлся с деревом проекта')
    }

    return New-CheckResult -Name 'source backup copies stayed within the baseline' -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems -Warning:($problems.Count -gt 0)
}

function Test-DatabaseLayerHonesty {
    <#
    .SYNOPSIS
        Слой живых баз: персональные данные не собираются, отпечаток детерминирован.

    .DESCRIPTION
        Проверка формулируется через СВОЙСТВО, а не через написание. Это не
        придирка: в этом проекте уже дважды был ложный FAIL из-за правила,
        искавшего буквальную строку (`record_run`, а затем
        `capability = str(cpg_info...)`). Оба раза код был исправен.

        Что проверяется:

        * **Значения персональных колонок не собираются.** Моделируются колонки,
          среди которых есть заведомо персональные в трёх формах написания
          (`FirstName`, `first_name`, `E-Mail`), и проверяется, что классификация
          отмечает их все. Формы взяты по факту: измерено на живой базе, что
          проверка по подстроке НЕ ловила `FirstName` и `E-Mail`, и имя человека
          попадало бы в индекс.
        * **Колонка удаляется из запроса, а не фильтруется после.** В модели
          запроса персональных колонок быть не должно вовсе — иначе значение
          успевает попасть в память процесса сбора.
        * **Скрытая колонка помечена, а не пуста.** Пустое значение агент прочитает
          как «поля нет», и это неотличимо от данных.
        * **Отпечаток структуры детерминирован.** Две модели с РАЗНЫМ порядком
          добавления таблиц и колонок обязаны дать один отпечаток: иначе сторож
          свежести будет переиндексировать корпус бесконечно.
        * **Отпечаток не зависит от данных.** Изменение оценки числа строк не
          должно менять отпечаток: иначе вставка строк пересобирала бы корпус.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0

    $probe = Join-Path $script:ProjectRoot 'tools/check_db_layer.py'
    if (-not (Test-Path -LiteralPath $probe)) {
        return New-CheckResult -Name 'database layer keeps personal data out and fingerprints deterministically' `
            -Ok $false -Examined 0 -Details @('NOT-IMPLEMENTED: нет tools/check_db_layer.py')
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        return New-CheckResult -Name 'database layer keeps personal data out and fingerprints deterministically' `
            -Ok $false -Examined 0 -Details @('NOT-IMPLEMENTED: python не найден в PATH')
    }

    $output = & $python.Source $probe 2>&1
    $code = $LASTEXITCODE
    $text = ($output | Out-String).Trim()
    Write-Host "    проверка слоя баз: $text"

    # Разбираются ВЕРДИКТЫ, а не наличие строк: проверка сама сообщает, сколько
    # свойств осмотрела и какие не выполняются.
    $lines = $output | Where-Object { $_ -is [string] }
    $examined = ([regex]::Match($text, 'осмотрено:\s*(\d+)')).Groups[1].Value
    if (-not $examined) { $examined = 0 } else { $examined = [int]$examined }

    if ($code -ne 0) {
        foreach ($line in $lines) {
            if ($line -match '^\s*FAIL:') { $problems.Add(($line -replace '^\s*FAIL:\s*', '')) }
        }
        if ($problems.Count -eq 0) {
            $problems.Add("проверка слоя баз завершилась кодом $code без расшифровки")
        }
    }

    return New-CheckResult -Name 'database layer keeps personal data out and fingerprints deterministically' `
        -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}


# ------------------------------------------------------------------- запуск

function Test-GlobalMapIsolation {
    <#
    Свойство: карта метаданных не может влиять на свежесть корпуса и не хранит
    содержимое.

    Почему проверка именно такая. Сумма корпуса считается по файлам, которые
    пойдут в индекс (`src/ragraf/corpus.py`). Если бы карта читалась сканером,
    сторож пересобирал бы корпус бесконечно — это уже измеренный класс дефекта.
    Поэтому проверяются три отношения, а не написание одного литерала:
      1. хранилище карты лежит в `catalog/`, а не в `.ragraf`;
      2. ни сканер, ни сторож, ни индексатор карту не импортируют;
      3. в таблицах карты нет колонок под текст/фрагмент/хэш содержимого.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0
    $mapPath = Join-Path $script:ProjectRoot 'src/ragraf/global_map.py'
    if (-not (Test-Path -LiteralPath $mapPath)) {
        return New-CheckResult -Name 'global map stays outside the corpus and holds no content' -Ok $false -Examined 0 -Details @('NOT-IMPLEMENTED: нет src/ragraf/global_map.py')
    }
    $examined++
    $mapText = Get-ProjectText $mapPath
    if ($mapText -notmatch 'CATALOG_DIR' -or $mapText -notmatch 'global_map\.sqlite') {
        $problems.Add('хранилище карты не привязано к catalog/: оно не должно лежать внутри .ragraf')
    }
    foreach ($name in @('corpus.py', 'indexer.py', 'watch.py')) {
        $path = Join-Path $script:ProjectRoot ("src/ragraf/" + $name)
        if (-not (Test-Path -LiteralPath $path)) { continue }
        $examined++
        $text = Get-ProjectText $path
        if ($text -match 'global_map|GlobalMap') {
            $problems.Add("$name читает карту: тогда карта может попасть в сумму корпуса и зациклить сторожа")
        }
    }
    $allowed = @{
        'file_meta' = @('source_id', 'rel_path', 'name', 'ext', 'size', 'mtime', 'mime', 'availability', 'first_seen', 'last_seen')
        'secret_place' = @('source_id', 'rel_path', 'rule', 'size', 'mtime')
    }
    foreach ($table in $allowed.Keys) {
        $m = [regex]::Match($mapText, "CREATE TABLE IF NOT EXISTS $table\s*\((?<body>[\s\S]*?)\);")
        if (-not $m.Success) {
            $problems.Add("не найдено описание таблицы $table")
            continue
        }
        $examined++
        $columns = @([regex]::Matches($m.Groups['body'].Value, '(?m)^\s*([a-z_]+)\s+') | ForEach-Object { $_.Groups[1].Value })
        $unknown = @($columns | Where-Object { $allowed[$table] -notcontains $_ })
        if ($unknown.Count -gt 0) {
            $problems.Add("$table содержит колонки вне разрешённого набора метаданных: " + ($unknown -join ', '))
        }
    }
    return New-CheckResult -Name 'global map stays outside the corpus and holds no content' -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Test-RouteAndDocumentWiring {
    <#
    Свойства двух новых слоёв:
      1. маршрут НЕ читает содержимое файлов (иначе он превращается в поиск,
         который цитирует без провенанса);
      2. документ извлекается одним и тем же кодом при индексации и при
         `live_read` — иначе номера строк разойдутся;
      3. маршрут объявляет провенанс `registry`, а не `index`.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0
    $routePath = Join-Path $script:ProjectRoot 'src/ragraf/route.py'
    $facadePath = Join-Path $script:ProjectRoot 'src/ragraf/facade.py'
    $indexerPath = Join-Path $script:ProjectRoot 'src/ragraf/indexer.py'
    foreach ($path in @($routePath, $facadePath, $indexerPath)) {
        if (-not (Test-Path -LiteralPath $path)) {
            return New-CheckResult -Name 'route and documents share one extraction contract' -Ok $false -Examined 0 -Details @("NOT-IMPLEMENTED: нет $path")
        }
    }
    $examined++
    $routeText = Get-ProjectText $routePath
    if ($routeText -match 'read_bytes|read_text|documents\.extract') {
        $problems.Add('route.py читает содержимое файлов: маршрут обязан отвечать «куда идти», не цитируя')
    }
    $examined++
    $facadeText = Get-ProjectText $facadePath
    $start = $facadeText.IndexOf('def op_route')
    if ($start -lt 0) {
        $problems.Add('в facade.py нет op_route')
    } else {
        $next = $facadeText.IndexOf('    def op_', $start + 10)
        $body = if ($next -gt $start) { $facadeText.Substring($start, $next - $start) } else { $facadeText.Substring($start) }
        if ($body -notmatch 'provenance="registry"') {
            $problems.Add('op_route не объявляет provenance=registry: факты карты выдаются за факты индекса')
        }
    }
    $examined++
    $liveStart = $facadeText.IndexOf('def op_live_read')
    if ($liveStart -lt 0) {
        $problems.Add('в facade.py нет op_live_read')
    } else {
        $liveNext = $facadeText.IndexOf('    def op_', $liveStart + 10)
        $liveBody = if ($liveNext -gt $liveStart) { $facadeText.Substring($liveStart, $liveNext - $liveStart) } else { $facadeText.Substring($liveStart) }
        if ($liveBody -notmatch '_documents') {
            $problems.Add('op_live_read не использует извлекатель документов: строки разойдутся с индексом')
        }
    }
    $examined++
    $indexerText = Get-ProjectText $indexerPath
    if ($indexerText -notmatch 'documents\.extract') {
        $problems.Add('indexer.py не извлекает текст документов: PDF/DOCX попадут в индекс байтами')
    }
    if ($indexerText -notmatch 'DOCUMENT_LANG') {
        $problems.Add('indexer.py не различает язык document')
    }
    $corpusPath = Join-Path $script:ProjectRoot 'src/ragraf/corpus.py'
    if (Test-Path -LiteralPath $corpusPath) {
        $examined++
        $corpusText = Get-ProjectText $corpusPath
        if ($corpusText -notmatch 'DOCUMENT_LANG') {
            $problems.Add('corpus.py отсекает документы как двоичные: язык document не учтён')
        }
    }
    return New-CheckResult -Name 'route and documents share one extraction contract' -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Test-AnalyticsLocalOnly {
    <#
    Свойства аналитики:
      1. ни экспорт, ни отчёт не открывают сеть (нет клиентов HTTP/Socket);
      2. удаление журнала стоит ПОСЛЕ проверки резервной копии — иначе
         «очистить для замера» означало бы потерять историю.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0
    foreach ($name in @('src/ragraf/analytics.py', 'src/ragraf/feedback.py',
                       'src/ragraf/sources.py', 'src/ragraf/global_map.py',
                       'src/ragraf/route.py', 'src/ragraf/documents.py')) {
        $path = Join-Path $script:ProjectRoot $name
        if (-not (Test-Path -LiteralPath $path)) {
            $problems.Add("нет $name")
            continue
        }
        $examined++
        $text = Get-ProjectText $path
        if ($text -match 'urllib|requests\.|httpx|socket\.|http\.client|aiohttp') {
            $problems.Add("$name содержит сетевой клиент: аналитика обязана оставаться локальной")
        }
    }
    $analyticsPath = Join-Path $script:ProjectRoot 'src/ragraf/analytics.py'
    if (Test-Path -LiteralPath $analyticsPath) {
        $examined++
        $text = Get-ProjectText $analyticsPath
        $backupAt = $text.IndexOf('backup.exists()')
        $deleteAt = $text.IndexOf('DELETE FROM usage_call')
        if ($backupAt -lt 0 -or $deleteAt -lt 0 -or $backupAt -gt $deleteAt) {
            $problems.Add('удаление журнала не проверяет резервную копию ДО удаления')
        }
    }
    return New-CheckResult -Name 'new layers stay local (no outbound clients); analytics deletes only after a backup' -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Test-LicenseMetadataAgreement {
    <#
    .SYNOPSIS
        Юридические метаданные говорят одно и то же во всех источниках.

    .DESCRIPTION
        Измеренный дефект (24.10.2026): в `LICENSE` лежал PolyForm Noncommercial,
        `pyproject.toml` объявлял MIT, `README.md` утверждал, что в метаданных
        стоит PolyForm, а строки правообладателя в `LICENSE` не было вовсе.
        Три источника — три разных ответа, и ни один из них не был сверен
        с другим: расхождение жило месяцами и не мешало ни сборке, ни тестам.

        Проверка формулируется через СВОЙСТВО, а не через написание. Выражение
        лицензии нормализуется тем же кодом, которым пользуется сборка пакета
        (`packaging.licenses`), опознанная по ЗАГОЛОВКУ текста лицензия
        сравнивается с ним, отдельно проверяются строка правообладателя,
        заполненность параметров BSL и нижняя граница setuptools. Именно так
        видны два дефекта, невидимых глазами: файл без правообладателя и
        `setuptools>=69` при `license-files`, то есть PEP 639, поддержка
        которого начинается с 77 — сборка проходила случайно.

        Отдельно проверяется, что README ССЫЛАЕТСЯ на файлы лицензии, а не
        пересказывает их: пересказ уже один раз разошёлся с фактом.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0

    $probe = Join-Path $script:ProjectRoot 'tools/check_licensing.py'
    if (-not (Test-Path -LiteralPath $probe)) {
        return New-CheckResult -Name 'license metadata agrees across all sources' `
            -Ok $false -Examined 0 -Details @('NOT-IMPLEMENTED: нет tools/check_licensing.py')
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        return New-CheckResult -Name 'license metadata agrees across all sources' `
            -Ok $false -Examined 0 -Details @('NOT-IMPLEMENTED: python не найден в PATH')
    }

    $output = & $python.Source $probe 2>&1
    $code = $LASTEXITCODE
    $text = ($output | Out-String).Trim()
    Write-Host "    проверка лицензии: $text"

    # Разбираются ВЕРДИКТЫ, а не наличие строк: проверка сама сообщает, сколько
    # свойств осмотрела и какие не выполняются.
    $lines = $output | Where-Object { $_ -is [string] }
    $examined = ([regex]::Match($text, 'осмотрено:\s*(\d+)')).Groups[1].Value
    if (-not $examined) { $examined = 0 } else { $examined = [int]$examined }

    if ($code -ne 0) {
        foreach ($line in $lines) {
            if ($line -match '^\s*FAIL:') { $problems.Add(($line -replace '^\s*FAIL:\s*', '')) }
        }
        if ($problems.Count -eq 0) {
            $problems.Add("проверка лицензии завершилась кодом $code без расшифровки")
        }
    }

    return New-CheckResult -Name 'license metadata agrees across all sources' `
        -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Test-CommunityWrapper {
    <#
    .SYNOPSIS
        Обвязка поставки лежит в живом дереве, названа в таблице источников
        и установщик в дереве один.

    .DESCRIPTION
        Измеренный дефект (24.10.2026): продуктовая обвязка (`CHANGELOG`,
        `CONTRIBUTING`, `SECURITY`, `ROADMAP`, `README.ru`, `CITATION`,
        `CODE_OF_CONDUCT`, `mkdocs.yml`, `.github/**`) существовала ТОЛЬКО
        в опубликованном репозитории, установщик — в двух разных версиях
        (34 242 против 37 794 байт), а живой `ci.yml:125` вызывал
        `-SummaryJson`, которого в живом установщике не было вовсе. Сборка
        «из живого дерева» теряла обвязку целиком, и увидеть это можно было
        только сверкой, которой никто не делал.

        Проверка формулируется через СВОЙСТВО, а не через написание. Состав
        файлов, согласие хэшей с таблицей источников, гигиена путей и
        соответствие параметров установщика своему CI проверяются скриптом
        `tools/check_community_wrapper.py`; здесь к ним добавляется то, что
        умеет только PowerShell: единственность установщика в дереве и разбор
        его текста парсером.

        Молчаливый пропуск запрещён: нет скрипта или нет python — это провал,
        а не «нечего проверять», иначе проверка зеленеет, ничего не осмотрев.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0

    $probe = Join-Path $script:ProjectRoot 'tools/check_community_wrapper.py'
    if (-not (Test-Path -LiteralPath $probe)) {
        return New-CheckResult -Name 'publication wrapper is present, sourced and has one installer' `
            -Ok $false -Examined 0 -Details @('NOT-IMPLEMENTED: нет tools/check_community_wrapper.py')
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        return New-CheckResult -Name 'publication wrapper is present, sourced and has one installer' `
            -Ok $false -Examined 0 -Details @('NOT-IMPLEMENTED: python не найден в PATH')
    }

    $output = & $python.Source $probe 2>&1
    $code = $LASTEXITCODE
    $text = ($output | Out-String).Trim()
    Write-Host "    проверка обвязки: $text"

    $lines = $output | Where-Object { $_ -is [string] }
    $examinedText = ([regex]::Match($text, 'осмотрено:\s*(\d+)')).Groups[1].Value
    $examined = if ($examinedText) { [int]$examinedText } else { 0 }

    if ($code -ne 0) {
        foreach ($line in $lines) {
            if ($line -match '^\s*FAIL:') { $problems.Add(($line -replace '^\s*FAIL:\s*', '')) }
        }
        if ($problems.Count -eq 0) {
            $problems.Add("проверка обвязки завершилась кодом $code без расшифровки")
        }
    }

    # Установщик в дереве один. Каталоги, которых в поставке нет по определению,
    # исключаются явно; `community` не исключается: там лежит опубликованный
    # установщик верхнего уровня, и его наличие — часть решения, а не помеха.
    $installers = @(Get-ChildItem -LiteralPath $script:ProjectRoot -Recurse -File -Filter 'install.ps1' -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch '\\(community|\.staging|\.venv|\.tools|\.zwork|\.ragraf|node_modules|\.git)\\' })
    $examined++
    if ($installers.Count -ne 1) {
        $names = ($installers | ForEach-Object { Get-RelativePath $_.FullName }) -join ', '
        $problems.Add("установщиков с именем install.ps1 вне community/ и служебных каталогов: $($installers.Count) ($names) — источник обязан быть один")
    }
    else {
        $relative = Get-RelativePath $installers[0].FullName
        $examined++
        if ($relative -ne 'scripts/install.ps1') {
            $problems.Add("единственный установщик — $relative, а не scripts/install.ps1")
        }
        $examined++
        $parseErrors = $null
        $null = [System.Management.Automation.Language.Parser]::ParseFile($installers[0].FullName, [ref]$null, [ref]$parseErrors)
        if ($parseErrors.Count -gt 0) {
            $problems.Add("scripts/install.ps1 не разбирается парсером: ошибок $($parseErrors.Count), первая — $($parseErrors[0].Message)")
        }
    }

    if ($examined -eq 0) {
        return New-CheckResult -Name 'publication wrapper is present, sourced and has one installer' `
            -Ok $false -Examined 0 -Details @('NOT-IMPLEMENTED: ни одного объекта не осмотрено — это ложный PASS')
    }

    return New-CheckResult -Name 'publication wrapper is present, sourced and has one installer' `
        -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Test-ReleaseManifest {
    <#
    .SYNOPSIS
        Поставка собирается по манифесту, собранное дерево проходит проверяльщик,
        а гейт ловит все четыре заведомые порчи.

    .DESCRIPTION
        Измеренный дефект (этапы 1-2, 24.10.2026): публикация собиралась руками
        в `.staging/release-v0.2` и отстала от кода на десять дней — обвязки
        (`CHANGELOG`, `CONTRIBUTING`, `SECURITY`, `CITATION`, `README.ru`,
        `.github/**`) в живом дереве не было вовсе, установщик существовал
        в трёх версиях. Манифест и сборщик решают это только вместе с проверкой:
        манифест без проверки сам становится четвёртой версией правды, и
        расхождение с деревом никто не заметит.

        Проверка формулируется через СВОЙСТВО, а не через написание строк.
        Состав манифеста, гигиена путей, осмысленность причин у исключений,
        соответствие пометок `active`/`guard` факту, сборка в песочницу, восемь
        правил проверяльщика и гейт на четырёх порчах проверяет
        `tools/check_release_manifest.py`; здесь добавляется то, что видно только
        из PowerShell: файлы конвейера на месте и проверка не оставляет мусора
        в живом дереве.

        Молчаливый пропуск запрещён: нет скрипта или нет python — это провал,
        а не «нечего проверять», иначе проверка зеленеет, ничего не осмотрев.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0

    $pipeline = @(
        'tools/release/manifest.toml'
        'tools/release/relcommon.py'
        'tools/release/build_release.py'
        'tools/release/verify_release.py'
        'tools/release/gate_release.py'
        'tools/check_release_manifest.py'
    )
    foreach ($relative in $pipeline) {
        $examined++
        if (-not (Test-Path -LiteralPath (Join-Path $script:ProjectRoot $relative) -PathType Leaf)) {
            $problems.Add("NOT-IMPLEMENTED: нет $relative — конвейер поставки неполон")
        }
    }
    if ($problems.Count -gt 0) {
        return New-CheckResult -Name 'release tree is built from the manifest and survives the gate' `
            -Ok $false -Examined $examined -Details $problems
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        return New-CheckResult -Name 'release tree is built from the manifest and survives the gate' `
            -Ok $false -Examined 0 -Details @('NOT-IMPLEMENTED: python не найден в PATH')
    }

    $probe = Join-Path $script:ProjectRoot 'tools/check_release_manifest.py'
    $output = & $python.Source $probe 2>&1
    $code = $LASTEXITCODE
    $text = ($output | Out-String).Trim()
    Write-Host "    проверка поставки: $text"

    $lines = $output | Where-Object { $_ -is [string] }
    $examinedText = ([regex]::Match($text, 'осмотрено:\s*(\d+)')).Groups[1].Value
    if ($examinedText) { $examined += [int]$examinedText }

    if ($code -ne 0) {
        foreach ($line in $lines) {
            if ($line -match '^\s*FAIL:') { $problems.Add(($line -replace '^\s*FAIL:\s*', '')) }
        }
        if ($problems.Count -eq 0) {
            $problems.Add("проверка поставки завершилась кодом $code без расшифровки")
        }
    }

    # Проверка обязана убирать за собой: песочница сборки создаётся во временном
    # каталоге, и её след в живом дереве означал бы, что сборка писала внутрь
    # индексируемого корпуса.
    $examined++
    $leftovers = @(Get-ChildItem -LiteralPath $script:ProjectRoot -Recurse -Directory -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like 'ragraf-manifest-check-*' -or $_.Name -like 'ragraf-gate-*' -or $_.Name -like 'ragraf-release-*' })
    if ($leftovers.Count -gt 0) {
        $names = ($leftovers | ForEach-Object { Get-RelativePath $_.FullName }) -join ', '
        $problems.Add("в живом дереве остались песочницы конвейера поставки: $names")
    }

    if ($examined -eq 0) {
        return New-CheckResult -Name 'release tree is built from the manifest and survives the gate' `
            -Ok $false -Examined 0 -Details @('NOT-IMPLEMENTED: ни одного объекта не осмотрено — это ложный PASS')
    }

    return New-CheckResult -Name 'release tree is built from the manifest and survives the gate' `
        -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

$script:CheckFunctions = @(
    'Test-ProjectLayout'
    'Test-SecretHygiene'
    'Test-RegistryCoverage'
    'Test-ExcludeInvariants'
    'Test-DedupeDeterminism'
    'Test-StoragePragmas'
    'Test-RevisionRules'
    'Test-CapabilityHonesty'
    'Test-JvmHeapSource'
    'Test-LanguageRouting'
    'Test-LexvecSchemaContract'
    'Test-McpClientCompatibility'
    'Test-AccessHygiene'
    'Test-ModeHasProbe'
    'Test-ExternalOpsAreRecorded'
    'Test-DocumentedNumbers'
    'Test-PowerShellVintage'
    'Test-SourceMaterialHygiene'
    'Test-DatabaseLayerHonesty'
    'Test-GlobalMapIsolation'
    'Test-RouteAndDocumentWiring'
    'Test-AnalyticsLocalOnly'
    'Test-LicenseMetadataAgreement'
    'Test-CommunityWrapper'
    'Test-ReleaseManifest'
)

$script:CheckNames = @()
foreach ($name in $script:CheckFunctions) { $script:CheckNames += $name }

Write-Host 'project doctrine check'
Write-Host "repository: $script:ProjectRoot"
Write-Host ''

$errors = 0
$warnings = 0
$notApplicable = 0
$voidChecks = 0

foreach ($name in $script:CheckFunctions) {
    $check = & $name
    Write-CheckResult -Check $check

    switch ($check.Kind) {
        'na'   { $notApplicable++ }
        'warn' { $warnings++ }
        'void' { $voidChecks++; $errors++ }
        'pass' { }
        default {
            if ($check.Warning -and -not $Strict) { $warnings++ } else { $errors++ }
        }
    }
}

Write-Host ''
Write-Host "checks: $($script:CheckFunctions.Count), errors: $errors, warnings: $warnings, not-applicable: $notApplicable"

if ($voidChecks -gt 0) {
    Write-Host "$voidChecks проверок сообщили ложный PASS (осмотрено 0 объектов) - это дефект чекера, а не проекта." -ForegroundColor Red
}

if ($errors -gt 0) {
    Write-Host 'Result: FAIL' -ForegroundColor Red
    exit 1
}

if ($warnings -gt 0) {
    Write-Host 'Result: PASS with warnings' -ForegroundColor Yellow
    exit 0
}

Write-Host 'Result: PASS' -ForegroundColor Green
exit 0
