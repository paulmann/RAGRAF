# RAGRAF в HARNESS и AutoClaw: настройка и полный каталог возможностей

Дата: 19.10.2026. Проект: `RAGRAF`.
Документ отвечает на два вопроса: **что умеет продукт** и **как включить его
в DeepSeek Harness и AutoClaw**. Числа проверены по коду: 41 инструмент MCP,
39 операций фасада, 19 подкоманд CLI, 16 языков разбора деревом и 11 текстовых
языков (включая `document`).

## 1. Что нового (G0–G7)

| Этап | Что появилось | Где |
|---|---|---|
| G0 | Реестр разрешённых корней и карта метаданных вне корпуса | `src/ragraf/sources.py`, `global_map.py` |
| G1 | Маршрутизатор: какой локальный корпус отвечает и почему | `src/ragraf/route.py`, `ragraf_route` |
| G2 | Новые текстовые форматы: CSV/TSV/лог/свойства/дампы и др. | `config.py`, `config/ragraf.yaml` |
| G3 | Реестр секретоносных мест: путь, правило, размер, дата | `global_map.py`, `sources` |
| G4 | Локальная польза: `feedback`, `analytics`, 30-дневный срез | `feedback.py`, `analytics.py`, `telemetry.py` |
| G5 | Документы: PDF/DOCX/XLSX/PPTX/EML/ODT/RTF, MSG при библиотеке | `documents.py`, язык `document` |
| G6 | Сеть: только UNC/смонтированный путь с `--network`; URL-коннекторы отклоняются | `sources.py` |
| G7 | Панель источников и пользы | `/sources`, `/value`, `admin_server.py` |

## 2. Каталог возможностей

### 2.1. Глобальный указатель: где лежат материалы

| Возможность | Команда / инструмент | Что отвечает |
|---|---|---|
| Разрешить корень | `sources add <путь>`; `ragraf sources` | что вообще scanned, включён ли, приоритет |
| Карта метаданных | `map scan`, `map status`, `map find` | имена, пути, размеры, дата; содержимое не читается |
| Секретоносные места | `sources secrets` | путь, правило, размер, дата; значения не хранятся |
| Маршрут | `route "<запрос>"`; `ragraf_route` | кандидаты, `why`, `recommended`, `local_index`, `stale` |

Ключевое свойство: карта живёт в `catalog/global_map.sqlite`, **вне** `.ragraf`,
поэтому не входит в `corpus_hash` и не зацикливает сторожа свежести.

### 2.2. Локальный индекс: операции вместо чтения файлов

| Вопрос | Операция |
|---|---|
| Актуален ли индекс; привести в порядок | `ensure`, `freshness`, `index_status` |
| Какой корпус выбран и почему | `context` |
| Где определён символ | `symbol`, `references` |
| Кто вызывает / что вызывает | `callers`, `callees` |
| Что затронет правка | `impact`, `slice` |
| Тело функции (только по запросу) | `code` |
| Где описано устройство | `search`, `search_multi` |
| Копии файлов и рабочая версия | `duplicates` |
| Точки входа: маршруты, `action=`, CLI | `entrypoints`, `routes` |
| Что не используется | `dead_code` |
| Схема из миграций | `schema`, `table_usage` |
| Живая база: структура, таблица, связи, пример | `db_schema`, `db_table`, `db_relations`, `db_sample` |
| Расхождение «код ↔ база» | `db_drift`, `db_cross` |
| План записи до SQL | `write_check` |
| Права и условия доступа | `policy_trace` |
| Классы оформления | `class_usage` |
| Что изменилось между ревизиями | `diff` |
| Заметки и задача | `note`, `notes`, `task`, `task_stats` |
| Статистика и польза | `usage` |

### 2.3. Документы

Поддерживаются PDF (текстовый слой), DOCX, XLSX, PPTX, EML, ODT, RTF; MSG — при
установленной `extract-msg`. Текст превращается в строки с маркерами
`--- page N ---`, `--- sheet: … ---`, `--- slide N ---`. Поиск идёт по
извлечённому тексту, `live_read` использует тот же извлекатель.

