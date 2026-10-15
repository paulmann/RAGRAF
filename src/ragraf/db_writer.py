"""Запись живого слоя баз в индекс: граф, отпечаток структуры, примеры.

Куда что попадает
    * **граф** — структура: базы, таблицы, колонки, индексы, представления,
      связи (с происхождением: факт из внешнего ключа или вывод по имени);
    * **лексика и векторы** — текстовые чанки: описание таблиц, комментарии,
      примеры строк, домены значений. По ним работает поиск по смыслу: «где
      хранятся заявки»;
    * **ревизия** — отпечаток структуры, по которому сторож свежести понимает,
      что база изменилась и нужна переиндексация.

Почему структура и примеры разделены
    Структура — это ФАКТ, она меняется редко и входит в отпечаток. Примеры —
    это снимок данных: они устаревают, не влияют на отпечаток и помечены
    отдельно. Смешать их значило бы переиндексировать корпус при каждой вставке
    строки.

Схема: отдельные таблицы живого слоя
    Живая схема пишется в СОБСТВЕННЫЕ таблицы `db_live_*` (`db_live_table`,
    `db_live_column`, `db_live_index`, `db_live_relation`, `db_live_meta`), а
    `db_meta` хранит снимок отпечатка структуры. Таблицы `db_table`,
    `db_column`, `db_fk` живого слоя НЕ касаются: они принадлежат схеме из
    миграций (`schema_db.py`), и это разделение принципиально — по нему
    `db_drift` умеет сравнивать миграции с живой базой, а не смешивать два
    источника в одном наборе строк. Прежняя редакция этого описания обещала
    «дополнить db_table/db_column/db_fk и добавить db_index» — код так не
    делал и не делает.

Автор: Михаил Дейнекин
Сайт: https://Deynekin.com
Почта: Mikhail@Deynekin.com
"""

from __future__ import annotations

import json
from typing import Any

from . import db_index as index_mod
from . import db_samples as samples_mod
from . import store, util
from .db_index import Database, IntrospectionResult, Table


# Дополнения схемы. Выполняются идемпотентно: повторный запуск ничего не меняет.
DB_LIVE_DDL = """
CREATE TABLE IF NOT EXISTS db_live_table (
  rev_id TEXT NOT NULL, database_name TEXT NOT NULL, table_name TEXT NOT NULL,
  table_id TEXT NOT NULL, kind TEXT, engine TEXT, row_estimate INTEGER,
  data_bytes INTEGER, index_bytes INTEGER, comment TEXT,
  PRIMARY KEY (rev_id, table_id)
);
CREATE INDEX IF NOT EXISTS idx_db_live_table_name
  ON db_live_table(rev_id, table_name);

CREATE TABLE IF NOT EXISTS db_live_column (
  rev_id TEXT NOT NULL, table_id TEXT NOT NULL, column_name TEXT NOT NULL,
  data_type TEXT, nullable INTEGER, default_value TEXT, column_key TEXT,
  extra TEXT, comment TEXT, ordinal INTEGER,
  sensitive INTEGER DEFAULT 0, sensitive_reason TEXT,
  PRIMARY KEY (rev_id, table_id, column_name)
);
CREATE INDEX IF NOT EXISTS idx_db_live_column_name
  ON db_live_column(rev_id, column_name);

CREATE TABLE IF NOT EXISTS db_live_index (
  rev_id TEXT NOT NULL, table_id TEXT NOT NULL, index_name TEXT NOT NULL,
  unique_flag INTEGER, index_type TEXT, columns TEXT,
  PRIMARY KEY (rev_id, table_id, index_name)
);

CREATE TABLE IF NOT EXISTS db_live_relation (
  rev_id TEXT NOT NULL, from_table TEXT NOT NULL, from_column TEXT NOT NULL,
  to_database TEXT, to_table TEXT NOT NULL, to_column TEXT,
  kind TEXT NOT NULL, confidence REAL, note TEXT, from_database TEXT
);
CREATE INDEX IF NOT EXISTS idx_db_live_relation_from
  ON db_live_relation(rev_id, from_table);
CREATE INDEX IF NOT EXISTS idx_db_live_relation_to
  ON db_live_relation(rev_id, to_table);

CREATE TABLE IF NOT EXISTS db_live_meta (
  rev_id TEXT NOT NULL PRIMARY KEY,
  dialect TEXT, via TEXT, host TEXT, port INTEGER,
  databases INTEGER, tables INTEGER, columns_count INTEGER, relations INTEGER,
  views INTEGER, sensitive_columns INTEGER,
  fingerprint TEXT, collected_at TEXT, samples_collected INTEGER DEFAULT 0,
  notes TEXT
);
CREATE TABLE IF NOT EXISTS db_meta (
  database_name TEXT NOT NULL, table_name TEXT NOT NULL, rev_id TEXT NOT NULL,
  charset TEXT, collation TEXT,
  PRIMARY KEY (database_name, table_name, rev_id)
);
"""


