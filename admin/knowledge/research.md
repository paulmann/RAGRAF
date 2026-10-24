# Research: как HARNESS и AutoClaw 2 хранят историю, навыки и знания агентов

> **Назначение:** доказательная база к `PROMPT-agent-knowledge.md`.
> **Дата замера:** 23.10.2026, эта машина (Windows).
> **Версии:** DeepSeek Harness `@deepseek-ai/dsh-desktop-runtime` 0.2.0-rc.2; AutoClaw 2 (`zwork`) 2.2.2.
> **Границы:** исследование read-only. Содержимое сессий и памяти не цитируется; приведены только схемы, счётчики и пути. Числа в живой БД AutoClaw меняются — они помечены как «на момент замера».

---

## 1. Резюме для руководителя

Максимум из идеи выжимается не написанием нового поиска с нуля, а комбинацией трёх уже существующих вещей:

1. **В HARNESS уже есть полнотекстовый backend по истории сессий** — `@deepseek-ai/dsh-session-query-sqlite` (FTS5, детерминированное ранжирование, курсоры, live-preferred сверка). В текущем профиле он **выключен**: `path: ':memory:'`, `openAt: never`.
2. **Навыки HARNESS — это файлы `SKILL.md`** с frontmatter (`name`, `description`, `whenToUse`, …). Каталог для модели отдаёт только имя и урезанное описание; `whenToUse` теряется. Использование навыка выводится из событий `tool/call name="skill"` — отдельной телеметрии нет.
3. **AutoClaw 2 уже содержит реляционную модель опыта**: `work_sessions`, `work_runs`, `neutral_session_entries` (127 тыс.), `memory_assets` с provenance, `agents` (21), `collaboration_run_agent_participations`. Но её FTS5-поиск по сессиям **покрывает только `request` и `answer`** (1 332 строки из 38 830 записей с текстом) — то есть история AutoClaw фактически не ищется по решениям, рассуждениям и результатам инструментов.

Итоговая архитектура: **включить персистентный session-query в HARNESS; построить над ним производный слой «актор → эпизод → навык → артефакт»; подключить AutoClaw 2 read-only адаптером; добавить настройки, расписание и инструменты диспетчеризации.** Это даёт искомый функционал без дублирования уже решённых задач.

---

## 2. Метод и границы

Изучены:

- файловая структура `~/.dsh` (сессии, проекции, навыки, настройки, профиль);
- `app.asar` HARNESS: `package.json`, `desktop-runtime.json`, README пакетов `@deepseek-ai/*` (извлечены из asar и прочитаны);
- живая БД AutoClaw 2 `runtime.sqlite` (read-only, `mode=ro`), её схема и агрегаты;
- код AutoClaw 2 (`resources/app/dist/main.cjs`) — точечно, по строкам миграций и ключевым идентификаторам;
- каталог навыков AutoClaw 2 и ledger `skills-preinstall.json`.

Не изучалось: содержимое личных сессий и памяти (не нужно для дизайна), сетевые протоколы AutoClaw, внутренние API HARNESS за пределами пакетов, доступных в asar.

---

## 3. HARNESS (DeepSeek Harness)

### 3.1. История запросов

**Формат:** `~/.dsh/sessions/<encoded-workspace>/<session-id>/session.v4.jsonl.zstd`.

- Это append-only поток **независимых zstd-фреймов**, по одному событию в кадре (в крупнейшей изученной сессии — 16 117 кадров, 19 245 964 байт).
- Заголовок: `{"type":"session","version":4,"id":"session-…","createdAt":…,"cwd":"…","isSeeded":false,"delegationDepth":0,"agentPreset":"standard"}`.
- События несут `seq`, `time`, `data`; типы, наблюдавшиеся в крупнейшей сессии:

| Тип события | Кол-во | Что содержит |
|---|---:|---|
| `tool/call` | 2 497 | имя инструмента, аргументы, `callId` |
| `tool/result` | 2 530 | результат инструмента |
| `assistant/message` | 2 389 | ответ модели |
| `step/start` | 2 391 | границы шага |
| `user/message` | 241 | запрос пользователя |
| `turn/start` / `turn/end` | 90 / 90 | границы хода |
| `deliverables/presented` | 76 | презентованные артефакты |
| `workspace/changes` | 45 | изменения файлов |
| `compaction/*` | 33+13+8+13 | сжатие контекста |
| `todo/write` | 7 | планы |
| `subagent/catalog` | 5 | подагенты |
| `session/title` | 2 | заголовок |
| `goal/change` | 4 | цели |
| `session/title-llm-request` | 1 | LLM-заголовок |

