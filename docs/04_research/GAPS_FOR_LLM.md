# Чего не хватает RAGRAF для качественной разработки кода LLM-агентом

**Дата:** 5 октября 2026 г. · **Корпус:** `C:\AutoClaw\z.batteryservice.ru`, ревизия индекса `rev-0006`, `capability=full`
**Область:** 17 операций фасада (`src/ragraf/facade.py`), схемы таблиц графа, телеметрия `usage_call`.
**Исходные данные:** `docs/03_implementation/POLYGON_REPORT.md` (10 дефектов), живой прогон операций.

---

## 1. Проверенные факты

### Ф1. `op_symbol` возвращает сигнатуру **вместе с типами**

Команда (из задания, п.5):

```powershell
python -c "import sys;sys.path.insert(0,'src');from ragraf.config import load;from ragraf.facade import Facade;f=Facade(load('config/z-batteryservice.yaml'));import json;print(json.dumps(f.op_symbol('z_agr_can_act_by_matrix'),ensure_ascii=False,indent=2));f.close()"
```

Фактический ответ (сокращённо, поле `result[0]`):

```json
{
  "kind": "function",
  "name": "z_agr_can_act_by_matrix",
  "fqn": "z_agr_can_act_by_matrix",
  "path": "src/Agreements/AccessMatrix.php",
  "start_line": 201,
  "end_line": 225,
  "signature": "function z_agr_can_act_by_matrix(string $action, string $journal): bool",
  "props": { "namespace": "", "class": null, "body_sha": "b804d7a446500ed7" }
}
```

**Вывод.** Для этой функции ответ **достаточен** для машинного построения вызова: типы (`string`), порядок (`$action`, `$journal`), обязательность (значений по умолчанию нет), возвращаемый тип (`bool`) — всё есть. Сигнатура извлекается tree-sitter'ом как дословный срез от начала узла до `{` (`src/ragraf/extract/php_ast.py:687-692`), поэтому PHP-типы в ней сохраняются.

### Ф2. Покрытие сигнатур по корпусу (341 символ)

| Показатель | Значение |
|---|---|
| Символов вида `function`/`method`/`class` | **341** |
| Без сигнатуры | **0** |
| Обрезанных (с «…») | **0** |
| Максимальная длина сигнатуры | **181** символ (`agreements/edit.php`, `z_agr_select`) |
| Сигнатур длиной ≥ 200 символов | **0** |
| Функций без параметров | 70 |
| Функций со **всеми** типизированными параметрами | **265** |
| Функций с хотя бы одним **нетипизированным** параметром | **2** |

Оба случая — в `lib/html.php`, и там это нетипизированный `$handle` (файловый хэндл): `function LoadVar($handle, string $startPattern): string`.

**Вывод.** Лимит `_collapse(sig, 200)` (`src/ragraf/extract/php_ast.py:690-692`) на этом корпусе **не срабатывает ни разу** — запас до обрезки 19 символов у худшей сигнатуры. Но запас тонкий: достаточно одного метода в классе с 7–8 параметрами, чтобы сигнатура исказилась молча и незаметно (без ошибки, с «…» в середине списка параметров).

### Ф3. Настоящий информационный провал — не типы параметров, а **состав массива**

Для функции, на которой возник TypeError (дефект №8), `op_symbol` даёт:

```json
"signature": "function z_agr_acl_audit(array $data): void"
```

Сигнатура полна и точна — и **бесполезна для построения вызова**. Тип `array` не сообщает, какие ключи обязательны. Тело (`src/Agreements/Access.php:769-809`) показывает:

* обязателен только `action` (без него функция молча возвращает управление);
* остальные ключи опциональны: `user_id`, `target_user_id`, `field`, `from`, `to`, `reason`, `actor_id`;
* `actor_id` по умолчанию берётся из `z_agr_uid()`.

**Ключевая находка.** Ровно эти сведения лежат в docblock'е **непосредственно над функцией** (`src/Agreements/Access.php:751-758`), в psalm-формате:

```php
/**
 * @param array{action:string, user_id?:int, target_user_id?:int, field?:string,
 *              from?:?string, to?:?string, reason?:string, actor_id?:int} $data
 */
function z_agr_acl_audit(array $data): void
```

RAGRAF этот docblock **проиндексировал** — он лежит в `text_chunk` строк 759-768, `text_kind='docblock'`, и находится поиском (`op_search('array{action')` → `src/Agreements/Access.php:769`). Но:

```text
docblock 759-768 owner_node_id = 066afd29ce75a66ff69a2b911170c35344fe3191
node 066afd29…  = kind='file', name='Access.php'
node z_agr_acl_audit = 5a0696cd3af30b6d8cb9585bb0f8747af54b0214
```

