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
    37 проверок — ровно столько, сколько записей в реестре `$script:CheckFunctions`;
    это сверяет Test-DocumentedNumbers. Прежние две ошибки («запись прогона в реестр
    не покрывала путь ensure» и «документы называли 18 и 16 инструментов там, где
    в коде 23») закрыты
    9 октября 2026; на каждую есть регрессионная защита. Правило чтения прогона:
    любая ошибка или новое предупреждение — находка. Ожидаемого красного
    состояния у чекера больше нет: если он падает — это либо новый дефект,
    либо сломанное исправление.

    27.10.2026 добавлена проверка терминологии переводов (Test-Glossary): термин
    основного документа назван в каждом переводе одним из разрешённых слов, и
    объявлено это ДАННЫМИ (docs/03_implementation/glossary.json), а не словарём в
    коде. Граница та же, что у состояний переводов: проверяется слово, а не смысл
    фразы, — зелёный результат не означает «перевод верен».

    27.10.2026 добавлена проверка прозы переводов (Test-ProseProfile): в каждом
    разделе считается число абзацев прозы, в каждом абзаце — число предложений, и
    расхождение с README.md сверх объявленного допуска это находка. Допуски лежат
    ДАННЫМИ (docs/03_implementation/prose.json) и сняты замером — ноль по абзацам
    и ноль по предложениям. Граница названа и здесь: проверяется счёт, а не смысл
    предложения; проза в списках, таблицах и блоках команд не считается.

    27.10.2026 добавлена проверка адресов переводов (Test-TranslationAddresses):
    каждый внешний адрес и каждое изображение основного документа названы в
    переводе ТЕМ ЖЕ текстом цели, а каждый якорь указывает на существующий
    заголовок своего файла и стоит в том же порядке, что в README.md. Строгость и
    допуск объявлены ДАННЫМИ (docs/03_implementation/addresses.json). Граница
    названа и здесь: сравнивается текст цели и место раздела, а доступность адреса
    по сети не проверяется — сети у проверки нет.

.PARAMETER ProjectRoot
    Корень проекта. По умолчанию — каталог двумя уровнями выше скрипта, то есть
    корректно, когда скрипт лежит в каталоге инструментов проекта.

.PARAMETER MaxExamples
    Сколько примеров печатать на проваленную проверку. По умолчанию 10.

.PARAMETER Strict
    Считать предупреждения ошибками. Публикация обязана идти с этим ключом:
    отставание документа от индекса на публикации — ошибка, а не предупреждение.

.PARAMETER Only
    Прогнать одну проверку, имя которой содержит эту подстроку. Нужно там, где
    проверяется поведение САМОЙ проверки (песочница на три исхода), а не
    состояние проекта: гонять все 32 ради одной — минуты впустую.
    Итоговая строка тогда называет и число прогнанных, и полный размер реестра.

.EXAMPLE
    pwsh -NoLogo -NoProfile -NonInteractive -File .\Invoke-ProjectDoctrine.ps1

.EXAMPLE
    pwsh -NoLogo -NoProfile -NonInteractive -File .\Invoke-ProjectDoctrine.ps1 -Strict
#>