```powershell
python tools/ragraf.py -p config/ragraf.yaml query search "формулировка" --kind file
python tools/ragraf.py -p config/ragraf.yaml query live_read --path docs/отчёт.pdf
```

Без библиотеки документ честно помечается `metadata_only` с причиной. OCR,
архивы, `.pst/.ost`, `.evtx/.pcap` не поддерживаются.

### 2.4. Польза, аналитика, панель

| Что | Как |
|---|---|
| Отчёт о пользе за период | `feedback --days 30` → `catalog/field_reports/*.md` |
| Экспорт аналитики | `analytics export` → JSON `ragraf.usage.v1` |
| Удаление аналитики | `analytics delete --yes` — только после копии |
| 30-дневный срез | `usage` (блок `last30`) |
| Панель | `/sources`, `/value`, `/api/sources` на `127.0.0.1:8777` |

Сеть не используется: экспорт — файл, удаление — после резервной копии.
Отправки аналитики наружу нет.

### 2.5. Безопасность и границы

- Секретоносные файлы не открываются: только имя, размер, правило, дата.
- Карта не читает содержимое: в схеме нет колонки под текст или хэш.
- URL-коннекторы (`ssh://`, `s3://`, `webdav://`, `http://`) не реализованы и
  отклоняются с причиной; сетевой корень — UNC или смонтированный путь с `--network`.
- `capability=full` означает «доступны операции среза», а не «всё построено».
- Пустой результат — код 0 и `found=false`, а не ошибка.

## 3. Настройка в AutoClaw

### 3.1. Канал MCP

`Settings → Tools & connections → Add tool → Local program`.

| Поле | Значение |
|---|---|
| Name | `RAGRAF` |
| Launch command | `.venv\Scripts\python.exe` |
| Arguments | `tools\ragraf-mcp.py --project config\ragraf.yaml --agent AutoClaw --agent-kind autoclaw` |
| Environment | пусто |

Ожидается статус `Connected · 41 tools`. Имена инструментов зависят от клиента:
`source_ragraf_ragraf_ensure` (AutoClaw) или `mcp__ragraf__ragraf_ensure`
(HARNESS). Короткая форма в инструкциях — `ragraf_ensure`.

Если MCP не подключён, работает CLI — это не отменяет обязательности индекса:

```powershell
python tools\ragraf.py ensure <каталог проекта>
python tools\ragraf.py -p <конфиг> query route "где лежит X"
```

### 3.2. Навык

Источник — `docs/skills/ragraf-core/`; копия — в каждом аккаунте:

```text
%APPDATA%\AutoClaw-oversea-official\accounts\<account>\runtime\zwork-agent\skills\ragraf-core\
    SKILL.md, reference/operations.md, reference/repair.md
```

Развёртывание: копировать после каждой правки источника, сверить SHA-256.
Копия в аккаунте, отставшая от проекта, — дефект, а не «локальная редакция».

### 3.3. Правила

`%USERPROFILE%\.pi\agent\AGENTS.md`, раздел 8 «Обязательное использование RAGRAF»:
первый шаг — `ensure`, затем операция вместо чтения; отчёт — ревизия, сумма,
ссылки `файл:строка`. Проектный `AGENTS.md` строже пользовательского.

### 3.4. Гейт

В AutoClaw PreToolUse-гейт не устанавливается: дисциплина обеспечивается
правилами раздела 8. Гейт — механизм HARNESS (см. 4.4).

## 4. Настройка в DeepSeek Harness

### 4.1. MCP-сервер

Блок `harness/ragraf-mcp.patch.yml` вставляется в слой патчей профиля
`$DSH_HOME/profiles/desktop/cordis.patch.yml` отдельной записью `- insert:`
(бэкап файла обязателен). Сервер: `.venv\Scripts\python.exe`,
`tools/ragraf-mcp.py`, `cwd=RAGRAF`.

Проверка без запуска:

```powershell
& $dsh --profile headless --patch harness/ragraf-mcp.patch.yml --dump-config |
  Select-String 'serverName: ragraf' -Context 1,3
```

