# Память HARNESS и знания RAGRAF: не заменять, а разделять и связывать

> **Дата:** 23.10.2026.
> **Контекст:** в HARNESS включён персистентный `session-query-sqlite`
> (`~/.dsh/cache/session-search.db`, `openAt: first-search`); обсуждается вопрос —
> отключить «память HARNESS» и пользоваться только RAGRAF или дополнять данные
> HARNESS фактами из графов.
> **Короткий ответ:** не отключать и не заменять. Это два разных слоя знаний с
> разными единицами хранения, жизненным циклом и доказательствами. Их нужно
> **связывать на уровне запроса**, а не сливать в одно хранилище.

---

## 1. Что именно хранит HARNESS

HARNESS хранит **эпизодическую память**: что произошло в сессии, в каком порядке,
с какими инструментами и результатами.

| Слой | Где | Что хранит |
|---|---|---|
| Журнал сессий | `~/.dsh/sessions/<workspace>/<session>/session.v4.jsonl.zstd` | append-only поток событий: `user/message`, `assistant/message`, `tool/call`, `tool/result`, `todo/write`, `turn/end`, `deliverables/presented`, `workspace/changes`, `subagent/catalog`, `session/title` |
| Проекция сессии | `~/.dsh/storages/session_projcache/sessions/*.json` | заголовок, статистика, `agentTeam`, `subagent`, `todos`, `plan`, `permissions`, `sessionListMetadata` |
| Поисковый индекс | `~/.dsh/cache/session-search.db` (после первого поиска) | FTS5 `persisted_docs` / `live_docs`: `text`, `session_id`, `seq`, `type`, `time`, `surface`, `codepoint_length`; токенизатор `unicode61` |
| Каталог навыков | `~/.dsh/skills`, проектные `.dsh/skills`, `.agents/skills` | `SKILL.md`: имя, описание, `whenToUse`, ресурсы; факт применения — в `tool/call` |

**Что индексирует `session-query`** (по коду `dsh-session-query` 0.2.1-rc.2):
текст `user/message` и `assistant/message`, имя и аргументы `tool/call`, результат
`tool/result`, `todo/write`, причину `turn/end` (ошибка/аборт/лимит). Структурные
события (`turn/start`, `step/*`, `assistant/attempt`, `request/header`) не дают
текста; reasoning-блоки в документы не попадают.

**Чего в HARNESS нет:**

- графа кода и связей «кто вызывает / что затронет»;
- `path:line` как обязательного доказательства;
- схемы БД, маршрутов, политик доступа, тестов как отдельных сущностей;
- векторного поиска по коду;
- долговременной «памяти» уровня AutoClaw (`memory_assets`); есть история сессий,
  а не память фактов.

**Текущее состояние на машине:** 181 файл сессий, ~210 МБ; после первого поиска
`session-search.db` создана — 245 МиБ, 177 persisted-сессий и 90 602 документа.
Индекс больше исходной истории из-за FTS5 и крупных `tool/result`; это важно для
бюджета памяти и для решения не включать второй `:memory:`-индекс. Подробный
замер — `docs/04_research/HARNESS_SESSION_SEARCH_VERIFICATION.md`.

---

## 2. Что хранит RAGRAF

RAGRAF хранит **семантическую память о коде**: не «что обсуждали», а «как устроен
код и что с чем связано».

| Хранилище | Где | Что хранит |
|---|---|---|
| `graph/graph.sqlite` | `.ragraf/index/graph/` | `node`, `edge` (`stated`/`derived`, `evidence_path`, `evidence_line`, `confidence`), `text_chunk`, `cfg_edge`, `dfg_edge`, `db_table`, `db_column`, `db_fk`, `web_route`, `coverage` |
| `lex/lexvec.sqlite` | `.ragraf/index/lex/` | FTS5 `ft_chunk` (symbol/code/sql/text tokens) + `vec_chunk` (384-мерные) + `chunk_text` |
| `control.sqlite` | `.ragraf/` | `rev_history`, `drift_entry`, покрытие, ledger, телеметрия; append-only |

**Что RAGRAF делает лучше HARNESS:**

- графовые операции: `symbol`, `callers`, `callees`, `impact`, `references`,
  `slice`, `repo_map`;
