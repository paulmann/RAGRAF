# Проверка: включённый session-search в HARNESS

> **Дата:** 23.10.2026, после первого поиска в UI.
> **Версия HARNESS:** `@deepseek-ai/dsh-desktop-runtime` 0.2.1-rc.2.
> **Что проверено:** конфигурация, код пакета, фактическая БД после первого
> поиска, покрытие, размер и скорость FTS-запросов.

---

## 1. Что подтверждено

| Факт | Значение |
|---|---|
| Backend включён в профиле `desktop` | `~/.dsh/profiles/desktop/cordis.patch.yml`, строки 232–236: `id: session-query-sqlite`, `path: !!js dshHomePath('cache/session-search.db')`, `openAt: first-search` |
| Патч заменяет `config` целиком | комментарий в том же файле, строки 230–231 |
| История сессий | 181 файл, ~210,1 МБ |
| Производная БД создана | `~/.dsh/cache/session-search.db`, 256 778 240 байт (~244,9 МиБ), `LastWriteTime` 23.10.2026 14:05:55 |
| Режим SQLite | `journal_mode = wal`, `user_version = 8`, `integrity_check = ok` |
| Пакет и схема | asar: `/dsh/package.json` → `0.2.1-rc.2`; `dsh-session-query-sqlite/lib/index.js` |

**Вывод:** первый поиск действительно построил персистентный индекс; это не
`:memory:` и не пустая заглушка.

## 2. Что именно индексирует backend

По коду `dsh-session-query` (`extractSessionEventText`):

- `user/message` — текст пользователя;
- `assistant/message` — текст ассистента (reasoning-блоки **не** попадают);
- `tool/call` — имя инструмента и аргументы;
- `tool/result` — содержимое результата, имя/код ошибки;
- `todo/write` — статусы и текст задач;
- `turn/end` — причина ошибки/аборта/лимита;
- структурные события (`turn/start`, `step/*`, `assistant/attempt`,
  `request/header`) текста не дают.

Схема FTS5 (`lib/index.js`, строки 106–116):

```sql
CREATE VIRTUAL TABLE persisted_docs USING fts5(
  text,
  session_id UNINDEXED,
  seq UNINDEXED,
  type UNINDEXED,
  time UNINDEXED,
  surface UNINDEXED,
  codepoint_length UNINDEXED,
  tokenize = 'unicode61'
);
```

Плюс `persisted_sessions` (заголовки), `temp.live_docs` / `temp.live_sessions`
для живых сессий и `search_state` (поколение корпуса). Ранжирование: число
подсвеченных совпадений, затем длина документа, затем время/seq; числового score
в ответе нет.

## 3. Фактическое покрытие

На момент проверки:

| Показатель | Значение |
|---|---:|
| `persisted_sessions` | 177 |
| `persisted_docs` | 90 602 |
| сессий с хотя бы одним документом | 173 |
| `search_state.global_generation` | 1 |
| `persisted_sessions.agent_preset`: `standard` / `NULL` | 168 / 9 |

Документы по типам событий:

| Тип | Документов |
|---|---:|
| `tool/result` | 31 196 |
| `tool/call` | 30 873 |
| `assistant/message` | 25 592 |
| `user/message` | 2 704 |
| `todo/write` | 156 |
| `turn/end` | 81 |

Распределение по длине текста:

| Длина | Документов | Символов |
|---|---:|---:|
| `< 100` | 14 271 | 1 021 538 |
| `100…1k` | 48 225 | 19 670 308 |
| `1k…10k` | 25 621 | 71 209 573 |
| `>= 10k` | 2 485 | 57 782 048 |

Итого ≈ **149,7 млн символов** текста; индекс занимает ~245 МиБ, то есть
**больше исходных 210 МБ сессий**. Причина — FTS5 хранит токены, позиции и
вспомогательные таблицы; крупные `tool/result` дают заметный вклад.

**Про 4 сессии из 181, которых нет в `persisted_sessions`:** это не потеря
данных. Живые сессии индексируются в `temp.live_docs` и не попадают в файл
`persisted_sessions`; часть файлов изменилась после снимка индекса (например,
`session-0a13da68…` обновлена в 14:11, позже `LastWriteTime` БД). При следующем
поиске backend сверяет ревизии и добавляет изменения.

