# Контракт `knowledge_search` v1

> **Статус:** проект контракта, implementation-ready.
> **Связанные решения:** `docs/02_architecture/ADR-AGENT-KNOWLEDGE-L3.md`,
> `docs/04_research/MEMORY_LAYERS_HARNESS_RAGRAF.md`.
> **Машинные примеры:** `docs/03_implementation/contracts/knowledge_search.request.json`,
> `docs/03_implementation/contracts/knowledge_search.response.json`.
> **Схема:** `docs/03_implementation/contracts/knowledge_search.v1.schema.json`.

---

## 1. Назначение

`knowledge_search` — единая точка входа для агента-руководителя и человека:
по вопросу вернуть **раздельные** результаты из двух независимых слоёв и их
агрегаты, не смешивая доказательства.

| Слой | Источник | Единица | Доказательство |
|---|---|---|---|
| L1 `episodes` | HARNESS `session-query` | эпизод/событие сессии | `session_id` + `seq` + snippet |
| L2 `code` | RAGRAF | чанк кода/документа | `path:line`, `provenance`, `index_rev` |
| L3 `skills` / `actors` | производный слой `agent-knowledge` | навык/актор | ссылки на L1/L2 + агрегаты |

**Чего контракт не делает:**

- не хранит и не переписывает сессии HARNESS;
- не пишет в индекс RAGRAF;
- не смешивает `session_id` и `path:line` в одном поле;
- не запускает агентов и не тратит деньги;
- не заменяет `ragraf_search` и `session-query` — он их **объединяет**.

---

## 2. Где живёт координатор

Приоритет размещения:

1. **Плагин/инструмент внутри HARNESS** (рекомендуется): есть живой
   `ctx.sessionQuery`, доступ к `ctx.skills` и MCP-инструментам RAGRAF.
2. **Отдельный MCP-сервер `knowledge`** — только если HARNESS-плагин невозможен.
   Тогда L1 читается через read-only представление, а не через файл
   `session-search.db` напрямую: у БД один владелец — процесс HARNESS, и
   live-сессии в файле могут быть не видны.
3. **Не размещать** координатор внутри RAGRAF: RAGRAF не должен зависеть от
   формата сессий HARNESS.

L3-хранилище: `~/.dsh/storages/agent-knowledge/knowledge.sqlite` (отдельная
производная БД, один владелец). Она не входит в `corpus_hash` RAGRAF и не
является источником истины.

---

## 3. Запрос

```json
{
  "schema": "knowledge.search/v1",
  "request_id": "req-2026-10-23-001",
  "query": "кто чинил доступ к MySQL на 176.99.5.35",
  "mode": "auto",
  "filters": {
    "workspaces": ["C:\\AutoClaw\\176.99.5.35"],
    "session_ids": [],
    "actor_ids": [],
    "since": "2026-09-01T00:00:00+03:00",
    "until": null,
    "outcome": "any",
    "skills": [],
    "paths": []
  },
  "include": ["episodes", "code", "skills"],
  "limit": 10,
  "budget": {"max_tokens": 4000, "snippet_chars": 240},
  "provenance": "strict",
  "trace": false
}
```

| Поле | Тип | Обязательно | Смысл |
|---|---|---|---|
| `schema` | string | да | ровно `knowledge.search/v1` |
| `request_id` | string | нет | корреляция с журналом/UI |
| `query` | string | да | 1…2000 символов; литерал, не FTS-синтаксис |
| `mode` | enum | нет | `auto` (по умолчанию), `find_agent`, `find_skill`, `find_code`, `find_knowledge` |
| `filters.workspaces` | string[] | нет | точные корни; пусто = все доступные |
| `filters.session_ids` | string[] | нет | ограничить L1 |
| `filters.actor_ids` | string[] | нет | ограничить L3 |
| `filters.since` / `until` | RFC3339 | нет | время события L1 / артефакта |
| `filters.outcome` | enum | нет | `success`, `failure`, `any` |
| `filters.skills` | string[] | нет | имена навыков |
| `filters.paths` | string[] | нет | префиксы путей L2 |
| `include` | enum[] | нет | по умолчанию все три lanes |
| `limit` | int | нет | 1…50, по умолчанию 10 |
| `budget.max_tokens` | int | нет | 500…20000, по умолчанию 4000 |
| `budget.snippet_chars` | int | нет | 80…1000, по умолчанию 240 |
| `provenance` | enum | нет | `strict` (по умолчанию) — каждый элемент со ссылкой; `locator` — без snippet |
| `trace` | bool | нет | добавить тайминги/счётчики без текста запроса |