- схема БД, `write_check`, `db_cross`, `db_drift`;
- политики доступа (`policy_trace`), маршруты (`routes`), стили (`class_usage`);
- гибридный поиск FTS5 + вектор с RRF и `why`;
- обязательный `path:line` и разделение `stated`/`derived`;
- ревизии, `corpus_hash`, сторож свежести, drift-журнал;
- мультикорпусный реестр и `route` «куда идти за ответом».

**Чего RAGRAF не делает и не должен:**

- не хранит сообщения, решения, ошибки и «кто это сделал»;
- не знает агента, сессию, время разговора и итог задачи;
- не индексирует append-only zstd-журналы: корпус — это файлы текущей ревизии;
- не должен включать изменяемые данные в `corpus_hash` — иначе сторож свежести
  зациклится (инвариант проекта: `.ragraf` и карта метаданных исключены из корпуса);
- не заменяет FTS5 сессий: его веса `bm25()` и чанкинг настроены под код, а не
  под диалог.

---

## 3. Почему нельзя «просто отключить HARNESS»

1. **RAGRAF не хранит эпизоды.** Если отключить сессии/поиск, исчезнет ответ на
   «кто, когда и почему это делал». Граф кода отвечает «где», но не «зачем».
2. **Sidebar search и `/resume` опираются на session-query.** Отключение ломает
   пользовательский сценарий, а не только «память агента».
3. **Это дешёвый и локальный слой.** Индекс производный, переживает перезапуск,
   обновляется инкрементально; его стоимость несоизмерима с потерей контекста.
4. **Доказательства разные.** HARNESS даёт `session_id` + `seq` + сниппет;
   RAGRAF — `path:line`. Одно не подменяет другое.

## 4. Почему нельзя «просто сложить сессии в RAGRAF»

1. **Другой формат.** Сессии — append-only поток zstd-кадров; RAGRAF индексирует
   файлы текущей ревизии. Парсер zstd и event-stream в RAGRAF — новый слой, которого
   там нет.
2. **Другой lifecycle.** Код меняется редко и переиндексируется по `corpus_hash`;
   сессии меняются постоянно. Смешение сделает `corpus_hash` дрожащим, а сторож —
   бесконечно переиндексирующим.
3. **Другая приватность.** В сессиях — пользовательский текст, ошибки, пути,
   возможные секреты. У RAGRAF другой retention и другая модель доверия.
4. **Другой ранжировщик.** FTS5 RAGRAF взвешен под код (`symbol_tokens`,
   `code_tokens`, `sql_tokens`, `text_tokens`); диалоговый текст в этой схеме
   будет шумом.
5. **Другой ответ.** RAGRAF обязан отдавать `path:line`; для сессии это
   `session_id` + `seq`, а не файл. Смешение типов доказательств ломает контракт.

## 5. Рекомендация: три слоя и один координатор

```
┌────────────────────────────────────────────────────────────┐
│ L1. Эпизодическая память — HARNESS                          │
│ sessions + projections + session-query FTS (persistent)     │
│ доказательство: session_id + event seq + snippet            │
└───────────────────────────┬────────────────────────────────┘
                            │ ссылки episode → code fact
┌───────────────────────────▼────────────────────────────────┐
│ L2. Семантическая память о коде — RAGRAF                    │
│ graph + lexvec + revisions + freshness                      │
│ доказательство: path:line, stated/derived, confidence       │
└───────────────────────────┬────────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────────┐
│ L3. Производный слой «знания агентов» (новый)               │
│ actor, episode, skill, artifact, assignment, links          │
│ не хранит копии сессий и кода — хранит связи и агрегаты     │
└───────────────────────────┬────────────────────────────────┘
                            │
                 Query coordinator (MCP)
       knowledge_search → RRF по L1+L2+L3, provenance
```

### Правила маршрутизации запросов