## 4. Скорость

Замеры на этой БД (read-only, один процесс, без прогрева кэша ОС до первого
запроса):

| Запрос | Совпадений | Время |
|---|---:|---:|
| `mysql` (count) | 4 899 | 5,2 мс |
| `поиск` (count) | 1 928 | 0,5 мс |
| `session` (count) | 3 936 | 0,4 мс |
| `deploy` (count) | 1 892 | 0,2 мс |
| `поиск`, limit 10, 5 повторов | 10 строк | 0,1–0,3 мс |

Это не полноценный бенчмарк UI-поиска (он добавляет сериализацию, доступ и
сортировку), но показывает: FTS-часть быстрая, узкое место — не SQLite.

## 5. Что это значит

1. **HARNESS L1 работает и пригоден для агентного поиска.** 90 тыс. документов,
   177 сессий, миллисекундные запросы.
2. **Индекс не бесплатный.** ~245 МиБ на 210 МБ истории. Это ещё один аргумент
   не включать второй `:memory:`-индекс из
   `dsh-experimental-session-search`: он удвоит память и потеряется после
   перезапуска.
3. **4 «пропавшие» сессии — это live/updated, а не баг.** Проверять покрытие
   нужно по `persisted_sessions` + live-сессиям, а не по числу файлов.
4. **Для L3 этого достаточно.** Эпизоды можно строить по `session_id` + `seq` +
   `type` + `time`; полный текст L3 не нужен.

## 6. Отдельно: experimental session-search tools

Пакет `@deepseek-ai/dsh-experimental-session-search` — **другой** бандл:
пять read-only инструментов для агента и собственный ленивый `:memory:`-индекс.
Он не использует персистентный backend из `cordis.patch.yml`; после перезапуска
индекс пересобирается, а память занята вторым индексом.

**Решение:** пока не включать. Если нужен поиск из чата — нужен один инструмент
поверх того же персистентного `ctx.sessionQuery`, а не второй провайдер. Это
зафиксировано в ADR `docs/02_architecture/ADR-AGENT-KNOWLEDGE-L3.md`.

## 7. Что это значит для RAGRAF

- HARNESS остаётся L1 (эпизоды), RAGRAF — L2 (код). Общей БД нет.
- Связывать их нужно на уровне запроса (`knowledge_search`), а не переносом
  сессий в корпус RAGRAF.
- Контракт координатора: `docs/03_implementation/KNOWLEDGE_SEARCH_CONTRACT.md`.
- Решение по слоям: `docs/02_architecture/ADR-AGENT-KNOWLEDGE-L3.md`.

## 8. Как повторить проверку

```powershell
Get-Item "$env:USERPROFILE\.dsh\cache\session-search.db" |
  Select-Object FullName, Length, LastWriteTime
```

```python
import sqlite3, os
p = os.path.expandvars(r"%USERPROFILE%\.dsh\cache\session-search.db")
con = sqlite3.connect("file:" + p.replace("\\", "/") + "?mode=ro", uri=True)
cur = con.cursor()
for table in ("persisted_sessions", "persisted_docs", "search_state"):
    print(table, cur.execute(f"select count(*) from {table}").fetchone()[0])
print("user_version", cur.execute("pragma user_version").fetchone()[0])
print("integrity", cur.execute("pragma integrity_check").fetchone()[0])
con.close()
```

Ожидаемо: `persisted_sessions` ≤ числа файлов сессий (живые не в файле),
`persisted_docs` — десятки тысяч, `user_version = 8`, `integrity_check = ok`.

## 9. Ограничения проверки

- Числа — снимок на 23.10.2026 14:05–14:11; при новых сессиях и поисках они
  растут.
- Скорость замерена SQL-запросами, а не полным UI-поиском; UI добавляет
  авторизацию, сериализацию и сортировку.
- `dsh --profile desktop --dump-config` через CLI на этой сборке не дал вывода
  (профиль desktop запускает приложение, а не печатает конфиг). Источник истины —
  `cordis.patch.yml` и код пакета, они проверены.
