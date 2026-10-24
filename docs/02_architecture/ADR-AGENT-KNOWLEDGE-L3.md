# ADR: слой «Знания агентов» (L3) поверх HARNESS и RAGRAF

> **Статус:** предложено, к реализации.
> **Дата:** 23.10.2026.
> **Контекст:** HARNESS хранит эпизоды, RAGRAF — факты о коде. Нужен производный
> слой, который отвечает «кто, что, когда, каким навыком и с каким итогом»,
> не переписывая ни один из источников.
> **Связанные документы:**
> `docs/04_research/MEMORY_LAYERS_HARNESS_RAGRAF.md`,
> `docs/04_research/HARNESS_SESSION_SEARCH_VERIFICATION.md`,
> `docs/03_implementation/KNOWLEDGE_SEARCH_CONTRACT.md`.

---

## 1. Решение

Добавить **L3 — производную базу знаний об агентах**:

- отдельная SQLite-БД `~/.dsh/storages/agent-knowledge/knowledge.sqlite`;
- хранит **ссылки и агрегаты**, а не копии текста сессий или кода;
- строится из L1 (HARNESS) и L2 (RAGRAF) по расписанию и по команде;
- обслуживает `knowledge_search`, карточки навыков и диспетчеризацию.

**Отвергнутые альтернативы:**

| Альтернатива | Почему нет |
|---|---|
| Отключить HARNESS memory, жить на RAGRAF | RAGRAF не хранит эпизоды, «кто/когда/почему» исчезает |
| Перенести сессии в корпус RAGRAF | другой формат/lifecycle/приватность; сломает `corpus_hash` и сторож свежести |
| Слить L1 и L2 в одну БД | разные доказательства и владельцы; смешение типов `session_id`/`path:line` |
| Включить experimental session-search (`:memory:`) | второй индекс по тем же данным, теряется после рестарта |
| Писать факты RAGRAF в сессии HARNESS | источник истины и формат не должны меняться |

## 2. Слои и владельцы

| Слой | Владелец | Хранит | Не хранит |
|---|---|---|---|
| L1 | HARNESS (`session-query`) | события, сниппеты, заголовки | граф кода, `path:line` |
| L2 | RAGRAF | граф, лексику, векторы, ревизии | сессии, агентов, «почему» |
| L3 | `agent-knowledge` | акторы, эпизоды-ссылки, навыки-ссылки, артефакты-ссылки, назначения, агрегаты | сырой текст сессий, тела файлов, копии чанков |

**L3 не является источником истины.** Удаление L3 не должно ломать L1/L2; полная
пересборка L3 — штатная операция.

## 3. Модель данных L3

Имена с префиксом `ak_`; схема версионируется.

### 3.1. Сущности

| Таблица | Ключ | Поля (минимум) |
|---|---|---|
| `ak_actor` | `actor_id` | `kind`, `source`, `display_name`, `workspace_root`, `first_seen`, `last_seen`, `model`, `stats_json` |
| `ak_episode` | `episode_id` | `actor_id`, `session_id`, `seq_start`, `seq_end`, `title`, `started_at`, `finished_at`, `workspace_root`, `outcome`, `tokens`, `summary_hash` |
| `ak_episode_code` | `(episode_id, code_ref)` | `path`, `start_line`, `end_line`, `symbol_fqn`, `provenance`, `index_rev` |
| `ak_skill` | `skill_name` | `path`, `description`, `when_to_use`, `content_hash`, `source`, `version` |
| `ak_skill_use` | `(actor_id, episode_id, skill_name)` | `used_at`, `outcome`, `evidence_session_id`, `evidence_seq` |
| `ak_artifact` | `artifact_id` | `episode_id`, `kind`, `path`, `url`, `sha256`, `created_at` |
| `ak_assignment` | `assignment_id` | `task_id`, `actor_id`, `mode`, `created_at`, `status`, `provenance_json` |
| `ak_link` | `(src_kind, src_id, dst_kind, dst_id)` | `relation`, `confidence`, `created_at` |
| `ak_index_run` | `run_id` | `started_at`, `finished_at`, `mode`, `counts_json`, `errors_json` |
| `ak_source_state` | `source` | `revision`, `last_indexed_at`, `schema_version`, `error` |