| Вопрос | Источник | Почему |
|---|---|---|
| «Кто занимался X?» | L1 + L3 | нужны эпизоды, агент, время, итог |
| «Почему так решили?» | L1 | решение и аргументы — в диалоге |
| «Где определено X?» | L2 | граф/символы и `path:line` |
| «Что затронет правка X?» | L2 | `impact`, `callers`, `references` |
| «Какие навыки применить?» | L3 + каталог навыков | `whenToUse` + история применения |
| «Что уже делали по теме X?» | L1 + L3 | эпизоды + агрегаты |
| «Какая таблица/маршрут/политика?» | L2 | `schema`, `routes`, `policy_trace` |

### Что делать с текущим включением

1. **Оставить** `session-query-sqlite` включённым. Это правильный слой L1.
2. **Не включать** experimental `dsh-experimental-session-search` с его отдельным
   `:memory:`-индексом, пока есть персистентный backend: это второй индекс по тем
   же данным, с потерей после перезапуска и другим покрытием. Если нужен поиск
   из чата — нужен один инструмент поверх **того же** персистентного
   `ctx.sessionQuery`, а не второй провайдер.
3. **Не переносить** сессии в корпус RAGRAF. Для L3 сделать отдельную производную
   БД (`agent-knowledge`) со ссылками на L1/L2, как описано в мастер-промте.
4. **Добавить координатор**: один инструмент `knowledge_search`, который:
   - определяет намерение (агент/навык/код/эпизод);
   - запрашивает L1 и L2 параллельно;
   - сливает результаты RRF;
   - возвращает **раздельные** блоки `episodes` и `code_facts` с их provenance;
   - не смешивает `session_id` и `path:line` в одном поле.
5. **Связать факты**: в L3 хранить `episode → symbol|path|table|route` и
   `skill → episode`, а не копировать текст. Это даёт «кто применял навык X» и
   «в каких задачах встречался файл Y».

### Что мерить

- recall@3 для «кто занимался X» на golden set;
- долю ответов, где есть и эпизод, и доказательство кода;
- время до первого полезного факта;
- stale/дрейф L1 и L2 по отдельности;
- объём индексов и стоимость инкремента;
- отсутствие секретов/личных данных в L3 (канарейка).

## 6. Итоговое решение

- **Отключать память HARNESS нельзя.** RAGRAF не является её заменой: он отвечает
  на другие вопросы и не хранит эпизоды.
- **Заменять HARNESS на RAGRAF нельзя.** Это разные единицы знания и разные
  доказательства.
- **Дополнять — нужно, но на уровне связей и выдачи, а не переписыванием данных.**
  HARNESS остаётся L1, RAGRAF — L2, новый `agent-knowledge` — L3, а координатор
  запросов сливает их с явным provenance.
- **Практический минимум:** оставить включённый `session-query-sqlite`, не
  включать второй `:memory:`-индекс, добавить `knowledge_search` и связи
  `episode → code fact`.

---

## 7. Опорные факты и ссылки

**HARNESS (0.2.1-rc.2):**

- конфигурация: `~/.dsh/profiles/desktop/cordis.patch.yml`, строки 218–236 —
  `path: !!js dshHomePath('cache/session-search.db')`, `openAt: first-search`;
- пакеты: `dsh-session`, `dsh-session-query`, `dsh-session-query-sqlite`,
  `dsh-tool-session-query`, `dsh-experimental-session-search`;
- извлечение текста: `dsh-session-query` 0.2.1-rc.2, `extractSessionEventText`
  (user/assistant/tool/todo/turn-end);
- схема FTS: `persisted_docs` / `live_docs`, `unicode61`, без векторов;
- experimental bundle: отдельный `:memory:`-индекс, пять read-only инструментов,
  выключен в Web/Desktop.

**RAGRAF:**

- три хранилища: `docs/02_architecture/ARCHITECTURE.md` §4;
- граф: `src/ragraf/store.py:186-333` (`node`, `edge`, `db_*`, `web_route`,
  `text_chunk`, `cfg_edge`, `dfg_edge`);
- лексика/вектор: `src/ragraf/store.py:336-394` (`ft_chunk`, `chunk_map`,
  `chunk_text`);
- гибридный поиск: `src/ragraf/facade.py:2445` (`op_search`, RRF, `path:line`);
- инвариант «карта и `.ragraf` вне корпуса»: `AGENTS.md` §4, п. 1, 15;
- операции и доказательства: `docs/02_architecture/ARCHITECTURE.md` §4, §6.