**Валидация:** пустой/пробельный `query` → `KNOWLEDGE_INVALID_REQUEST`; NUL →
`KNOWLEDGE_INVALID_REQUEST`; неизвестный `mode`/`include` — отказ, а не
молчаливый default.

---

## 4. Ответ

Ответ всегда содержит **раздельные lanes**, даже если один из них пуст или
деградировал. Общего `score` между слоями нет: сравнивать BM25 сессий и RRF
RAGRAF напрямую нельзя.

```json
{
  "schema": "knowledge.search/v1",
  "request_id": "req-2026-10-23-001",
  "query": "кто чинил доступ к MySQL на 176.99.5.35",
  "routing": {
    "intent": "find_agent",
    "lanes": ["episodes", "code", "skills"],
    "confidence": 0.86,
    "reason": "вопрос содержит «кто» + техническую тему; включены эпизоды и код"
  },
  "lanes": {
    "episodes": {
      "source": "harness.session-query",
      "index_rev": "session-search:2026-10-23T13:58:00+03:00",
      "observed_at": "2026-10-23T14:01:00+03:00",
      "degraded": false,
      "degradation_reason": null,
      "counts": {"sessions": 181, "indexed_docs": 0, "live": 0},
      "items": [
        {
          "id": "ep:session-abc:1842",
          "session_id": "session-abc",
          "seq": 1842,
          "event_type": "assistant/message",
          "time": "2026-10-21T09:14:00+03:00",
          "surface": "current",
          "snippet": "…доступ к MySQL восстановлен, пароль лежит в …",
          "session": {
            "cwd": "C:\\AutoClaw\\176.99.5.35",
            "created_at": "2026-10-21T08:50:00+03:00",
            "agent_preset": "standard",
            "live": false,
            "persisted": true
          },
          "why": ["fts: match in assistant/message"]
        }
      ]
    },
    "code": {
      "source": "ragraf",
      "index_rev": "rev-0208",
      "capability": "degraded",
      "freshness": {"stale": false, "verified_by": "fingerprint"},
      "degraded": true,
      "degradation_reason": "CPG is not built: capability=degraded",
      "items": [
        {
          "id": "chunk:cfg:...",
          "chunk_id": "cfg:...",
          "path": "src/Config.php",
          "start_line": 12,
          "end_line": 28,
          "source_kind": "symbol",
          "provenance": "stated",
          "score": 0.71,
          "why": ["lex: mysql", "vec: 0.83"],
          "snippet": "…mysql: host=…"
        }
      ]
    },
    "skills": {
      "source": "harness.skills+agent-knowledge",
      "index_rev": "ak-2026-10-23T03:00:00+03:00",
      "items": [
        {
          "name": "server-31",
          "path": "C:\\Users\\…\\.dsh\\skills\\server-31\\SKILL.md",
          "description": "…",
          "when_to_use": "…",
          "content_hash": "sha256:…",
          "used_by": [{"actor_id": "actor:…", "episode_id": "ep:…"}]
        }
      ]
    }
  },
  "actors": [
    {
      "actor_id": "actor:harness:subagent:session-abc:verify-admin",
      "name": "verify-admin",
      "kind": "subagent",
      "source": "harness",
      "confidence": 0.84,
      "score": 0.032,
      "why": ["3 эпизода по MySQL", "навык server-31", "успешный итог"],
      "evidence": [
        {"lane": "episodes", "id": "ep:session-abc:1842"},
        {"lane": "code", "id": "chunk:cfg:..."}
      ],
      "skills": [{"name": "server-31", "uses": 3}],
      "dispatch": {"mode": "spawn_replica", "available": true}
    }
  ],
  "fusion": {
    "strategy": "rrf",
    "k": 60,
    "weights": {"episodes": 1.0, "code": 0.4, "skills": 0.6},
    "dedup": "by id"
  },
  "limits": {
    "requested": 10,
    "returned": 7,
    "truncated": false,
    "budget": {"max_tokens": 4000, "used_tokens": 612}
  },
  "warnings": [
    {"code": "KNOWLEDGE_LANE_DEGRADED", "lane": "code", "message": "CPG is not built"}
  ],
  "errors": []
}
```