**Счётчики на машине:** 179 файлов сессий, 19 workspace-каталогов, ~201,6 МБ.

**Вывод для индекса:** доступны цель пользователя, ответы, вызовы инструментов, артефакты, изменения файлов, планы и подагенты. Этого достаточно для извлечения эпизодов и доказательств без LLM.

### 3.2. Проекция сессии (кеш)

**Путь:** `~/.dsh/storages/session_projcache/sessions/<session-id>.json` (179 файлов).

Структура: `{version, record:{identity, rows}}`, где `rows` — версионированные значения. Ключевые строки:

| Строка | Содержимое |
|---|---|
| `title`, `titleInput` | заголовок и исходный ввод |
| `sessionStats` | turns, steps, llmMs, toolMs, tokens |
| `tokenUsage`, `contextPressure`, `contextBreakdown` | расход контекста |
| `userQuestions` | активные/закрытые вопросы |
| `agentTeam` | `members[]` (id, name, description, provider, context, phase), `tasks[]`, `messages[]` |
| `subagent` | `identity.label`, режим |
| `subagentCatalog` | каталог подагентов |
| `permissions` | sandbox/approval/preset |
| `todos`, `plan` | планы |
| `sessionListMetadata` | `blank`, `lastPromptAt` |
| `timeContext`, `modelSelection`, `turnOutline` | служебное |

**Вывод:** это готовый дешёвый источник метаданных для списка «кто/когда/что делал», без чтения тяжёлых логов.

### 3.3. Реестр workspace'ов

`~/.dsh/storages/workspace.json` (версия 2):

- `global.workspaceIds`, `archivedSessionIds`, `pinnedSessionIds`;
- `tables.workspaces.<id>`: `path`, `title`, `sessionIds[]`, `createdAt`, `updatedAt`.

**Вывод:** даёт связь сессия → проект/каталог, что нужно для фильтра «в этом workspace» и для резолва имени агента/проекта.

### 3.4. Навыки

**Корни (из README `dsh-skill-filesystem`):**

| Ранг | Источник | Путь |
|---:|---|---|
| 100 | project-dsh | `<projectRoot>/.dsh/skills` |
| 200 | project-agents | `<projectRoot>/.agents/skills` |
| 300 | custom | `Config.customSkillDirs` |
| 400 | user-dsh | `<dshHome>/skills` |
| 500 | user-agents | `<agentsHome>/skills` |
| 600 | bundled | `bundledSkillDir` |

**Формат:** `<name>/SKILL.md` или `<name>.md`; frontmatter: `name` (kebab-case), `description` (обязательно), `whenToUse`, `metadata`, `disable-model-invocation`, `user-invocable`.

**Что видит модель:** каталог «имя + урезанное описание»; тело — по вызову `skill(name)`. README `dsh-tool-skill` прямо фиксирует ограничение: **`whenToUse`, source и provider metadata в каталог не попадают**. Тело не имеет ограничения размера; ресурсы — только подсказка (base dir/URL), не содержимое.

**Использование навыка:** отдельной телеметрии нет; факт выводится из `tool/call` с `name="skill"` и аргументом `{"name": "..."}`. В крупнейшей сессии — 9 таких вызовов (например, `diagnose-windows-sandbox-acl`, `clean-bom-crlf`). Пользовательский жест `/name` тоже виден в `user/message`.

**Счётчики:** 10 валидных навыков в `~/.dsh/skills` (без бэкапов). В каталоге сессии — 12+ (включая проектные/кастомные).

**Вывод:** индекс навыков должен хранить frontmatter + тело + `whenToUse` + хэш + ресурсы, а использование — агрегировать из сессий. Это закрывает дыру «каталог не показывает whenToUse».

### 3.5. Полнотекстовый поиск по сессиям — уже есть, но выключен

Пакеты:

