# RAGRAF × DeepSeek Harness: подключение, устройство, проверка

Документ описывает, как индекс RAGRAF работает в среде **DeepSeek Harness (dsh)** —
Electron-приложения `DeepSeek Harness 0.2.0-rc.2` (`%LOCALAPPDATA%\Programs\DeepSeek Harness`).
Всё здесь — измерения текущей сессии: команда или путь, который можно повторить.

Оглавление:

1. [Как это устроено](#1-как-это-устроено)
2. [Что было доработано в RAGRAF](#2-что-было-доработано-в-ragraf)
3. [Установка: три шага](#3-установка-три-шага)
4. [Проверка](#4-проверка)
5. [Поведение клиента HARNESS: что он поддерживает и что игнорирует](#5-поведение-клиента-harness-что-он-поддерживает-и-что-игнорирует)
6. [Три ограничения, которые надо знать](#6-три-ограничения-которые-надо-знать)
7. [Откат](#7-откат)
8. [Поведение HARNESS: где лежит что](#8-поведение-harness-где-лежит-что)

---

## 1. Как это устроено

RAGRAF подключается к HARNESS как **MCP-сервер по stdio**. Клиент HARNESS —
плагин `@deepseek-ai/dsh-mcp-client`; один экземпляр плагина = один сервер,
имя инструментов строится как `mcp__<serverName>__<rawName>`
(`dsh/node_modules/@deepseek-ai/dsh-mcp-client/lib/index.js:96-97`,
функция `publicToolName`), то есть `mcp__ragraf__ragraf_search`, `mcp__ragraf__ragraf_symbol`
и так далее. Путь — ВНУТРИ архива `resources/app.asar` (это ОДИН файл размером
121 348 951 байт; каталога `resources/dsh/lib` на диске не существует, `dsh/lib`
— виртуальный путь внутри архива). Файл 837 строк, не минифицирован; извлечь его
можно разбором заголовка архива, не распаковывая 121 МБ целиком.

| Что | Где |
|---|---|
| Точка входа сервера | `tools/ragraf-mcp.py` (репозиторий RAGRAF) |
| Встроенные 40 инструментов | `src/ragraf/mcp_server.py` |
| Фасад операций (38) | `src/ragraf/facade.py` |
| Реестр корпусов | `catalog/catalog.json` |
| Слой патчей профиля | `%USERPROFILE%\.dsh\profiles\desktop\cordis.patch.yml` |
| Навык | `%USERPROFILE%\.dsh\skills\ragraf-core\` (`SKILL.md` + `reference/`) |
| Глобальные правила | `%USERPROFILE%\.dsh\AGENTS.md`, раздел 6 |

**Почему stdio, а не `streamable-http`.** MCP-клиент HARNESS объявляет
`capabilities: {}` (`lib/index.js:600`, объект `new Client(...)` в
`connectGeneration`) — то есть **корней (roots) сервер не получает**. Это
подтверждено вторым фактом: в клиенте нет НИ ОДНОГО обращения к `roots`,
`sampling` и `elicitation` (поиск по извлечённому файлу: 0 совпадений на каждое
слово). Если поднимать RAGRAF по HTTP как один сервис, «какой сейчас проект»
определить нечем: текущий каталог клиента до сервера не доходит. У stdio-сервера
рабочий каталог процесса известен, и корпус определяется по реестру разработок.

**Почему один сервер на все проекты.** Клиент HARNESS не даёт инструментам RAGRAF
перезапустить себя, а держать по серверу на проект значило бы держать по копии
индекса в памяти на каждый. Вместо этого корпус переключается операцией
`mcp__ragraf__ragraf_use_project`.

## 2. Что было доработано в RAGRAF

Дефект был не «неудобно», а «не работает вовсе». Измерено запуском:

```
UnicodeEncodeError: 'charmap' codec can't encode characters in position 97-105
  File "src/ragraf/mcp_server.py", line 551, in send
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
```

Три причины, каждая проверена замером:

1. **Кодировка потока.** MCP SDK наследует дочернему процессу только
   `APPDATA`, `HOMEDRIVE`, `HOMEPATH`, `LOCALAPPDATA`, `PATH`, `PROCESSOR_ARCHITECTURE`,
   `SYSTEMDRIVE`, `SYSTEMROOT`, `TEMP`, `USERNAME`, `USERPROFILE`, `PROGRAMFILES`
   (`@modelcontextprotocol/client/dist/stdio.mjs`, `DEFAULT_INHERITED_ENV_VARS`), а HARNESS
   дополнительно вычищает имена с `PASSWORD|SECRET|TOKEN|KEY` и все `DSH_*`. Ни
   `PYTHONUTF8`, ни `PYTHONIOENCODING` в этот набор не входят — локаль процесса
   остаётся `cp1252`, и первый же ответ с русским текстом убивает сервер.
   **Правка:** `force_utf8_stdio()` в `src/ragraf/mcp_server.py` перенастраивает
   `stdin`, `stdout`, `stderr` на UTF-8, не полагаясь на окружение.
2. **Зависимости.** На этой машине `fastembed`, `PyYAML`, `sqlite_vec`, `tree_sitter`,
   `ijson` достижимы интерпретатору из `PATH` только через переменную `PYTHONUSERBASE`
   (измерено: в обычном окружении `import yaml` находит
   `%APPDATA%\AutoClaw-oversea-official\toolchain\python\Python313\site-packages`).
   HARNESS её вычищает → «не установлен PyYAML». **Решение:** сервер запускается
   интерпретатором из `.venv` самого проекта: замерено, что при полностью снятых
   `PYTHON*`-переменных он импортирует все шесть пакетов.
3. **Обязательный `--project`.** Клиент, запускающий сервер без аргументов, не мог
   стартовать вообще. **Правка:** корпус определяется по рабочему каталогу через реестр;
   если не нашёлся — сервер всё равно поднимается, честно отвечает на `catalog` и
   `use_project`, а остальным операциям отказывает с причиной.

Итог правок в RAGRAF:

| Файл | Что сделано |
|---|---|
| `src/ragraf/mcp_server.py` | `force_utf8_stdio()`; `--project` не обязателен; `_resolve_default_project()`; `ragraf_use_project`; `--agent` берётся ещё и из `RAGRAF_AGENT`; отказ по невыбранному корпусу — понятный текст, а не `ConfigError` |
| `src/ragraf/cli.py` | команда `reindex` теперь записывает прогон в реестр, как `index` (см. ограничение 3 ниже) |
| `tests/test_mcp_compat.py` | новый файл: 15 проверок на урезанное окружение, автоопределение корпуса и переключение |
| `tools/Invoke-ProjectDoctrine.ps1` | проверки `Test-McpClientCompatibility` и `Test-ExternalOpsAreRecorded`; правило записи в реестр теперь смотрит функции, а не файл (19 проверок в чекере) |
| `README.md`, `docs/03_implementation/INTEGRATION.md`, `AGENTS.md` | числа приведены к коду: 39 инструментов, 32 операции фасада; ловушки 24–26 с замерами |

Совместимость с прежними клиентами сохранена: AutoClaw и SagaAI передают
`--project` явно и выставляют `PYTHONIOENCODING` — для них ничего не изменилось.

## 2a. Состояние на 25.10.2026 (ревизия rev-0284)

К прежнему набору добавлены: `mcp__ragraf__ragraf_route` (глобальный указатель),
реестр источников и карта метаданных (CLI `sources`, `map`, страница
`/sources`), документы PDF/DOCX/XLSX/PPTX/EML/ODT/RTF (язык `document`,
`search` + `live_read`) и локальная польза (CLI `feedback`, `analytics`,
страница `/value`). Числа берутся из кода, а не из этого текста, и сверяются
проверкой `Test-DocumentedNumbers`: сервер отдаёт **59 инструментов**,
фасад — 54 операции, CLI — 21 подкоманда; чекер — 35 проверок.

Настройка обоих агентов и полный каталог возможностей —
`docs/03_implementation/AGENTS_SETUP_AND_CAPABILITIES.md`.

## 3. Установка: три шага

### Шаг 1. Подключить MCP-сервер

Блок лежит готовым: `harness/ragraf-mcp.patch.yml`. Его надо вставить в слой
патчей профиля. Приложение дописывает в `cordis.patch.yml` **массив** патчей,
поэтому блок вставляется отдельной строкой `- insert:` в конец файла.

Источник (в репозитории): `harness\ragraf-mcp.patch.yml`

```powershell
$src = 'harness\ragraf-mcp.patch.yml'
$dst = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.dsh\profiles\desktop\cordis.patch.yml'

# 1. бэкап (обязательно: файл принадлежит пользователю)
Copy-Item $dst "$dst.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"

# 2. проверить, что блока ещё нет
Select-String -LiteralPath $dst -Pattern 'mcp-ragraf'

# 3. дописать блок
Add-Content -LiteralPath $dst -Value (Get-Content -LiteralPath $src -Raw) -Encoding utf8NoBOM
```

Проверить, что патч применяется (ничего не запуская):

```powershell
$dsh = 'C:\Users\<пользователь>\AppData\Local\Programs\DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd'
& $dsh --profile headless --patch $src --dump-config | Select-String 'serverName: ragraf' -Context 1,3
```

Для профиля, отличного от `desktop`, блок кладётся в его `cordis.patch.yml`
тем же способом. Профиль `desktop` — рабочий профиль приложения; профиль
`headless` — одноразовые задачи из командной строки.

### Шаг 2. Установить навык

```powershell
$skills = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.dsh\skills\ragraf-core'
New-Item -ItemType Directory -Path $skills -Force | Out-Null
Copy-Item 'harness\skills\ragraf-core\*' $skills -Recurse -Force
```

Корень `%USERPROFILE%\.dsh\skills` читается провайдером навыков HARNESS
(`@deepseek-ai/dsh-skill-filesystem`): на верхнем уровне ищется `<имя>/SKILL.md`.
Навык в этом каталоге обнаруживается **без перезапуска** — провайдер следит за корнем.

### Шаг 3. Добавить правило в глобальные инструкции

HARNESS читает `%USERPROFILE%\.dsh\AGENTS.md` (`dsh-agent-instructions`,
`dshHome` = `$DSH_HOME` или `~/.dsh`). Раздел для вставки: `harness/agents-section.md`.
Ставится перед разделом «Журнал изменений», иначе правило окажется после истории.

Готовый раздел уже добавлен на этой машине как **раздел 6**
(ожидаемое поведение: агент на вопрос «с чего начинается задача о коде» отвечает
`mcp__ragraf__ragraf_ensure` и ссылается на раздел 6).

## 4. Проверка

Четыре уровня — от узкого к широкому.

**0. Гейт использования индекса (§3.2).** Подключён штатным `PreToolUse`-хуком
(правка ядра DSH невозможна: клиент в упакованном `app.asar`). Ставится так:

```powershell
python tools\install_harness_gate.py           # идемпотентно, с резервной копией
python tools\install_harness_gate.py --check   # состояние
python tests\test_gate.py                      # правила, состояние, живой прогон хука
python tests\test_gate_live.py                 # свежий и отставший корпус
```

Решение по одному вызову можно получить без DSH — это ровно то, что увидит клиент:

```powershell
'{"tool_name":"grep","tool_input":{"pattern":"x","path":"src"},"cwd":"RAGRAF","session_id":"t1"}' |
  .venv\Scripts\python.exe tools\ragraf.py -p config\ragraf.yaml query gate --payload - --json
```

Режим — `RAGRAF_GATE_MODE` (`warn` по умолчанию; `ask`, `deny`, `off`). Довод в
пользу `warn` по умолчанию измерен: HARNESS в этой установке сообщает
«approval prompts are disabled: actions that require approval are rejected
automatically» — то есть `ask` здесь молча превращается в отказ.

**1. Сервер сам по себе, в урезанном окружении.** Это проверка того самого дефекта:

```powershell
cd RAGRAF
Remove-Item Env:\PYTHONUTF8, Env:\PYTHONIOENCODING -ErrorAction SilentlyContinue
python tests\test_mcp_compat.py
```

Ожидается: `ИТОГ: пройдено 13, провалено 0`.

**2. Живой запуск внутри HARNESS.** Задача, где агент обязан вызвать инструмент:

```powershell
$dsh = 'C:\Users\<пользователь>\AppData\Local\Programs\DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd'
& $dsh --profile headless --patch 'harness\ragraf-mcp.patch.yml' --json `
  "Вызови mcp__ragraf__ragraf_index_status и приведи первую строку ответа"
```

Ожидается в потоке событий:

```json
{"type":"tool_call","tool":"mcp__ragraf__ragraf_index_status","input":{}}
{"type":"tool_result","status":"completed","result":"op=index_status rev=rev-0043 capability=full ..."}
```

**3. Навык виден и загружается:**

```
dsh --profile headless --patch <блок> "Загрузи навык ragraf-core и назови первый шаг"
```

Ожидается вызов `{"tool":"skill","input":{"name":"ragraf-core"}}` с телом навыка.

**4. Чекер проекта** (правило на свойство совместимости):

```powershell
pwsh -NoLogo -NoProfile -NonInteractive -File tools\Invoke-ProjectDoctrine.ps1
```

Ожидается: `checks: 19, errors: 0, warnings: 0, Result: PASS` и строки
`[PASS] MCP server survives a scrubbed client environment`,
`[PASS] operations outside the facade are recorded`.

## 5. Поведение клиента HARNESS: что он поддерживает и что игнорирует

Ниже — разведка по коду установки, а не по документации: извлечён файл
`dsh/node_modules/@deepseek-ai/dsh-mcp-client/lib/index.js` (837 строк) из архива
`resources/app.asar` разбором его заголовка. Это ОДИН файл-архив на
121 348 951 байт; `dsh/lib` — виртуальный путь внутри него, подкаталога
`resources/dsh/lib` на диске нет. Номера строк ниже относятся именно к этому
файлу.

**Поддерживается:**

| Что | Где в клиенте | Следствие для RAGRAF |
|---|---|---|
| `tools/list`, `tools/call` | `lib/index.js:127-161` (`syncTools`), `:140-147` | инструменты `mcp__ragraf__*` видны и вызываются |
| `resources/list`, `resources/templates/list`, `resources/read` | `lib/index.js:685-699` (`resources.request`, закрытый набор из трёх методов) | `ragraf://status`, `ragraf://catalog`, `ragraf://corpus/{name}/map` ДОСТУПНЫ |
| `instructions` из `initialize` | `lib/index.js:654-656`, предел `DEFAULT_MAX_INSTRUCTION_BYTES = 32768` (`:441`) | инструкции сервера (1 198 байт) попадают в системный промпт агента |
| `outputSchema` + `structuredContent` | `lib/index.js:186`, `:200-221`, `:237-240` | копия ВАЛИДИРУЕТСЯ и сохраняется, но модель видит только текстовые блоки — см. ниже |
| `notifications/cancelled` | клиент передаёт в вызов `signal` и `timeout` (`:140-147`); сервер обрабатывает это в `src/ragraf/mcp_server.py:1200-1201`, `:1647-1660` | отмена помечает ещё НЕ начатую фоновую переиндексацию; идущая пересборка доводится до конца намеренно — обрыв на середине оставил бы индекс, которому нельзя верить |

**НЕ поддерживается — и это надо знать, а не обнаруживать опытом:**

* **`prompts/list`, `prompts/get` — не поддерживаются вовсе** (в README пакета:
  «MCP prompt templates are unsupported»; в коде — 0 совпадений на `prompts/list`).
  Поэтому подсказки `ragraf-check` / `ragraf-repair` (`src/ragraf/mcp_server.py:1556-1594`)
  в HARNESS видны НЕ будут, хотя сервер их отдаёт и другой клиент их получит.
  Полагаться на них в инструкциях для HARNESS нельзя;
* **`notifications/progress` не запрашивается**: клиент не передаёт `progressToken`
  (0 совпадений в файле), а незапрошенное уведомление о ходе он считает ошибкой.
  Сервер поэтому шлёт прогресс только тогда, когда клиент сам передал
  `progressToken` (`src/ragraf/mcp_server.py:1686-1713`, `:2075-2088`);
* **`logging/setLevel`, `notifications/message`, `completion/complete`, `ping`**
  не поддерживаются: `logging`, `completion`, `setLevel` — 0 совпадений.
  Диагностика обязана идти в stderr, а не через MCP;
* **`roots`, `sampling`, `elicitation`** — 0 совпадений на каждое слово;
  `capabilities: {}` в `initialize` (`:600`) это подтверждает.

**Модель не видит `structuredContent` — ответ обязан оставаться текстом.**
Это не предположение, а следствие кода: рендер инструмента возвращает ровно один
текстовый блок из `result.content` (`lib/index.js:200-221`), а `structuredContent`
лишь кладётся в значение рядом (`:237-240`). Поэтому:
1) факты и ссылки `файл:строка` обязаны быть в ТЕКСТЕ ответа;
2) «посмотри поле в structuredContent» — невыполнимая инструкция для агента
   HARNESS; в других клиентах она может работать, здесь — нет;
3) `outputSchema` всё равно нужен: по нему клиент проверяет форму ответа и
   отбрасывает несоответствующий.

**Таймаут вызова — из профиля, а не по умолчанию.** Собственное значение плагина
`DEFAULT_TOOL_CALL_TIMEOUT_MS = 60000` (`lib/index.js:765`, схема — `:787`), но
профиль RAGRAF перекрывает его: `toolCallTimeoutMs: 300000` в
`harness/ragraf-mcp.patch.yml` (то есть 5 минут). Прогрессом таймаут НЕ
продлевается: незапрошенный прогресс — ошибка (см. выше), поэтому долгая полная
индексация упирается в 5 минут, и это правильно лечить глубиной переиндексации
(`fast` вместо `full`), а не надеждой на уведомления о ходе.

**Что это меняет в самом RAGRAF.** `structuredContent`, `title`, `annotations`
(`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) и аргумент
`brief` (`src/ragraf/mcp_server.py:908-937`) клиент HARNESS никак не читает —
`annotations` и `title` в его коде отсутствуют вовсе. Это не делает их
бесполезными: `title` и `annotations` — язык описания инструмента для других
клиентов, `brief` — экономия контекста внутри самого ответа (`catalog`: 11 290 →
6 287 байт), а `structuredContent` — машинная копия для программного вызывающего.
Но ни одно из этих полей НЕ заменяет читаемый текст ответа.

## 6. Три ограничения, которые надо знать

**1. Перезапуск приложения обязателен.** Настройки профиля читаются при старте.
Вставленный блок `mcp-ragraf` начнёт работать после перезапуска DeepSeek Harness;
до этого инструментов `mcp__ragraf__*` в интерфейсе не будет. Это нормальное
поведение, а не сбой.

**2. Висящий сервер Joern при индексации.** Полная индексация поднимает Joern
(JVM) на порту 9500. Замерено в этой сессии: после первой переиндексации остался
процесс `java.exe` на 3 ГБ, державший порт 9500; он был снят вместе с родительским
`cmd.exe` (`taskkill /PID <cmd> /T /F`). Перед долгой работой и после неё:

```powershell
Get-CimInstance Win32_Process -Filter "Name='java.exe'" |
  Select-Object ProcessId, @{n='MB';e={[int]($_.WorkingSetSize/1MB)}}
Get-NetTCPConnection -LocalPort 9500 -ErrorAction SilentlyContinue
```

**3. В команде `reindex` была потеряна запись прогона в реестр.** Это найдено
при работе над этой же задачей и исправлено в `src/ragraf/cli.py`: `index`
записывал результат, а `reindex` — нет. Измерено на корпусе самого RAGRAF:
в индексе `rev-0046`, в реестре `rev-0045` со старой суммой; `check` из-за
этого реиндексировал корпус заново, а `catalog` показывал прошедшее состояние.
Внешне это выглядело как «RAGRAF тормозит на каждой проверке». Признак
расхождения — команда `catalog` показывает ревизию, отличную от
`index_status`. Правило чекера `Test-RegistryCoverage` теперь смотрит каждую
функцию, запускающую индексатор, а не файл целиком.

## 7. Откат

Все изменения — в двух владельческих файлах HARNESS и в репозитории RAGRAF.

**HARNESS и его настройки:**

```powershell
$home2 = [Environment]::GetFolderPath('UserProfile')
# 1. вернуть слой патчей из бэкапа (имя печатала установка)
Copy-Item "$home2\.dsh\profiles\desktop\cordis.patch.yml.bak-<метка>" `
          "$home2\.dsh\profiles\desktop\cordis.patch.yml" -Force
# 2. вернуть глобальные правила
Copy-Item "$home2\.dsh\AGENTS.md.bak-<метка>" "$home2\.dsh\AGENTS.md" -Force
# 3. убрать навык
Remove-Item "$home2\.dsh\skills\ragraf-core" -Recurse -Force
```

**RAGRAF.** Правки аддитивные; чтобы вернуть прежнее поведение, достаточно
трёх изменений: убрать вызов `force_utf8_stdio()`, вернуть `required=True`
у `--project` и удалить инструмент `ragraf_use_project` из `TOOLS` и
`_handlers`. Ни одно из них не затрагивает индекс, схему хранилища, нумерацию
ревизий и реестр — откат не требует переиндексации.

## 8. Поведение HARNESS: где лежит что

| Что | Путь |
|---|---|
| Установка приложения | `%LOCALAPPDATA%\Programs\DeepSeek Harness` |
| Плагины HARNESS | внутри ОДНОГО файла-архива `resources\app.asar` (121 348 951 байт), виртуальное дерево `dsh/node_modules`; на диске такого каталога нет |
| Домашний каталог HARNESS | `%USERPROFILE%\.dsh` |
| Настройки профилей | `%USERPROFILE%\.dsh\profiles\<имя>` |
| Данные Electron | `%APPDATA%\@deepseek-ai\dsh-desktop` |
| Реестр плагинов | `https://registry.npmjs.org/` |
| CLI | `resources\runtime\cli\bin\dsh.cmd` |

Разработка (приватный репозиторий): `github.com/deepseek-ai/deepseek-harness`,
пути пакетов вида `packages/mcp/mcp-client`.

**Переменные, которыми управляется домашний каталог:** `DSH_HOME` (явный путь
важнее), `DSH_AGENTS_HOME` (общий каталог навыков агентов, по умолчанию `~/.agents`).
Обе переменные HARNESS вычищает из окружения дочерних процессов — на запуск MCP-сервера
это не влияет, потому что он читает только свой `--project`/рабочий каталог.

**Порядок разрешения корней навыков** (rank): `<проект>/.dsh/skills` (100),
`<проект>/.agents/skills` (200), пользовательские `customSkillDirs` (300),
`~/.dsh/skills` (400), `~/.agents/skills` (500). Проектный корень — ближайший
каталог с `.git`.

**Уборка битых ссылок на удалённую установку (сделано 13.10.2026).**
В `%USERPROFILE%\.dsh\profiles\node_modules` лежали ссылки на
`C:\Program Files\Harness AI\resources\app.asar\node_modules\...` — эту
установку удалили, а ссылки остались. Замерено полностью: **447 битых ссылок**
(прежняя оценка в 173 была ошибкой: она считалась только по верхнему уровню,
без областей `@scope`), **0 ссылок с живой целью**; все 447 указывали в один
и тот же несуществующий каталог. Рабочие файлы владельца в дереве профиля
(10 файлов: `cordis.yml`, `cordis.patch.yml` и их резервные копии, `package.json`,
`pnpm-workspace.yaml`) не тронуты ни одним.

Ссылки удалены инструментом `harness/Remove-BrokenSymlinks.ps1`. Инструмент
намеренно узкий — удаляет ТОЛЬКО битую ссылку (нет цели — нечему ломаться):

```powershell
$home2 = [Environment]::GetFolderPath('UserProfile')

# 1. сухой прогон: печатает инвентарь и пишет манифест, ничего не меняя
pwsh -NoLogo -NoProfile -NonInteractive -File .\harness\Remove-BrokenSymlinks.ps1 `
    -Root "$home2\.dsh" -Manifest .\broken.txt

# 2. удаление (сначала читать п.1)
pwsh -NoLogo -NoProfile -NonInteractive -File .\harness\Remove-BrokenSymlinks.ps1 `
    -Root "$home2\.dsh" -Apply
```

Отличия от привычного `Remove-Item -Recurse`, из-за которых инструмент и написан:
ссылка удаляется как ссылка (`Directory::Delete(path, false)`) без обхода цели;
по умолчанию — сухой прогон; каталог вне домашнего каталога отвергается
(защита от запуска по чужому дереву). Каждое имя пишется в манифест — его
можно предъявить, а не рассказывать.

Проверка после уборки: повторный прогон даёт «ссылок с живой целью 0,
битых 0»; профиль `desktop` по-прежнему разбирается (`--dump-config`
показывает `mcp-ragraf`), а живой вызов через `dsh` вернул факты индекса —
то есть работоспособность подтверждена не рассуждением, а вызовом.

**База знаний HARNESS.** По документации разработчика это **приватный** репозиторий
`github.com/deepseek-ai/deepseek-harness`; публичной проверки API в этой сессии
не делалось, поэтому контракт выше описан по фактам текущей установки
(`lib/index.js`, README пакетов внутри `app.asar`), а не по документации.