### 4.1. Инварианты ответа

1. `lanes` всегда содержит `episodes` и `code`; `skills` — если запрошен.
2. Каждый lane имеет `source`, `index_rev`, `degraded`.
3. Элемент `episodes` имеет `session_id` + `seq`; **не имеет** `path`.
4. Элемент `code` имеет `path` + `start_line`; **не имеет** `session_id`.
5. `actors[].evidence[]` ссылается только на существующие `lane`/`id`.
6. `fusion.strategy` — `rrf`; `k` — положительное целое.
7. `limits.truncated=true` только если элементы реально отброшены бюджетом.
8. `errors` содержит коды; при обоих недоступных источниках — `found=false` и
   `KNOWLEDGE_NO_SOURCE`, без выдуманных элементов.
9. `index_rev` каждого lane относится **только к этому lane**; глобального
   «общего» `index_rev` нет.
10. Порядок детерминирован: `(score desc, lane_priority, time desc, id asc)`;
    `lane_priority`: `episodes > code > skills`.

---

## 5. Контракты lanes

### 5.1. L1 `episodes` (HARNESS)

- Источник: `ctx.sessionQuery.searchSessions` (живые + persisted).
- Ответ backend: `{header, live, persisted, bestMatch:{sessionId, seq, type, time, surface, snippet}}`.
- Обёртка: `id = "ep:" + sessionId + ":" + seq`, `session.cwd/created_at/
  agent_preset`; `agent` не выдумывается — если нужен актор, его даёт L3.
- `counts` — из ответа/покрытия backend; `indexed_docs` может быть 0, пока
  первый поиск не построил индекс.
- Ошибки backend: `SESSION_QUERY_SEARCH_DISABLED`, `SESSION_QUERY_INDEX_FAILED`,
  `SESSION_QUERY_STALE_CURSOR`, `SESSION_QUERY_SESSION_NOT_FOUND` →
  `degraded=true` / `errors[]`; **не** ломать code lane.

### 5.2. L2 `code` (RAGRAF)

- Источник: `ragraf_search` (гибрид FTS5 + vec, RRF), затем при необходимости
  `ragraf_symbol`/`ragraf_code` для уточнения.
- Поля: `chunk_id`, `path`, `start_line`, `end_line`, `source_kind`, `score`,
  `why`, `snippet` (если `with_text`), `provenance`.
- Envelope RAGRAF: `index_rev`, `capability`, `drift`, `freshness`;
  `capability=degraded` — честная деградация, не ошибка.
- `stale=true` → `warnings[]`, не скрывать.
- Ошибки RAGRAF (`found=false`, `index_rev=null`, `KNOWLEDGE_SOURCE_UNAVAILABLE`)
  → lane `degraded`, episodes продолжают работать.

### 5.3. L3 `skills` / `actors` (agent-knowledge)

- Источник: `agent-knowledge` (производная БД) + каталог `SKILL.md`.
- L3 **не хранит** сырой текст сессий/кода: только `session_id`+`seq`,
  `path`+`line`, названия навыков, теги, исход, счётчики.
