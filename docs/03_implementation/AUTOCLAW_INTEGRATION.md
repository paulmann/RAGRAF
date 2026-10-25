# RAGRAF × AutoClaw: подключение, устройство, проверка

Документ описывает, как индекс RAGRAF работает в среде **AutoClaw** (`zwork` 2.0.2,
Electron-приложение в `%LOCALAPPDATA%\Programs\AutoClaw2`), и что нужно, чтобы
инструменты `ragraf_*` были доступны агенту. Всё ниже — замеры текущей сессии
и факты из кода приложения; где утверждение опирается на документ, это сказано.

Парный документ по DeepSeek Harness: `docs/03_implementation/HARNESS_INTEGRATION.md`.
Разница сред важна и разобрана в разделе 1.

Проверка свежести копий навыка в аккаунтах сделана замером: `SKILL.md`,
`reference/operations.md` и `reference/repair.md` совпадают с проектной редакцией
по SHA-256 в обоих аккаунтах.

Оглавление:

1. [Два независимых канала: CLI и MCP](#1-два-независимых-канала-cli-и-mcp)
2. [Что уже настроено на этой машине](#2-что-уже-настроено-на-этой-машине)
3. [Канал 1 — CLI: работает без настройки](#3-канал-1-cli-работает-без-настройки)
4. [Канал 2 — MCP: что даёт и как подключается](#4-канал-2-mcp-как-подключается-найдено-в-коде)
5. [Результат доработки RAGRAF: 4 дефекта совместимости](#5-результат-доработки-ragraf-4-дефекта-совместимости)
6. [Навык и правила](#6-навык-и-правила)
7. [Проверка](#7-проверка)
8. [Откат](#8-откат)
9. [Где что лежит в AutoClaw](#9-где-что-лежит-в-autoclaw)

---

## 1. Два независимых канала: CLI и MCP

Смешивать их нельзя — от этого зависит, что именно проверять.

| | Канал CLI | Канал MCP |
|---|---|---|
| Как вызывается | `python tools/ragraf.py …` через инструмент оболочки | инструменты `ragraf_*` в списке агента |
| Кто запускает | агент вручную команду | платформа, при подключённом сервере |
| Зависит от настроек приложения | нет | да (сервер надо зарегистрировать) |
| Что даёт | все 13 подкоманд и 22 операции | 24 операции как инструменты |
| Требует `--project` | да, или `ensure <каталог>` | нет: корпус берётся по рабочему каталогу |

**Главное следствие.** Если инструментов `ragraf_*` в списке нет — это **не поломка
RAGRAF**: индекс, поиск, схема и графы остаются полностью рабочими через CLI. Замер
этой сессии: `tool_search` по имени `ragraf` вернул **0 инструментов**, то есть в текущей
сессии канал MCP не активен, и правила проекта (`.pi/agent/AGENTS.md`, раздел 8)
работают именно через первый канал.

## 2. Что уже настроено на этой машине

| Элемент | Состояние | Где |
|---|---|---|
| Правило «сначала RAGRAF» | **есть** | `~/.pi/agent/AGENTS.md`, раздел 8 |
| Навык `ragraf-core` | **есть в обоих аккаунтах** | `<account>\runtime\zwork-agent\skills\ragraf-core` |
| Корпуса в реестре | **18 разработок** | `catalog\catalog.json` |
| CLI RAGRAF в окружении агента | **работает** | `python tools/ragraf.py` найден и отвечает |
| Инструменты расширения (MCP) | **не подключены** | замер: 0 строк в `governed_connected_sources`, `tool_search` → 0 |

**Ошибка в правилах, исправленная 13.10.2026.** Раздел 8.2.1 утверждал
«RAGRAF подключён к AutoClaw как локальный MCP-сервер: он отдаёт 23 инструмента
с префиксом `ragraf_`». Оба числа неверны: подключения нет (0 инструментов), а в коде
их 24. Правка внесена скриптом `harness/fix_agents_section.py` (с резервной копией),
заодно добавлена таблица имён вызова по клиентам — см. §4.4.

Аккаунты AutoClaw на машине (у каждого своё дерево навыков и свой `runtime`):

```
%APPDATA%\AutoClaw-oversea-official\accounts\53fa6b3b…   (основной)
%APPDATA%\AutoClaw-oversea-official\accounts\fe79f917…
```

Навык разложен в **оба** — тогда при смене учётной записи он остаётся виден.

## 3. Канал 1 — CLI: работает без настройки

Это основной путь, пока MCP не зарегистрирован. Три правила.

**Первым шагом — актуализировать корпус:**

```powershell
python tools\ragraf.py ensure <каталог проекта>
```

Ответ несёт `status`: `actual` — индекс актуален, `reindexed` — пересобран только что,
`created` — корпуса не было и он создан, `not_a_project` — каталог не похож на проект,
`error` — причина в поле `reason`.

**Дальше — операция, а не чтение файлов:**

```powershell
python tools\ragraf.py -p config\<корпус>.yaml query symbol --name corpus_hash
python tools\ragraf.py -p config\<корпус>.yaml check
```

**Признак `stale: true` означает, что фактам верить нельзя** до переиндексации:
код ушёл вперёд. Это не «предупреждение на всякий случай».

Список заведённых корпусов даёт команда, которой конфигурация не нужна:

```powershell
python tools\ragraf.py catalog
```

### Почему CLI здесь предпочтительнее MCP

Не из-за удобства. Каждая команда печатает **текст, который агент читает сам**, а MCP-ответ
надо интерпретировать по схеме. Плюс CLI не зависит ни от перезапуска приложения, ни от
регистрации сервера — то есть не может «тихо отвалиться». Замер по полигону
(`docs/03_implementation/RAGRAF_EFFECT_REPORT.md`): 14 вызовов инструментов против 28 без
индекса, попадание в файл-цель с первого запроса — 92%.

## 4. Канал 2 — MCP: как подключается (найдено в коде)

Интерфейс: **Settings → Tools & connections → Add tool**. Диалог предлагает два типа:
`Local program` и `Online service`; для локального — «Name», «Launch command»,
«Arguments», «Environment variables (optional, one KEY=value per line)».

### 4.1 Где сохраняется запись

Регистрация внешнего инструмента живёт в **состоянии приложения**, а не в файле профиля:

```
<account>\runtime\runtime.sqlite
    governed_connected_sources            (owner_id, workspace_id, kind, slug, name,
                                           endpoint, enabled, status, tools_json, …)
    governed_connected_source_revisions   (история изменений записи)
```

На момент написания — **0 строк** в обеих таблицах, то есть внешних инструментов не заведено.
Колонки читаются read-only запросом; писать туда руками нельзя (см. §5 в конце).

### 4.2 Декларация сервера

Поле `mcpServers` есть в схеме вертикального пакета:
`dist\resources\vertical-packs\code\zwork-pack.json`. У официальных паков оно пустое
(`"mcpServers": []`), пользовательских паков на диске нет вовсе (поиск `zwork-pack.json`
вне установки — 0 файлов). То есть штатный путь добавления — именно интерфейс.

### 4.3 Как запускается локальный сервер (прочитано в `main.cjs`)

Хост: `zwork-agent/src/host/stdio-mcp-process-host.ts` (в бандле — `createNodeStdioMcpProcessHost`).

```js
const child = spawn(resolved, [...args], {
    stdio: ['pipe', 'pipe', 'pipe'],
    env: stdioMcpChildEnv(process.env, input.env),
    cwd: input.cwd,            // ← берётся из записи; поля в интерфейсе НЕТ
    windowsHide: true,
});
```

**Белый список окружения** (`INHERITED_ENV_KEYS`) — дословно из бандла:
`PATH`, `HOME`, `TMPDIR`, `TMP`, `TEMP`, `LANG`, `LC_ALL`, `TERM`, `SHELL`, `USER`,
`LOGNAME`, `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, `http_proxy`, `https_proxy`, `no_proxy`,
плюс на Windows: `SystemRoot`, `ComSpec`, `APPDATA`, `LOCALAPPDATA`, `USERPROFILE`.
Переменные из поля «Environment variables» добавляются поверх.

**Три следствия, каждое важно:**

1. `PYTHONUTF8`, `PYTHONIOENCODING` **не наследуются**. Без правки `force_utf8_stdio()`
   сервер падал бы на первом же русском ответе — это и был дефект, найденный через
   DeepSeek Harness (см. §5).
2. `PYTHONUSERBASE` тоже не наследуется, поэтому `python` из `PATH` не нашёл бы
   зависимости («не установлен PyYAML»). Отсюда требование: **интерпретатор из `.venv`**.
3. **`cwd` задать через интерфейс нельзя.** Поэтому в аргументах обязателен
   `--project` — иначе корпус придётся выбирать инструментом `ragraf_use_project`
   (он есть в сборке от 13.10.2026; на более старой сборке без аргумента сервер не поднимется).

### 4.4 Имя сервера и имена инструментов

`slug` выводится из поля «Name» и обязан подходить под `^[a-z][a-z0-9_]{0,39}$`:
латиница в нижнем регистре, цифры, подчёркивание, первая — буква. Имя `ragraf` подходит.

Имена инструментов у модели строятся **не** как `mcp__<server>__<tool>` (строки `mcp__`
в бандле нет вовсе), а так:

```
toolId   = source.connected.ragraf.ragraf_search
name     = source_ragraf_ragraf_search
label    = RAGRAF: search
```

То есть в списке агента это `source_ragraf_ragraf_index_status`,
`source_ragraf_ragraf_ensure` и так далее. **Правило в `~/.pi/agent/AGENTS.md`
(раздел 8) написано под имена `ragraf_*` и требует уточнения после подключения** —
иначе агент будет искать имена, которых нет, и решит, что индекс недоступен.

### 4.5 Передача параметров инструментов

Схема `ConnectedSourceToolSchema` ожидает у каждого инструмента `name` по образцу
`^[a-z][a-z0-9_]{0,63}$`, а параметры берёт из `inputSchema` самого сервера
(до 16 КиБ). RAGRAF отдаёт имена `ragraf_search`, `ragraf_index_status` — образцу
подходят, поэтому параметры приходят как есть. Если сервер не отдаёт `inputSchema`,
действует старая форма: один строковый аргумент `input` с JSON внутри. RAGRAF схему
отдаёт — этот случай не наш.

### 4.6 Что вписать в поля интерфейса

**Name:** `RAGRAF` (отображаемое имя; slug получится `ragraf`).

**Type:** `Local program`.

**Launch command:**

```
.venv\Scripts\python.exe
```

**Arguments** — одной строкой:

```
tools\ragraf-mcp.py --project config\ragraf.yaml --agent AutoClaw --agent-kind autoclaw
```

**Environment variables:** оставить пустым. Причины: (1) зависимостям RAGRAF ничего
не нужно — `.venv` самодостаточен, замерено при полностью снятых `PYTHON*`; (2) любые
секреты через это поле не нужны, а значения попадают в состояние приложения;
(3) лишняя переменная — это ещё одна причина отказа запуска.

После сохранения нажать **Test** и дождаться статуса `Connected · N tools`.
Ожидается **41** инструмент. Если статус `Connection failed`, код причины виден
в подписи ошибки: `stdio_executable_not_found` — неверный путь к python;
`stdio_mcp_command_required` — пустая команда; `connected_source_slug_exists` —
сервер с таким именем уже есть.

### 4.7 Проверка подключения (замером)

В новой сессии спросить у агента список инструментов или вызвать
`source_ragraf_ragraf_index_status`. Ожидается ответ вида
`op=index_status rev=… capability=full`.

Независимо от приложения — прогон сервера в тех же условиях, что создаёт AutoClaw
(тот же белый список окружения):

```powershell
python .staging\autoclaw-mcp\probe_autoclaw_env.py
```

Замер этой сессии: `tools/list: 24`, `ensure` — `rev-0057`, `symbol` —
`src/ragraf/mcp_server.py`, `use_project` → `revoap.com` `rev-0035`, stderr чист.

## 5. Результат доработки RAGRAF: 4 дефекта совместимости

Подключение ко второму клиенту (DeepSeek Harness) вскрыло дефекты, которые касались
и AutoClaw. Все четыре закрыты, каждый — с регрессионной защитой.

### 1. Сервер падал на первом русском ответе при урезанном окружении

MCP-клиент передаёт дочернему процессу только `APPDATA`, `HOMEDRIVE`, `HOMEPATH`,
`LOCALAPPDATA`, `PATH`, `PROCESSOR_ARCHITECTURE`, `SYSTEMDRIVE`, `SYSTEMROOT`, `TEMP`,
`USERNAME`, `USERPROFILE`, `PROGRAMFILES`. Ни `PYTHONUTF8`, ни `PYTHONIOENCODING` туда
не попадают, локаль процесса остаётся `cp1252` — и `send()` падал с
`UnicodeEncodeError: 'charmap' codec` (`src/ragraf/mcp_server.py`).

Оболочка AutoClaw ставит обе переменные, поэтому **для AutoClaw дефект был невидим** —
он проявился только на втором клиенте.

**Правка:** `force_utf8_stdio()` в `src/ragraf/mcp_server.py` — потоки приводятся к UTF-8
в самом сервере, а не берутся из окружения.

### 2. Обязательный `--project` не давал работать с несколькими проектами

**Правка:** `--project` необязателен, корпус ищется по рабочему каталогу (реестр
разработок), добавлен инструмент `ragraf_use_project`. Один сервер обслуживает все
18 корпусов.

### 3. Команда `reindex` не записывала прогон в реестр

Измерено на собственном корпусе: в индексе `rev-0046`, в реестре `rev-0045` со старой
суммой; `check` из-за этого переиндексировал корпус заново, а `catalog` и панель
показывали прошедшее состояние.

**Правка:** `src/ragraf/cli.py` — `reindex` пишет прогон той же общей точкой
`watch.record_index_run`, что и `index`.

### 4. `ensure` и `use_project` не попадали в статистику

Телеметрия навешивается обёрткой на методы `op_*` фасада, а эти две операции живут
в MCP-сервере. В сводке `usage` их не было — то есть **нельзя было ответить, соблюдают ли
агенты правило первого шага**.

**Правка:** `Facade.record_external_call` (`src/ragraf/facade.py`) + запись со стороны
сервера. Замер после правки: `ensure 1`, `use_project 1` в сводке по агенту.

### Зависимости: почему интерпретатор из `.venv`

На этой машине зависимости RAGRAF достижимы интерпретатору из `PATH` только через
переменную `PYTHONUSERBASE` (`%APPDATA%\AutoClaw-oversea-official\toolchain\python\Python313\site-packages`).
Внешний клиент её вычищает — и `python` из `PATH` падает на «не установлен PyYAML».
Замерено: `.venv` проекта самодостаточен — при полностью снятых `PYTHON*`-переменных он
импортирует `yaml`, `fastembed`, `sqlite_vec`, `tree_sitter`, `ijson`, `tiktoken`.
**Для AutoClaw это тоже полезно:** CLI в оболочке агента работает и так, но `.venv`
перестаёт зависеть от настроек окружения.

## 6. Навык и правила

### Навык `ragraf-core`

Лежит в дереве каждого аккаунта:

```
%APPDATA%\AutoClaw-oversea-official\accounts\<account>\runtime\zwork-agent\skills\ragraf-core\
    SKILL.md                 (вход: когда применять, порядок работы)
    reference/operations.md  (выбор операции и границы)
    reference/repair.md      (что делать, когда индекс сломан)
```

**Источник — каталог проекта:** `docs\skills\ragraf-core\`. Правка идёт
в проект, затем копируется в служебный каталог аккаунта. Иначе при следующем развёртывании
копия в аккаунте «отстанет» от проекта и агент будет работать по старой редакции.

Копий ровно две (**не** по числу аккаунтов: их два, и в обоих навык есть).

Проверено замером свежести копий (эта сессия): в обоих аккаунтах лежат три файла
(`SKILL.md` 13124 байта, `reference/operations.md` 16633, `reference/repair.md` 13759),
и **SHA-256 каждого совпадает** с проектной редакцией — то есть копия в аккаунте не
отстала от источника.

**Обновление копии после правки навыка:**

```powershell
$src = 'docs\skills\ragraf-core'
Get-ChildItem "$env:APPDATA\AutoClaw-oversea-official\accounts" -Directory | ForEach-Object {
    $dst = Join-Path $_.FullName 'runtime\zwork-agent\skills\ragraf-core'
    if (Test-Path -LiteralPath $dst) {
        Copy-Item -Recurse -Force "$src\*" $dst
        Write-Host "обновлено: $dst"
    }
}
```

**Проверка навыка:** файл начинается с `---`, поля `name` и `description` разбираются
построчно, размер меньше 256 КиБ, кодировка UTF-8 без BOM, переводы строк LF, а копия
в аккаунте совпадает с проектной побайтово.

### Правила: `~/.pi/agent/AGENTS.md`

Это **пользовательский** файл правил AutoClaw — он подхватывается в любом проекте, где
нет своего `AGENTS.md`, и наоборот: проектный `AGENTS.md` строже. Подхватывается также
`AGENTS.md` из родительских каталогов до границы `.git`.

Раздел, который заставляет работать через индекс, — **раздел 8** «Обязательное
использование RAGRAF при разработке кода». Три места, которые надо знать:

| Что | Где |
|---|---|
| Первый шаг задачи | §8.2: `ensure <каталог>` |
| Инструменты MCP предпочитать оболочке | §8.2.1 (исправлен 13.10.2026) |
| Отчётность: ревизия, сумма, ссылки `файл:строка` | §8.7 |

**Важно про формулировку.** §8.2.1 прямо говорит: если инструментов в Run нет, работа
идёт через оболочку командами §8.3 и далее — «это не отменяет обязательности RAGRAF,
а меняет только способ вызова». Поэтому правило исполнимо в обеих конфигурациях — вместе
с MCP и без него. Именно поэтому индекс работал на этой машине, хотя сервер не был
зарегистрирован ни разу.

## 6a. Состояние на 25.10.2026 (ревизия rev-0284)

К прежним возможностям добавлены: глобальный указатель (`route`), реестр
источников и карта метаданных (`sources`, `map`, страница `/sources`), документы
PDF/DOCX/XLSX/PPTX/EML/ODT/RTF в языке `document`, локальная польза
(`feedback`, `analytics`, страница `/value`). Числа сверяются с кодом
(`Test-DocumentedNumbers`): сервер отдаёт **59 инструментов**, фасад —
52 операции, CLI — 21 подкоманда.

Полная настройка обоих агентов и каталог возможностей —
`docs/03_implementation/AGENTS_SETUP_AND_CAPABILITIES.md`.

## 7. Проверка
Четыре уровня, от узкого к широкому.

**1. Канал CLI жив и видит корпуса:**

```powershell
python tools\ragraf.py catalog
python tools\ragraf.py ensure RAGRAF
```

Ожидается: 18 разработок; `status: actual` и совпадение сумм.

**2. Свежесть конкретного корпуса:**

```powershell
python tools\ragraf.py -p config\ragraf.yaml check
```

Ожидается: `сумма корпуса сейчас` = `сумма при индексации`, `изменилось после индексации: нет`.

**4. Канал MCP (когда подключён):**

```
в списке инструментов — 24 штуки вида source_ragraf_ragraf_*
source_ragraf_ragraf_ensure()       → status = actual | reindexed | created
source_ragraf_ragraf_index_status() → index_rev, capability
source_ragraf_ragraf_use_project(project="revoap.com")  → ревизия ДРУГОГО корпуса
```

Независимая проверка сервера — в тех же условиях, что создаёт AutoClaw:

```powershell
python .staging\autoclaw-mcp\probe_autoclaw_env.py
```

Замер 13.10.2026: окружение без единой `PYTHON*`, `tools/list` — 24 инструмента,
`ensure` → `rev-0057`, `symbol` — `src/ragraf/mcp_server.py`, `use_project` →
`revoap.com rev-0035`, stderr чист. Штатная регрессия (без приложения):

```powershell
cd RAGRAF
Remove-Item Env:\PYTHONUTF8, Env:\PYTHONIOENCODING -ErrorAction SilentlyContinue
python tests\test_mcp_compat.py        # 15 проверок
```

**4. Телеметрия — видно, кто и что вызвал:**

```powershell
python tools\ragraf.py -p config\ragraf.yaml usage --agent AutoClaw
```

Ожидается строка по агенту с числом вызовов и операциями, включая `ensure`.
Если `ensure` в разбивке нет — значит клиент его не вызывал, а не «телеметрия потеряла».

## 8. Откат

Регистрация MCP — единственное обратимое действие в интерфейсе: снять сервер в том же
разделе настроек (Tools & connections) и перезапустить приложение. Индекс, реестр и корпуса
от этого не меняются: запись живёт в `governed_connected_sources`, а не в файлах проекта.

Правка правил (`~/.pi/agent/AGENTS.md`, раздел 8.2.1) откатывается возвратом резервной
копии — имя пишет применявший скрипт:

```powershell
$dir = "$([Environment]::GetFolderPath('UserProfile'))\.pi\agent"
Copy-Item "$dir\AGENTS.md.bak-20261013-150821-mcp-names" "$dir\AGENTS.md" -Force
```

Правки самого RAGRAF **аддитивные**; чтобы вернуть прежнее поведение, достаточно трёх
изменений: убрать вызов `force_utf8_stdio()`, вернуть `required=True` у `--project`,
удалить `ragraf_use_project` из `TOOLS` и `_handlers`. Ни одно из них не затрагивает
индекс, схему хранилища, нумерацию ревизий или реестр — **переиндексация при откате
не требуется**.

Копии навыка в аккаунтах восстанавливаются из проекта:

```powershell
Copy-Item -Recurse -Force docs\skills\ragraf-core `
    "$env:APPDATA\AutoClaw-oversea-official\accounts\53fa6b3b559c638c798a72686d01e6da331c2f0a886bd5428db5ed926884a587\runtime\zwork-agent\skills\ragraf-core"
```

## 9. Где что лежит в AutoClaw

| Что | Путь |
|---|---|
| Установка приложения (`zwork` 2.0.2) | `%LOCALAPPDATA%\Programs\AutoClaw2` |
| Код приложения (не asar) | `…\resources\app\dist\main.cjs` |
| Встроенный агент | `…\resources\app\node_modules\@earendil-works\pi-coding-agent` |
| Вертикальные паки (в т. ч. `mcpServers`) | `…\app\dist\resources\vertical-packs\<pack>\zwork-pack.json` |
| Каталог данных продукта | `%APPDATA%\AutoClaw-oversea-official` |
| Учётные записи | `…\accounts\<sha256>` |
| Навыки агента | `<account>\runtime\zwork-agent\skills\<имя>\SKILL.md` |
| Проекты | `<account>\projects\<имя>` |
| Состояние (БД, права, артефакты) | `<account>\runtime\runtime.sqlite` |
| Секреты и связи | `<account>\runtime\*.enc`, `credentials.enc`, `owner-secrets.enc` |
| Пользовательские правила (все проекты) | `~/.pi/agent/AGENTS.md` |
| Правила каталога проекта | `AGENTS.md` в корне проекта |

**Что не читать и не править.** `*.enc` в `runtime` (содержимое не нужно — только имя,
размер и правило) и `runtime.sqlite` в обход продукта: в нём права инструментов
(`governed_tool_approvals`), артефакты (`governed_tool_*`) и привязки каналов. Правка
этой базы руками обходит учёт прав — то есть делает больше, чем просили.

**Особенность установки:** код приложения лежит распакованным (`resources\app\dist`),
а не в `app.asar`. Это удобно для разведки: файлы читаются обычными средствами, и
`main.cjs` можно искать по строкам. Глубокая разведка через `app.asar` — задача
DeepSeek Harness, и инструменты для неё остались в `.staging/harness-mcp/`.