def ensure_schema(conn) -> None:
    conn.executescript(DB_LIVE_DDL)
    # Дополняющая миграция: у баз, созданных до появления поля `from_database`,
    # колонки нет, и `INSERT` с ней упал бы. Проверяем и добавляем.
    try:
        columns = {str(r[1]) for r in conn.execute(
            "PRAGMA table_info(db_live_relation)").fetchall()}
        if "from_database" not in columns:
            conn.execute("ALTER TABLE db_live_relation ADD COLUMN from_database TEXT")
        # Индекс создаётся ПОСЛЕ колонки. В DDL его быть не может: у уже
        # существующей базы колонки нет, и `CREATE INDEX` падал бы до миграции —
        # измерено: `no such column: from_database` при каждом запуске.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_db_live_relation_from_db "
            "ON db_live_relation(rev_id, from_database, from_table)")
    except Exception:
        pass
    conn.commit()


def table_id(database: str, table: str) -> str:
    """Устойчивый идентификатор узла таблицы: не зависит от порядка обхода."""
    return util.stable_id("dblive", database, table)


def write_introspection(writer, rev_id: str, result: IntrospectionResult,
                        samples: dict[str, samples_mod.TableSample] | None = None,
                        ) -> dict[str, Any]:
    """Записать структуру живой базы в граф. Возвращает статистику."""
    conn = writer.conn
    ensure_schema(conn)

    # Прежние строки этой ревизии удаляются перед записью. У `db_live_relation`
    # нет первичного ключа (`INSERT OR REPLACE` работает только при ключе), и
    # старые записи оставались бы рядом с новыми — измерено: 16 «фактов» вместо
    # 4, среди них дубли с пустой базой источника. Остальные таблицы имеют
    # первичный ключ, но чистка идёт по всем: пересбор ревизии должен давать
    # ровно то, что собрано сейчас, а не сумму прогонов.
    for table_name in ("db_live_table", "db_live_column", "db_live_index",
                       "db_live_relation", "db_meta"):
        try:
            conn.execute(f"DELETE FROM {table_name} WHERE rev_id = ?", (rev_id,))
        except Exception:
            pass

    table_rows: list[tuple] = []
    column_rows: list[tuple] = []
    index_rows: list[tuple] = []
    relation_rows: list[tuple] = []
    meta_rows: list[tuple] = []
    sensitive = 0

    for database in result.databases:
        for table in database.tables:
            tid = table_id(database.name, table.name)
            table_rows.append((
                rev_id, database.name, table.name, tid, table.kind, table.engine,
                table.row_estimate, table.data_bytes, table.index_bytes, table.comment,
            ))
            meta_rows.append((database.name, table.name, rev_id,
                              database.charset, database.collation))
            for column in table.columns:
                if column.sensitive:
                    sensitive += 1
                column_rows.append((
                    rev_id, tid, column.name, column.data_type,
                    1 if column.nullable else 0, column.default, column.key,
                    column.extra, column.comment, column.ordinal,
                    1 if column.sensitive else 0, column.sensitive_reason,
                ))
            for index in table.indexes:
                index_rows.append((
                    rev_id, tid, index["name"], 1 if index["unique"] else 0,
                    index.get("type", ""), json.dumps(index["columns"], ensure_ascii=False),
                ))
            for rel in table.relations:
                relation_rows.append((
                    rev_id, table.name, rel[0], rel[1], rel[2], rel[3],
                    rel[4], float(rel[5]), rel[6], database.name,
                ))

    store.executemany_chunked(
        conn, "INSERT OR REPLACE INTO db_live_table(rev_id, database_name, "
              "table_name, table_id, kind, engine, row_estimate, data_bytes, "
              "index_bytes, comment) VALUES(?,?,?,?,?,?,?,?,?,?)", table_rows)
    store.executemany_chunked(
        conn, "INSERT OR REPLACE INTO db_live_column(rev_id, table_id, column_name, "
              "data_type, nullable, default_value, column_key, extra, comment, "
              "ordinal, sensitive, sensitive_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        column_rows)
    store.executemany_chunked(
        conn, "INSERT OR REPLACE INTO db_live_index(rev_id, table_id, index_name, "
              "unique_flag, index_type, columns) VALUES(?,?,?,?,?,?)", index_rows)
    store.executemany_chunked(
        conn, "INSERT OR REPLACE INTO db_live_relation(rev_id, from_table, "
              "from_column, to_database, to_table, to_column, kind, confidence, "
              "note, from_database) VALUES(?,?,?,?,?,?,?,?,?,?)", relation_rows)
    store.executemany_chunked(
        conn, "INSERT OR REPLACE INTO db_meta(database_name, table_name, rev_id, "
              "charset, collation) VALUES(?,?,?,?,?)", meta_rows)

    fingerprint = index_mod.schema_fingerprint(result)
    stats = {
        "dialect": result.dialect, "via": result.via,
        "host": result.host, "port": result.port,
        "databases": len(result.databases), "tables": result.tables_total,
        "columns": result.columns_total, "relations": result.relations_total,
        "views": sum(1 for d in result.databases for t in d.tables if t.kind == "view"),
        "sensitive_columns": sensitive,
        "fingerprint": fingerprint, "collected_at": util.now_iso(),
        "samples_collected": len(samples or {}),
        "notes": json.dumps(result.notes[:20], ensure_ascii=False),
    }
    store.executemany_chunked(
        conn, "INSERT OR REPLACE INTO db_live_meta(rev_id, dialect, via, host, port, "
              "databases, tables, columns_count, relations, views, sensitive_columns, "
              "fingerprint, collected_at, samples_collected, notes) "
              "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(rev_id, stats["dialect"], stats["via"], stats["host"], stats["port"],
          stats["databases"], stats["tables"], stats["columns"], stats["relations"],
          stats["views"], stats["sensitive_columns"], stats["fingerprint"],
          stats["collected_at"], stats["samples_collected"], stats["notes"])])
    conn.commit()
    return stats