- `actors` — агрегат: `actor_id`, `kind` (`root_session|subagent|team_member|
  autoclaw_agent`), `source`, `confidence`, `score`, `why`, `evidence[]`,
  `skills[]`, `dispatch`.
- `dispatch.mode`: `spawn_replica` (исторический DSH-актор),
  `team_task` (текущая команда), `external_handoff` (AutoClaw),
  `none` (только рекомендация). `available` — правда о текущей среде.
- Если L3 ещё не построен: `skills`/`actors` отсутствуют или `degraded=true`;
  L1/L2 всё равно возвращаются.

---

## 6. Маршрутизация

Детерминированные правила, без LLM (LLM-роутинг — только опция P2):

| Признаки в запросе | `intent` | Lanes по умолчанию |
|---|---|---|
| «кто», «когда», «почему», «решал», «делал», «сессия», «агент», «навык» | `find_agent` | episodes 1.0, skills 0.6, code 0.4 |
| «где», «функция», «класс», «символ», «вызывает», «затронет», «impact», «схема», «таблица», «маршрут», «политика» | `find_code` | code 1.0, episodes 0.3, skills 0.2 |
| «навык», «skill», «whenToUse», «применял», «инструкция» | `find_skill` | skills 1.0, episodes 0.5, code 0.2 |
| всё остальное | `find_knowledge` | episodes 0.8, code 0.8, skills 0.4 |

- `mode` пользователя важнее эвристики; `auto` использует таблицу.
- Если запрос смешанный — обе темы, confidence ниже; `routing.reason` объясняет.
- `routing.confidence` — не вероятность, а уровень уверенности эвристики
  (`high|medium|low` в тексте; число 0…1 для сортировки).
- Ошибка маршрутизации не должна терять данные: при сомнении включать обе lanes.

## 7. Fusion

1. Каждый lane возвращает top K (по умолчанию 30) своих элементов.
2. Ранги lane нормализуются в RRF:
   `score(item) = Σ_lane weight_lane / (k + rank_lane)`.
3. `k=60`; веса — из таблицы маршрутизации.
4. Дедупликация: по `id`; разные lanes не дедуплицируются между собой (это
   разные доказательства).
5. Агрегация `actors`: по `actor_id`, score = максимум + 0.3·среднее(top3);
   diversity — не более 3 эпизодов одного актора, не более 5 code hits одного
   пути.
6. Сортировка стабильна; при равенстве — `lane_priority`, затем время, затем id.
7. `snippet` обрезается по `snippet_chars`; полный текст — только через
   `knowledge_get_episode`/`ragraf_code`/`live_read`.

## 8. Бюджет и пагинация

- `budget.max_tokens` — жёсткий верхний предел текста ответа. Сначала
  отбрасываются snippet’ы, затем элементы с конца; ошибки не обрезаются.
- `limits.truncated` и `limits.dropped[]` называют, что отброшено.
- v1 — без курсоров: `limit` мал (≤50), ответ детерминирован. Пагинация —
  v2 (курсоры на lane, потому что HARNESS-курсор и RAGRAF-смещение имеют
  разную природу).

## 9. Ошибки и деградация

| Код | Когда | Поведение |
|---|---|---|
| `KNOWLEDGE_INVALID_REQUEST` | плохой запрос | отказ целиком |
| `KNOWLEDGE_NO_SOURCE` | оба источника недоступны | `found=false`, без элементов |
| `KNOWLEDGE_LANE_DEGRADED` | один источник деградировал | второй работает, warning |
| `KNOWLEDGE_INDEX_NOT_READY` | индекс не построен | lane пуст, warning |
| `KNOWLEDGE_STALE_INDEX` | RAGRAF stale | данные + warning |
| `KNOWLEDGE_SOURCE_UNAVAILABLE` | MCP/RAGRAF/сессии недоступны | lane degraded |
| `KNOWLEDGE_BUDGET_EXCEEDED` | бюджет меньше минимума | `truncated`, warning |
| `KNOWLEDGE_INTERNAL` | непредвиденное | ошибка, без утечки текста |