### 4.2. Навык

```powershell
$dst = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.dsh\skills\ragraf-core\SKILL.md'
Copy-Item 'harness\skills\ragraf-core\SKILL.md' $dst -Force
```

Провайдер навыков HARNESS читает `$DSH_HOME/skills/<имя>/SKILL.md` и следит за
каталогом — перезапуск не нужен.

### 4.3. Правила

`$DSH_HOME/AGENTS.md`, раздел 6 «Работа с кодом: сначала RAGRAF». Источник —
`harness/agents-section.md`; вставлять перед журналом изменений. Раздел требует
`mcp__ragraf__ragraf_ensure` первым шагом и ссылку `файл:строка`.

### 4.4. Гейт PreToolUse

```powershell
python tools/install_harness_gate.py           # поставить
python tools/install_harness_gate.py --check   # состояние
python tools/install_harness_gate.py --remove  # убрать
```

Установщик правит `$DSH_HOME/profiles/desktop/cordis.patch.yml`, добавляя
`@deepseek-ai/dsh-hooks-claude-code` с `configPath` на `harness/hooks.json`.
Режим: `RAGRAF_GATE_MODE=warn|ask|deny|off`; по умолчанию `warn`.
После установки нужен перезапуск сеанса: конфиг хуков читается один раз.

Гейт смотрит вызов ДО выполнения: есть ли корпус, свеж ли он, пользовались ли
индексом в сеансе. Решения видны в `task_stats`.

### 4.5. Метка задачи

`RAGRAF_TASK=<метка>` собирает вызовы одной задачи; снимать после задачи.
В HARNESS метку можно задать инструментом `task`.

## 5. Полный список MCP-инструментов (41)

| Группа | Инструменты |
|---|---|
| Состояние и корпус | `index_status`, `context`, `freshness`, `catalog`, `use_project`, `ensure`, `drift_report`, `drift_ack`, `diff` |
| Поиск и навигация | `search`, `search_multi`, `symbol`, `references`, `callers`, `callees`, `impact`, `slice`, `code`, `repo_map`, `live_read` |
| Структура и точки входа | `routes`, `entrypoints`, `duplicates`, `dead_code` |
| Схема и запись | `schema`, `table_usage`, `write_check` |
| Права и оформление | `policy_trace`, `class_usage` |
| Глобальный указатель | `route` |
| Живые базы | `db_schema`, `db_table`, `db_relations`, `db_sample`, `db_drift`, `db_cross` |
| Знания и статистика | `note`, `notes`, `task`, `task_stats`, `usage` |

## 6. Подкоманды CLI (19)

`index`, `reindex`, `drift`, `query`, `status`, `drop`, `selftest`, `usage`,
`feedback`, `analytics`, `db`, `register`, `init`, `check`, `ensure`, `catalog`,
`sources`, `map`, `route`.

Примеры:

```powershell
python tools/ragraf.py sources list
python tools/ragraf.py map find AMD --limit 5
python tools/ragraf.py --json -p config/ragraf.yaml query route "AMD" --limit 5
python tools/ragraf.py feedback --days 30
python tools/ragraf.py analytics export
```

## 7. Проверка после настройки

```powershell
# MCP виден и отвечает
# (в чате агента) ragraf_ensure / mcp__ragraf__ragraf_ensure
python tools/ragraf.py -p config/ragraf.yaml status
python tools/ragraf.py -p config/ragraf.yaml check
python tools/ragraf.py sources list
python tools/ragraf.py map status
python tools/ragraf.py --json -p config/ragraf.yaml query route "AMD" --limit 3
```

Ожидается: `status` — ревизия и сумма; `check` — «ревизия не требуется»;
`sources` — разрешённые корни; `route` — `recommended` и `why`, без цитат.

## 8. Что не реализовано

Отправка аналитики наружу; SSH/S3/WebDAV-коннекторы; OCR, архивы, `.pst/.ost`,
`.evtx/.pcap`; MSG без `extract-msg`. Это заявленные границы, а не недоделки.