- `@deepseek-ai/dsh-session-query` — сервис: `listSessions`, `readSession`, `filterSessions`, `filterEvents`, `readTitleSnapshots`, `listEvents`, `readSurface`, `readEvent`, `traceSession`, `traceEvent`, `searchSessions`, `searchEvents`.
- `@deepseek-ai/dsh-session-query-sqlite` — backend: FTS5, отдельная производная БД, `openAt: startup|first-search|never`, курсоры с генерациями, live-preferred сверка, детерминированное ранжирование, `unicode61` (токены/фразы, не произвольные подстроки), лимиты 20/100.

**Текущая конфигурация** `~/.dsh/profiles/desktop/cordis.yml`:

```yaml
- id: session-query-sqlite
  name: '@deepseek-ai/dsh-session-query-sqlite'
  config:
    path: ':memory:'
    openAt: never
```

То есть backend смонтирован, но индекс в памяти и **не открывается** — поиск отключён. Это подтверждается README: `openAt: never` отключает full-text search, оставляя точные чтения.

**Вывод:** включение персистентного пути и `openAt: startup` (или `first-search`) — первый шаг, дающий ranked-поиск по истории без нового кода.

### 3.6. Команды и подагенты

- `dsh-experimental-agent-team` + `dsh-experimental-tool-agent-team`: Lead создаёт именованных teammate'ов (`spawn_teammate`), обменивается durable-сообщениями (`send_message`), ведёт общую доску (`team_task_create/list/get/update`), `list_agents`, `wait_agent`, `interrupt_agent`. Ростер и задачи восстанавливаются из лога Lead-сессии; область — одна сессия/процесс.
- `dsh-subagent`: one-shot/continuable дети, каталог `subagentCatalog`, глубина, лимиты.
- В проекциях сессий: 81 файл содержит `subagent` или `agentTeam`; в одном примере — 2 teammate'а (`verify-admin`, `docs-hygiene`) с описанием, провайдером и фазой.

**Вывод:** «найти агента» в HARNESS = найти **сессионного актора** (root/subagent/team member). Диспетчеризация возможна через team tools; для исторического актора — через реплику (`spawn_teammate` с ролью) или возобновление сессии, если это поддержано.

### 3.7. Настройки и расписание

- `dsh-settings`: редактирует волатильные поля плагинов, хранит через профильный Cordis patch; `settings.yaml` из старых версий импортируется один раз и переименовывается в `settings.yaml.imported` (на машине содержит только `ui-onboarding`).
- `dsh-client-ui-settings`: секции `settings.section`, `settings.plugins.tab`, `settings.onboarding`; страницы регистрируются клиентскими плагинами.
- `dsh-schedule`: one-shot, fixed-rate, daily, weekly, cron; инструменты `schedule_create/list/update/delete`; доставка в исходную сессию. В shipped Web composition строки `schedule` может не быть — включается experimental bundle.

**Вывод:** новая секция настроек и расписание 2 раза в сутки делаются штатными механизмами, без своего конфиг-файла и своего таймера.

### 3.8. Чего в HARNESS нет

- Нет модели «актор/эпизод/навык/артефакт» и агрегации по агенту.
- Нет карточки навыка с `whenToUse`, историей применения и применимостью к задаче.
- Нет межпродуктового реестра агентов.
- Нет семантического поиска: среди 289 пакетов `@deepseek-ai/*` не найдено embedding/vector-пакетов (только web-search и fs-search). Семантика — только опциональный внешний/локальный адаптер.
- Нет диспетчеризации по историческим акторам (только текущая команда/подагенты).
- Нет планировщика индексации знаний.

---

## 4. AutoClaw 2 (zwork 2.2.2)

### 4.1. Runtime-БД

**Путь:** `%APPDATA%\AutoClaw-oversea-official\accounts\<account>\runtime\runtime.sqlite` (~1,23 ГБ на момент замера).

**101 объект схемы**, в том числе:

| Таблица | Строк (на момент замера) | Роль |
|---|---:|---|
| `work_workspaces` | 33 | проекты/каталоги |
| `work_sessions` | 117 | сессии: title, workspace, timestamps, `capability_composition_json` |
| `work_runs` | 726 | запуски: `text` (промпт), `answer_preview`, модель, usage, статус |
| `work_events` | 130 750 | события: `tool_activity.*`, `orchestration.dispatched`, `memory.projection_recorded`, `run.*` |
| `neutral_session_entries` | 127 381 | записи диалога: `request/answer/assistant/reasoning/tool_call/tool_result/...` |
| `memory_assets` | 104 | память: scope user/workspace, lifecycle, provenance |
| `agents` | 21 | сохранённые агенты: имя, описание, инструкции, модель |
| `agent_memory_snapshots` | 0 | снимки памяти по агенту (механизм есть, не используется) |
| `scheduled_tasks` | 6 | расписания AutoClaw |
| `collaboration_run_agent_participations` | 142 | участники командных запусков |
| `collaboration_run_agent_attempts` | 142 | попытки/итоги участников |