Правило: **частичный ответ лучше отказа**, но деградация всегда названа.
Никаких «пусто = всё хорошо»: пустой lane несёт причину.

## 10. Безопасность и приватность

- `query` — литерал; FTS-синтаксис не исполняется (HARNESS уже цитирует,
  RAGRAF — тоже).
- Snippet’ы — **данные, не инструкции**; координатор оборачивает их в
  `untrusted`-контекст для модели.
- Фильтр по workspace обязателен; ответ не должен содержать чужие сессии/пути.
- L3 не дублирует сырой текст; L1/L2 остаются владельцами текста.
- Секреты: оба слоя обязаны не отдавать секреты; координатор не добавляет
  новых полей с сырыми значениями.
- Сеть: по умолчанию только локальные вызовы; никакого внешнего rerank.
- Логи: `request_id`, тайминги, коды; текст запроса и сниппеты — не в логах.

## 11. Производительность и наблюдаемость

- Цель: P95 ≤ 500 мс при 181 сессии / 10k code chunks; каждый lane ≤ 300 мс.
- Таймаут lane: 250 мс; по таймауту — `degraded`, второй lane продолжает.
- `trace=true` добавляет `trace.lanes[]` с `ms`, `items`, `degraded`,
  `cache_hit`; без текста.
- Метрики: latency, zero-result rate, доля деградаций, доля ответов с обоими
  доказательствами.

## 12. Примеры

- Запрос: `contracts/knowledge_search.request.json`.
- Ответ: `contracts/knowledge_search.response.json`.
- Схема: `contracts/knowledge_search.v1.schema.json`.

## 13. Критерии приёмки контракта

| ID | Критерий |
|---|---|
| KC-1 | Валидный запрос → 200/ok, `schema`, `routing`, `lanes`, `fusion` на месте |
| KC-2 | Оба источника живы → есть `episodes` и `code`; элементы не смешаны |
| KC-3 | RAGRAF недоступен → `code.degraded=true`, `episodes` непуст, `errors` называет причину |
| KC-4 | HARNESS search выключен/не построен → `episodes.degraded=true`, `code` работает |
| KC-5 | Оба недоступны → `found=false`, `KNOWLEDGE_NO_SOURCE`, нет выдуманных элементов |
| KC-6 | Каждый элемент L1 имеет `session_id`+`seq`; каждый L2 — `path`+`start_line` |
| KC-7 | `actors[].evidence[]` не ссылается на несуществующие id |
| KC-8 | RRF: при равных входных рангах порядок стабилен; веса применяются |
| KC-9 | `budget.max_tokens` не превышен; `truncated`/`dropped` честны |
| KC-10 | Workspace-фильтр не пропускает чужие сессии/пути |
| KC-11 | Секрет-канарейка не появляется в ответе/логах |
| KC-12 | Повторный запрос при том же `index_rev` даёт тот же порядок |
| KC-13 | Один lane не пишет в БД другого; HARNESS/RAGRAF не изменяются |

## 14. `knowledge_dispatch` v1

Отдельный контракт — передача задачи выбранному актору. **По умолчанию
`dry_run`; `commit` только фиксирует план в L3 и не запускает агента.**

```json
{
  "schema": "knowledge.dispatch/v1",
  "task": {
    "text": "clean-bom-crlf: проверить BOM и CRLF",
    "acceptance": ["..."],
    "constraints": ["..."]
  },
  "target": "auto",
  "actor_id": null,
  "dry_run": true
}
```

Ответ:

```json
{
  "schema": "knowledge.dispatch/v1",
  "mode": "dry_run",
  "target": {
    "actor_id": "actor:...",
    "name": "...",
    "kind": "root_session|subagent|team_member|autoclaw_agent",
    "source": "harness|autoclaw",
    "why": ["применял навык clean-bom-crlf (8 раз)"],
    "dispatch": {"mode": "spawn_replica|team_task|external_handoff|none", "available": true}
  },
  "context_pack": {
    "goal": "...",
    "acceptance": ["..."],
    "constraints": ["..."],
    "prior_episodes": [{"session_id": "...", "seq": 1, "snippet": "...", "why": ["..."]}],
    "code_refs": [{"path": "...", "start_line": 1, "why": ["..."]}],
    "skills": [{"name": "...", "uses": 8}],
    "risks": ["..."]
  },
  "evidence": {"episodes": [], "code": [], "skills": []},
  "warnings": [],
  "assignment": null,
  "errors": []
}
```

Правила:

1. `target=auto`: сначала навык из задачи (`ak_skill_use` — кто применял чаще),
   иначе лучший актор из `knowledge_search`.
2. `target=actor:<id>` или `actor_id` — явный выбор; проверяется, что актор есть.
3. `dry_run` не пишет и не запускает; `commit` пишет `ak_assignment` со статусом
   `recorded_plan`; повторный `commit` идемпотентен.
4. Реальная отправка/спавн — отдельный P2-контракт с явным подтверждением.
5. `context_pack` содержит только ссылки и короткие сниппеты; полный текст —
   из L1/L2 по запросу.

Критерии:

| ID | Критерий |
|---|---|
| KD-1 | `dry_run` не создаёт `ak_assignment` |
| KD-2 | `commit` создаёт одну запись, повтор идемпотентен |
| KD-3 | при названном навыке цель — актор с максимумом `ak_skill_use` |
| KD-4 | `target.dispatch.available` — правда о среде, а не обещание |
| KD-5 | `context_pack` содержит acceptance, episodes, code_refs, skills, risks |
| KD-6 | нет автозапуска агента и записи в HARNESS/RAGRAF |

### 14.1 Handoff package (`knowledge.handoff/v1`)

`knowledge_dispatch` готовит пакет для agent-team HARNESS:

```json
{
  "schema": "knowledge.handoff/v1",
  "assignment_id": "assign:...",
  "target": {"actor_id": "...", "name": "...", "dispatch": {"mode": "spawn_replica"}},
  "task": {"text": "...", "acceptance": ["..."]},
  "tool_calls": [
    {"tool": "spawn_teammate", "arguments": {"name": "...", "description": "...", "prompt": "..."}},
    {"tool": "team_task_create", "arguments": {"title": "...", "details": "...", "owner": "..."}},
    {"tool": "send_message", "arguments": {"target": "...", "message": "..."}}
  ],
  "execution": "not_executed",
  "requires": "HARNESS Lead / agent-team tools",
  "context_pack": {}
}
```

Правила:

1. Пакет **не исполняется** внешним процессом: `execution=not_executed`.
2. Выполняет Lead HARNESS после явного подтверждения; иначе это обход границы
   «не запускать чужих агентов».
3. Для `external_handoff` (AutoClaw) tool-calls не создаются, только пакет.
4. Пакет пишется в outbox и может быть проверен до выполнения.

## 15. Версионирование

- v1 фиксирует `schema: knowledge.search/v1` и `knowledge.dispatch/v1`.
- Добавление необязательных полей — v1.
- Изменение семантики `score`, слияние lanes, удаление полей — v2.
- `index_rev` и `capability` — обязательные поля для аудита; не убирать.

## 16. Открытые вопросы

1. Кто владеет L3: HARNESS-плагин или отдельный сервис?
2. Нужна ли пагинация в v1 (сейчас нет; лимит 50).
3. Нужен ли `find_agent` без L3 (только по L1 `agentTeam`/`subagent`)?
4. Как считать `confidence` актора без L3 — из сессий или из явной разметки?
5. Включать ли AutoClaw `memory_assets` как L1-совместимый источник (read-only)
   в v1.1?