### 3.2. Инварианты

1. **Нет сырого текста.** L3 хранит `session_id`+`seq` и `path`+`line`, но не
   сообщения и не код. Для чтения текста — L1/L2.
2. **Нет записи в L1/L2.** L3 только читает HARNESS/RAGRAF (через API/плагин).
3. **Один владелец.** Lock-файл; второй процесс — read-only или ждёт.
4. **Идемпотентность.** `episode_id = hash(actor_id, session_id, seq_start,
   seq_end, content_hash)`; повторный прогон не создаёт дублей.
5. **Provenance.** У каждой связи — источник и `index_rev`; `derived`-связи
   помечены.
6. **Удаление.** «Забудь» удаляет L3-строки, но не L1/L2; проверка отсутствия
   остатков.
7. **Свежесть.** `ak_source_state.revision` обновляется после успешного прогона;
   отставший источник помечается `degraded`.
8. **Приватность.** L3 не хранит PII/секреты; `summary_hash` вместо текста.

## 4. Наполнение

### 4.1. Из L1 (HARNESS)

- Источник: `ctx.sessionQuery` (живые + persisted) или read-only адаптер к
  `session-search.db` (только если плагин невозможен).
- Единица: **эпизод** — turn или группа шагов с одной целью; границы — по
  `turn/start`/`turn/end`, `todo/write`, `subagent/catalog`.
- Извлекается: `session_id`, `cwd`, `created_at`, `agent_preset`, `seq`-диапазон,
  тип/время, `outcome` (по `turn/end`/`tool/result`), использованные `skill`
  (`tool/call name=skill`), артефакты (`deliverables/presented`,
  `workspace/changes`), участники (`agentTeam`, `subagent`).
- **Не извлекается:** reasoning, полный текст; только ссылки + короткий
  `summary` (≤240 символов) с `summary_hash`.

### 4.2. Из L2 (RAGRAF)

- Источник: MCP-инструменты `ragraf_search`, `ragraf_symbol`, `ragraf_code`,
  `ragraf_impact`; read-only.
- Связь `episode → code_ref`: по путям из `workspace/changes`, `tool/call`
  (`read/write/edit`), `deliverables/presented`, а также по запросу
  `ragraf_search` в рамках эпизода.
- Хранится `path`+`line`+`symbol_fqn`+`provenance`+`index_rev`; тело — только
  по запросу через RAGRAF.
- `capability=degraded` и `stale` сохраняются как факты, не скрываются.

### 4.3. Навыки

- Источник: `SKILL.md` (L1-каталог) + ledger AutoClaw (если подключён).
- Хранится `name`, `path`, `content_hash`, `when_to_use` (да, это метаданные
  навыка, не текст сессии), `source`.
- Использование: `tool/call name=skill` + `tool/result`; `used_by`.

## 5. API

- `knowledge_search` — контракт v1:
  `docs/03_implementation/KNOWLEDGE_SEARCH_CONTRACT.md`.
- `knowledge_get_episode(episode_id)` — ссылки + актор + навыки; текст — из L1.
- `knowledge_get_skill(name, task?)` — карточка навыка; тело — из файла.
- `knowledge_get_actor(actor_id)` — профиль + эпизоды + навыки.
- `knowledge_reindex(mode=incremental|full|verify)` — L3.
- `knowledge_dispatch` — план/выдача задачи (P2).
- `knowledge_forget` — удаление L3-строк.

**Правило:** ни один метод не пишет в L1/L2.

## 6. Расписание и обслуживание