Статусы `work_runs`: completed 617, stopped 92, failed 14, cancelled 2, running 1.

### 4.2. Сессии и связка с агентом

- `work_sessions.capability_composition_json` содержит `presetRef`, `label`, `agentInstructions` — то есть сессия знает, **какой сохранённый агент** её выполнял.
- `collaboration_run_agent_participations` содержит `agent_id`, `name`, `description`, `instructions`, `assignment`, `reason`, `actual_model_json`, `stopped_at` — это готовый источник «кто какую подзадачу выполнял».
- `collaboration_run_agent_attempts` содержит `outcome`, `detail`, `contribution_evidence`, `usage_json` — доказательства вклада и результат.

**Вывод:** AutoClaw 2 — единственный из двух продуктов, где есть **долгоживущие именованные агенты** и история их назначений. Это надо использовать в модели акторов.

### 4.3. Полнотекстовый поиск AutoClaw — важное ограничение

Схема:

```sql
CREATE VIRTUAL TABLE neutral_session_fts USING fts5(
  session_id UNINDEXED,
  entry_id UNINDEXED,
  body,
  tokenize='trigram'
);
```

Заполнение — из `neutral_session_entries` по `json_extract(payload,'$.data.text')` (миграция + backfill).

**Замер покрытия:**

- записей с непустым текстом: **38 830**;
- строк в `neutral_session_fts`: **1 332**;
- по типам в FTS: `request` — 716, `answer` — 616; **`assistant` (31 909) и `reasoning` (20 090) не проиндексированы**;
- FTS покрывает 115 сессий (все, у кого есть записи), но не покрывает основную массу текста.

`memory_fts` (FTS5 trigram по `content`, `supporting_quote`) — 104 строки, полное покрытие `memory_assets`; есть триггеры insert/update/delete.

**Вывод:** нельзя полагаться на FTS AutoClaw как на единственный поиск. Новый контур должен индексировать базовые таблицы (`neutral_session_entries`, `work_runs`, `work_events`) и, при желании, использовать FTS AutoClaw только как вспомогательный lane.

### 4.4. Память

`memory_assets`:

- `scope` (`user` / `workspace`), `lifecycle_state` (`proposed/active/superseded/conflicted/revoked/deleted`);
- `content`, `content_hash`;
- `source_session_id`, `source_entry_id`, `supporting_quote` — **провенанс** (откуда взято);
- `authorized_digest`, `command_id`, `command_payload_hash`, `authorized_at` — авторизация записи;
- `supersedes_memory_id` — версионирование;
- FTS-триггеры.

Событие `memory.projection_recorded` (428 в изученной выборке) содержит `retrievalRevision`, `placement: provider_ephemeral_context`, `entries[]` с `memoryId`, `scope`, `contentHash`, `source`, `projectionHash`, `tokenCost` — то есть AutoClaw уже умеет **проецировать память в контекст провайдера с провенансом**.

**Вывод:** модель памяти AutoClaw — готовый образец для `ak_memory`: scope, lifecycle, provenance, supersedes, FTS. В HARNESS аналога нет — там память можно извлекать из решений/эпизодов (опционально).

### 4.5. Агенты и командная работа

- 21 сохранённый агент: имя, описание, инструкции (до 18 КБ), модель, composition.
- `agent_memory_snapshots` (kind `memory`/`user`) — механизм снимков памяти агента, пока пуст.
- `collaboration_run_agent_participations` — 142 участия; `collaboration_run_agent_attempts` — 142 попытки; `orchestration.dispatched` — 142 события.
- `work_events` содержит `orchestration.dispatched`, `orchestration.wait`, `orchestration.rejected`, `run.*`, `question.*`, `memory.projection_recorded`, `tool_activity.bound` (73 474), `egress.decision` (36 293).