**Docblock привязан к узлу ФАЙЛА, а не к функции.** По корпусу: `docblock` → owner `file` — **371**, owner `class` — 32, owner `function`/`method` — **0**. Контракт параметров в индексе есть, но по `symbol`/`code` **недостижим**: нет ни одной операции, которая по имени функции отдала бы docblock над ней.

### Ф4. `table_usage` теряет признак чтение/запись и отдаёт часть ложных таблиц

Признак `write` вычисляется при извлечении (`src/ragraf/extract/php_ast.py:568`: `"write": bool(SQL_WRITE_RE.search(raw))`), но:

* рёбра к таблицам создаются **без** него (`php_ast.py:572`: `_edge(container, "@table:{tbl}", "queries", "stated", line, 0.8)`);
* `op_table_usage` выбирает только `src, kind, provenance, evidence_path, evidence_line, confidence` (`src/ragraf/facade.py:742-746`);
* таблица `sql_literal` в графе **отсутствует вовсе** (`sqlite_master`: `meta, rev, file, node, edge, text_chunk, cfg_edge, dfg_edge, db_table, db_column, db_fk, web_route, invalidated, coverage, stage_timing, db_view`), поэтому ветка `op_table_usage` по `text_kind='sql_literal'` (`facade.py:769-776`) **всегда возвращает пустой список**.

Регулярка имени таблицы `SQL_TABLE_RE = \b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+[`"']?([A-Za-z_][A-Za-z0-9_]*)` (`php_ast.py:93-95`) — **безусловная**: слово `TABLE` матчится и в HTML-разметке, и в `CREATE TABLE`. Подтверждено: в графе есть рёбра на несуществующие таблицы `@table:value`, `@table:hash`, `@table:domains`, `@table:createdat`, `@table:accesslevel`, `@table:fromcompany`, `@table:ugroup` — их нет ни среди 44 разобранных `db_table`, ни среди реальных `Settings`/`DocSetting` (проверено содержимым `admin/settings.php:20-23` — там `'INSERT INTO Settings (`Key`, `Value`, `Comment`)'`, и `src/Agreements/Setting.php:348-352` — `'INSERT INTO DocSetting (KeyName, Value, …)'`).

### Ф5. Контракт «операция знает схему таблицы» есть, но между операциями не связан

```text
op_schema(table='DocTasks') → found=true, 29 колонок: ID No Status DepartmentID InitiatorID CommentText … DeletedAt DeletedBy
op_table_usage('DocTasks')  → found=false, references=0, sql_literals=0
```

При `found=false` `op_table_usage` не содержит ключа `extra`/`suggest` в ответе (хотя ветка `facade.py:778-779` её формирует) — агент видит пустоту без подсказки, что делать.

Колонки подтверждают дефект №7: у `DocTasks` есть `InitiatorID` и **нет** `UpdatedBy`; во всём графе `UpdatedBy` есть только у `DocSetting` (`DocDepartment` в графе имеет колонки `ID, ParentID, Name, ShortName, Path, Sort, HeadUserID, Active, CreatedAt, UpdatedAt` — то есть **отчёт и граф расходятся**, см. §5).

### Ф6. Телеметрия знает только факт вызова, не результат работы агента

`usage_call` (`src/ragraf/telemetry.py:109-126`) хранит: `op, rev_id, capability, duration_ms, found, fact_count, facts_with_span, result_chars, est_tokens, replaces, error, tool`. **Аргумента запроса нет.**

**Следствие.** Дефект №7 в телеметрии **не виден никак**: агент мог вызвать `schema`/`table_usage` перед правкой и мог не вызвать — обе ситуации дают одну запись `{op, found}`. Гипотезу «операция X предотвращает дефект» на данных полигона проверить **невозможно**: `op=table_usage` не различает аргумент `DocApproval` и `value`.

### Ф7. Фактическая поверхность у агента — 16 инструментов, не 17

`src/ragraf/mcp_server.py` объявляет **16** инструментов; `op_calibrate` через MCP не отдан. Для LLM-агента пункт `calibrate` фактически отсутствует.

---

## 2. Таблица «дефект → нужная операция → что она даёт»