def structure_text(database: Database, table: Table) -> str:
    """Текст таблицы для поиска по смыслу: структура словами.

    Идёт в лексику и векторы. Заголовок с полным именем важен: агент спрашивает
    «где хранятся заявки», и совпадение должно находиться по имени и назначению,
    а не только по точному имени таблицы.
    """
    keys = [c.name for c in table.columns if c.key]
    sensitive = [c.name for c in table.columns if c.sensitive]
    lines = [
        f"Таблица базы данных: {database.name}.{table.name}",
        f"вид: {'представление' if table.kind == 'view' else 'таблица'}"
        + (f", движок {table.engine}" if table.engine else ""),
    ]
    if table.comment:
        lines.append(f"комментарий: {table.comment}")
    if table.row_estimate:
        lines.append(f"строк (оценка): {table.row_estimate}")
    lines.append("колонки: " + ", ".join(
        f"{c.name} {c.data_type}" + ("" if c.nullable else " NOT NULL")
        for c in table.columns[:60]))
    if keys:
        lines.append("ключи: " + ", ".join(keys))
    if table.indexes:
        lines.append("индексы: " + ", ".join(
            i["name"] + (" (уникальный)" if i["unique"] else "")
            for i in table.indexes[:20]))
    if table.depends_on:
        lines.append("представление основано на: " + ", ".join(table.depends_on[:20]))
    stated = [r for r in table.relations if r[4] == "stated"]
    derived = [r for r in table.relations if r[4] == "derived"]
    if stated:
        lines.append("связи (из внешних ключей): " + ", ".join(
            f"{r[0]} → {r[2]}.{r[3]}" for r in stated[:20]))
    if derived:
        lines.append("связи (вывод по имени, не факт): " + ", ".join(
            f"{r[0]} → {r[2]}" for r in derived[:20]))
    if sensitive:
        lines.append("персональные данные: " + ", ".join(sensitive[:20]))
    return "\n".join(lines)