- Инкрементальный прогон: 2 раза в сутки (по умолчанию 03:00/15:00 local),
  настраивается; ручной `knowledge_reindex`.
- Полная пересборка — в отдельную БД/поколение с атомарной заменой.
- Crash-safe: WAL, транзакции, resumable cursor, lock.
- Retention: 180 дней по умолчанию; «забудь» и purge.
- Наблюдаемость: `ak_index_run`, `index_rev`, coverage, errors, размер.

## 7. Приватность и безопасность

- L3 не хранит сырой текст; только ссылки и хэши.
- Сниппеты для ответа берутся из L1/L2 на лету; они **untrusted** для модели.
- Фильтр по workspace/владельцу; чужие сессии/пути не попадают.
- Секреты: оба источника обязаны не отдавать их; L3 не добавляет новых полей
  с сырыми значениями.
- Сеть: локально; никаких внешних эмбеддингов/rerank по умолчанию.
- Логи: без query text и сниппетов.

## 8. Этапы

### P0 — контракт и чтение

- L3-схема + миграции.
- Инкрементальное наполнение из L1/L2.
- `knowledge_search` (L1+L2, RRF) + `knowledge_get_episode/skill/actor`.
- Статус, reindex, тесты, golden set.

### P1 — польза руководителю

- `actors` и `dispatch` (dry-run → commit).
- Карточка навыка под задачу.
- UI-страница (уже есть каркас в админ-панели RAGRAF; можно переиспользовать).
- AutoClaw read-only адаптер (если нужен).

### P2 — максимум

- Обратная связь и обучение ранжирования.
- Опциональная семантика/LLM-суммаризация с согласием.
- Retention/forget UI, i18n, полировка.

## 9. Критерии приёмки

| ID | Критерий |
|---|---|
| ADR-1 | L3 не содержит сырого текста сессий/кода (проверка схемы + канарейка) |
| ADR-2 | L3 не пишет в L1/L2; хэши источников до/после совпадают |
| ADR-3 | `knowledge_search` проходит KC-1…KC-13 |
| ADR-4 | Инкрементальный прогон после одной изменённой сессии трогает только её |
| ADR-5 | Полная пересборка атомарна; предыдущая L3 доступна до успеха |
| ADR-6 | Crash/kill -9 не ломает предыдущую L3 |
| ADR-7 | «Забудь» удаляет L3-строки и не трогает L1/L2 |
| ADR-8 | P95 поиска ≤ 500 мс; L3-прогон ≤ 60 с на 200 сессий |
| ADR-9 | Нет секретов/PII в L3 и логах (канарейка) |
| ADR-10 | Документация: контракт, схема, эксплуатация, troubleshooting |

## 10. Риски

| Риск | Мера |
|---|---|
| Дублирование текста | схема без `text`/`body`; только ссылки; тест-канарейка |
| Расхождение L1/L2 | `index_rev`/`source_state`; `degraded`; не смешивать |
| Стоимость индексации | инкремент по `updated_at`/seq; лимиты; skip unchanged |
| Приватность | фильтр workspace; retention; forget; no network |
| Сложность диспетчеризации | P2; `dry_run` по умолчанию; явное подтверждение |
| Дрейф схем HARNESS/AutoClaw | `source_state.schema_version`; `SOURCE_SCHEMA_UNSUPPORTED`; не падать |

## 11. Открытые вопросы

1. Где живёт L3: HARNESS-плагин или отдельный сервис? Рекомендация — плагин
   HARNESS, потому что L1 живой и доступен только там.
2. Включать ли AutoClaw `memory_assets` в L1-совместимый адаптер в v1.1?
3. Нужна ли пагинация `knowledge_search` в v1? Сейчас нет, лимит 50.
4. Кто владеет `dispatch`: Lead-агент HARNESS или отдельный оркестратор?
5. Как считать confidence актора до появления L3 (только L1)?