| № | Дефект (POLYGON_REPORT) | Какая операция/поле предотвратили бы его **до** запуска кода | Статус в текущей сборке |
|---|---|---|---|
| 1 | Метрика векторного поиска: score=0.000 (векторы не нормированы) | Операция диагностики индекса: `index_status` с полем `vector_metric: {model, norms_p50, cos_min, cos_max, samples}` | Нет поля: `index_status` отдаёт `vector_available`, `lexvec`, `embedding_model` (`facade.py:303-310`), но не распределение норм/оценок |
| 2 | RRF без весов поднимал шум | Не предотвращается операцией: дефект формулы. Косвенно — `search` с разбивкой вклада: `score_parts: {lex, vec, penalty}` | Нет: `search` отдаёт только `score` и `why` строкой (`facade.py:585`) |
| 3 | Чанкинг процедурного PHP: 1 чанк на весь файл (до 11 331 токена) | Операция контроля качества чанкинга: `index_status` → `chunks: {max_tokens, count_over_budget, files_without_symbols}` | Нет: в `index_status` размеров чанков нет |
| 4 | Слой схемы БД не реализован (`schema()` → 0 таблиц) | Операция `schema` + поле `source` у таблицы с явной пустотой (`found=false, error`) | **Сделано**: 44 таблицы, `source='migration'` (`schema_db.py:write_schema`) |
| 5 | `table_usage`/`repo_map` не отдавали плоские `path`/`line` | `table_usage` → плоские `path`, `line`; `repo_map` → `path` | **Сделано** (`facade.py:757-775`) |
| 6 | Права расширены: роль `employee` получала `approve`/`mark_paid`/`sign`/`archive` | Трассировка матрицы прав: «для символа, решающего действие, верни все ветви, дающие доступ, и по каждой — файл:строка» | **Нет.** `callers`/`slice` дают граф вызовов, но не матрицу «роль × действие» из кода |
| 7 | `AuthorUserID`/`UpdatedBy` не существуют в журналах (42S22) | **`write_plan(table, columns[])` → `{ok, unknown_columns, nullable, missing_required}`** — сверка списка колонок с индексом схемы **до** выполнения | **Нет.** `schema('DocTasks')` даёт все 29 колонок, но операции «проверь мой список колонок» нет; сверку каждый раз делает человек глазами. Это и названо в §9 отчёта |
| 8 | `z_agr_acl_audit()` принимает массив, вызвали 4 аргументами (TypeError) | **`symbol(name, include='params')` → машинный список `{name, type, optional, default}` + `array_shape` из docblock'а** | **Частично.** Типы и порядок есть в `signature` (Ф1), **состава массива — нет** (Ф3) |
| 9 | Обязательное поле с пустым справочником делало форму неотправляемой молча | Операция «источник данных справочника — прочитай фактическое содержимое»: `schema`+маршрут наполняющего запроса + признак «справочник пуст» | **Нет.** Это дефект уровня данных, не структуры: `schema`/`table_usage` не говорят, что вернёт наполняющий запрос на стенде |
| 10 | Слой схемы нерабочий → нечем проверить имена колонок | То же, что №4: `schema` | **Сделано** (44 таблицы / 453 колонки) |

**Сводка.** Структурой индекса закрыты 3 (№4, №5, №10), закрываемы малой доработкой 3 (№1, №3, №7), требуют новой операции/поля 2 (№6, №8-array), не решается навигацией по коду 1 (№9), 1 — дефект алгоритма (№2).

---

## 3. Предлагаемые улучшения

Форма: **«операция X с параметрами Y возвращает Z»**. Данные для реализации уже лежат в графе — речь не о переписывании RAGRAF.

### U1 (P0, малая). Сверка плана записи со схемой — закрывает №7

* **Операция:** `schema` в режиме проверки (или `write_check`), MCP-инструмент `ragraf_write_check`.
* **Вход:** `{table: str, columns: [str], operation: "insert"|"update"}`.
* **Выход:** `{ok: bool, table_found: bool, unknown_columns: [{name, nearest, distance}], nullable_columns: [...], not_null_without_value: [...], primary_key: [...]}`.
* **Чем подтверждается польза:** предотвращает №7 напрямую — `AuthorUserID` и `UpdatedBy` отсутствуют у `DocTasks`; сверка дала бы `ok=false, unknown_columns=[AuthorUserID, UpdatedBy]` **до** запуска, вместо ошибки драйвера `42S22` при первом сохранении. Данные есть: `db_table`/`db_column` отдают 29 колонок `DocTasks`, `column_key`/`is_nullable` разбираются (`schema_db.py:ColumnDef`).
* **Дополнение:** вернуть ключ `suggest` наружу в `op_table_usage` (сейчас при `found=false` он не доходит до агента, §Ф5).

### U2 (P0, малая). Привязка docblock к символу и разбор array-shape — закрывает №8

