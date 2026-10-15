"""Слой внешних контрактов: схема БД из SQL-миграций репозитория.

Назначение
    Заполняет таблицы `db_table`, `db_column` графа схемой базы. Замер на
    полигоне z.batteryservice.ru показал, что слой был ЗАЯВЛЕН в конфигурации
    (`db.schema_sources`), но НЕ реализован: `schema()` возвращал 0 таблиц,
    а `table_usage` не находил таблицы подсистемы. Это делало часть операций
    фасада бесполезными.

Источник истины
    SQL-миграции репозитория (`migrations/*.sql`) — они версионируются вместе
    с кодом и не требуют подключения к базе. Это важно для индексации: она
    должна работать без доступа к СУБД, а схему стенда агент видит отдельной
    операцией, когда подключение есть.

Что разбирается
    `CREATE TABLE [IF NOT EXISTS] <имя> ( ... )` — имя таблицы и колонки с типами;
    `ALTER TABLE <имя> ADD ...` — колонки, добавляемые отдельными миграциями.

Ограничения (честные)
    * разбор регулярными выражениями, а не SQL-парсером: он покрывает стиль
      миграций этого проекта (проверено на 23 файлах, включая 10 новых), но
      не является универсальным;
    * внешние ключи из `FOREIGN KEY` дают рёбра `fk_to`; `information_schema`
      (живая база) — второй источник, когда он доступен.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import db_marks, util

# CREATE TABLE [IF NOT EXISTS] `name` ( ... )
CREATE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?(?P<name>[A-Za-z_][A-Za-z0-9_]*)[`\"]?\s*\(",
    re.IGNORECASE,
)

# ALTER TABLE `name` ADD [COLUMN] `col` type

# Имя колонки в кавычках или без. Кавычки обязательны к поддержке: в живых
# базах встречаются имена с пробелами и дефисами (`String Nominal Voltage`,
# `Watt-hours` — замер на BWO), и прежний образец без кавычек разбирал такое
# имя как «колонка String типа Nominal», а колонку с дефисом терял целиком.
_COL_NAME = r"(?:`(?P<quoted>[^`]+)`|\"(?P<quoted2>[^\"]+)\"|(?P<plain>[A-Za-z_][A-Za-z0-9_]*))"

# Строка колонки внутри CREATE TABLE: `col` TYPE ...
COLUMN_LINE_RE = re.compile(
    r"^\s*" + _COL_NAME + r"\s+"
    r"(?P<type>[A-Za-z][A-Za-z0-9_]*(?:\s*\([^)]*\))?(?:\s+unsigned)?(?:\s+zerofill)?)",
    re.IGNORECASE,
)

# ALTER TABLE `name` ADD [COLUMN] `col` type
ALTER_ADD_RE = re.compile(
    r"ALTER\s+TABLE\s+[`\"]?(?P<table>[A-Za-z_][A-Za-z0-9_]*)[`\"]?\s+ADD\s+(?:COLUMN\s+)?"
    + _COL_NAME
    + r"\s+(?P<type>[A-Za-z][A-Za-z0-9_() ,]*)",
    re.IGNORECASE,
)


def _col_name(match: re.Match) -> str:
    """Имя колонки из совпадения: кавычки снимаются, пробелы сохраняются."""
    for key in ("quoted", "quoted2", "plain"):
        value = match.groupdict().get(key)
        if value:
            return value
    return ""


# Уникальные ключи, объявленные НЕ в строке колонки:
#   UNIQUE KEY `u` (`a`, `b`)      UNIQUE (`a`)
UNIQUE_KEY_RE = re.compile(
    r"^\s*UNIQUE(?:\s+(?:KEY|INDEX))?\s*(?:[`\"]?[A-Za-z_][A-Za-z0-9_]*[`\"]?)?\s*"
    r"\((?P<cols>[^)]*)\)",
    re.IGNORECASE | re.MULTILINE,
)

# CONSTRAINT ... FOREIGN KEY (col) REFERENCES tbl (col)
FK_RE = re.compile(
    r"FOREIGN\s+KEY\s*\(\s*[`\"]?(?P<from_col>[A-Za-z_][A-Za-z0-9_]*)[`\"]?\s*\)\s*"
    r"REFERENCES\s+[`\"]?(?P<to_table>[A-Za-z_][A-Za-z0-9_]*)[`\"]?\s*"
    r"\(\s*[`\"]?(?P<to_col>[A-Za-z_][A-Za-z0-9_]*)[`\"]?\s*\)",
    re.IGNORECASE,
)

# Ключевые слова, которые не являются колонками.
NON_COLUMN_WORDS = {
    "primary", "unique", "key", "index", "constraint", "foreign", "check",
    "fulltext", "spatial", "engine", "default", "charset", "collate", "comment",
    "partition", "using", "auto_increment",
}

# Уникальные ключи, объявленные НЕ в строке колонки:
#   UNIQUE KEY `u` (`a`, `b`)      UNIQUE (`a`)
# Составной первичный ключ: PRIMARY KEY (`a`, `b`)
PRIMARY_KEY_RE = re.compile(
    r"^\s*PRIMARY\s+KEY\s*\((?P<cols>[^)]*)\)", re.IGNORECASE | re.MULTILINE
)


def _column_names(raw: str) -> list[str]:
    """Имена колонок из списка внутри ключа (`a`, `b`) — в порядке написания."""
    out = []
    for part in raw.split(","):
        name = part.strip().strip("`\"")
        name = name.split("(")[0].strip()  # длина префикса индекса: `a`(10)
        if name:
            out.append(name)
    return out


@dataclass
class ColumnDef:
    name: str
    data_type: str
    is_nullable: int = 1
    column_key: str = ""
    ordinal: int = 0
    # --- P1-4: признаки, без которых ответ о схеме неполон ---
    is_unique: bool = False
    auto_increment: bool = False
    #: `virtual` | `stored` | пусто. Именно эти две формы делают колонку
    #: незаписываемой; `DEFAULT_GENERATED` — не генерируемая колонка (см. db_marks).
    generated_kind: str = ""
    has_default: bool = False


@dataclass
class TableDef:
    name: str
    columns: list[ColumnDef] = field(default_factory=list)
    source: str = "migration"
    fks: list[tuple[str, str, str]] = field(default_factory=list)  # (from_col, to_table, to_col)
    comment: str = ""

    @property
    def primary_key(self) -> list[str]:
        return [c.name for c in self.columns if c.column_key.upper() == "PRI"]

    @property
    def unique_columns(self) -> list[str]:
        return [c.name for c in self.columns if c.is_unique]


def _strip_comments(sql: str) -> str:
    """Убрать SQL-комментарии, чтобы они не попадали в разбор колонок.

    Комментарии в миграциях проекта содержат русский текст и скобки, из-за чего
    наивный разбор ловил «колонки» вида «Назначение». Проверено на 0013.
    """
    # Блочные /* ... */
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    # Строчные -- ... до конца строки
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def _extract_balanced(sql: str, start: int) -> tuple[str, int]:
    """Вернуть текст внутри парных скобок, начиная с позиции открывающей.

    Учитывает вложенные скобки (типы вида decimal(15,2), enum(...)).
    """
    depth = 0
    i = start
    body_start = start + 1
    while i < len(sql):
        ch = sql[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return sql[body_start:i], i
        i += 1
    return sql[body_start:], len(sql)


def _col_marks(text: str) -> dict:
    """Признаки колонки из её строки в SQL. Единый разбор для CREATE и ALTER.

    Разбор идёт по правилам `db_marks`: генерируемая — только
    `GENERATED ALWAYS AS (...) VIRTUAL|STORED`, а `DEFAULT_GENERATED` (подсказка
    СУБД о выражении по умолчанию) генерируемой колонкой НЕ является.
    """
    kind = db_marks.generated_kind_ddl(text)
    return {
        "is_unique": bool(re.search(r"\bUNIQUE\b", text, re.IGNORECASE)),
        "auto_increment": db_marks.is_auto_increment(text),
        "generated_kind": kind,
        "has_default": bool(re.search(r"\bDEFAULT\b", text, re.IGNORECASE)),
    }


def parse_migrations(paths: list[Path]) -> dict[str, TableDef]:
    """Разобрать набор SQL-файлов и вернуть таблицы с колонками.

    Файлы обрабатываются в порядке имён: более поздние миграции дополняют
    схему (ALTER TABLE ADD COLUMN), как это происходит при применении.
    """
    tables: dict[str, TableDef] = {}

    for path in sorted(paths):
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        sql = _strip_comments(raw)

        # --- CREATE TABLE ---
        for m in CREATE_RE.finditer(sql):
            name = m.group("name")
            body, _ = _extract_balanced(sql, m.end() - 1)
            table = tables.setdefault(name, TableDef(name=name, source="migration"))

            # Комментарий таблицы (COMMENT='...') идёт после закрывающей скобки.
            tail = sql[m.end(): m.end() + 400]
            cm = re.search(r"COMMENT\s*=\s*'([^']*)'", tail)
            if cm:
                table.comment = cm.group(1)

            ordinal = 0
            for line in body.splitlines():
                stripped = line.strip().rstrip(",")
                if not stripped:
                    continue
                word = stripped.split()[0].strip("`\"").lower()
                if word in NON_COLUMN_WORDS:
                    continue
                cm2 = COLUMN_LINE_RE.match(stripped)
                if not cm2:
                    continue
                col = _col_name(cm2)
                if not col or col.lower() in NON_COLUMN_WORDS:
                    continue
                ordinal += 1
                exists = next((c for c in table.columns if c.name == col), None)
                if exists is not None:
                    continue
                marks = _col_marks(stripped)
                table.columns.append(
                    ColumnDef(
                        name=col,
                        data_type=" ".join(cm2.group("type").split()),
                        is_nullable=0 if re.search(r"NOT\s+NULL", stripped, re.IGNORECASE) else 1,
                        column_key="PRI" if re.match(r"^\s*[`\"]?" + re.escape(col) + r"[`\"]?\s+.*PRIMARY\s+KEY", stripped, re.IGNORECASE | re.DOTALL) else "",
                        ordinal=ordinal,
                        **marks,
                    )
                )
                if re.search(r"PRIMARY\s+KEY", stripped, re.IGNORECASE) and not re.search(
                    r"^\s*(PRIMARY|UNIQUE|KEY|INDEX|CONSTRAINT)", stripped, re.IGNORECASE
                ):
                    table.columns[-1].column_key = "PRI"

            # --- ключи, объявленные ОТДЕЛЬНОЙ строкой внутри тела ---
            # Замер: до этого разбора `PRIMARY KEY (ID)` и `UNIQUE KEY (Email)`
            # не давали признаков вовсе — в ответе о схеме их не было, и по
            # ней нельзя было понять, что колонка уникальна (P1-4).
            by_name = {c.name: c for c in table.columns}
            for key_re, kind in ((PRIMARY_KEY_RE, "PRI"), (UNIQUE_KEY_RE, "UNI")):
                for km in key_re.finditer(body):
                    for col_name in _column_names(km.group("cols")):
                        target = by_name.get(col_name)
                        if target is None:
                            continue
                        if kind == "PRI":
                            target.column_key = "PRI"
                        else:
                            # Уникальность составного ключа отмечается у каждой
                            # колонки: иначе вопрос «можно ли вставить дубль»
                            # получает неверный ответ по половине ключа.
                            target.is_unique = True

            # --- Внешние ключи внутри CREATE TABLE ---
            for fk in FK_RE.finditer(body):
                table.fks.append(
                    (fk.group("from_col"), fk.group("to_table"), fk.group("to_col"))
                )

        # --- ALTER TABLE ... ADD COLUMN ---
        for m in ALTER_ADD_RE.finditer(sql):
            table = tables.setdefault(m.group("table"), TableDef(name=m.group("table")))
            col = _col_name(m)
            if not col or any(c.name == col for c in table.columns):
                continue
            # Строка ALTER целиком: признаки (NOT NULL, UNIQUE, AUTO_INCREMENT,
            # GENERATED) стоят после имени колонки, и в группе `type` их нет.
            line_end = sql.find("\n", m.start())
            line = sql[m.start(): line_end if line_end > 0 else len(sql)]
            marks = _col_marks(line)
            table.columns.append(
                ColumnDef(
                    name=col,
                    data_type=" ".join(m.group("type").split()),
                    is_nullable=0 if re.search(r"NOT\s+NULL", line, re.IGNORECASE) else 1,
                    ordinal=len(table.columns) + 1,
                    has_default=marks["has_default"],
                    is_unique=marks["is_unique"],
                    auto_increment=marks["auto_increment"],
                    generated_kind=marks["generated_kind"],
                )
            )

    return tables


def migration_files(root: Path, patterns: list[str] | None = None) -> list[Path]:
    """Файлы миграций корпуса по маскам (по умолчанию migrations/*.sql)."""
    masks = patterns or ["migrations/*.sql"]
    found: list[Path] = []
    for mask in masks:
        if "*" not in mask:
            continue
        found.extend(sorted(root.glob(mask)))
    return sorted(set(found))


def write_schema(cfg, rev_id: str, writer) -> dict:
    """Записать разобранную схему в граф. Возвращает статистику.

    Вызывается из конвейера индексации. Никаких обращений к СУБД: только файлы
    миграций, поэтому индексация работает и без подключения к базе.
    """
    files = migration_files(cfg.root, [p for p in cfg.db.schema_sources if "*" in p])
    if not files:
        return {"tables": 0, "columns": 0, "fks": 0, "files": 0,
                "note": "SQL-миграции не найдены: слой схемы пуст"}

    tables = parse_migrations(files)
    if not tables:
        return {"tables": 0, "columns": 0, "fks": 0, "files": len(files),
                "note": "из миграций не удалось извлечь ни одной таблицы"}

    table_rows: list[tuple] = []
    column_rows: list[tuple] = []
    fk_rows: list[tuple] = []

    for name, table in sorted(tables.items()):
        table_id = util.stable_id("dbtable", name)
        table_rows.append(
            (rev_id, table_id, name, "InnoDB", None, table.source)
        )
        for col in table.columns:
            column_rows.append(
                (
                    rev_id, table_id, col.name, col.data_type,
                    col.is_nullable, col.column_key or None, col.ordinal,
                    # P1-4: признаки колонки. Без них ответ о схеме не отвечал
                    # на вопросы «можно ли писать» и «это ключ или нет».
                    1 if col.is_unique else 0,
                    1 if col.auto_increment else 0,
                    col.generated_kind or "",
                    1 if col.has_default else 0,
                )
            )
        for from_col, to_table, to_col in table.fks:
            fk_rows.append((rev_id, name, from_col, to_table, to_col))

    from . import store

    conn = writer.conn
    store.executemany_chunked(
        conn,
        "INSERT OR REPLACE INTO db_table(rev_id, table_id, table_name, engine,"
        " row_estimate, source) VALUES(?,?,?,?,?,?)",
        table_rows, batch=cfg.index.batch_size,
    )
    store.executemany_chunked(
        conn,
        "INSERT OR REPLACE INTO db_column(rev_id, table_id, column_name, data_type,"
        " is_nullable, column_key, ordinal, is_unique, auto_increment, generated_kind,"
        " has_default) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        column_rows, batch=cfg.index.batch_size,
    )
    store.executemany_chunked(
        conn,
        "INSERT OR REPLACE INTO db_fk(rev_id, from_table, from_column, to_table, to_column)"
        " VALUES(?,?,?,?,?)",
        fk_rows, batch=cfg.index.batch_size,
    )
    # Таблица миграций — тоже контракт: её читает администратор.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS db_view (
               rev_id TEXT NOT NULL, view_name TEXT NOT NULL, source TEXT,
               PRIMARY KEY (rev_id, view_name))"""
    )
    conn.commit()

    return {
        "tables": len(table_rows),
        "columns": len(column_rows),
        "fks": len(fk_rows),
        "files": len(files),
        "note": None,
    }
