# RAGRAF в Claude Code: плагин, навык, MCP — что выбрано и почему

Документ отвечает на вопрос «какой способ подключения эффективнее» замерами
клиента **Claude Code 2.1.316** (03.11.2026), и описывает выбранный способ —
**плагин с маркетплейсом в этом же репозитории**. Всё, что ниже, проверено
вызовом: команды приведены полностью, чтобы их можно было повторить.

Оглавление:

1. [Четыре способа и их роли](#1-четыре-способа-и-их-роли)
2. [Что измерено](#2-что-измерено)
3. [Выбранный способ: плагин](#3-выбранный-способ-плагин)
4. [Как устроен плагин RAGRAF](#4-как-устроен-плагин-ragraf)
5. [Установка и миграция](#5-установка-и-миграция)
6. [Реклама: каталог, метаданные, иконка](#6-реклама-каталог-метаданные-иконка)
6а. [Community-версия и подача в каталог](#6а-community-версия-и-подача-в-каталог)
7. [Границы](#7-границы)
8. [Где что лежит](#8-где-что-лежит)

---

## 1. Четыре способа и их роли

Это не «или-или»: у способов разные роли, и путать их — главная ошибка при
выборе.

| Способ | Что это в Claude Code | Роль для RAGRAF | Годится? |
|---|---|---|---|
| **Connectors** (коннекторы каталога) | удалённые MCP-серверы, которые клиент подключает по HTTP с OAuth | RAGRAF — локальный индекс: держать его за публичным HTTPS значило бы выставить индекс кода наружу | **нет** |
| **Прямой MCP** | `claude mcp add` / `.mcp.json` | транспорт: инструменты `ragraf_*` в сессии | да, но это один слой из четырёх |
| **Skill** | `~/.claude/skills/<имя>/SKILL.md`, подгружается по контексту | дисциплина: когда и как спрашивать индекс, что делать при `stale` | да, но сам по себе не даёт инструментов |
| **Plugin** | пакет `.claude-plugin/plugin.json`: MCP + хуки + навыки + команды + агенты | поставка всего перечисленного одной командой, с версией и каталогом | **да — выбран** |

Ключевой факт, который решает выбор: **плагин — единственный способ поставить
MCP-сервер, хуки, навык и команды одной командой**, и единственный, который
попадает в каталог плагинов (то есть работает ещё и как витрина).

## 2. Что измерено

Все команды выполнены на этой машине; конфигурация клиента переводилась в
песочницу через `CLAUDE_CONFIG_DIR`, чтобы не менять рабочую учётную запись.

| Что | Результат |
|---|---|
| `claude plugin --help` | есть `init`, `install`, `details`, `disable`, `enable`, `configure`, `eval`, `validate`, `marketplace` |
| `claude plugin init <имя>` | создаёт `~/.claude/skills/<имя>/{SKILL.md,.claude-plugin/plugin.json}` — минимальный плагин из одного навыка |
| `claude plugin validate plugin` | `√ Validation passed`, 0 замечаний **после** двух правок (см. ниже) |
| `claude plugin validate .claude-plugin/marketplace.json` | `√ Validation passed` |
| `claude plugin marketplace add <репозиторий>` | `√ Successfully added marketplace: ragraf`; в списке виден ещё и встроенный `anthropic-plugin-directory` |
| `claude plugin install ragraf@ragraf` | `√ Successfully installed plugin: ragraf@ragraf (scope: user)` |
| `claude plugin details ragraf` | Skills 3 (`ragraf-core`, `ragraf-status`, `ragraf-task`), Hooks 4, MCP servers 1, **always-on ≈ 117 токенов** |
| `claude mcp list` | `plugin:ragraf:ragraf: … ragraf_launcher.py mcp … - √ Connected` |
| прогон хука как у клиента | `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "RAGRAF: корпус «ragraf», ревизия rev-1343…"}}` |

Две правки, без которых валидатор не пропускал плагин:

1. **`${CLAUDE_PLUGIN_ROOT}` без кавычек** — предупреждение: путь с пробелом
   развалит команду. Стало: `python "${CLAUDE_PLUGIN_ROOT}/scripts/ragraf_launcher.py" hook …`
   (команда начинается с `python`, поэтому кавычки внутри безопасны — в отличие от
   случая, измеренного 02.11.2026, где строка начиналась с кавычки и PowerShell
   падал `ParserError`).
2. **Иконка** — `ICON_MISSING`: каталог плагинов требует квадратную PNG
   512–2048 px. Иконка рисуется детерминированно: `python tools/make_plugin_icon.py`
   → `plugin/.claude-plugin/icon.png` (1024×1024, 12 223 байта).

Отдельный замер, который меняет устройство плагина: **у плагинных MCP-серверов
другое имя инструмента**. Клиент строит его как
`mcp__plugin_<плагин>_<сервер>__<инструмент>` (в бинарнике клиента это шаблон
`mcp__plugin_`, пример — `mcp__plugin_claude-test_browser__…`). Значит для
плагина `ragraf` с сервером `ragraf` инструмент называется
`mcp__plugin_ragraf_ragraf__ragraf_search`, а не `mcp__ragraf__ragraf_search`.

Из этого вышли три следствия, все сделаны:

* текст навыка для плагина свой (`plugin/skills/ragraf-core/SKILL.md`), вариант
  объявлен в `docs/skills/ragraf-core/VARIANTS.json` как `claude-plugin`
  (режим `declared`: пара sha256 плюс правила `marker` и `numbers_match_code`);
* матчер `PreToolUse` в `plugin/hooks/hooks.json` ловит оба префикса;
* `src/ragraf/gate.py` получил `is_index_tool()`: вызов
  `mcp__plugin_ragraf_ragraf__ragraf_search` теперь считается обращением к
  индексу — иначе гейт считал бы его «иным» и напоминал об индексе ровно тогда,
  когда им пользуются.

## 3. Выбранный способ: плагин

Одна команда ставит четыре слоя, версия видна в каталоге, обновление — `claude
plugin marketplace update`:

```text
/plugin marketplace add paulmann/RAGRAF
/plugin install ragraf@ragraf
```

Почему не «только MCP»: сервер без навыка — «инструмент есть, пользоваться не
обязательно»; без хуков — «написано, но не проверено». Плагин — это упаковка,
которая ставит всё сразу, и при этом остаётся **тонкой**: сам RAGRAF (индекс,
пакет, зависимости) лежит отдельно и не копируется в каталог клиента.

Почему прямой MCP всё равно остаётся: установка `python tools/install_claude_code.py`
(без плагина) — рабочий режим для тех, кто не ставит плагины, и способ поставить
сервер в `~/.claude.json` без каталога. Оба режима описаны и проверяются; вместе
их ставить нельзя — хуки сработали бы дважды, поэтому у установщика есть
`--plugin --replace`.

## 4. Как устроен плагин RAGRAF

```
.claude-plugin/marketplace.json     # каталог: объявляет плагин ragraf, source ./plugin
plugin/
├── .claude-plugin/plugin.json      # манифест: имя, версия, описание, ключевые слова, лицензия
├── .claude-plugin/icon.png         # иконка каталога (1024×1024, рисуется скриптом)
├── .mcp.json                       # MCP-сервер ragraf → scripts/ragraf_launcher.py mcp
├── hooks/hooks.json                # SessionStart, UserPromptSubmit, PreToolUse, Stop
├── skills/ragraf-core/             # SKILL.md (свой текст) + reference/* (копия проекта)
├── commands/ragraf-status.md       # /ragraf-status — состояние корпуса таблицей
├── commands/ragraf-task.md         # /ragraf-task <имя> — метка задачи
├── scripts/ragraf_launcher.py      # находит установку RAGRAF и передаёт ей работу
└── README.md
```

**Лаунчер вместо абсолютного пути.** В манифесте не может стоять
`<RAGRAF>\...` с путём автора: плагин ставится в каталог клиента, и такой манифест
работал бы только у автора. `ragraf_launcher.py` ищет установку в порядке:
`RAGRAF_HOME` → известные каталоги рабочей копии (`<RAGRAF>`,
`~/RAGRAF`, `~/src/RAGRAF`, `/opt/ragraf`, `/usr/local/share/ragraf`) → пакет
`ragraf` в текущем интерпретаторе. Для MCP он запускает `tools/ragraf-mcp.py`
(или `-m ragraf.mcp_server`), для хука — `tools/harness_gate.py`, то есть ту же
единственную реализацию стадий, что и ручная установка.

**Про интерпретатор — измеренная ловушка.** Первый вариант лаунчера запускал
сервер тем `python`, который клиент взял из `PATH`; в этой среде это вендоренный
интерпретатор AutoClaw без `fastembed`/`sqlite_vec`, и плагин давал
`CONNECTION_CLOSED`. Стало: `RAGRAF_PYTHON` → `.venv` рабочей копии → текущий
интерпретатор. После правки — `√ Connected`.

**Про `execve`.** Первая версия лаунчера заменяла процесс через `os.execve`;
на Windows это завершалось аварийно (код `0xC0000005`) ещё до первого ответа.
Стало: дочерний процесс с наследованным stdio (`subprocess.call`), одинаково на
всех платформах.

## 5. Установка и миграция

Из репозитория (сборка + проверка):

```powershell
python tools/install_claude_code.py --sync-plugin   # навык и версии из кода
python tools/install_claude_code.py --plugin        # маркетплейс + установка
python tools/install_claude_code.py --plugin --replace   # снять ручную установку и поставить плагин
claude plugin validate plugin                        # манифест и хуки
claude plugin details ragraf                         # состав и цена в контексте
```

Замер перехода на этой машине: `--plugin --replace` снял 4 ручных хука, ручной
MCP-сервер, раздел правил `CLAUDE.md` и каталог навыков; установил плагин;
`claude mcp list` → `plugin:ragraf:ragraf … √ Connected`; состав: 3 навыка,
4 хука, 1 MCP-сервер, always-on ≈ 117 токенов.

## 6. Реклама: каталог, метаданные, иконка

Плагин — это ещё и витрина, и она измеряется, а не предполагается:

* **Каталог в репозитории.** `claude plugin marketplace add paulmann/RAGRAF`
  добавляет маркетплейс `ragraf`; любой пользователь Claude Code видит плагин в
  `/plugin` с описанием, ключевыми словами и версией.
* **Встроенный каталог Anthropic.** В `claude plugin marketplace list` на этой
  машине виден `anthropic-plugin-directory` — источник `Built in (Anthropic
  Directory)`. Это и есть канал «официального» каталога: чтобы туда попасть,
  плагин проходит проверку каталога; `claude plugin validate --profile` проверяет
  её заранее (README, иконка, метаданные).
* **Метаданные вместо лозунгов.** В `plugin.json` и в записи маркетплейса стоят
  `description` (что даёт и чем отличается), `keywords` (по ним ищут:
  `code-search`, `code-index`, `code-navigation`, `graph`, `impact-analysis`,
  `offline`, `mcp`), `category: development`, `homepage`, `repository`, `license`.
  Это ровно те поля, которые клиент показывает в `/plugin` и в `details`.
* **Цена в контексте названа.** `claude plugin details` печатает always-on
  стоимость (≈117 токенов) и стоимость по компонентам. Это честнее, чем
  «почти ничего не стоит»: пользователь видит цену до установки.
* **Чего не делаем.** Заявку в каталог Anthropic подаёт владелец: это внешнее
  действие от его имени, и часть шагов необратима (иконка и метаданные первого
  сохранения фиксируются). Здесь подготовлено всё, что нужно для подачи, и
  проверено валидатором клиента — см. раздел 6а.

## 6а. Community-версия и подача в каталог

**Что публикуется.** Папка `plugin/` из публичного репозитория `paulmann/RAGRAF`,
ветка `main`. Каталог читает **папку**, а не архив, поэтому версия в
`plugin.json` и в записи маркетплейса должна совпадать с версией кода — это
проверяет `--sync-plugin` и тест.

**Что значит Community.** Издание по умолчанию (`src/ragraf/edition_baseline.json`):
сервер работает без файла лицензии и открывает шесть возможностей — `documents`,
`search`, `graph`, `schema`, `db-live`, `audit`. Платные возможности перечислены
в `EULA.md` и отказывают с названием издания, а не молча: `doc-ocr`, `doc-graph`,
`exclude-management`, `shared-index`, `secret-broker`, `quality-loop` — Pro;
`sso`, `siem-export`, `offline-license`, `attestation` — Enterprise.

**Что лежит внутри плагина для каталога.** `README.md` (что даёт, установка,
издания, приватность, границы, поддержка), `LICENSE` (BUSL-1.1), `NOTICE`,
`EULA.md` (условия платных уровней) — копии из корня репозитория, их кладёт
`--sync-plugin`, поэтому источник один. Иконка — `.claude-plugin/icon.png`.

**Сборка пакета и бланка подачи.**

```powershell
python tools/install_claude_code.py --sync-plugin   # лицензии и версии — из проекта
python tools/package_plugin.py                      # dist/ + проверки + бланк
```

Замер 03.11.2026: пакет `dist/ragraf-community-0.5.6.zip`, 14 файлов,
44 499 байт, sha256 `2d657aa3…f345d`; `claude plugin validate` и
`claude plugin validate --profile directory` — обе проверки пройдены; бланк
`dist/ragraf-community-0.5.6.submission.json` собран из манифеста (имя,
`displayName`, версия, описание, категория, ключевые слова, ссылки, лицензия,
sha256 пакета).

**Как подать (портал, а не форма в репозитории).** Каталог Anthropic принимает
заявки из портала `https://claude.ai/directory/manage`; порядок описан на
`https://claude.com/docs/plugins/submit`. Существенное:

1. к `claude.ai` должен быть подключён GitHub-аккаунт с правом push в
   репозиторий; проверка выполняется при создании и подаче заявки;
2. репозиторий может быть приватным на время проверки, но **до публикации
   должен стать публичным**;
3. одна папка = одна заявка; лимит — 10 заявок в сутки на организацию;
4. `name` плагина — **неизменяемый слаг**: после публикации переименование
   ломает установки (для смены подписи есть `displayName`, для переименования —
   `renames` в маркетплейсе);
5. `Submit for review` запускает проверки каталога и сканирование безопасности;
   прошедшая версия публикуется кнопкой `Publish` (или автоматически, если так
   настроено);
6. обновления — обычный пуш в отслеживаемую ветку с поднятой версией в
   `plugin.json`; каталог подхватывает коммит сам.

**Официальный маркетплейс `claude-plugins-official`** заявки через портал не
принимает: туда попадают через партнёрский контакт Anthropic. Наш путь —
каталог каталога (`directory`) и собственный маркетплейс в репозитории.

**Что остаётся за владельцем.** Подключить GitHub, пройти `Validate`, нажать
`Submit for review`, затем `Publish`, и заранее обновить числа публикации в
README (гейт выпуска их сверяет). Отправка не автоматизируется намеренно: это
подпись владельца под содержимым.

## 7. Границы

* **Хук требует рабочей копии.** Стадии хука живут в `tools/harness_gate.py`;
  при установке только пакетом (`pip install ragraf`) хук не подменяется
  суррогатом: он завершается кодом 0 (сеанс не ломается) и пишет в stderr, чего
  не хватает. Сервер, навык и команды работают.
* **`python` в `PATH`.** В манифесте плагина одно имя команды; если в системе
  есть только `python3` — симлинк либо ручная установка установщиком.
* **Индекс не пересобирается хуком.** Пересборка идёт минутами; её запускает
  агент вызовом `ensure` (или человек командой `ragraf ensure`).
* **Плагин не выдаёт себе прав.** Разрешение `mcp__plugin_ragraf_ragraf__*`
  добавляет установщик в `permissions.allow`; это видимое решение владельца.
* **Коннекторы не подходят.** RAGRAF — локальный индекс; подключение через
  облачный коннектор выставило бы индекс наружу, поэтому этот путь назван
  неподходящим, а не «отложенным».

## 8. Где что лежит

| Что | Где |
|---|---|
| манифест плагина | `plugin/.claude-plugin/plugin.json` |
| каталог (маркетплейс) | `.claude-plugin/marketplace.json` |
| MCP-сервер плагина | `plugin/.mcp.json`, `plugin/scripts/ragraf_launcher.py` |
| хуки плагина | `plugin/hooks/hooks.json` |
| навык плагина | `plugin/skills/ragraf-core/SKILL.md` + `reference/` |
| команды | `plugin/commands/ragraf-status.md`, `ragraf-task.md` |
| иконка | `plugin/.claude-plugin/icon.png`, генератор — `tools/make_plugin_icon.py` |
| сборка и установка | `tools/install_claude_code.py` (`--sync-plugin`, `--plugin`, `--replace`) |
| объявление варианта навыка | `docs/skills/ragraf-core/VARIANTS.json`, вариант `claude-plugin` |
| распознавание плагинных имён | `src/ragraf/gate.py`, `is_index_tool` |
| проверки | `tests/test_claude_code_integration.py` |

Ручная установка без плагина — `docs/03_implementation/CLAUDE_CODE_INTEGRATION.md`.