* **Операция:** `symbol(name, include="docblock")` → доп. поля `doc` и разобранный `array_shape`.
* **Вход:** имя символа (как сейчас), параметр `include`.
* **Выход:** к `result[0]` добавляется
  ```json
  {"doc": {"text": "...", "params": {"data": {"shape": {"action": {"type":"string","required":true},
      "user_id": {"type":"int","required":false}}}}}}
  ```
  для `z_agr_acl_audit` — ровно то, что лежит в `Access.php:751-758`.
* **Чем подтверждается польза:** предотвращает №8. Агент собрал бы вызов `z_agr_acl_audit(['action'=>'create','target_user_id'=>5])`, а не 4 позиционных аргумента. Технически: docblock уже в `text_chunk` (`chunk_id='b7404525fe3ad2936f55d562c4d8c461aab906a5'`, строки 759-768) и находится поиском; нужна привязка по позиции (docblock с наибольшим `end_line` ≤ `node.start_line` в том же `path`) — правило то же, что уже применяется для комментариев внутри функций (`comment` привязан к `function` в 250 случаях).
* **Источник данных — по надёжности:** (1) psalm/PHPDoc `array{...}` — есть в корпусе (37 чанков с `array{`); (2) tree-sitter типы из `signature` — есть для 265 из 267 функций с параметрами; (3) живого стенда/PHP-рефлексии — нет, индексация обязана работать без СУБД (`schema_db.py`).

### U3 (P1, малая). Полнота сигнатуры: не обрезать молча

* **Операция:** та же `symbol`; правка — поле `signature_params: [{name, type, by_ref, variadic, default, optional}]` рядом с текстовой `signature` + флаг `signature_truncated: bool`.
* **Вход:** без изменений.
* **Выход:** машинный список параметров (порядок сохранён), независимый от лимита 200 символов.
* **Чем подтверждается польза:** страхует №8 от второго сценария — обрезка по 200 символам (`php_ast.py:690-692`) сейчас не срабатывает (максимум 181, §Ф2), но она **молчаливая** и попадает ровно в середину списка параметров, т.е. даёт вызов с перепутанным порядком. Узел `formal_parameters` уже доступен — `_signature` считается от того же узла.

### U4 (P1, малая). Разделение чтение/запись в `table_usage`

* **Операция:** `table_usage`; добавить признак в рёбра и вернуть его в каждом факте.
* **Вход:** `{table, limit, mode?: "read"|"write"|"all"}`.
* **Выход:** каждый факт получает `"write": bool`, `"access": "select"|"insert"|"update"|"delete"|"ddl"`; при `mode="write"` сортировка «сначала запись».
* **Чем подтверждается польза:** признак уже вычисляется и **выбрасывается** (§Ф4): `php_ast.py:568` пишет `write`, ребро на `php_ast.py:572` — нет. Для №7 это даёт «какие вызовы пишут в журнал» — место подстановки `AuthorUserID`/`UpdatedBy`; частично закрывает №9 (писатель с `NOT NULL` — кандидат в обязательные поля).
* **Требует:** сохранить `sql_literal` (таблицы нет, ветка `facade.py:769-776` мертва, §Ф4).

### U5 (P1, средняя). Фильтр ложных таблиц — защита доверия к `table_usage`/`impact`

* **Операция:** `table_usage`, `impact`, `schema` — поле `unresolved_tables`.
* **Вход:** без изменений (либо `{strict: true}`).
* **Выход:** таблицы, на которые есть рёбра, но которых нет в `db_table` и в живой схеме, выносятся отдельным списком `unresolved_tables: [{name, evidence: "path:line"}]`, а не смешиваются с подтверждёнными.
* **Чем подтверждается польза:** сейчас 7 из 42 таблиц с рёбрами — фантомы, порождённые безусловным `TABLE\s+` (`php_ast.py:93-95`), который ловит `<table>` в HTML и `CREATE TABLE` в DDL. `op_table_usage('value')` возвращает `admin/settings.php:21` как «потребителя таблицы value», хотя это `'INSERT INTO Settings (`Key`, `Value`, `Comment`)'`. Без фильтра агент может пойти «смотреть таблицу value».

### U6 (P1, средняя). Трассировка матрицы прав — закрывает №6 (тяжелейший по последствиям)

* **Операция:** `policy_trace` (надстройка над `slice`).
* **Вход:** `{symbol: str, action?: str}`, напр. `symbol='z_agr_can_act_by_matrix'`.
* **Выход:** `{roles: [{role, allowed_actions, evidence: "path:line", branch}], default: "deny"|"allow", hard: [...], heuristic: [...]}` — все ветви, возвращающие разрешение, с файлом, строкой и условием.
* **Чем подтверждается польза:** предотвращает №6, который нашла только проверка под реальной ролью, а не замер навигации. Операция даёт факт «роль `employee` встречается в ветви, возвращающей `approve`» из кода. Граф для этого достаточен: `slice('back')` уже строит обратный срез 4 уровня через `calls`/`calls_heuristic` (`facade.py:435-463`); нужен разбор условий возврата внутри тела.

