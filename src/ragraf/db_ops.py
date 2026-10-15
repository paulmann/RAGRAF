"""Операции слоя баз: выборка из индекса для агента.

Что отвечают операции
    * `db_schema`    — что в базе: базы, таблицы, колонки, типы, комментарии;
    * `db_table`     — одна таблица подробно: колонки, индексы, ключи, связи,
      пример, домены значений, скрытые колонки;
    * `db_relations` — связи таблицы с РАЗДЕЛЕНИЕМ на факт и вывод по именам;
    * `db_sample`    — собранный пример строк с происхождением и пометкой сокрытия;
    * `db_drift`     — что изменилось с прошлой ревизии;
    * `db_cross`     — сверка «код ↔ база»: чего не хватает с обеих сторон.

Контракт ответа тот же, что у остальных операций фасада: конверт с ревизией,
режимом, происхождением и ссылками. Ссылка здесь — полное имя объекта
(`база.схема.таблица.колонка`), потому что «строка файла» у базы отсутствует,
но адрес объекта обязан быть точным: без него факт нельзя проверить.

Про вывод и факт
    Связь, выведенная по имени колонки, отдаётся ОТДЕЛЬНЫМ списком и с
    достоверностью не выше 0,5. Смешать её с фактом из внешнего ключа значило бы
    вернуть агенту догадку под видом структуры — в этом проекте уже был ложный
    `full`, из-за которого клиент получал ошибку вместо честного отказа.
"""

from __future__ import annotations

import json
from typing import Any

from . import db_marks, store, util