**Вывод:** AutoClaw уже умеет распределять работу между агентами и фиксировать вклад. Для нового контура это источник «кто кому что назначал» и «чем закончилось».

### 4.6. Навыки

**Путь:** `...\runtime\zwork-agent\skills`.

- 60+ каталогов навыков; формат тот же `SKILL.md` с frontmatter.
- `skills-preinstall.json` — ledger: `schemaVersion`, `deployed[skillId]` с `name`, `dirName`, `version`, `contentSha256`, `byteLength`, `fileCount`, `deployedAt`, `appVersion`, `reason`.
- `skills-tombstones.json` — удалённые/отозванные.
- `.zwork-bundles/<bundle>@<rev>/.zwork-bundle.json` — `bundleId`, `sourceDigest`.
- Есть навык `find-skills` (поиск/установка навыков из экосистемы), `memory`, `skill-creator` и др.

**Вывод:** AutoClaw даёт версионирование и целостность навыков (version/hash/byteLength), которых нет в HARNESS. Это можно использовать для карточки навыка и истории версий.

### 4.7. Чего в AutoClaw 2 нет

- Нет поиска «кто занимался вопросом» как продуктовой функции: есть FTS по `request/answer`, но не по решениям/действиям.
- FTS не покрывает `assistant`/`reasoning`/`tool_result`.
- Нет единой карточки навыка с историей применения и применимостью.
- Нет межпродуктового индекса (HARNESS ↔ AutoClaw).
- `agent_memory_snapshots` не заполняется.
- Нет настраиваемого расписания индексации знаний (есть `scheduled_tasks`, но для других задач).

---

## 5. Сравнение

| Возможность | HARNESS | AutoClaw 2 |
|---|---|---|
| История запросов | zstd-поток событий + проекции | SQLite: sessions/runs/entries/events |
| Поиск по истории | FTS5 backend есть, **выключен** | FTS5 trigram, **покрывает только request/answer** |
| Навыки | `SKILL.md`, каталог без `whenToUse` | `SKILL.md`, ledger с версией/хэшем |
| Использование навыков | выводится из `tool/call` | выводится из событий/истории |
| Агенты | сессионные акторы (root/subagent/team) | 21 долгоживущий именованный агент + участия |
| Память | нет встроенной | `memory_assets` + provenance + FTS |
| Диспетчеризация | team tools (в рамках сессии) | collaboration/orchestration (в рамках продукта) |
| Планировщик | `dsh-schedule` (cron/daily/weekly) | `scheduled_tasks` |
| Семантика | нет пакетов эмбеддингов | нет данных о локальных эмбеддингах |
| Приватность | локальное хранилище | локальная БД, но контент уходит провайдеру при работе |

---

## 6. Что даёт максимум (10 рычагов)

1. **Включить `session-query-sqlite`** с персистентным путём — бесплатный ranked-поиск по истории HARNESS.
2. **Агрегировать по актору**, а не по сессии: «кто» важнее «какая сессия».
3. **Индексировать навыки целиком** (`whenToUse` + тело + ресурсы) — закрыть дыру каталога.
4. **Извлекать эпизоды**: цель, действия, результат, артефакты, ошибки — единица знания, пригодная для повторного использования.
5. **Подключить AutoClaw read-only**: сессии, запуски, события, память, агенты, участия, ledger навыков.
6. **Нормализовать акторов**: `kind ∈ {root_session, subagent, team_member, autoclaw_agent}` + `dispatch_mode`.
7. **Диспетчеризация через штатные team tools**: `spawn_teammate`/`team_task_create`/`send_message`, а не свой мессенджер.
8. **Карточка навыка под задачу**: не энциклопедия, а «что применить, чего избежать, какие шаги».
9. **Расписание + ручной reindex + статус**: 2 раза в сутки по умолчанию, настройка, команда агенту.
10. **Приватность по умолчанию**: локально, редакция секретов, исключения, забывание, retention.

---

## 7. Риски и ограничения