### U7 (P2, средняя). Контроль качества индекса — закрывает №1 и №3

* **Операция:** `index_status` с блоком `quality`.
* **Вход:** без изменений.
* **Выход:** `quality: {vector: {model, norms:{p50,max}, cos_used, cos_min, cos_max}, chunks: {max_tokens, count_over_budget, files_without_symbols, window_size}, signatures: {total, truncated, untyped_params}}`.
* **Чем подтверждается польза:** №1 и №3 — класс дефектов, выглядящих как «пустой результат», а не ошибка (score=0.000; чанк 11 331 токен). Сейчас `index_status` отдаёт `vector_available`, `embedding_model`, `lexvec.counts`, `coverage`, `stages` (`facade.py:303-313`), но **ни одного** показателя качества. По выходу U7 №1 ловится как `cos_max ≈ 0` при `norms.p50 ≈ 3.5`, №3 — как `max_tokens=11331`.

### U8 (P2, малая). Телеметрия аргумента

* **Операция:** запись в `usage_call` (не фасад): колонки `arg_hash` (хеш нормализованного первичного аргумента, напр. `table_usage:docapproval`) и `arg_digest` (короткий человекочитаемый вид).
* **Вход/выход:** генерируется автоматически; правка `src/ragraf/telemetry.py:109-126` и `:160-163`.
* **Чем подтверждается польза:** без этого нельзя измерить эффект U1 — сегодня `usage_call` `{op='table_usage', found=0/1}` **не отличает** аргумент `DocApproval` от `value` (§Ф6), поэтому на вопрос «вызывали ли сверку схемы перед правкой этой таблицы» нельзя ответить ни для одного из 10 дефектов.

---

## 4. Что важнее всего реализовать первым

Первым — **U1 (`write_check`: сверка списка колонок со схемой перед записью)**: это единственное улучшение, закрывающее подтверждённый дефект целиком (№7: `AuthorUserID`/`UpdatedBy` → `42S22` при первом сохранении), и все данные для него уже в графе — `db_table`/`db_column` отдают 29 колонок `DocTasks`, включая `InitiatorID` и отсутствие `UpdatedBy`. Вторым — **U2 (привязка docblock к символу и разбор `array{...}`)**: без него `symbol` для `z_agr_acl_audit` навсегда возвращает бесполезное `array $data`, хотя точный контракт ключей уже проиндексирован (`Access.php:751-758`) и теряется только из-за привязки docblock'а к узлу файла (371 из 371). Обе — малой сложности и не требуют переписывания RAGRAF.

---

## 5. Оговорки и расхождения

1. **Типы параметров в `signature` есть** — это противоречит формулировке задания («operation symbol отдаёт сигнатуру текстом, но без типов параметров»). Фактический вывод (Ф1) показывает типы для всех трёх проверенных функций. Настоящий провал — **состав массива** (`array $data`) и **молчаливая обрезка** длинной сигнатуры, а не отсутствие типов. Если под «без типов» имелся в виду другой корпус/ревизия — стоит уточнить отдельно.
2. **Расхождение отчёт ↔ граф:** POLYGON_REPORT §8 п.7 говорит, что `UpdatedBy` есть у `DocDepartment`. В графе `rev-0006` у `DocDepartment` колонки `ID, ParentID, Name, ShortName, Path, Sort, HeadUserID, Active, CreatedAt, UpdatedAt` — `UpdatedBy` **нет**; единственная таблица с `UpdatedBy` — `DocSetting`. Расхождение **не разрешено**: возможны неполнота разбора миграций, разные ревизии или устаревший фрагмент отчёта.
3. **Ревизии:** замеры сделаны на `rev-0006`, а POLYGON_REPORT описывает `rev-0004`. На выводы о структуре операций это не влияет, но числа сверять нужно с `rev-0006` (44 таблицы, в графе есть `db_view`).
4. **Не проверено (бюджет вызовов исчерпан):** состав `usage_call` в `control.sqlite` прочитан из кода `telemetry.py:109-126`, а не из живого `PRAGMA`; не измерена длина вывода `op_code(style='compact')` для `z_agr_can_act_by_matrix` (для `z_agr_acl_audit` `style='signatures'` вернул ровно одну строку 769 с сигнатурой).