def write_lexvec(lexvec, embedder, rev_id: str, result: IntrospectionResult,
                 samples: dict[str, samples_mod.TableSample] | None = None,
                 ) -> dict[str, Any]:
    """Записать тексты баз в лексику и векторы.

    Один чанк на таблицу со структурой плюс один чанк с примером, если пример
    собран. Разделение нужно, чтобы поиск по структуре не размывался случайными
    значениями данных.

    Векторы берутся тем же путём, что и у чанков кода: сначала ищутся уже
    посчитанные по хэшу текста (`reusable_vectors`), и только для новых текстов
    вызывается модель. Иначе повторная индексация баз считала бы все векторы
    заново, а это самая дорогая часть прогона.
    """
    from .extract.php_ast import ChunkRec

    chunks: list[ChunkRec] = []
    for database in result.databases:
        for table in database.tables:
            text = structure_text(database, table)
            path = f"db://{database.name}/{table.name}"
            chunks.append(ChunkRec(
                chunk_id=util.stable_id("dbchunk", path),
                source_kind="db-structure", path=path, node_id=None,
                start_line=1, end_line=text.count(chr(10)) + 1, lang="text",
                text=text, text_hash=util.sha256_text(text),
                label=f"{database.name}.{table.name}",
                tokens=max(1, len(text) // 4),
            ))
    for _key, sample in (samples or {}).items():
        text = samples_mod.sample_text(sample)
        path = f"db://{sample.database}/{sample.table}#sample"
        chunks.append(ChunkRec(
            chunk_id=util.stable_id("dbchunk", path),
            source_kind="db-sample", path=path, node_id=None,
            start_line=1, end_line=text.count(chr(10)) + 1, lang="text",
            text=text, text_hash=util.sha256_text(text),
            label=f"{sample.database}.{sample.table} пример",
            tokens=max(1, len(text) // 4),
        ))

    vectors: dict[str, list[float]] = {}
    notes: list[str] = []
    if chunks:
        hashes = [c.text_hash for c in chunks]
        try:
            vectors = lexvec.reusable_vectors(getattr(lexvec.cfg.embedding, "model", ""),
                                             hashes)
        except Exception:
            vectors = {}
        missing = {c.text_hash: c.text for c in chunks if c.text_hash not in vectors}
        if missing and embedder is not None:
            try:
                status = embedder.status()
                if status.available:
                    keys = list(missing.keys())
                    fresh = embedder.embed([missing[k] for k in keys])
                    for key, vector in zip(keys, fresh):
                        vectors[key] = vector
                else:
                    notes.append(f"векторы не собраны: {status.reason}")
            except Exception as exc:
                notes.append(f"векторы не собраны: {type(exc).__name__}")
    written = lexvec.add_chunks(rev_id, chunks, vectors or None)
    return {"chunks": written, "structures": len(chunks) - len(samples or {}),
            "samples": len(samples or {}), "vectors": len(vectors),
            "notes": notes}