[CmdletBinding()]
param(
    [string] $ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [int] $MaxExamples = 10,
    [switch] $Strict,
    [string] $Only = ''
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
        Репозиторий всё ещё тот проект, который описан в паспорте, и копия
        навыка не разошлась с проектом молча.

    .DESCRIPTION
        Защита от запуска старого чекера на переехавшем дереве: иначе прогон
        рапортует чисто, осмотрев пустоту.

        Второе свойство (ПРОМТ-И11): файл навыка в копии либо совпадает с
        проектным побайтово, либо различие ОБЪЯВЛЕНО в
        `docs/skills/ragraf-core/VARIANTS.json`. Измеренный дефект: требование
        `AGENTS.md` §11 «копия совпадает побайтово» не выполнялось
        (`operations.md` в копии называл 39 операций против 49), а копии при этом
        совпадали МЕЖДУ СОБОЙ — то есть сравнение копий друг с другом ничего не
        обнаруживало. Проверяется свойство, а не написание: решение принимается
        по sha256 файлов, а правила объявления (существование имён инструментов,
        числа против кода) исполняет `tools/check_skill_variants.py`.
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
        'docs/02_architecture/TZ.md', 'docs/02_architecture/ACCEPTANCE.md',
        'docs/skills/ragraf-core/SKILL.md', 'docs/skills/ragraf-core/VARIANTS.json',
        'docs/skills/ragraf-core/reference/operations.md',
        'docs/skills/ragraf-core/reference/repair.md',
        'tools/check_skill_variants.py', 'tools/sync_skill_variants.py'
    )

    foreach ($relative in $expected) {
        $examined++
        if (-not (Test-Path -LiteralPath (Join-Path $script:ProjectRoot $relative) -PathType Leaf)) {
            $problems.Add("missing: $relative")
        }
    }

    # Копии навыка: объявление покрывает все markdown-файлы навыка. Это то, что
    # видно только из дерева; байты и правила объявления проверяет скрипт ниже.
    $skillsDir = Join-Path $script:ProjectRoot 'docs/skills/ragraf-core'
    $variantsPath = Join-Path $skillsDir 'VARIANTS.json'
    $examined++
    if (-not (Test-Path -LiteralPath $variantsPath -PathType Leaf)) {
        $problems.Add('нет docs/skills/ragraf-core/VARIANTS.json: различие копий навыка объявлять нечем, а без объявления копия обязана совпадать побайтово')
    }
    else {
        $examined++
        $declaration = $null
        try {
            $declaration = Get-ProjectText $variantsPath | ConvertFrom-Json -AsHashtable
        }
        catch {
            $problems.Add("VARIANTS.json не разбирается: $($_.Exception.Message)")
        }
        if ($null -ne $declaration) {
            $declared = @()
            if ($declaration.ContainsKey('files')) {
                foreach ($name in @($declaration['files'])) { $declared += ([string]$name -replace '\\', '/') }
            }
            if ($declared.Count -eq 0) {
                $problems.Add('VARIANTS.json не перечисляет файлы навыка: объявление непроверяемо')
            }
            $present = @()
            if (Test-Path -LiteralPath $skillsDir -PathType Container) {
                $present = @(Get-ChildItem -LiteralPath $skillsDir -Recurse -File -Filter '*.md' -Force |
                    Where-Object { $_.Name -notmatch '\.bak' } |
                    ForEach-Object { (Get-RelativePath $_.FullName).Replace('docs/skills/ragraf-core/', '') })
            }
            $examined += $present.Count
            if ($present.Count -eq 0) {
                $problems.Add('в docs/skills/ragraf-core нет ни одного markdown-файла: обход разошёлся с деревом')
            }
            foreach ($name in $present) {
                if ($declared -notcontains $name) {
                    $problems.Add("файл навыка $name не объявлен в VARIANTS.json: копию такого файла не проверяет никто")
                }
            }
            foreach ($name in $declared) {
                if (-not (Test-Path -LiteralPath (Join-Path $skillsDir $name) -PathType Leaf)) {
                    $problems.Add("VARIANTS.json объявляет $name, а в источнике его нет")
                }
            }
        }
    }

    # sha256 копий и правила объявления. Нет скрипта или нет python — это провал,
    # а не «нечего проверять»: иначе проверка зеленеет, ничего не сравнив.
    $skillProbe = Join-Path $script:ProjectRoot 'tools/check_skill_variants.py'
    $python = Get-Command python -ErrorAction SilentlyContinue
    $examined++
    if (-not (Test-Path -LiteralPath $skillProbe -PathType Leaf)) {
        $problems.Add('нет tools/check_skill_variants.py: сравнить байты копий навыка нечем')
    }
    elseif (-not $python) {
        $problems.Add('python не найден в PATH: sha256 копий навыка проверить нечем')
    }
    else {
        $beforeProblems = $problems.Count
        $skillOutput = & $python.Source $skillProbe 2>&1
        $skillCode = $LASTEXITCODE
        $skillText = ($skillOutput | Out-String)
        $summary = [regex]::Match($skillText, 'осмотрено:\s*(\d+)')
        if ($summary.Success) {
            $examined += [int]$summary.Groups[1].Value
            Write-Host ("    копии навыка: осмотрено " + [int]$summary.Groups[1].Value)
        }
        else {
            $problems.Add('проверка копий навыка не напечатала «осмотрено: N»: итог неизвестен')
        }
        if ($skillCode -ne 0) {
            foreach ($line in $skillOutput) {
                if ($line -is [string] -and $line -match '^\s*FAIL:\s*(.+)$') {
                    $problems.Add('копии навыка: ' + $Matches[1])
                }
            }
            if ($problems.Count -eq $beforeProblems) {
                $problems.Add("проверка копий навыка завершилась кодом $skillCode без расшифровки")
            }
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
    # Законные исключения — ключи, которые обязаны существовать:
    #   * `secrets.key` — мастер-ключ хранилища секретов;
    #   * `audit-signing.key` — ключ подписи выгрузок аудита (этап И5). Он
    #     ОТДЕЛЬНЫЙ намеренно: компрометация ключа подписи позволяет подделать
    #     утверждение «набор собран нами», но не открыть сохранённые пароли.
    # Оба лежат вне корпуса (`catalog/**`), права сужены до текущей учётной
    # записи, в git не попадают, значения не читаются ни проверкой, ни
    # индексатором.
    $baseline = @('catalog/secrets.key', 'catalog/audit-signing.key')

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
    # $notes — сообщения о том, что проверено и отложено. Они печатаются в
    # строке результата, но в $problems не попадают: «сверка отложена» не
    # находка и не провал.
    $notes = [System.Collections.Generic.List[string]]::new()
    # $lagNotes — предупреждения об ОТСТАВАНИИ: названные числа были верны на
    # одной из прошлых ревизий. Это не ложь и не находка, но и не PASS:
    # список отдельный, чтобы различие двух исходов было видно в коде, а не
    # только в тексте сообщения.
    $lagNotes = [System.Collections.Generic.List[string]]::new()
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
    # Образцы терпят ОБЕ формы записи жирным: `**49 операций**` и
    # `**49** операций`. Прежний образец требовал только вторую форму, поэтому
    # первое же число README (`**48 операций**`) им не ловилось, и проверка
    # молча ничего не осматривала в этом файле — зелёный результат при
    # разошедшемся числе. Это измеренный дефект, а не стилистика: шаблон,
    # который не совпал, пропускался строкой `continue`.
    # Документы, которые называют числа поверхности продукта. Три документа
    # интеграции добавлены 25.10.2026 (ПРОМТ-И13): они отстали на 10 операций,
    # 18 инструментов, 2 подкоманды, 7 проверок и 13 языков дерева, при этом
    # читаются как действующее описание. `Kind` — из какого числа брать факт.
    $documents = @(
        @{ Path = 'README.md'; Pattern = '\*\*(\d+)\s*(?:\*\*)?\s*operations?\b'; Kind = 'ops'; Label = 'операций фасада' },
        @{ Path = 'README.md'; Pattern = '\*\*(\d+)\s*(?:\*\*)?\s*MCP tools?\b'; Kind = 'tools'; Label = 'инструментов поверхности для агентов' },
        @{ Path = 'docs/03_implementation/INTEGRATION.md'; Pattern = '\*\*(\d+)\s*(?:\*\*)?\s*инструмент'; Kind = 'tools'; Label = 'инструментов поверхности для агентов' },
        @{ Path = 'docs/05_article/RAGRAF_ARTICLE.md'; Pattern = '(\d+)\s+операци'; Kind = 'ops'; Label = 'операций фасада' },
        @{ Path = 'docs/03_implementation/HARNESS_INTEGRATION.md'; Pattern = 'сервер отдаёт \*\*(\d+) инструмент'; Kind = 'tools'; Label = 'инструментов поверхности для агентов' },
        @{ Path = 'docs/03_implementation/HARNESS_INTEGRATION.md'; Pattern = 'фасад — (\d+) операци'; Kind = 'ops'; Label = 'операций фасада' },
        @{ Path = 'docs/03_implementation/HARNESS_INTEGRATION.md'; Pattern = 'CLI — (\d+) подкоманд'; Kind = 'subcommands'; Label = 'подкоманд CLI' },
        @{ Path = 'docs/03_implementation/HARNESS_INTEGRATION.md'; Pattern = 'чекер — (\d+) провер'; Kind = 'checks'; Label = 'проверок чекера' },
        @{ Path = 'docs/03_implementation/AUTOCLAW_INTEGRATION.md'; Pattern = 'сервер отдаёт \*\*(\d+) инструмент'; Kind = 'tools'; Label = 'инструментов поверхности для агентов' },
        @{ Path = 'docs/03_implementation/AUTOCLAW_INTEGRATION.md'; Pattern = 'фасад —\s+(\d+) операци'; Kind = 'ops'; Label = 'операций фасада' },
        @{ Path = 'docs/03_implementation/AUTOCLAW_INTEGRATION.md'; Pattern = 'CLI — (\d+) подкоманд'; Kind = 'subcommands'; Label = 'подкоманд CLI' },
        @{ Path = 'docs/03_implementation/AGENTS_SETUP_AND_CAPABILITIES.md'; Pattern = '(\d+) инструмент\w* MCP'; Kind = 'tools'; Label = 'инструментов поверхности для агентов' },
        @{ Path = 'docs/03_implementation/AGENTS_SETUP_AND_CAPABILITIES.md'; Pattern = '(\d+) операци\w* фасада'; Kind = 'ops'; Label = 'операций фасада' },
        @{ Path = 'docs/03_implementation/AGENTS_SETUP_AND_CAPABILITIES.md'; Pattern = '(\d+) подкоманд\w*\s+CLI'; Kind = 'subcommands'; Label = 'подкоманд CLI' },
        @{ Path = 'docs/03_implementation/AGENTS_SETUP_AND_CAPABILITIES.md'; Pattern = '(\d+) язык\w* разбора деревом'; Kind = 'tree'; Label = 'языков разбора деревом' },
        @{ Path = 'docs/03_implementation/AGENTS_SETUP_AND_CAPABILITIES.md'; Pattern = '(\d+) текстов\w*'; Kind = 'text'; Label = 'текстовых языков' }
    )

    # Число подкоманд CLI. Измерено 25.10.2026 (ПРОМТ-И12): три документа
    # называли 19 подкоманд, а разбор аргументов объявлял 21 — `analytics`,
    # `secrets` и `edition` появились позже и в счёт не попали. Число берётся из
    # разбора аргументов, а не из литерала: добавили подкоманду — ожидание
    # сдвинулось само, а документ, отставший от кода, назван находкой.
    $subcommandCount = 0
    $cliPath = Join-Path $script:ProjectRoot 'src/ragraf/cli.py'
    if (-not (Test-Path -LiteralPath $cliPath)) {
        $problems.Add('нет src/ragraf/cli.py: число подкоманд CLI сверять не с чем')
    }
    else {
        $examined++
        $cliText = Get-ProjectText $cliPath
        $subcommands = @([regex]::Matches($cliText, '(?m)^\s*p[a-z]+ = sub\.add_parser\(\s*\n?\s*"([a-z_]+)"') |
            ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique)
        $subcommandCount = $subcommands.Count
        if ($subcommands.Count -lt 1) {
            $problems.Add('не удалось посчитать подкоманды CLI: регулярное выражение разошлось с разбором аргументов')
        }
        else {
            $subcommandDocs = @(
                @{ Path = 'README.md'; Pattern = '\*\*(\d+)\*\*\s+CLI subcommands' },
                @{ Path = 'AGENTS.md'; Pattern = '(\d+)\s+подкоманд' },
                @{ Path = 'docs/AGENTS-context.md'; Pattern = '(?m)^\*\*CLI\.\*\*\s+(\d+)\s+подкоманд' }
            )
            foreach ($doc in $subcommandDocs) {
                $full = Join-Path $script:ProjectRoot $doc.Path
                if (-not (Test-Path -LiteralPath $full)) { continue }
                $examined++
                $docText = Get-ProjectText $full
                $docMatch = [regex]::Match($docText, $doc.Pattern)
                if (-not $docMatch.Success) {
                    $problems.Add("$($doc.Path) не называет число подкоманд CLI: заявление проверить нечем")
                    continue
                }
                if ([int]$docMatch.Groups[1].Value -ne $subcommands.Count) {
                    $problems.Add("$($doc.Path) называет $($docMatch.Groups[1].Value) подкоманд CLI, а в разборе аргументов их $($subcommands.Count)")
                }
            }
        }
    }

    # Снимок индекса в README — против АКТИВНОЙ записи ревизии, а не литерала.
    # Измеренный дефект (25.10.2026): README называл 804 файла, 4 771 узел и
    # 61 080 рёбер при фактических 810 / 4 814 / 62 162, а пометка «сверены
    # 25.10.2026» выдавала прошлое состояние за измерение текущего.
    #
    # Почему сверка идёт с активной ревизией, а не с названной в документе:
    # ротация оставляет в таблице `rev` только последние ревизии (замер: из
    # rev-0282…rev-0287 осталось три), поэтому подпись «на ревизии rev-XXXX»
    # переживает два пересбора, и сверять становится не с чем. Активная запись
    # есть всегда, и её числа — это и есть текущий размер продукта.
    $readmeForSnapshot = Join-Path $script:ProjectRoot 'README.md'
    if (Test-Path -LiteralPath $readmeForSnapshot) {
        $examined++
        $readmeText = (Get-ProjectText $readmeForSnapshot) -replace [char]0x00A0, ' '
        $snapshot = [regex]::Match($readmeText, 'the RAGRAF index itself — ([\d ]+?)\s*files,\s*([\d ]+?)\s*nodes\s+and\s+([\d ]+?)\s*edges')
        if (-not $snapshot.Success) {
            $problems.Add('README не называет размеры собственного индекса: снимок нельзя сверить с фактом, а значит он не измерение')
        }
        else {
            $snapshotTool = Join-Path $script:ProjectRoot 'tools/rev_snapshot.py'
            $python = Get-Command python -ErrorAction SilentlyContinue
            if (-not (Test-Path -LiteralPath $snapshotTool) -or -not $python) {
                $problems.Add('нет tools/rev_snapshot.py или python: размеры индекса в README сверить не с чем')
            }
            else {
                $rawActive = & $python.Source $snapshotTool 2>&1
                $activeRow = $null
                # -DateKind String: ISO-дата остаётся строкой. Без этого
                # ConvertFrom-Json делает из неё [datetime], и сообщение печатает
                # её по культуре процесса (замер 25.10.2026: «10/25/2026 15:54:12»
                # в русском отчёте, месяц перед днём). Сверка даты с записью
                # ревизии должна быть посимвольной, а не зависящей от локали.
                try { $activeRow = ($rawActive | Out-String) | ConvertFrom-Json -DateKind String } catch { $activeRow = $null }
                # Ревизия, которая ещё запечатывается, — это НЕ расхождение:
                # сверять не с чем, числа появятся после seal. Измеренный дефект
                # (25.10.2026, ПРОМТ-И14): прогон в 12:51 получил от снимка нули
                # и объявил ложный FAIL, хотя через минуту тот же инструмент дал
                # 815/4828/62812. Два состояния различаются и по сообщению:
                # «сверка отложена» — не то же самое, что «числа разошлись».
                $snapshotDeferred = $false
                if ($activeRow -and $activeRow.PSObject.Properties['status'] -and
                    [string]$activeRow.status -eq 'in_progress') {
                    $snapshotDeferred = $true
                    $notes.Add("ревизия $($activeRow.rev_id) ещё запечатывается (state=$($activeRow.state), создана $($activeRow.created_at)): сверка размеров индекса отложена")
                }
                if ($snapshotDeferred) {
                    # Сверка невозможна сейчас — это не находка.
                }
                elseif (-not $activeRow -or -not $activeRow.PSObject.Properties['rev_id']) {
                    $problems.Add('активная ревизия не прочитана: сверить снимок README с текущим состоянием нечем')
                }
                else {
                    $statedFiles = [int](($snapshot.Groups[1].Value) -replace '\s', '')
                    $statedNodes = [int](($snapshot.Groups[2].Value) -replace '\s', '')
                    $statedEdges = [int](($snapshot.Groups[3].Value) -replace '\s', '')
                    if ($statedFiles -eq [int]$activeRow.files -and
                        $statedNodes -eq [int]$activeRow.nodes -and
                        $statedEdges -eq [int]$activeRow.edges) {
                        # Исход «а»: документ описывает активную ревизию — молчание.
                    }
                    else {
                        # Исходы «б» и «в» до 25.10.2026 были одним: любое
                        # расхождение объявлялось ошибкой. Измеренное следствие —
                        # живой документ, отставший на один пересбор, валил прогон
                        # точно так же, как выдуманные числа, и различие «отстал»
                        # и «врёт» не проверялось вовсе. Который из двух — решает
                        # история ревизий: было ли такое состояние у корпуса.
                        # Объяснение для случая, когда различается ТОЛЬКО число
                        # рёбер: замер 25.10.2026 на неизменном дереве — три
                        # пересбора дали 64 971 / 64 972 / 64 968 рёбер при
                        # одинаковых файлах и узлах. Число разрешённых вызовов
                        # берётся из файла CPG и от прогона к прогону меняется,
                        # поэтому «сошлось по рёбрам» не гарантирует даже
                        # повторная индексация. Без этой оговорки следующий
                        # читатель решит, что документ врёт.
                        $edgesOnlyNote = ''
                        if ($statedFiles -eq [int]$activeRow.files -and
                            $statedNodes -eq [int]$activeRow.nodes) {
                            $edgesOnlyNote = ' (различается только число рёбер: замер 25.10.2026 — оно не воспроизводится между пересборами, 64 971 / 64 972 / 64 968 при неизменном дереве)'
                        }
                        $rawMatch = & $python.Source $snapshotTool --match "$statedFiles,$statedNodes,$statedEdges" 2>&1
                        $matchRow = $null
                        try { $matchRow = ($rawMatch | Out-String) | ConvertFrom-Json -DateKind String } catch { $matchRow = $null }
                        if (-not $matchRow -or $matchRow.PSObject.Properties['error'] -or -not $matchRow.PSObject.Properties['active']) {
                            $problems.Add("README публикует $statedFiles файлов, $statedNodes узлов и $statedEdges рёбер, а активная ревизия $($activeRow.rev_id) — $($activeRow.files) / $($activeRow.nodes) / $($activeRow.edges): историю ревизий прочитать не удалось, поэтому отличить отставание от вымысла нечем")
                        }
                        elseif ($null -eq $matchRow.match) {
                            $problems.Add("README называет $statedFiles файлов, $statedNodes узлов и $statedEdges рёбер — такого состояния не было ни в одной из $($matchRow.rows) ревизий ($($matchRow.oldest)…$($matchRow.newest)): документ называет состояние, которого не было; активная ревизия $($activeRow.rev_id) — $($activeRow.files) / $($activeRow.nodes) / $($activeRow.edges)$edgesOnlyNote")
                        }
                        else {
                            $lagNotes.Add("в README отставание, а не расхождение: $statedFiles файлов, $statedNodes узлов и $statedEdges рёбер верны на $($matchRow.match.rev_id) ($($matchRow.match.created_at)); активная ревизия $($activeRow.rev_id) — $($activeRow.files) / $($activeRow.nodes) / $($activeRow.edges): обновить перед публикацией (на публикации это ошибка — tools/release/gate_release.py)$edgesOnlyNote")
                        }
                    }
                }
            }
        }
    }

    # Бюджет инструкций агента: заявление в AGENTS.md сравнивается с ФАКТИЧЕСКИМ
    # размером файла по байтам. Измеренный дефект (25.10.2026): файл заявлял
    # 65 536 байт, занимая 74 268, и ни одна проверка этого не видела — то есть
    # заявление выглядело проверенным, ничего не проверяя. Свойство, а не
    # написание: число берётся из заявления, размер — из файловой системы.
    $agentsPath = Join-Path $script:ProjectRoot 'AGENTS.md'
    if (Test-Path -LiteralPath $agentsPath) {
        $examined++
        $agentsText = Get-ProjectText $agentsPath
        $budgetMatch = [regex]::Match($agentsText, 'бюджет\w*\s+инструкций агента\s*\(\s*([\d\s]+)\s*байт\s*\)')
        if (-not $budgetMatch.Success) {
            $problems.Add('AGENTS.md не называет бюджет инструкций агента: заявление о собственном размере отсутствует, и проверить его нечем')
        }
        else {
            $statedBudget = [int](($budgetMatch.Groups[1].Value -replace '\s', ''))
            $actualSize = (Get-Item -LiteralPath $agentsPath).Length
            if ($statedBudget -lt 1) {
                $problems.Add("AGENTS.md называет недопустимый бюджет инструкций агента: $statedBudget байт")
            }
            elseif ($actualSize -gt $statedBudget) {
                $problems.Add("AGENTS.md заявляет бюджет $statedBudget байт, а занимает $actualSize байт: заявление расходится с фактом на $($actualSize - $statedBudget)")
            }
        }
    }

    # Число проверок в шапке — против РЕЕСТРА, а не против литерала.
    # Формы две: «34 проверки» и «35 проверок». Прежний образец требовал букву «к»
    # сразу после «провер» и потому родительного падежа множественного числа не
    # видел: шапка говорила «35 проверок», а проверка объявляла, что числа нет
    # вовсе (измерено этапом И27 на переходе 34 → 35).
    # Измеренный дефект (25.10.2026): шапка заявляла «14 проверок» при
    # действующих 29, то есть чекер расходился с фактом о самом себе — ровно тот
    # класс, который он ловит в документах и не видел у себя. Свойство
    # формулируется через `$script:CheckFunctions.Count`: добавление проверки
    # сдвигает ожидание само, а правка шапки без реестра — нет.
    $checkerPath = Join-Path $script:ProjectRoot 'tools/Invoke-ProjectDoctrine.ps1'
    if (-not (Test-Path -LiteralPath $checkerPath)) {
        $problems.Add('нет tools/Invoke-ProjectDoctrine.ps1: число проверок в шапке сверять не с чем')
    }
    else {
        $examined++
        $checkerText = Get-ProjectText $checkerPath
        $headerMatch = [regex]::Match($checkerText, '(?s)<#(.*?)#>')
        $stated = @()
        if ($headerMatch.Success) {
            $stated = @([regex]::Matches($headerMatch.Groups[1].Value, '(\d+)\s+провер(?:ок|к[а-я]{0,2})'))
        }
        $registryCount = @($script:CheckFunctions).Count
        if (-not $headerMatch.Success -or $stated.Count -eq 0) {
            $problems.Add('шапка чекера не называет число проверок: заявление о самом чекере отсутствует, и сверить его не с чем')
        }
        elseif ($stated.Count -gt 1) {
            $problems.Add("шапка чекера называет число проверок $($stated.Count) раза: читатель не может понять, какое верно")
        }
        elseif ($registryCount -lt 1) {
            $problems.Add('реестр проверок пуст: сверять заявление шапки не с чем')
        }
        elseif ([int]$stated[0].Groups[1].Value -ne $registryCount) {
            $problems.Add("шапка чекера называет $($stated[0].Groups[1].Value) проверок, а в реестре `$script:CheckFunctions их $registryCount")
        }
    }


    # --- Числа полигона: снимок и текущий реестр — РАЗНЫЕ состояния ---
    # Измеренный дефект (25.10.2026, ПРОМТ-И14): README называл «28 корпусов,
    # 25 проиндексированы» и 7 527 файлов по замеру 16.10.2026, а в реестре
    # было 37 записей. Числа не сверялись ни с чем и читались как описание
    # сегодняшнего продукта.
    #
    # Разрешены ровно два состояния, и проверка их различает:
    #   1) число объявлено СНИМКОМ: рядом стоят дата и оговорка «не текущее
    #      состояние» — сверять его с реестром нельзя и не нужно;
    #   2) число объявлено ТЕКУЩИМ: «по реестру на ДД.ММ.ГГГГ: N корпусов,
    #      M проиндексированы» — тогда оно обязано совпасть с catalog.json
    #      и с датой его обновления.
    # Третьего состояния нет: число, которое не снимок и не сверено, читается
    # как факт о сегодняшнем дне, то есть является догадкой.
    $readmePath = Join-Path $script:ProjectRoot 'README.md'
    if (-not (Test-Path -LiteralPath $readmePath)) {
        $problems.Add('нет README.md: числа полигона сверять не с чем')
    }
    else {
        $examined++
        $readmeText = Get-ProjectText $readmePath
        $live = [regex]::Match($readmeText, 'by the registry on (\d{2})\.(\d{2})\.(\d{4})[^\d]{0,80}?(\d+)\s+corpora,\s*(\d+)\s+indexed')
        $snapshot = [regex]::Match($readmeText, 'snapshot of (\d{2}\.\d{2}\.\d{4})')

        if (-not $snapshot.Success -and -not $live.Success) {
            $problems.Add('числа полигона в README не помечены ни снимком с датой, ни сверкой с реестром: читатель принимает их за текущее состояние')
        }

        if ($snapshot.Success) {
            $examined++
            $tail = $readmeText.Substring($snapshot.Index, [Math]::Min(600, $readmeText.Length - $snapshot.Index))
            if ($tail -notmatch 'not the current state') {
                $problems.Add('снимок полигона помечен датой, но не сказано, что числа не текущие: дата без этой оговорки не отделяет замер от сегодняшнего дня')
            }
            if ($tail -match 'as of today|on the current date|на сегодня|на текущий момент') {
                $problems.Add('рядом со снимком полигона стоит оборот текущего состояния: замер выдан за сегодняшний')
            }
        }

        if ($live.Success) {
            $examined++
            $catalogPath = Join-Path $script:ProjectRoot 'catalog/catalog.json'
            if (-not (Test-Path -LiteralPath $catalogPath)) {
                $problems.Add('README называет числа реестра, а catalog/catalog.json нет: сверять нечем')
            }
            else {
                $catalog = Get-Content -LiteralPath $catalogPath -Raw -Encoding utf8 | ConvertFrom-Json
                $entries = @($catalog.entries.PSObject.Properties)
                $indexed = @($entries | Where-Object { $_.Value.rev_id })
                $statedDate = "$($live.Groups[3].Value)-$($live.Groups[2].Value)-$($live.Groups[1].Value)"
                $registryDate = ''
                if ($catalog.updated_at) {
                    # ConvertFrom-Json разбирает ISO-метку в DateTime, поэтому
                    # строка «10/25/2026» здесь — не ошибка даты, а её формат.
                    if ($catalog.updated_at -is [datetime]) {
                        $registryDate = $catalog.updated_at.ToString('yyyy-MM-dd')
                    }
                    else {
                        $registryDate = ([string]$catalog.updated_at).Substring(0, 10)
                    }
                }
                if ($entries.Count -lt 1) {
                    $problems.Add('реестр пуст: число корпусов в README сверить не с чем')
                }
                elseif ([int]$live.Groups[4].Value -ne $entries.Count) {
                    $problems.Add("README называет $($live.Groups[4].Value) корпусов, а в реестре $($entries.Count)")
                }
                elseif ([int]$live.Groups[5].Value -ne $indexed.Count) {
                    $problems.Add("README называет $($live.Groups[5].Value) проиндексированных корпусов, а в реестре $($indexed.Count)")
                }
                elseif ($registryDate -and $statedDate -ne $registryDate) {
                    $problems.Add("README говорит «по реестру на $($live.Groups[1].Value).$($live.Groups[2].Value).$($live.Groups[3].Value)», а реестр обновлён $registryDate")
                }
            }
        }
    }

    # Итог различает три состояния: находка, отставание, молчание.
    # Отставание печатается как предупреждение и в $problems не попадает, но и
    # заработанным PASS не считается: при -Strict (публикация) оно становится
    # ошибкой. Иначе «мягкое» предупреждение превращается в вечное оправдание.
    $softLag = ($lagNotes.Count -gt 0 -and $problems.Count -eq 0)
    return New-CheckResult -Name 'documented operation counts match the code' `
        -Ok ($problems.Count -eq 0 -and -not $softLag) `
        -Examined $examined `
        -Details @($problems + $lagNotes + $notes) `
        -Warning:$softLag
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

        Обход расширен 25.10.2026 (ПРОМТ-И12) на деревья навыка: `docs/skills` —
        источник, `harness/skills` — копия для HARNESS. Повод измерен: три
        прежние редакции `SKILL.md` (две в источнике, одна в копии) лежали рядом
        с рабочим файлом, и старая редакция навыка читалась как действующая.
        Копии сняты по правилу §8.23 (копия в `archive/`, затем удаление), а
        обход теперь называет число осмотренных корней — чтобы расширение
        проверки было видно в наблюдениях, а не только в её тексте.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0
    $found = [System.Collections.Generic.List[object]]::new()

    # Объявленные копии СОСТОЯНИЯ, а не исходника: причина названа, границы
    # узкие — каталог И образец имени. Прежний baseline перечислял три копии
    # исходников (`*.bak-ensure`) и тем самым разрешал им лежать рядом с рабочими
    # файлами вечно; 25.10.2026 они сняты по §8.23, а объявление состояния
    # осталось: копии настроек корпусов пишет панель при каждом сохранении.
    $declaredRules = @(
        @{ Dir = 'config'; Pattern = '\.yaml\.bak\.\d{8}-\d{6}-[a-z]+$';
           Reason = 'копии настроек корпусов: их пишет панель, это состояние, а не исходник продукта' }
    )

    # Корни обхода. Деревья навыка добавлены 25.10.2026 (ПРОМТ-И12): источник
    # навыка лежит в `docs/skills`, копия для HARNESS — в `harness/skills`, и
    # рядом с обоими стояли прежние редакции самого `SKILL.md`. Проверка,
    # осматривавшая только деревья кода, их не видела: копия исходника рядом с
    # рабочим файлом существовала и была невидима всем, кто читает по маске.
    # Корни расширены 25.10.2026 (ПРОМТ-И13): копии лежали в `config`, `catalog`,
    # `admin`, `docs` и в корне дерева, то есть рядом с рабочими файлами и вне
    # осмотра. `docs` и `harness` взяты целиком: копия рядом с документом — тот же
    # дефект, что копия рядом с кодом.
    $roots = @('src', 'tools', 'tests', 'scripts', 'docs', 'harness', 'config', 'catalog', 'admin')
    foreach ($root in $roots) {
        $dir = Join-Path $script:ProjectRoot $root
        if (-not (Test-Path -LiteralPath $dir -PathType Container)) {
            # Молчаливый `continue` здесь и был дефектом: объявленный корень,
            # которого нет, сужал обход, уменьшал число наблюдений, и отличить
            # это от «копий нет» было нечем.
            $problems.Add("объявленный корень обхода отсутствует: $root — обход разошёлся с деревом проекта")
            continue
        }
        # Корень считается осмотренным: иначе расширение обхода на новое дерево
        # не видно в числе наблюдений, а «0 копий» неотличимо от «дерево не
        # осматривалось» — это и есть ложный PASS при нуле.
        $examined++
        foreach ($file in (Get-ChildItem -LiteralPath $dir -Recurse -File -Force -ErrorAction SilentlyContinue)) {
            if ($file.FullName -match $script:CodeExclusionPattern) { continue }
            if (-not ($file.Name -match '\.(bak|orig|old|save|before|after)(\.|$)' -or $file.Name -match '\.bak-')) { continue }
            $found.Add([pscustomobject]@{
                Relative = (Get-RelativePath $file.FullName)
                Name = $file.Name
            })
        }
    }

    # Файлы корня дерева: `AGENTS.md.bak-*` и подобные копии жили именно здесь и
    # в обход не попадали ни одним корнем.
    $examined++
    foreach ($file in (Get-ChildItem -LiteralPath $script:ProjectRoot -File -Force -ErrorAction SilentlyContinue)) {
        if (-not ($file.Name -match '\.(bak|orig|old|save|before|after)(\.|$)' -or $file.Name -match '\.bak-')) { continue }
        $found.Add([pscustomobject]@{ Relative = $file.Name; Name = $file.Name })
    }

    # Норма, объявленная с причиной, не прячет копию исходника: она узкая по
    # двум признакам и проверяется с двух сторон.
    $declaredHits = 0
    foreach ($rule in $declaredRules) {
        $examined++
        $covered = @($found | Where-Object { $_.Relative -like "$($rule.Dir)/*" -and $_.Name -match $rule.Pattern })
        if ($covered.Count -eq 0) {
            $problems.Add("объявление копий состояния в «$($rule.Dir)» устарело: ни одного файла оно не накрывает")
        }
        $outside = @($found | Where-Object { $_.Relative -notlike "$($rule.Dir)/*" -and $_.Name -match $rule.Pattern })
        if ($outside.Count -gt 0) {
            $problems.Add("образец объявления «$($rule.Dir)» накрывает копии вне своего каталога: $(($outside | ForEach-Object { $_.Relative }) -join ', ') — так можно спрятать копию исходника")
        }
        $declaredHits += $covered.Count
    }

    foreach ($file in $found) {
        $examined++
        $rule = $declaredRules | Where-Object { $file.Relative -like "$($_.Dir)/*" -and $file.Name -match $_.Pattern } | Select-Object -First 1
        if ($rule) { continue }
        $problems.Add("новая копия рядом с рабочим файлом: $($file.Relative)")
    }
    Write-Host "    копий найдено: $($found.Count), из них объявлено состоянием: $declaredHits"

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

    # Классы файлов tools/. Объявление живёт в манифесте поставки, обход
    # считает обе группы и печатает числа. Смысл класса: читатель должен
    # отличать рабочую проверку от разового патча, и отличать не по имени —
    # 8 файлов с «разовым» именем гейт вызывает, а keep_cut держит инвариант
    # о копии до удаления, не входя в ворота.
    $classesTool = Join-Path $script:ProjectRoot 'tools/check_tools_classes.py'
    if (-not (Test-Path -LiteralPath $classesTool)) {
        $problems.Add('NOT-IMPLEMENTED: нет tools/check_tools_classes.py — классы файлов tools/ не проверяются')
    }
    else {
        $examined++
        $classesRaw = & $python.Source $classesTool --json 2>&1
        $classesCode = $LASTEXITCODE
        $classesPayload = $null
        try { $classesPayload = ($classesRaw | Out-String) | ConvertFrom-Json } catch { $classesPayload = $null }
        if (-not $classesPayload) {
            $problems.Add("машинный ответ проверки классов tools/ не разобран (код $classesCode): " + (($classesRaw | Out-String).Trim()))
        }
        elseif ([int]$classesPayload.files -le 0) {
            $problems.Add('ни одного файла tools/ не осмотрено: «ноль осмотренных» — это не PASS')
        }
        else {
            Write-Host ("    классы tools/: файлов {0}, рабочих {1}, разовых {2}, не объявлено {3}, разовых в поставке {4}" -f `
                $classesPayload.files, $classesPayload.working, $classesPayload.oneoff, $classesPayload.unclassified, $classesPayload.oneoff_shipped)
            foreach ($item in @($classesPayload.failures)) { $problems.Add("tools/: $item") }
        }
    }


    if ($examined -eq 0) {
        return New-CheckResult -Name 'release tree is built from the manifest and survives the gate' `
            -Ok $false -Examined 0 -Details @('NOT-IMPLEMENTED: ни одного объекта не осмотрено — это ложный PASS')
    }

    return New-CheckResult -Name 'release tree is built from the manifest and survives the gate' `
        -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Test-AdminPanelOrigin {
    <#
    .SYNOPSIS
        Панель отказывает запросу с чужим `Host` и с чужим `Origin`.

    .DESCRIPTION
        Измеренный дефект (24.10.2026): привязка к 127.0.0.1 считалась границей
        доверия, а проверок `Host`/`Origin` в `src/ragraf/admin_server.py` не было
        ни одной. Замер до правки: запрос с `Host: evil.example:<порт>` получал
        200 на `/api/health`, то есть чужая страница в браузере владельца
        (DNS-rebinding) читала состояние панели.

        Проверка ищет СВОЙСТВО, а не написание. Текст с именем `origin_problem`
        без вызова зеленел бы на живой дыре, поэтому свойство проверяется
        поведением: `tests/test_admin_origin.py` поднимает настоящий обработчик
        панели на свободном порту и подаёт ему запросы с разными заголовками.

        Учтено то, что глазами не видно: подкласс статики переопределяет
        `do_GET` и не зовёт `super()`. Поэтому граница обязана стоять в
        `parse_request` — иначе `/style.css` и `/app.js` остаются открытыми.
        Это свойство проверяется подклассом внутри самого теста, а не чтением
        кода.

        Молчаливый пропуск запрещён: нет файла проверки или нет python — провал,
        а не «нечего проверять».
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0

    $probe = Join-Path $script:ProjectRoot 'tests/test_admin_origin.py'
    if (-not (Test-Path -LiteralPath $probe)) {
        return New-CheckResult -Name 'admin panel refuses foreign Host and Origin' `
            -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: нет tests/test_admin_origin.py — граница доверия панели не проверяется')
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        return New-CheckResult -Name 'admin panel refuses foreign Host and Origin' `
            -Ok $false -Examined 0 -Details @('NOT-IMPLEMENTED: python не найден в PATH')
    }

    $output = & $python.Source $probe 2>&1
    $code = $LASTEXITCODE
    $text = ($output | Out-String).Trim()
    $countText = ([regex]::Match($text, 'проверок:\s*(\d+)')).Groups[1].Value
    $failText = ([regex]::Match($text, 'провалов:\s*(\d+)')).Groups[1].Value
    $examined = if ($countText) { [int]$countText } else { 0 }
    $failed = if ($failText) { [int]$failText } else { 0 }
    Write-Host "    граница доверия панели: проверок $examined, провалов $failed, код $code"

    if ($code -ne 0 -or $failed -gt 0) {
        $lines = @($output | Where-Object { $_ -is [string] -and $_ -match '\[FAIL\]' })
        foreach ($line in $lines) {
            $problems.Add(($line -replace '.*\[FAIL\]\s*', ''))
        }
        if ($problems.Count -eq 0) {
            $problems.Add("проверка границы доверия завершилась кодом $code без расшифровки")
        }
    }

    if ($examined -gt 0 -and $examined -lt 30) {
        $problems.Add("проверка осмотрела $examined свойств — меньше 30: часть границы доверия осталась без наблюдения")
    }

    if ($examined -eq 0) {
        return New-CheckResult -Name 'admin panel refuses foreign Host and Origin' `
            -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: ни одного свойства не осмотрено — это ложный PASS')
    }

    return New-CheckResult -Name 'admin panel refuses foreign Host and Origin' `
        -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Test-EditionLayer {
    <#
    .SYNOPSIS
        Операция вне издания получает отказ с причиной, а подделанный файл
        лицензии — отказ, называющий подпись.

    .DESCRIPTION
        Измеренный дефект, из-за которого проверка появилась (ПРОМТ-И6):
        `editions/` был картой удержания файлов, а не механизмом. Проверить
        «это Pro или Community» было нечем, поэтому платные возможности
        оставались обещанием: ни одна строка кода не отвечала на вопрос, что
        этой сборке разрешено.

        Проверка ищет СВОЙСТВО, а не написание. Наличие поля `edition` в
        конверте зеленело бы и на «издании enterprise без лицензии», и на
        подделанном файле лицензии, и на возможности, которой нет в сборке.
        Поэтому свойство проверяется поведением: `tests/test_editions.py`
        создаёт лицензии в песочнице (свой ключ, чужой ключ, истёкший срок,
        неизвестное издание внутри файла, файл без издания), правит копию
        базовой линии и смотрит, ЧТО отвечает фасад — конверт с `error` и
        разделом `refused`, запись в журнале доступа, отсутствие отвергнутой
        возможности в `features`.

        Отдельно проверяется обратное: лицензия pro действительно РАЗРЕШАЕТ
        платную возможность, если она в сборке есть. Без этого «отказ» мог бы
        быть отказом всегда, и проверка не отличала бы работающий слой от
        запертого.

        Молчаливый пропуск запрещён: нет файла проверки или нет python —
        провал, а не «нечего проверять». Ноль осмотренных свойств при успехе
        тоже провал: это ложный PASS.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0

    # Два набора проверок, а не один. ПРОМТ-И9 добавил возможность `quality-loop`
    # (`quality_suite`, `quality_diff`) — она объявлена платной, и её гейт тоже
    # издание. Держать её вне этой проверки значило бы оставить десятую
    # возможность издания без наблюдения: `test_editions.py` про неё не знает.
    #
    # Статусы возможностей (`shipped` | `declared`) проверяются ТАМ ЖЕ и
    # ПОВЕДЕНИЕМ: копия данных с опечаткой в статусе, с `declared` без `planned`
    # и с `shipped`, у которого есть `planned`, обязана быть отвергнута
    # проверкой данных. Отдельная проверка написания («в файле есть слово
    # status») зеленела бы на бессмысленном значении — здесь так нельзя.
    $probes = @(
        @{ Path = 'tests/test_editions.py'; Minimum = 30; What = 'слоя изданий' }
        @{ Path = 'tests/test_quality_loop.py'; Minimum = 25; What = 'контура качества' }
    )

    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        return New-CheckResult -Name 'edition layer refuses gated operations and forged licences' `
            -Ok $false -Examined 0 -Details @('NOT-IMPLEMENTED: python не найден в PATH')
    }

    foreach ($probe in $probes) {
        $file = Join-Path $script:ProjectRoot $probe.Path
        if (-not (Test-Path -LiteralPath $file)) {
            $problems.Add("NOT-IMPLEMENTED: нет $($probe.Path) — проверка $($probe.What) не выполняется")
            continue
        }

        $output = & $python.Source $file 2>&1
        $code = $LASTEXITCODE
        $text = ($output | Out-String).Trim()
        $countText = ([regex]::Match($text, 'проверок:\s*(\d+)')).Groups[1].Value
        $failText = ([regex]::Match($text, 'провалов:\s*(\d+)')).Groups[1].Value
        $probeExamined = if ($countText) { [int]$countText } else { 0 }
        $failed = if ($failText) { [int]$failText } else { 0 }
        $examined += $probeExamined
        Write-Host "    $($probe.What): проверок $probeExamined, провалов $failed, код $code"

        if ($code -ne 0 -or $failed -gt 0) {
            $lines = @($output | Where-Object { $_ -is [string] -and $_ -match '\[FAIL\]' })
            foreach ($line in $lines) {
                $problems.Add(($line -replace '.*\[FAIL\]\s*', ''))
            }
            if ($lines.Count -eq 0) {
                $problems.Add("проверка $($probe.What) завершилась кодом $code без расшифровки")
            }
        }

        if ($probeExamined -gt 0 -and $probeExamined -lt [int]$probe.Minimum) {
            $problems.Add("$($probe.What): осмотрено $probeExamined свойств — меньше $($probe.Minimum): часть осталась без наблюдения")
        }
    }

    if ($examined -eq 0) {
        return New-CheckResult -Name 'edition layer refuses gated operations and forged licences' `
            -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: ни одного свойства не осмотрено — это ложный PASS')
    }

    return New-CheckResult -Name 'edition layer refuses gated operations and forged licences' `
        -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Test-ExternalAccess {
    <#
    .SYNOPSIS
        Единый вход и выгрузка в SIEM проверяются поведением: поддельные токены
        отвергаются, курсор доставки не теряет и не дублирует записи.

    .DESCRIPTION
        Измеренный дефект, из-за которого проверка появилась (ПРОМТ-И7): две
        возможности слоя изданий были объявлены в данных и отсутствовали в
        сборке. Соблазн закрыть это «наличием модуля» велик: файл sso.py есть —
        значит вход работает. Такая проверка зеленела бы и на приёме токена без
        проверки подписи, и на роли, взятой из заголовка клиента, и на выгрузке,
        которая при сбое сети теряет записи или дублирует их при повторе.

        Поэтому проверяется ПОВЕДЕНИЕ. tests/test_external_access.py предъявляет
        просроченный токен, токен чужого издателя и чужого приложения, чужой
        nonce, подпись другим ключом, неизвестный kid, alg=none и HS256 — и
        требует отказа С ПРИЧИНОЙ у каждого. Роль берётся из группы, а не из
        утверждения в токене. Для выгрузки проверяются: причина при пустом
        приёмнике, неизменность курсора при сбое доставки, отсутствие повторов
        при повторной выгрузке, отсутствие в наборе значений секретов и
        свободного текста журнала. Слой изданий проверяется на живом фасаде:
        на Community — EditionRefusal, на enterprise с подписанной лицензией —
        работа, с подделанной — снова отказ.

        Ноль осмотренных свойств при успехе — ложный PASS и провал проверки.
        Нет файла проверки или python — тоже провал: «нечего проверять» не
        означает «всё в порядке».
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0

    $probe = Join-Path $script:ProjectRoot 'tests/test_external_access.py'
    if (-not (Test-Path -LiteralPath $probe)) {
        return New-CheckResult -Name 'external access verifies tokens and never loses siem records' `
            -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: нет tests/test_external_access.py — внешний доступ не проверяется')
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        return New-CheckResult -Name 'external access verifies tokens and never loses siem records' `
            -Ok $false -Examined 0 -Details @('NOT-IMPLEMENTED: python не найден в PATH')
    }

    $output = & $python.Source $probe 2>&1
    $code = $LASTEXITCODE
    $text = ($output | Out-String).Trim()
    $countText = ([regex]::Match($text, 'проверок:\s*(\d+)')).Groups[1].Value
    $failText = ([regex]::Match($text, 'провалов:\s*(\d+)')).Groups[1].Value
    $examined = if ($countText) { [int]$countText } else { 0 }
    $failed = if ($failText) { [int]$failText } else { 0 }
    Write-Host "    внешний доступ: проверок $examined, провалов $failed, код $code"

    if ($code -ne 0 -or $failed -gt 0) {
        $lines = @($output | Where-Object { $_ -is [string] -and $_ -match '\[FAIL\]' })
        foreach ($line in $lines) {
            $problems.Add(($line -replace '.*\[FAIL\]\s*', ''))
        }
        if ($problems.Count -eq 0) {
            $problems.Add("проверка внешнего доступа завершилась кодом $code без расшифровки")
        }
    }

    if ($examined -gt 0 -and $examined -lt 40) {
        $problems.Add("проверка осмотрела $examined свойств — меньше 40: часть внешнего доступа осталась без наблюдения")
    }

    if ($examined -eq 0) {
        return New-CheckResult -Name 'external access verifies tokens and never loses siem records' `
            -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: ни одного свойства не осмотрено — это ложный PASS')
    }

    return New-CheckResult -Name 'external access verifies tokens and never loses siem records' `
        -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Test-OfflineAttestation {
    <#
    .SYNOPSIS
        Офлайн-лицензия привязана к установке, а набор доказательств проверяется
        пересчётом хэшей из файлов.

    .DESCRIPTION
        Измеренный дефект, из-за которого проверка появилась (ПРОМТ-И8): две
        последние возможности слоя изданий — `offline-license` и `attestation` —
        были объявлены в данных и отсутствовали в сборке, поэтому `edition`
        честно называл их в разделе `missing`. Соблазн закрыть это «наличием
        модуля» велик: файл attest.py есть — значит аттестация работает. Такая
        проверка зеленела бы на лицензии, подписанной чужим ключом, на лицензии
        для другой установки и на наборе, который сверяет хэши с собственным
        манифестом, то есть проверяет сам себя.

        Поэтому проверяется ПОВЕДЕНИЕ. tests/test_offline_attest.py:
          * отпечаток установки зависит от хоста и корня, машино-локального пути
            в нём нет, а изменённая заявка отвергается по request_id;
          * выпуск без ключа подписи отказывает С ПРИЧИНОЙ и не оставляет файла
            лицензии; подпись чужим ключом, подмена содержимого, истёкший срок,
            чужая установка, чужая заявка и отсутствие привязки отвергаются —
            каждая с названным состоянием;
          * перенос оформляется заявкой и сам по себе лицензию не включает;
          * набор доказательств подписывается, подмена байта в КАЖДОМ его
            документе ловится по имени файла, подделанный манифест отвергается
            подписью, набор без подписи помечается signed=false и проверенным не
            называется;
          * пределы доказательства (`limits`) есть в самом наборе: аттестация не
            утверждает больше, чем проверяет;
          * на Community обе операции получают EditionRefusal, на enterprise с
            подписанной лицензией работают, и обе попытки видны в журнале доступа.

        Отдельно проверяется обратное: с действующей лицензией у enterprise
        РОВНО девять возможностей (пять базовых и четыре платные). Без этого
        «работает» означало бы только «не отказывает по изданию».

        Молчаливый пропуск запрещён: нет файла проверки или python — провал, а не
        «нечего проверять». Ноль осмотренных свойств при успехе — ложный PASS.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $examined = 0

    $probe = Join-Path $script:ProjectRoot 'tests/test_offline_attest.py'
    if (-not (Test-Path -LiteralPath $probe)) {
        return New-CheckResult -Name 'offline licence binds to install and attestation rehashes files' `
            -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: нет tests/test_offline_attest.py — офлайн-лицензия и аттестация не проверяются')
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        return New-CheckResult -Name 'offline licence binds to install and attestation rehashes files' `
            -Ok $false -Examined 0 -Details @('NOT-IMPLEMENTED: python не найден в PATH')
    }

    $output = & $python.Source $probe 2>&1
    $code = $LASTEXITCODE
    $text = ($output | Out-String).Trim()
    $countText = ([regex]::Match($text, 'проверок:\s*(\d+)')).Groups[1].Value
    $failText = ([regex]::Match($text, 'провалов:\s*(\d+)')).Groups[1].Value
    $examined = if ($countText) { [int]$countText } else { 0 }
    $failed = if ($failText) { [int]$failText } else { 0 }
    Write-Host "    офлайн-лицензия и аттестация: проверок $examined, провалов $failed, код $code"

    if ($code -ne 0 -or $failed -gt 0) {
        $lines = @($output | Where-Object { $_ -is [string] -and $_ -match '\[FAIL\]' })
        foreach ($line in $lines) {
            $problems.Add(($line -replace '.*\[FAIL\]\s*', ''))
        }
        if ($problems.Count -eq 0) {
            $problems.Add("проверка офлайн-лицензии и аттестации завершилась кодом $code без расшифровки")
        }
    }

    if ($examined -gt 0 -and $examined -lt 40) {
        $problems.Add("проверка осмотрела $examined свойств — меньше 40: часть закрытого контура осталась без наблюдения")
    }

    if ($examined -eq 0) {
        return New-CheckResult -Name 'offline licence binds to install and attestation rehashes files' `
            -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: ни одного свойства не осмотрено — это ложный PASS')
    }

    return New-CheckResult -Name 'offline licence binds to install and attestation rehashes files' `
        -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Test-DocumentAddresses {
    <#
    .SYNOPSIS
        Ссылки «файл:строка» в документах текущего состояния ведут туда, куда говорят.

    .DESCRIPTION
        Измеренный дефект (25.10.2026, ПРОМТ-И13): из 40 объявленных ссылок 31 вела
        не в тот объект. `tools/Invoke-ProjectDoctrine.ps1:2386` указывала в пустую
        строку (свойства двинули код), `src/ragraf/cli.py:336-356` — в запись прогона
        вместо отказа `slice`, `store.py:122-131` — в загрузку sqlite-vec вместо DDL
        таблицы `node`. Файл существовал, номер был в пределах файла, разошёлся
        ОБЪЕКТ — поэтому глазами это не видно, а «документ указывает в пустоту»
        стоит доверия к факту.

        Разбор не повторяется здесь: за него отвечает `tools/check_line_refs.py`, а
        чекер берёт наблюдения из его машинного ответа. Две реализации одного
        правила расходятся — в этом проекте так уже было.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()

    $toolPath = Join-Path $script:ProjectRoot 'tools/check_line_refs.py'
    if (-not (Test-Path -LiteralPath $toolPath)) {
        return New-CheckResult -Name 'document addresses point at their object' -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: нет tools/check_line_refs.py — адреса документов не проверяются')
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        return New-CheckResult -Name 'document addresses point at their object' -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: python не найден в PATH')
    }

    $raw = & $python.Source $toolPath --json 2>&1
    $code = $LASTEXITCODE
    $payload = $null
    try { $payload = ($raw | Out-String) | ConvertFrom-Json } catch { $payload = $null }
    if (-not $payload) {
        return New-CheckResult -Name 'document addresses point at their object' -Ok $false -Examined 0 `
            -Details @("машинный ответ проверки адресов не разобран (код $code): " + (($raw | Out-String).Trim()))
    }

    $examined = [int]$payload.refs + [int]$payload.pairs
    Write-Host ("    адреса документов: ссылок {0}, пар {1}, находок {2}, код {3}" -f `
        $payload.refs, $payload.pairs, @($payload.failures).Count, $code)

    foreach ($item in @($payload.failures)) {
        $problems.Add("$($item.doc):$($item.line) — $($item.ref) [$($item.kind)] $($item.detail)")
    }
    if ($code -eq 2 -or $examined -eq 0) {
        $problems.Add('ни одной ссылки не осмотрено: «ноль проверенных адресов» — это не PASS')
    }

    return New-CheckResult -Name 'document addresses point at their object' -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Test-DeliveryVisibleToGit {
    <#
    .SYNOPSIS
        Ни один файл поставки не скрыт от git правилом .gitignore.

    .DESCRIPTION
        Измеренный дефект (26.10.2026, публикация 0.3.1): файл
        `tests/fixtures/proj/config/app.local.php` входил в сборку поставки, но
        правило `config/` в `.gitignore` не пускало его в коммит. Сборщик переносил
        файл, проверяльщик его видел, а опубликованное дерево отличалось от
        проверенного на один файл. Свойство формулируется без имён файлов: состав
        поставки берётся из манифеста, а у git спрашивается про КАЖДЫЙ его файл.

        Разбор не повторяется здесь: за него отвечает
        `tools/check_delivery_visible_to_git.py`, а чекер берёт наблюдения из его
        машинного ответа. Две реализации одного правила расходятся — в этом проекте
        так уже было.

        Код 2 означает «не проверено» (git недоступен) и валит прогон: молчаливая
        зелень после провала проверки была бы вторым дефектом.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()

    $toolPath = Join-Path $script:ProjectRoot 'tools/check_delivery_visible_to_git.py'
    if (-not (Test-Path -LiteralPath $toolPath)) {
        return New-CheckResult -Name 'delivery files are visible to git' -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: нет tools/check_delivery_visible_to_git.py — скрытые от git файлы поставки не проверяются')
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        return New-CheckResult -Name 'delivery files are visible to git' -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: python не найден в PATH')
    }

    $raw = & $python.Source $toolPath --json 2>&1
    $code = $LASTEXITCODE
    $payload = $null
    try { $payload = ($raw | Out-String) | ConvertFrom-Json } catch { $payload = $null }
    if (-not $payload) {
        return New-CheckResult -Name 'delivery files are visible to git' -Ok $false -Examined 0 `
            -Details @("машинный ответ проверки не разобран (код $code): " + (($raw | Out-String).Trim()))
    }

    $examined = [int]$payload.delivered
    Write-Host ("    видимость поставки для git: файлов {0}, скрыто {1}, код {2}" -f `
        $payload.delivered, $payload.ignored, $code)

    foreach ($item in @($payload.failures)) {
        $problems.Add("$($item.path) — скрыт правилом $($item.rule): файл не попадёт в коммит")
    }
    if ($code -eq 2) {
        $problems.Add("не проверено: $($payload.error)")
    }
    if ($examined -eq 0) {
        $problems.Add('ни одного файла поставки не осмотрено: «ноль проверенных» — это не PASS')
    }

    return New-CheckResult -Name 'delivery files are visible to git' -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}


function Test-RagrafIgnored {
    <#
    .SYNOPSIS
        Каталог .ragraf не попадает в git: правило в .gitignore, слой, который его
        держит, и отсутствие отслеживаемых файлов.

    .DESCRIPTION
        Правило «.ragraf в корневом .gitignore» живёт в правилах проекта с
        24.10.2026, но проверки не имело: чекер смотрел ОБРАТНОЕ свойство (файлы
        поставки не скрыты от git), а сам .gitignore никто не сверял. Дефект
        дешёвый и необратимый: `git add -A` забирает индекс в коммит, и из
        истории он уже не убирается — 1,6 ГБ на корпусе revoap.com.

        С 27.10.2026 на машине есть глобальный слой (`~/.gitignore_global`
        через `core.excludesFile`), и он МАСКИРУЕТ отсутствие проектного
        правила: `git check-ignore` отвечает «игнорируется», хотя в чужом клоне
        правила нет. Поэтому проверка спрашивает ФАЙЛ, а не только git.

        Разбор — в `tools/check_ragraf_ignored.py`; чекер берёт наблюдения из
        его машинного ответа, чтобы не держать вторую реализацию правила.

        Код 2 означает «не проверено» (нет git) и валит прогон: молчаливая
        зелень после провала проверки была бы вторым дефектом.
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $checkName = 'ragraf index is out of git'

    $toolPath = Join-Path $script:ProjectRoot 'tools/check_ragraf_ignored.py'
    if (-not (Test-Path -LiteralPath $toolPath)) {
        return New-CheckResult -Name $checkName -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: нет tools/check_ragraf_ignored.py — правило .ragraf/ в .gitignore не проверяется')
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        return New-CheckResult -Name $checkName -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: python не найден в PATH')
    }

    $raw = & $python.Source $toolPath --root $script:ProjectRoot --json 2>&1
    $code = $LASTEXITCODE
    $payload = $null
    try { $payload = ($raw | Out-String) | ConvertFrom-Json } catch { $payload = $null }
    if (-not $payload) {
        return New-CheckResult -Name $checkName -Ok $false -Examined 0 `
            -Details @("машинный ответ проверки не разобран (код $code): " + (($raw | Out-String).Trim()))
    }

    $examined = [int]$payload.examined
    $ruleLine = 'нет'
    if ($payload.PSObject.Properties['project_rule'] -and $payload.project_rule -and `
        $payload.project_rule.PSObject.Properties['line'] -and $payload.project_rule.line) {
        $ruleLine = "строка $($payload.project_rule.line)"
    }
    $gitChecked = if ($payload.PSObject.Properties['git_checked']) { $payload.git_checked } else { $false }
    $trackedCount = if ($payload.PSObject.Properties['tracked']) { $payload.tracked } else { 'не проверялось' }
    Write-Host ("    .ragraf и git: правило {0}, git-слой проверялся {1}, отслеживается файлов {2}, код {3}" -f `
        $ruleLine, $gitChecked, $trackedCount, $code)

    foreach ($note in @($payload.notes)) {
        if ($note) { Write-Host "    $note" -ForegroundColor DarkGray }
    }
    foreach ($item in @($payload.failures)) {
        if ($item) { $problems.Add("$($item.path) — $($item.rule)") }
    }
    if ($code -eq 2) {
        $problems.Add("не проверено: $($payload.error)")
    }
    if ($examined -eq 0) {
        $problems.Add('ни одного наблюдения не сделано: «ноль проверенных» — это не PASS')
    }

    return New-CheckResult -Name $checkName -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}


function Test-SingleDeliveryCopy {
    <#
    .SYNOPSIS
        Документы поставки описывают одну закрытую поставку и два уровня лицензии.

    .DESCRIPTION
        Измеренный дефект (26.10.2026, этап И17): решение «закрытая поставка одна,
        Pro — уровень лицензии внутри неё» проведено в коде этапом И15, а
        `community/EULA.md` продолжал говорить «относится к изданиям Pro и
        Enterprise» и «сборку соответствующего издания». Расхождение видит
        покупатель, и не видит ни одна проверка: EULA никто не сверял с базовой
        линией, а слова «издание» и «Pro» встречаются в документах десятки раз
        законно — поэтому grep тут не помогает.

        Проверка формулируется через свойства, а не через написание: EULA называет
        оба уровня и говорит, что поставка одна; переход между уровнями описан как
        активация лицензии, а не установка; названы ВСЕ написанные платные
        возможности из `src/ragraf/edition_baseline.json`; объявленные не выданы за
        написанные; Pro не назван отдельным пакетом или сборкой; манифест
        действительно кладёт EULA в поставку; переводы README не заявляют двух
        пакетов; `editions.toml` называет закрытую поставку единственной;
        `docs/AGENTS-context.md` объявляет границу про сайт.

        Молчаливый пропуск запрещён: нет скрипта, нет python или ноль осмотренных
        объектов — это провал, а не «нечего проверять».
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $checkName = 'delivery documents describe one closed delivery and two licence levels'

    $toolPath = Join-Path $script:ProjectRoot 'tools/check_single_delivery.py'
    if (-not (Test-Path -LiteralPath $toolPath)) {
        return New-CheckResult -Name $checkName -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: нет tools/check_single_delivery.py')
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        return New-CheckResult -Name $checkName -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: python не найден в PATH')
    }

    $output = & $python.Source $toolPath --root $script:ProjectRoot 2>&1
    $code = $LASTEXITCODE
    $text = ($output | Out-String).Trim()
    Write-Host "    документы поставки: $text"

    $examinedText = ([regex]::Match($text, 'осмотрено:\s*(\d+)')).Groups[1].Value
    $examined = if ($examinedText) { [int]$examinedText } else { 0 }

    if ($code -ne 0) {
        foreach ($line in ($output | Where-Object { $_ -is [string] })) {
            if ($line -match '^\s*FAIL:') { $problems.Add(($line -replace '^\s*FAIL:\s*', '')) }
        }
        if ($problems.Count -eq 0) {
            $problems.Add("проверка документов поставки завершилась кодом $code без расшифровки")
        }
    }
    if ($examined -eq 0) {
        $problems.Add('ни одного наблюдения не сделано: «ноль проверенного» — это не PASS')
    }

    return New-CheckResult -Name $checkName -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Test-TranslationStates {
    <#
    .SYNOPSIS
        Состояние каждого перевода README объявлено и подтверждается деревом.

    .DESCRIPTION
        Измеренный дефект (27.10.2026, этап И21). Этап И20 привёл переводы к
        решению «полный либо частичный», но решение держалось шапкой файла и
        записью в `docs/03_implementation/RELEASE-SOURCES.md`, то есть не
        проверялось ничем: зеркало `community/README.ru.md` могло разойтись с
        `README.md` на одну строку, шапка частичного перевода — исчезнуть,
        ссылка `docs/ru/**` — вернуться, и ни одна проверка этого не видела.
        Тот же этап измерил и второй дефект: шапки четырёх переводов ссылались
        на `assets/social-preview.png`, которого не было ни в живом дереве, ни в
        поставке, а прежний замер ссылок исключал изображения шаблоном `(?<!!)`
        — обложка была битой у читателя при зелёном замере.

        Свойства проверяет `tools/check_translations.py`: равенство зеркала с
        `README.md` плюс ровно пять строк шапки, признак неполноты на своём языке
        у каждого частичного перевода, перечень недостающего (не менее трёх
        пунктов), запрещённые пути `docs/ru/**` и `docs/DEFECTS.md`, русский
        заполнитель команды, кириллица только в ярлыке «Русский» и разрешение
        каждой относительной ссылки — включая изображения — против состава
        поставки из манифеста. Состояние каждого перевода объявлено ДАННЫМИ (`docs/03_implementation/translations.json`), а не словарём в коде: следующий полный перевод объявляется правкой данных, и проверку ради этого править не нужно; числа индекса в каждом переводе сверяются с активной записью ревизии через `tools/rev_snapshot.py` — отставание выдаётся предупреждением с её `rev_id`, а в режиме `--strict` становится находкой (так его требует гейт публикации).

        Здесь к ним добавляется то, что видно только PowerShell: перечень
        переводов в `community/` сверяется с тем, что инструмент действительно
        осмотрел. Иначе новый файл `README.fr.md` появился бы в дереве и в
        поставке непроверенным, а прогон остался бы зелёным — ровно тот класс
        «зелёной проверки, которая ничего не осмотрела», из-за которого этот
        чекер и написан.

        Молчаливый пропуск запрещён: нет скрипта, нет python или ноль
        осмотренных объектов — это провал, а не «нечего проверять».
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $checkName = 'translation states are declared and hold'

    $toolPath = Join-Path $script:ProjectRoot 'tools/check_translations.py'
    if (-not (Test-Path -LiteralPath $toolPath)) {
        return New-CheckResult -Name $checkName -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: нет tools/check_translations.py')
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        return New-CheckResult -Name $checkName -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: python не найден в PATH')
    }

    $output = & $python.Source $toolPath --root $script:ProjectRoot 2>&1
    $code = $LASTEXITCODE
    $text = ($output | Out-String).Trim()
    Write-Host "    состояния переводов: $text"

    $lines = @($output | Where-Object { $_ -is [string] })
    $examinedText = ([regex]::Match($text, 'осмотрено:\s*(\d+)')).Groups[1].Value
    $examined = if ($examinedText) { [int]$examinedText } else { 0 }

    if ($code -ne 0) {
        foreach ($line in $lines) {
            if ($line -match '^\s*FAIL:') { $problems.Add(($line -replace '^\s*FAIL:\s*', '')) }
        }
        if ($problems.Count -eq 0) {
            $problems.Add("проверка состояний переводов завершилась кодом $code без расшифровки")
        }
    }

    # Перечень переводов сверяется с осмотренным. Инструмент печатает строку
    # «перевод: <путь>» на каждый файл, который действительно прочитал; здесь
    # она сравнивается с фактическим составом community/.
    $community = Join-Path $script:ProjectRoot 'community'
    $translations = @(if (Test-Path -LiteralPath $community) {
        Get-ChildItem -LiteralPath $community -File -Filter 'README.*.md' -ErrorAction SilentlyContinue
    })
    $seen = @($lines | Where-Object { $_ -match '^перевод:\s+\S' } |
        ForEach-Object { ($_ -replace '^перевод:\s+', '').Trim() })

    $examined++
    if ($translations.Count -eq 0) {
        $problems.Add('в community/ нет ни одного перевода README.*.md: состояние переводов объявлено, а осматривать нечего')
    }
    foreach ($file in $translations) {
        $relative = Get-RelativePath $file.FullName
        $examined++
        if ($seen -notcontains $relative) {
            $problems.Add("перевод $relative есть в дереве, но проверка о нём не знает: инструмент его не осмотрел")
        }
    }
    $examined++
    if ($translations.Count -gt 0 -and $seen.Count -ne $translations.Count) {
        $problems.Add("инструмент сообщил об осмотре $($seen.Count) переводов, а в community/ их $($translations.Count): перечень и осмотр разошлись")
    }

    if ($examined -eq 0) {
        return New-CheckResult -Name $checkName -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: ни одного объекта не осмотрено — это ложный PASS')
    }

    return New-CheckResult -Name $checkName -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Test-Glossary {
    <#
    .SYNOPSIS
        Терминология переводов README объявлена данными и подтверждается вхождениями.

    .DESCRIPTION
        Измеренный дефект (27.10.2026, этап И27). `tools/check_translations.py`
        подтверждал, что все пять переводов полные, и честно называл свою границу:
        «полный» там означает «приведён к текущему продукту и подтверждён структурой,
        ссылками и числами», а не «переведён без потерь». Смысл фраз машинной
        проверке не поддаётся — но ТЕРМИНОЛОГИЯ поддаётся, и она расходилась молча.
        Замер до правки: термин `stale` в русском переводе был назван «отставший
        индекс» в шаге установки и «устаревшая строка» ниже в том же файле; в
        немецком — «zurückgebliebener Index» против «veraltete Zeile»; в испанском
        «índice atrasado» (отставание по времени) вместо состояния факта; в
        китайском «过期» (истёк срок) и «过时» (устарел) для одного понятия. Подмена
        слова на синоним проходила зелёной: ни одна проверка слова не считала.

        Свойства проверяет `tools/check_glossary.py`: термин объявлен ДАННЫМИ
        (`docs/03_implementation/glossary.json`), в каждом переводе назван одним из
        разрешённых слов, канонический вариант встречается не реже синонимов, а
        обязательный термин (операция, режим или объект продукта) назван вообще; одно
        слово перевода не может обозначать два разных понятия (обратный дрейф).

        Здесь к ним добавляется то, что видно только PowerShell: перечень терминов
        из объявления сверяется с тем, что инструмент действительно осмотрел. Иначе
        новый термин `quorum` появился бы в словаре и остался непроверенным, а прогон
        остался бы зелёным — ровно тот класс «зелёной проверки, которая ничего не
        осмотрела», из-за которого этот чекер и написан.

        Молчаливый пропуск запрещён: нет скрипта, нет python или ноль осмотренных
        объектов — это провал, а не «нечего проверять».
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $checkName = 'translation terminology is declared and holds'

    $toolPath = Join-Path $script:ProjectRoot 'tools/check_glossary.py'
    if (-not (Test-Path -LiteralPath $toolPath)) {
        return New-CheckResult -Name $checkName -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: нет tools/check_glossary.py')
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        return New-CheckResult -Name $checkName -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: python не найден в PATH')
    }

    $output = & $python.Source $toolPath --root $script:ProjectRoot 2>&1
    $code = $LASTEXITCODE
    $text = ($output | Out-String).Trim()
    Write-Host "    терминология переводов: $text"

    $lines = @($output | Where-Object { $_ -is [string] })
    $examinedText = ([regex]::Match($text, 'осмотрено:\s*(\d+)')).Groups[1].Value
    $examined = if ($examinedText) { [int]$examinedText } else { 0 }

    if ($code -ne 0) {
        foreach ($line in $lines) {
            if ($line -match '^\s*FAIL:') { $problems.Add(($line -replace '^\s*FAIL:\s*', '')) }
        }
        if ($problems.Count -eq 0) {
            $problems.Add("проверка терминологии завершилась кодом $code без расшифровки")
        }
    }

    # Перечень терминов из объявления сверяется с осмотренным. Инструмент печатает
    # строку «термин: <имя>» на каждый термин, у которого есть форма в README.md;
    # термин, которого в основном документе нет, назван находкой — и это не пропуск.
    $glossaryPath = Join-Path $script:ProjectRoot 'docs/03_implementation/glossary.json'
    $declared = @()
    if (Test-Path -LiteralPath $glossaryPath) {
        $examined++
        try {
            $glossary = (Get-ProjectText $glossaryPath) | ConvertFrom-Json
            $declared = @($glossary.terms | ForEach-Object { [string]$_.term_en })
        }
        catch {
            $problems.Add("docs/03_implementation/glossary.json не разобран: терминология объявлена, а осмотреть её нечем")
            $declared = @()
        }
    }
    else {
        $problems.Add('нет docs/03_implementation/glossary.json: терминология переводов не объявлена данными')
    }

    $seen = @($lines | Where-Object { $_ -match '^термин:\s+\S' } |
        ForEach-Object { (($_ -replace '^термин:\s+', '') -replace '\s+—.*$', '').Trim() })

    if ($declared.Count -eq 0 -and $problems.Count -eq 0) {
        $problems.Add('в объявлении нет ни одного термина: словарь пуст, а проверка объявлена')
    }
    foreach ($term in $declared) {
        $examined++
        if ($seen -notcontains $term) {
            $problems.Add("термин '$term' объявлен в словаре, но проверка его не осмотрела: строка «термин: $term» в выводе отсутствует")
        }
    }

    if ($examined -eq 0) {
        return New-CheckResult -Name $checkName -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: ни одного объекта не осмотрено — это ложный PASS')
    }

    return New-CheckResult -Name $checkName -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Test-ProseProfile {
    <#
    .SYNOPSIS
        Проза переводов README считается: раздел за разделом, абзац за абзацем.

    .DESCRIPTION
        Измеренный дефект (27.10.2026, этап И28). К этому моменту перевод
        подтверждался структурой разделов, ссылками, числами индекса и
        терминологией — и каждая из этих проверок честно называла границу: смысл
        фраз машинной проверке не поддаётся. Но между смыслом и словом есть слой,
        который поддаётся счёту, и его не сторожил никто. Профиль раздела в
        `tools/check_translations.py` (`section_profile`) состоит из двух чисел —
        блоков команд и строк таблиц; прозы в нём нет. Поэтому перевод, из
        которого пропал абзац, оставался «полным»: структура совпала, ссылки на
        месте, числа сошлись, а текста меньше. Пропавшее предложение внутри
        абзаца не видел вообще никто: ни число разделов, ни профиль, ни ссылки,
        ни словарь терминов на такое не реагируют.

        Свойства проверяет `tools/check_prose_profile.py`: в каждом разделе
        считается число абзацев прозы, в каждом абзаце — число предложений, и
        расхождение с `README.md` сверх допуска это находка с номером раздела,
        номером абзаца и обоими числами. Допуски объявлены ДАННЫМИ
        (`docs/03_implementation/prose.json`) и сняты замером: ноль по абзацам и
        ноль по предложениям, потому что фактическое расхождение в пяти переводах
        равно нулю. Нулевой допуск — самый сильный: он ловит и пропавший абзац, и
        склейку двух абзацев в один, и потерянное предложение внутри абзаца.
        Границы счёта названы в шапке инструмента: проза в списках, таблицах,
        цитатах и блоках команд не считается, а знаки конца предложения
        разбираются по языку (латинские, `。！？`, `।`).

        Здесь к ним добавляется то, что видно только PowerShell: языки из
        объявления допусков сверяются с тем, что инструмент действительно
        осмотрел, и с файлами в `community/`. Иначе новый перевод `README.fr.md`
        появился бы в дереве, не попал ни в объявление, ни в осмотр — и прогон
        остался бы зелёным ровно на том классе дефекта, ради которого этот чекер
        написан.

        Молчаливый пропуск запрещён: нет скрипта, нет python или ноль осмотренных
        объектов — это провал, а не «нечего проверять».
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $checkName = 'translation prose matches the main document'

    $toolPath = Join-Path $script:ProjectRoot 'tools/check_prose_profile.py'
    if (-not (Test-Path -LiteralPath $toolPath)) {
        return New-CheckResult -Name $checkName -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: нет tools/check_prose_profile.py')
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        return New-CheckResult -Name $checkName -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: python не найден в PATH')
    }

    $output = & $python.Source $toolPath --root $script:ProjectRoot 2>&1
    $code = $LASTEXITCODE
    $text = ($output | Out-String).Trim()
    Write-Host "    проза переводов: $text"

    $lines = @($output | Where-Object { $_ -is [string] })
    $examinedText = ([regex]::Match($text, 'осмотрено:\s*(\d+)')).Groups[1].Value
    $examined = if ($examinedText) { [int]$examinedText } else { 0 }

    if ($code -ne 0) {
        foreach ($line in $lines) {
            if ($line -match '^\s*FAIL:') { $problems.Add(($line -replace '^\s*FAIL:\s*', '')) }
        }
        if ($problems.Count -eq 0) {
            $problems.Add("проверка прозы переводов завершилась кодом $code без расшифровки")
        }
    }

    # Языки из объявления допусков сверяются с осмотренными. Инструмент печатает
    # строку «язык: <код> — разделов N, абзацев M» на каждый перевод, который
    # действительно прочитал; язык, о котором отчёта нет, остался непроверенным.
    $prosePath = Join-Path $script:ProjectRoot 'docs/03_implementation/prose.json'
    $declared = @()
    if (Test-Path -LiteralPath $prosePath) {
        $examined++
        try {
            $prose = (Get-ProjectText $prosePath) | ConvertFrom-Json
            $declared = @($prose.languages.PSObject.Properties.Name)
        }
        catch {
            $problems.Add('docs/03_implementation/prose.json не разобран: допуски объявлены, а осмотреть их нечем')
            $declared = @()
        }
    }
    else {
        $problems.Add('нет docs/03_implementation/prose.json: допуски проверки прозы не объявлены данными')
    }

    $seen = @($lines | Where-Object { $_ -match '^язык:\s+\S' } |
        ForEach-Object { (($_ -replace '^язык:\s+', '') -replace '\s+—.*$', '').Trim() })

    if ($declared.Count -eq 0 -and $problems.Count -eq 0) {
        $problems.Add('в объявлении нет ни одного языка: допуски пусты, а проверка объявлена')
    }
    foreach ($language in $declared) {
        $examined++
        if ($seen -notcontains $language) {
            $problems.Add("язык '$language' объявлен в допусках, но проверка его не осмотрела: строки «язык: $language» в выводе нет")
        }
    }

    # Файлы в community/ сверяются с осмотренным: новый перевод не должен
    # оставаться непроверенным даже там, где о нём молчит объявление.
    $community = Join-Path $script:ProjectRoot 'community'
    $translations = @(if (Test-Path -LiteralPath $community) {
        Get-ChildItem -LiteralPath $community -File -Filter 'README.*.md' -ErrorAction SilentlyContinue
    })
    $examined++
    if ($translations.Count -eq 0) {
        $problems.Add('в community/ нет ни одного перевода README.*.md: прозу объявлено проверять, а сверять не с чем')
    }
    if ($translations.Count -gt 0 -and $seen.Count -ne $translations.Count) {
        $problems.Add("инструмент осмотрел $($seen.Count) переводов, а в community/ их $($translations.Count): перечень и осмотр разошлись")
    }

    if ($examined -eq 0) {
        return New-CheckResult -Name $checkName -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: ни одного объекта не осмотрено — это ложный PASS')
    }

    return New-CheckResult -Name $checkName -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
}

function Test-TranslationAddresses {
    <#
    .SYNOPSIS
        Внешние адреса и якоря переводов README: каждый адрес — поимённо.

    .DESCRIPTION
        Измеренный дефект (27.10.2026, этап И29). К этому моменту перевод
        подтверждался пятью способами: состоянием (признак неполноты, шапка,
        структура разделов, профиль раздела), ссылками на файлы поставки,
        числами индекса, терминологией и прозой. Все пять называют одну и ту же
        границу — смысл фраз не проверяется, — но между смыслом и структурой
        оставался слой, который поддаётся проверке ПОЛНОСТЬЮ: АДРЕСА.

        функция `relative_targets` в `tools/check_translations.py` (строка 227) отбрасывает всё,
        что начинается с `http://`, `https://`, `mailto:`, `ftp://` и `#`, а
        покрытие ссылок сравнивает только ОТНОСИТЕЛЬНЫЕ цели. Значит внешний
        адрес и якорь не сторожил никто: перевод, где ссылка на GitHub заменена
        на «см. репозиторий», а якорь `#installation` ведёт в никуда, проходил
        зелёным. Замер 27.10.2026: в README.md восемь внешних адресов, пять
        изображений и тринадцать якорей на одиннадцать заголовков — свойство
        выполнялось во всех пяти переводах, но проверять его было нечем.

        Свойства проверяет `tools/check_document_addresses.py`: разбирает
        разметку, отдельно считает цели ссылок и источники изображений (включая
        относительную обложку `assets/social-preview.png`, которую прежний замер
        этапа И20 не видел вовсе), требует от перевода того же ТЕКСТА цели и
        сверяет порядок якорей с порядком разделов основного документа. Разбор
        вложенной разметки обязателен: на бейдже `[![CI](badge.svg)](url)`
        простой шаблон теряет цель внешней ссылки, и черновой замер назвал шесть
        адресов вместо восьми. Строгость объявлена ДАННЫМИ
        (`docs/03_implementation/addresses.json`): виды адресов, перечень
        основного документа числом, допуск на повторы и требование порядка по
        языку; по всем трём видам допуск равен нулю и снят замером.

        Здесь к ним добавляется то, что видно только PowerShell: языки из
        объявления сверяются с осмотренными и с файлами в `community/`, а числа
        внешних адресов, изображений и якорей из объявления — с тем, что
        инструмент действительно назвал в выводе. Иначе новый перевод
        `README.fr.md` или новый адрес в README.md остались бы непроверенными
        при зелёном прогоне — ровно тот класс дефекта, ради которого этот чекер
        написан.

        Молчаливый пропуск запрещён: нет скрипта, нет python или ноль осмотренных
        объектов — это провал, а не «нечего проверять».
    #>
    $problems = [System.Collections.Generic.List[string]]::new()
    $checkName = 'translation addresses match the main document'

    $toolPath = Join-Path $script:ProjectRoot 'tools/check_document_addresses.py'
    if (-not (Test-Path -LiteralPath $toolPath)) {
        return New-CheckResult -Name $checkName -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: нет tools/check_document_addresses.py')
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        return New-CheckResult -Name $checkName -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: python не найден в PATH')
    }

    $output = & $python.Source $toolPath --root $script:ProjectRoot 2>&1
    $code = $LASTEXITCODE
    $text = ($output | Out-String).Trim()
    Write-Host "    адреса переводов: $text"

    $lines = @($output | Where-Object { $_ -is [string] })
    $examinedText = ([regex]::Match($text, 'осмотрено:\s*(\d+)')).Groups[1].Value
    $examined = if ($examinedText) { [int]$examinedText } else { 0 }

    if ($code -ne 0) {
        foreach ($line in $lines) {
            if ($line -match '^\s*FAIL:') { $problems.Add(($line -replace '^\s*FAIL:\s*', '')) }
        }
        if ($problems.Count -eq 0) {
            $problems.Add("проверка адресов переводов завершилась кодом $code без расшифровки")
        }
    }

    # Объявление адресов: языки, строгость и перечень основного документа.
    $addressesPath = Join-Path $script:ProjectRoot 'docs/03_implementation/addresses.json'
    $declared = @()
    $baseline = $null
    if (Test-Path -LiteralPath $addressesPath) {
        $examined++
        try {
            $addresses = (Get-ProjectText $addressesPath) | ConvertFrom-Json
            $declared = @($addresses.languages.PSObject.Properties.Name)
            $baseline = $addresses.baseline
        }
        catch {
            $problems.Add('docs/03_implementation/addresses.json не разобран: адреса объявлены, а осмотреть их нечем')
            $declared = @()
        }
    }
    else {
        $problems.Add('нет docs/03_implementation/addresses.json: адреса переводов не объявлены данными')
    }

    $seen = @($lines | Where-Object { $_ -match '^язык:\s+\S' } |
        ForEach-Object { (($_ -replace '^язык:\s+', '') -replace '\s+—.*$', '').Trim() })

    if ($declared.Count -eq 0 -and $problems.Count -eq 0) {
        $problems.Add('в объявлении нет ни одного языка: строгость пуста, а проверка объявлена')
    }
    foreach ($language in $declared) {
        $examined++
        if ($seen -notcontains $language) {
            $problems.Add("язык '$language' объявлен в адресах, но проверка его не осмотрела: строки «язык: $language» в выводе нет")
        }
    }

    # Числа объявленного перечня сверяются с тем, что инструмент осмотрел:
    # адрес, добавленный в README.md без правки объявления, иначе прошёл бы молча.
    if ($baseline) {
        $pairs = @(
            @{ Key = 'external'; Pattern = 'внешних адресов (\d+)'; Label = 'внешних адресов' }
            @{ Key = 'images'; Pattern = 'изображений (\d+)'; Label = 'изображений' }
            @{ Key = 'anchors'; Pattern = 'якорей (\d+)'; Label = 'якорей' }
        )
        foreach ($pair in $pairs) {
            $examined++
            $measured = ([regex]::Match($text, $pair.Pattern)).Groups[1].Value
            if (-not $measured) {
                $problems.Add("инструмент не называет число $($pair.Label): сверять объявление не с чем")
                continue
            }
            $declaredValue = $baseline.($pair.Key)
            if ([int]$measured -ne [int]$declaredValue) {
                $problems.Add("адреса: объявлено $declaredValue $($pair.Label) в основном документе, а инструмент осмотрел $measured")
            }
        }
    }

    # Файлы в community/ сверяются с осмотренным: новый перевод не должен
    # оставаться непроверенным даже там, где о нём молчит объявление.
    $community = Join-Path $script:ProjectRoot 'community'
    $translations = @(if (Test-Path -LiteralPath $community) {
        Get-ChildItem -LiteralPath $community -File -Filter 'README.*.md' -ErrorAction SilentlyContinue
    })
    $examined++
    if ($translations.Count -eq 0) {
        $problems.Add('в community/ нет ни одного перевода README.*.md: адреса объявлено проверять, а сверять не с чем')
    }
    if ($translations.Count -gt 0 -and $seen.Count -ne $translations.Count) {
        $problems.Add("инструмент осмотрел $($seen.Count) переводов, а в community/ их $($translations.Count): перечень и осмотр разошлись")
    }

    if ($examined -eq 0) {
        return New-CheckResult -Name $checkName -Ok $false -Examined 0 `
            -Details @('NOT-IMPLEMENTED: ни одного объекта не осмотрено — это ложный PASS')
    }

    return New-CheckResult -Name $checkName -Ok ($problems.Count -eq 0) -Examined $examined -Details $problems
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
    'Test-AdminPanelOrigin'
    'Test-EditionLayer'
    'Test-ExternalAccess'
    'Test-OfflineAttestation'
    'Test-DocumentAddresses'
    'Test-DeliveryVisibleToGit'
    'Test-RagrafIgnored'
    'Test-SingleDeliveryCopy'
    'Test-TranslationStates'
    'Test-Glossary'
    'Test-ProseProfile'
    'Test-TranslationAddresses'
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

# Реестр прогона: весь или отфильтрованный ключом -Only. Число в итоговой
# строке берётся из него, поэтому «прогнал одну» не выглядит как «проверил всё».
$registry = @($script:CheckFunctions)
$scope = ''
if ($Only) {
    $registry = @($registry | Where-Object { $_ -like "*$Only*" })
    if ($registry.Count -eq 0) {
        throw "нет проверки, имя которой содержит '$Only'; в реестре: $($script:CheckFunctions -join ', ')"
    }
    $scope = " (из $($script:CheckFunctions.Count): фильтр -Only '$Only')"
    Write-Host "фильтр -Only '$Only': проверок $($registry.Count) из $($script:CheckFunctions.Count)"
}

foreach ($name in $registry) {
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
Write-Host "checks: $($registry.Count)$scope, errors: $errors, warnings: $warnings, not-applicable: $notApplicable"

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