| Риск | Проявление | Мера |
|---|---|---|
| Schema drift | AutoClaw обновится, таблицы/колонки изменятся | версия схемы, проверка наличия, `SOURCE_SCHEMA_UNSUPPORTED`, не падать |
| zstd-формат | multi-frame поток, крупные сессии | использовать `ctx.sessionQuery`, не парсить вручную; streaming + лимиты |
| Блокировки AutoClaw | WAL/запись во время чтения | `mode=ro`, busy_timeout, retry/backoff, короткие запросы |
| Секреты в истории | токены, пароли, приватные ключи | детекция/редакция до записи, канарейка, исключения |
| Prompt injection | в истории есть чужие инструкции | трактовать как данные, оборачивать, не исполнять |
| Кросс-продуктовая запись | можно испортить AutoClaw | только read-only; handoff-пакет вместо записи |
| Имена акторов | коллизии между источниками | `actor_id` с источником, отображать badge |
| Устаревание | индекс отстаёт | `index_rev`, stale-порог, расписание, ручной reindex |
| Размер | 1,2 ГБ БД + 200 МБ сессий | инкрементальность, лимиты на тексты, retention |
| Мультиэкземплярность | два процесса пишут индекс | lock, один владелец, read-only второй |
| Приватность семантики | эмбеддинги уходят провайдеру | по умолчанию выключено, локальный провайдер, предупреждение |

---

## 8. Что проверить в коде перед реализацией

1. Точный API `ctx.sessionQuery` в 0.2.0-rc.2: `searchSessions`/`searchEvents`, фильтры, курсоры, лимиты, коды ошибок.
2. Можно ли включить `session-query-sqlite` с путём вне `sessions` и `openAt: startup` в текущем профиле; где создаётся файл; права.
3. Как `dsh-settings` регистрирует волатильные поля и секции; как добавить `settings.section` без форка `ui-settings-general`.
4. Как `dsh-schedule` монтируется в текущей сборке (в shipped Web composition строки может не быть); нужен ли experimental bundle.
5. Точные имена и семантика team tools в текущей версии; как создать реплику исторического актора.
6. Схема AutoClaw 2 и версия миграций; как открыть `mode=ro` при WAL; как обходить блокировки.
7. Есть ли в AutoClaw API/БД признак «агент, который выполнял сессию» помимо `capability_composition_json`.
8. Формат `session/title` и `titleInput`: где брать человекочитаемый заголовок, а где — исходный промпт.

---

## 9. Воспроизводимость

Команды и приёмы, которыми получены факты:

- список сессий: `Get-ChildItem ~/.dsh/sessions -Recurse -Filter *.zstd`;
- декомпрессия кадров: Node `zlib.zstdDecompressSync` по каждому кадру (magic `28 b5 2f fd`);
- проекции: чтение JSON `~/.dsh/storages/session_projcache/sessions/*.json`;
- конфигурация: `~/.dsh/profiles/desktop/cordis.yml` (строки 76–103 — session/storage, 185–195 — skill, 1290–1293 — schedule);
- пакеты: извлечение README из `app.asar` (asar-заголовок: JSON по смещению 16, данные после заголовка);
- AutoClaw: `sqlite3.connect("file:...?mode=ro", uri=True)`; `sqlite_master`, `pragma table_info`, `count(*)`, `json_extract(payload,'$.data.text')`;
- навыки AutoClaw: `skills-preinstall.json`, `.zwork-bundles/*/.zwork-bundle.json`, `SKILL.md`.

---

## 10. Приложение: пакеты и документы, которые стоит прочитать

| Пакет | Зачем |
|---|---|
| `dsh-session-query` | Контракт чтения/фильтрации/поиска истории |
| `dsh-session-query-sqlite` | FTS5 backend, `openAt`, курсоры, сверка |
| `dsh-session` | Формат событий и проекций |
| `dsh-skill` | Реестр навыков, `renderSkillContent` |
| `dsh-skill-filesystem` | Корни, frontmatter, watch |
| `dsh-tool-skill` | Каталог и загрузка навыка |
| `dsh-settings` + `dsh-client-ui-settings` | Настройки и секции UI |
| `dsh-schedule` | Расписание (daily/cron) |
| `dsh-experimental-agent-team` + `dsh-experimental-tool-agent-team` | Команда, задачи, сообщения |
| `dsh-subagent` | Подагенты и каталог |
| `dsh-experimental-agent-team-profile` | Профиль команды |

---

**Итог:** техническая основа для контура уже на 70–80% есть в самих продуктах. Работа — не в изобретении поиска, а в **включении выключенного**, **нормализации акторов**, **извлечении эпизодов**, **карточке навыка**, **диспетчеризации** и **приватности по умолчанию**.