def _rows(conn, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _has_layer(conn) -> bool:
    return store.table_exists(conn, "db_live_table")


def meta(conn, rev_id: str) -> dict[str, Any] | None:
    rows = _rows(conn, "SELECT * FROM db_live_meta WHERE rev_id = ?", (rev_id,))
    if not rows:
        return None
    row = rows[0]
    if row.get("notes"):
        try:
            row["notes"] = json.loads(row["notes"])
        except Exception:
            row["notes"] = [str(row["notes"])]
    return row


def schema(conn, rev_id: str, table: str | None = None,
           database: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Таблицы с колонками. Фильтры — по имени таблицы и базы."""
    if not _has_layer(conn):
        return []
    sql = ("SELECT table_id, database_name, table_name, kind, engine, row_estimate, "
           "data_bytes, index_bytes, comment FROM db_live_table WHERE rev_id = ?")
    params: list[Any] = [rev_id]
    if table:
        # Регистр не учитывается: имена таблиц в живой базе приходят в нижнем
        # регистре (`testconfig`), а агент спрашивает так, как написано в коде
        # (`TestConfig`), — точное сравнение давало бы «таблицы нет».
        sql += " AND lower(table_name) = lower(?)"
        params.append(table)
    if database:
        sql += " AND database_name = ?"
        params.append(database)
    sql += " ORDER BY database_name, table_name LIMIT ?"
    params.append(limit)
    out: list[dict[str, Any]] = []
    for row in _rows(conn, sql, tuple(params)):
        columns = _rows(
            conn,
            "SELECT column_name, data_type, nullable, default_value, column_key, "
            "extra, comment, ordinal, sensitive, sensitive_reason "
            "FROM db_live_column WHERE rev_id = ? AND table_id = ? ORDER BY ordinal",
            (rev_id, row["table_id"]))
        indexes = _rows(
            conn,
            "SELECT index_name, unique_flag, index_type, columns FROM db_live_index "
            "WHERE rev_id = ? AND table_id = ? ORDER BY index_name",
            (rev_id, row["table_id"]))
        for index in indexes:
            try:
                index["columns"] = json.loads(index.get("columns") or "[]")
            except Exception:
                index["columns"] = []
        row["columns"] = columns
        row["indexes"] = indexes
        # P1-4: признаки колонки собираются в ОДНОМ месте и по одному правилу.
        # «Генерируемая» — только VIRTUAL/STORED GENERATED; DEFAULT_GENERATED —
        # это выражение по умолчанию, а не генерируемый столбец (подстрочный
        # поиск «GENERATED» давал 92 ложных запрета записи из 171 на живой базе).
        unique_from_index: set[str] = set()
        for index in indexes:
            if not index.get("unique_flag"):
                continue
            for col in index.get("columns") or []:
                if isinstance(col, str):
                    unique_from_index.add(col.lower())
        for col in columns:
            col.update(
                db_marks.marks(
                    col.get("extra"), col.get("column_key"),
                    unique_index=str(col.get("column_name", "")).lower() in unique_from_index,
                )
            )
        row["primary_key"] = [c["column_name"] for c in columns if c["pk"]]
        row["unique_columns"] = [c["column_name"] for c in columns if c["unique"]]
        row["generated_columns"] = [c["column_name"] for c in columns if c["generated"]]
        row["auto_increment_columns"] = [
            c["column_name"] for c in columns if c["auto_increment"]]
        # Скрытые колонки называются отдельно: агент должен видеть, что поле есть,
        # но значение не собиралось, и почему.
        row["sensitive_columns"] = [
            {"column": c["column_name"], "reason": c.get("sensitive_reason") or ""}
            for c in columns if c.get("sensitive")]
        out.append(row)
    return out


def table_detail(conn, rev_id: str, table: str,
                 database: str | None = None) -> dict[str, Any] | None:
    rows = schema(conn, rev_id, table=table, database=database, limit=5)
    if not rows:
        return None
    row = rows[0]
    row["relations"] = relations(conn, rev_id, table)
    return row


def relations(conn, rev_id: str, table: str) -> dict[str, Any]:
    """Связи таблицы: объявленные и выведенные РАЗДЕЛЬНО.

    Разделение обязательно: объявленный внешний ключ — факт, а совпадение имён —
    вывод. Если отдать их одним списком, агент не отличит проверенное от
    предположения, и вывод о влиянии правки будет неверным.
    """
    if not _has_layer(conn):
        return {"stated": [], "derived": []}
    # База источника берётся ИЗ САМОЙ таблицы связей, без соединения с таблицей
    # таблиц. Причина: имена таблиц повторяются в разных базах (`contracts` есть
    # и в `bsdocs`, и в `z_bsdocs_test`), и соединение по одному имени давало
    # декартово произведение — измерено: 16 «фактов» вместо 4, каждый по 4 раза.
    rows = _rows(
        conn,
        "SELECT from_table, from_column, to_database, to_table, to_column, "
        "kind, confidence, note, from_database FROM db_live_relation "
        "WHERE rev_id = ? AND (from_table = ? OR to_table = ?) "
        "ORDER BY kind, from_database, from_table, from_column, to_table",
        (rev_id, table, table))
    stated = [r for r in rows if r["kind"] == "stated"]
    derived = [r for r in rows if r["kind"] != "stated"]
    return {"stated": stated, "derived": derived,
            "note": ("«stated» — объявлено в базе; «derived» — вывод по имени "
                     "колонки, достоверность не выше 0,5")}


def sample(conn, rev_id: str, table: str,
           database: str | None = None) -> dict[str, Any] | None:
    """Пример строк из индекса. Пометка устаревания обязательна.

    Пример — снимок данных на момент индексации. Выдавать его за текущее
    состояние нельзя: строки меняются, а структура — нет. Об этом сказано в
    каждом ответе, потому что агент иначе начнёт ссылаться на пример как на факт.
    """
    if not _has_layer(conn):
        return None
    node = None
    for row in _rows(conn, "SELECT table_id, database_name, table_name FROM "
                           "db_live_table WHERE rev_id = ? AND table_name = ?",
                     (rev_id, table)):
        if database and row["database_name"] != database:
            continue
        node = row
        break
    if node is None:
        return None
    # Текст примера лежит в лексике: там он и собирается, отдельной таблицы под
    # примеры нет, чтобы не дублировать хранилище.
    return {
        "database": node["database_name"], "table": node["table_name"],
        "path": f"db://{node['database_name']}/{node['table_name']}#sample",
        "note": ("пример — снимок на момент индексации: строки меняются, "
                 "структура нет. Структуру смотрите в db_table"),
        "how_to_read": ("полный текст примера: операция search по этому пути или "
                        "code с entry_ids из поиска"),
    }


def drift(conn, rev_id: str, previous_rev: str) -> dict[str, Any]:
    """Что изменилось между ревизиями: таблицы, колонки, связи.

    Сравнение идёт по структуре, а не по данным: изменение строк здесь не
    показывается намеренно — данные не входят в отпечаток структуры.
    """
    if not _has_layer(conn):
        return {"from": previous_rev, "to": rev_id, "tables_added": [],
                "tables_removed": [], "columns_added": [], "columns_removed": [],
                "relations_added": [], "relations_removed": []}

    # Сравниваются ДВЕ РЕВИЗИИ: первая редакция запрашивала обе по текущей и
    # всегда показывала пустой список изменений — это ловится проверкой на
    # сдвинутом отпечатке.
    now = {str(r[0]) for r in conn.execute(
        "SELECT database_name || '.' || table_name FROM db_live_table WHERE rev_id = ?",
        (rev_id,)).fetchall()}
    before = {str(r[0]) for r in conn.execute(
        "SELECT database_name || '.' || table_name FROM db_live_table WHERE rev_id = ?",
        (previous_rev,)).fetchall()}
    now_cols = {str(r[0]) for r in conn.execute(
        "SELECT t.database_name || '.' || t.table_name || '.' || c.column_name "
        "FROM db_live_column c JOIN db_live_table t ON t.table_id = c.table_id "
        "AND t.rev_id = c.rev_id WHERE c.rev_id = ?", (rev_id,)).fetchall()}
    before_cols = {str(r[0]) for r in conn.execute(
        "SELECT t.database_name || '.' || t.table_name || '.' || c.column_name "
        "FROM db_live_column c JOIN db_live_table t ON t.table_id = c.table_id "
        "AND t.rev_id = c.rev_id WHERE c.rev_id = ?", (previous_rev,)).fetchall()}
    now_rel = {str(r[0]) for r in conn.execute(
        "SELECT from_table || '.' || from_column || '→' || to_table || '.' || "
        "COALESCE(to_column,'') FROM db_live_relation WHERE rev_id = ?",
        (rev_id,)).fetchall()}
    before_rel = {str(r[0]) for r in conn.execute(
        "SELECT from_table || '.' || from_column || '→' || to_table || '.' || "
        "COALESCE(to_column,'') FROM db_live_relation WHERE rev_id = ?",
        (previous_rev,)).fetchall()}
    return {
        "from": previous_rev, "to": rev_id,
        "tables_added": sorted(now - before)[:200],
        "tables_removed": sorted(before - now)[:200],
        "columns_added": sorted(now_cols - before_cols)[:300],
        "columns_removed": sorted(before_cols - now_cols)[:300],
        "relations_added": sorted(now_rel - before_rel)[:200],
        "relations_removed": sorted(before_rel - now_rel)[:200],
    }


def migrations_vs_live(conn, rev_id: str, *, limit: int = 300) -> dict[str, Any]:
    """Сверка «схема из миграций ↔ живая база» (P1-4).

    Зачем отдельная сверка. `db_drift` сравнивает две РЕВИЗИИ живой базы, а
    `db_cross` — живую базу с именами таблиц ИЗ КОДА. Вопроса «миграции проекта
    описывают таблицу, которой в базе ещё нет» не закрывал никто, а он
    первый при выкатке: миграция написана, код уже пишет в таблицу, база её
    не знает.

    Направление сверки называется явно:
      * `only_in_migrations` — таблица описана миграцией, но в живой базе её
        НЕТ (миграция не применена либо корпус и база — от разных стендов);
      * `only_live` — таблица есть в базе, но не описана миграциями (создана
        руками, или миграции в корпус не попали);
      * `columns_only_in_migrations` / `columns_only_live` — то же по колонкам
        для таблиц, которые есть с обеих сторон.

    Пустая живая схема — НЕ «всё совпало»: это отсутствие данных для сверки, и
    ответ говорит об этом полем `plausible=false`, а не зелёным результатом.
    """
    from . import store as _store

    mig_tables = {
        str(r[0]).lower() for r in conn.execute(
            "SELECT table_name FROM db_table WHERE rev_id = ?", (rev_id,)).fetchall()
    }
    mig_cols: dict[str, set[str]] = {}
    for r in conn.execute(
        "SELECT t.table_name, c.column_name FROM db_column c"
        " JOIN db_table t ON t.rev_id = c.rev_id AND t.table_id = c.table_id"
        " WHERE c.rev_id = ?",
        (rev_id,),
    ).fetchall():
        mig_cols.setdefault(str(r[0]).lower(), set()).add(str(r[1]).lower())

    live_tables = {
        str(r[0]).lower() for r in conn.execute(
            "SELECT table_name FROM db_live_table WHERE rev_id = ?", (rev_id,)).fetchall()
    }
    live_cols: dict[str, set[str]] = {}
    if _store.table_exists(conn, "db_live_column"):
        for r in conn.execute(
            "SELECT t.table_name, c.column_name FROM db_live_column c"
            " JOIN db_live_table t ON t.rev_id = c.rev_id AND t.table_id = c.table_id"
            " WHERE c.rev_id = ?",
            (rev_id,),
        ).fetchall():
            live_cols.setdefault(str(r[0]).lower(), set()).add(str(r[1]).lower())

    shared = sorted(mig_tables & live_tables)
    cols_mig_only: list[dict] = []
    cols_live_only: list[dict] = []
    for name in shared:
        for col in sorted(mig_cols.get(name, set()) - live_cols.get(name, set())):
            cols_mig_only.append({"table": name, "column": col})
        for col in sorted(live_cols.get(name, set()) - mig_cols.get(name, set())):
            cols_live_only.append({"table": name, "column": col})

    return {
        "compared": True,
        "plausible": bool(mig_tables) and bool(live_tables),
        "migrations_tables": len(mig_tables),
        "live_tables": len(live_tables),
        "shared_tables": len(shared),
        "only_in_migrations": sorted(mig_tables - live_tables)[:limit],
        "only_live": sorted(live_tables - mig_tables)[:limit],
        "columns_only_in_migrations": cols_mig_only[:limit],
        "columns_only_live": cols_live_only[:limit],
        "note": (
            ""
            if mig_tables and live_tables
            else "сверка неполна: пусто на одной из сторон "
                 "(миграций нет либо живая схема не собрана) — пустой список "
                 "расхождений здесь ничего не доказывает"
        ),
    }


def cross_check(conn, rev_id: str, code_tables) -> dict[str, Any]:
    """Сверка «код ↔ база»: чего не хватает с обеих сторон И кто что делает.

    Что изменилось против первой редакции и почему. Раньше операция возвращала
    два списка ИМЁН: «имя есть в коде, таблицы нет в базе» и «таблица есть в
    базе, кода не касается». Имя без доказательства бесполезно: агент не может
    проверить, откуда оно взялось (`SELECT` в комментарии и настоящий `INSERT`
    выглядят одинаково), не видит, ГДЕ таблицу читают и пишут, и не знает, что
    где-то имя таблицы собирается во время выполнения (`FROM $table`) — то есть
    что статический разбор там бессилен. Поэтому теперь каждое имя приходит с
    источником (`edge` — факт разбора кода, `text` — текстовый образец), видом
    доступа, доказательством `path:line` и текстом конструкции.

    `code_tables` — либо словарь фактов (имя в нижнем регистре → факт), либо
    множество имён: множество принимается для совместимости, но тогда ответ
    не сможет назвать доказательства.

    База сравнения — ДВА независимых источника, и оба нужны: живая схема
    (`db_live_*`) отвечает «как есть сейчас», схема из миграций (`db_table`)
    — «как задумано». Таблица, которая есть только в миграциях, НЕ является
    отсутствующей: её ещё не применили. Это разные утверждения, и раньше они
    были смешаны.
    """
    facts = _normalize_code_tables(code_tables)
    if not _has_layer(conn):
        return {"used_missing": [], "unused": [], "checked": 0, "checked_live": 0,
                "checked_migrations": 0, "plausible": False,
                "note": "живой слой баз для этой ревизии не собран: сверять не с чем"}

    live_rows = _rows(
        conn,
        "SELECT table_name, COALESCE(database_name, '') AS db,"
        " COALESCE(row_estimate, 0) AS rows_est, COALESCE(data_bytes, 0) AS bytes"
        " FROM db_live_table WHERE rev_id = ?", (rev_id,))
    live: dict[str, list[dict]] = {}
    for r in live_rows:
        key = str(r["table_name"]).lower()
        live.setdefault(key, []).append({
            "name": str(r["table_name"]), "database": str(r["db"]),
            "rows": int(r["rows_est"] or 0), "bytes": int(r["bytes"] or 0),
            "source": "live",
        })
    migrations: dict[str, dict] = {}
    if store.table_exists(conn, "db_table"):
        for r in _rows(conn, "SELECT table_name, COALESCE(source, '') AS src"
                             " FROM db_table WHERE rev_id = ?", (rev_id,)):
            migrations.setdefault(str(r["table_name"]).lower(), {
                "name": str(r["table_name"]), "database": "", "rows": 0, "bytes": 0,
                "source": f"migrations:{r['src']}" if r["src"] else "migrations",
            })

    known = set(live) | set(migrations)
    used = set(facts)
    matched = used & known

    def evidence_for(fact: dict) -> list[dict]:
        """Доказательства факта: сначала рёбра (факт разбора), потом текст."""
        out: list[dict] = []
        for e in fact["edges"][:3]:
            out.append({"path": e["path"], "line": e["line"], "via": "edge",
                        "access": e.get("access") or "", "sql": e.get("sql") or ""})
        for t in fact["text"][:2]:
            out.append({"path": t["path"], "line": t["line"], "via": "text",
                        "access": "", "sql": t.get("sql") or ""})
        return out

    used_missing = []
    for key in sorted(used - known):
        fact = facts[key]
        access = sorted({str(e.get("access") or "") for e in fact["edges"] if e.get("access")})
        used_missing.append({
            "name": fact["name"],
            "sources": (["edge"] if fact["edges"] else []) + (["text"] if fact["text"] else []),
            "access": access,
            "evidence": evidence_for(fact),
        })

    unused = []
    # Базы, с которыми код РАБОТАЕТ: те, где нашлось хотя бы одно совпадение.
    # Без этого в «неиспользуемые» попадали таблицы чужой базы: на BWO живой
    # слой собирает несколько баз (`wizard_online` и `bsdocs`), и `domofon`,
    # `1c`, `ozon_update` из `bsdocs` выглядели как «код их не касается», хотя
    # код этого проекта к `bsdocs` вообще не обращается. Такие строки остаются
    # в списке (удалять их — решение владельца), но помечаются
    # `database_in_use: false` и уходят в конец.
    databases_in_use = {
        info["database"]
        for key in matched
        for info in (live.get(key) or [migrations.get(key) or {}])
        if info.get("database")
    }
    for key in sorted(set(live) | set(migrations)):
        if key in used:
            continue
        info = (live.get(key) or [migrations[key]])[0]
        db_name = str(info.get("database", ""))
        unused.append({
            "name": info["name"], "database": db_name,
            "rows": info.get("rows", 0), "bytes": info.get("bytes", 0),
            "source": info.get("source", ""),
            "database_in_use": (not db_name) or (db_name in databases_in_use),
        })
    # Сначала базы, с которыми код работает, затем самые крупные: «мёртвая»
    # таблица на 12 ГБ важнее пустой заглушки.
    unused.sort(key=lambda x: (not x["database_in_use"],
                               -int(x["bytes"] or 0), -int(x["rows"] or 0), x["name"]))

    # Кто ПИШЕТ. Это ответ на вопрос «таблица перестала заполняться»: у таблицы
    # есть обращения, но среди них нет ни insert/update/delete.
    write_paths: dict[str, list[dict]] = {}
    for key in sorted(matched):
        fact = facts[key]
        writes = [
            {"path": e["path"], "line": e["line"], "access": e.get("access") or "",
             "sql": e.get("sql") or ""}
            for e in fact["edges"]
            if str(e.get("access") or "").lower() in ("insert", "update", "delete", "ddl")
        ]
        if writes:
            write_paths[fact["name"]] = writes[:5]

    # Расхождение ТОЛЬКО по регистру: `StringData` в коде и `stringdata` в базе.
    # Это не «таблицы нет», но и не полное совпадение — SQL на Windows-базе
    # может не найти таблицу, и агент обязан это видеть.
    case_mismatch = [
        {"code": facts[key]["name"], "db": (live.get(key) or [migrations[key]])[0]["name"]}
        for key in sorted(matched)
        if facts[key]["name"] != (live.get(key) or [migrations[key]])[0]["name"]
    ]

    overlap = sorted(matched)
    plausible = bool(overlap)
    note = ("«used_missing» — имя встречается в коде, но таблицы нет НИ в живой базе, "
            "НИ в миграциях; «unused» — таблица есть, а код её не касается (отсортировано "
            "по размеру: крупные сверху); «write_paths» — кто пишет")
    if not plausible:
        note = ("ВНИМАНИЕ: ни одно имя таблицы из базы не встречается в коде — "
                "похоже, база принадлежит ДРУГОМУ проекту. В таком случае оба "
                "списка ниже ничего не доказывают. Сначала убедитесь, что "
                "подключена база того же проекта, что и корпус.")
    return {
        "used_missing": used_missing[:200],
        "unused": unused[:200],
        "checked": len(known),
        "checked_live": len(live),
        "checked_migrations": len(migrations),
        "checked_code_names": len(facts),
        "overlap": overlap[:200],
        "overlap_count": len(overlap),
        "databases_in_use": sorted(databases_in_use),
        "unused_in_use_databases": sum(1 for u in unused if u["database_in_use"]),
        "write_paths": dict(list(write_paths.items())[:60]),
        "write_tables": len(write_paths),
        "case_mismatch": case_mismatch[:100],
        "plausible": plausible,
        "note": note,
    }


def _normalize_code_tables(code_tables) -> dict[str, dict]:
    """Факты по именам таблиц из кода: словарь как есть, множество — обёрткой."""
    if isinstance(code_tables, dict):
        out: dict[str, dict] = {}
        for key, value in code_tables.items():
            if isinstance(value, dict):
                out[str(key).lower()] = {
                    "name": str(value.get("name") or key),
                    "edges": list(value.get("edges") or []),
                    "text": list(value.get("text") or []),
                }
            else:
                out[str(key).lower()] = {"name": str(value), "edges": [], "text": []}
        return out
    return {str(t).lower(): {"name": str(t), "edges": [], "text": []}
            for t in (code_tables or []) if str(t).strip()}



def summary(conn, rev_id: str) -> dict[str, Any]:
    """Краткая сводка слоя: числа и состояние сбора."""
    info = meta(conn, rev_id)
    if info is None:
        return {"collected": False,
                "note": "слой баз для этой ревизии не собран"}
    return {"collected": True, **info}
