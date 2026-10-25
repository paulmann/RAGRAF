"""Сбор структуры живой базы: базы, таблицы, колонки, индексы, представления, связи.

Почему через CLI-клиенты, а не драйвер
    Python-драйверов СУБД в окружении нет (`pymysql`, `psycopg` отсутствуют), а
    проект держит нулевые зависимости по замыслу — тянуть их ради индексации
    нельзя. Поэтому источник — штатный клиент (`mysql.exe`, `psql.exe`) с машинным
    выводом, а если драйвер всё же есть, он используется: быстрее и без разбора
    текста. Выбор фиксируется в ответе (`via`), как это уже сделано для CPG.

Что собирается
    * базы: имя, кодировка, сопоставление;
    * таблицы: движок, оценка числа строк, размер данных и индексов, комментарий;
    * колонки: тип, длина и точность, допустимость NULL, значение по умолчанию,
      ключ, комментарий, порядок;
    * **индексы** (отдельно от первичного ключа) и **уникальные** ограничения;
    * **представления** и таблицы, от которых они зависят;
    * внешние ключи с правилами (`ON DELETE`/`ON UPDATE`).

Связи шире, чем внешние ключи
    В «живых» базах внешние ключи часто не объявлены, и агент не видит связей
    вообще. Поэтому к фактам добавляется **эвристика по именам**: колонка `*_id`
    сопоставляется с таблицей, у которой подходящее имя. Каждая связь помечается
    происхождением: `stated` (объявлено в базе) или `derived` (выведено по имени),
    и достоверность эвристики не выше 0,5. Выдавать вывод за факт нельзя — в этом
    проекте уже случался ложный `full`.

Только чтение
    Ни один запрос здесь не меняет данные: SELECT к системным таблицам и выборки
    строк. Операций записи, DDL и управления правами нет вовсе.

Автор: Михаил Дейнекин
Сайт: https://Deynekin.com
Почта: Mikhail@Deynekin.com
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import db_access as access_mod
from . import util

# Разделители машинного вывода. Управляющие символы выбраны так, чтобы их не
# содержали имена объектов и комментарии.
FIELD = "\x1f"
ROW = "\x1e"
NULL = "\x00"


@dataclass
class Column:
    name: str
    data_type: str
    length: int | None = None
    precision: int | None = None
    scale: int | None = None
    nullable: bool = True
    default: str = ""
    key: str = ""
    extra: str = ""
    comment: str = ""
    ordinal: int = 0
    # Признак «значение колонки не собирать»: персональные данные и секреты.
    sensitive: bool = False
    sensitive_reason: str = ""


@dataclass
class Table:
    name: str
    database: str
    # Схема внутри базы. Для MySQL пусто (база и схема — одно), для PostgreSQL
    # содержит `public` или имя схемы: без неё таблицу не адресовать.
    schema: str = ""
    kind: str = "table"          # table | view
    engine: str = ""
    row_estimate: int | None = None
    data_bytes: int | None = None
    index_bytes: int | None = None
    comment: str = ""
    columns: list[Column] = field(default_factory=list)
    indexes: list[dict[str, Any]] = field(default_factory=list)
    # (from_column, to_database, to_table, to_column, kind, confidence, note)
    relations: list[tuple] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)


@dataclass
class Database:
    name: str
    charset: str = ""
    collation: str = ""
    tables: list[Table] = field(default_factory=list)


@dataclass
class IntrospectionResult:
    dialect: str = "mysql"
    via: str = ""                # cli | driver
    host: str = ""
    port: int = 0
    databases: list[Database] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def tables_total(self) -> int:
        return sum(len(d.tables) for d in self.databases)

    @property
    def columns_total(self) -> int:
        return sum(len(t.columns) for d in self.databases for t in d.tables)

    @property
    def relations_total(self) -> int:
        return sum(len(t.relations) for d in self.databases for t in d.tables)

    def to_dict(self) -> dict[str, Any]:
        """Компактное представление: без тел, с числами и примерами структуры."""
        return {
            "dialect": self.dialect, "via": self.via,
            "host": self.host, "port": self.port,
            "databases": len(self.databases),
            "tables": self.tables_total,
            "columns": self.columns_total,
            "relations": self.relations_total,
            "views": sum(1 for d in self.databases for t in d.tables
                         if t.kind == "view"),
            "names": [d.name for d in self.databases][:40],
            "errors": self.errors[:20],
            "notes": self.notes[:20],
            "seconds": round(self.seconds, 2),
        }


# ---------------------------------------------------------------------------
# Поиск клиента и запуск команды
# ---------------------------------------------------------------------------


def find_client(dialect: str) -> str:
    """Путь к штатному клиенту СУБД. Пустая строка — клиент не найден.

    Возвращается именно ПУТЬ, а не команда: команду собирает `_base_command`,
    потому что она зависит от диалекта и от выбранной базы. Первая редакция
    возвращала кортеж, и вызовы передавали список там, где нужен путь —
    это ловится первой же пробой на живой базе.
    """
    commands, _reason = _find_client(dialect)
    return commands[0] if commands else ""


def _find_client(dialect: str) -> tuple[list[str], str]:
    """Найти штатный клиент СУБД. Возвращает (команда, путь) или ([], причина)."""
    if dialect == "mysql":
        candidates = [
            Path(r"C:\tools\mysql\current\bin\mysql.exe"),
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "MySQL" /
            "MySQL Server 9.7" / "bin" / "mysql.exe",
        ]
        named = shutil.which("mysql")
        if named:
            candidates.insert(0, Path(named))
    else:
        candidates = [
            Path(r"C:\Program Files\PostgreSQL\18\bin\psql.exe"),
            Path(r"C:\Program Files\PostgreSQL\17\bin\psql.exe"),
        ]
        named = shutil.which("psql")
        if named:
            candidates.insert(0, Path(named))
    for path in candidates:
        if path and path.is_file():
            return [str(path)], str(path)
    return [], (f"клиент {dialect} не найден: установите его или добавьте в PATH")


def _run_cli(command: list[str], env: dict[str, str],
             timeout: int, stdin_text: str = "") -> tuple[str, str, int]:
    """Запустить клиент и вернуть (stdout, stderr, код).

    Значение доступа передаётся ТОЛЬКО через окружение дочернего процесса: так оно
    не попадает ни в список процессов, ни в историю команд.
    """
    try:
        proc = subprocess.run(
            command, input=stdin_text or None, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            env={**os.environ, **env},
        )
        return proc.stdout or "", proc.stderr or "", proc.returncode
    except subprocess.TimeoutExpired:
        return "", f"превышено время ожидания ({timeout} с)", -1
    except Exception as exc:
        return "", f"{type(exc).__name__}: {exc}", -1


def _cli_env(access: access_mod.DbAccess) -> dict[str, str]:
    if access.dialect == "mysql":
        return {"MYSQL_PWD": access.password}
    return {"PGPASSWORD": access.password}


def _base_command(access: access_mod.DbAccess, client: str,
                  database: str = "") -> list[str]:
    """Команда клиента с машинным выводом и без интерактивных эффектов."""
    if access.dialect == "mysql":
        cmd = [client, "-h", access.host, "-P", str(access.port),
               "-u", access.user, "--batch", "--raw", "-N",
               "--default-character-set=utf8mb4"]
        if database:
            cmd += ["-D", database]
        return cmd
    # Разделитель НЕ задаётся флагом. Измерено на двух серверах:
    #   * MySQL 9.7 игнорирует выбранный разделитель и всегда отдаёт табуляцию;
    #   * psql 18 поддерживает `-F`, но управляющий символ в аргументе ломает
    #     разбор: в выводе появлялся разделитель строк внутри значения, и номер
    #     колонки разбирался как «1\x1eимя_таблицы».
    # Поэтому полагаемся на машинный режим без разделителя (`-A` даёт табуляцию),
    # а фактический разделитель определяет `_parse_rows` по содержимому.
    cmd = [client, "-h", access.host, "-p", str(access.port),
           "-U", access.user, "-X", "-q", "-t", "-A"]
    if database:
        cmd += ["-d", database]
    return cmd


def _parse_rows(out: str, delimiter: str = "") -> list[list[str]]:
    """Разобрать машинный вывод в строки полей.

    Разделитель ОПРЕДЕЛЯЕТСЯ ПО ФАКТУ, а не задаётся заранее. Измерено: клиент
    MySQL 9.7 не принимает выбранный разделитель и всегда отдаёт табуляцию,
    поэтому заранее заданный символ давал одну «колонку» на всю строку и список
    баз оказывался пустым. Пробуем известные варианты и берём тот, при котором
    строки распадаются на большее число полей.

    Значение NULL приходит как `\\N` (MySQL) или пустая строка (psql): для нас оба
    означают «нет данных», и это отмечено в поле `default`.
    """
    text = out.replace("\r\n", "\n")
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if not lines:
        return []

    # Кандидаты разделителя, включая `|`: psql в машинном режиме отдаёт
    # именно вертикальную черту, а MySQL — табуляцию. Выбирается тот, при
    # котором строки распадаются на большее число полей.
    candidates = [d for d in (delimiter, FIELD, "\t", "|") if d]
    best: list[list[str]] = []
    best_width = 0
    for candidate in candidates:
        rows = [[("" if cell == r"\N" else cell) for cell in ln.split(candidate)]
                for ln in lines]
        width = max((len(r) for r in rows), default=0)
        if width > best_width:
            best, best_width = rows, width
    return best


# ---------------------------------------------------------------------------
# Чувствительные колонки
# ---------------------------------------------------------------------------

SENSITIVE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("password", "пароль"),
    ("passwd", "пароль"),
    ("pwd", "пароль"),
    ("secret", "секрет"),
    ("token", "токен"),
    ("api_key", "ключ API"),
    ("apikey", "ключ API"),
    ("private_key", "личный ключ"),
    ("ssh_key", "ключ SSH"),
    ("credit_card", "банковская карта"),
    ("card_number", "номер карты"),
    ("cvv", "код карты"),
    ("iban", "банковский счёт"),
    ("passport", "паспорт"),
    ("snils", "СНИЛС"),
    ("inn", "ИНН"),
    ("oauth", "данные авторизации"),
    ("session_id", "идентификатор сессии"),
    ("phone", "телефон"),
    ("mobile", "телефон"),
    ("email", "электронная почта"),
    ("e_mail", "электронная почта"),
    ("birth", "дата рождения"),
    ("birthday", "дата рождения"),
    ("address", "адрес"),
    ("fullname", "ФИО"),
    ("first_name", "имя"),
    ("last_name", "фамилия"),
    ("surname", "фамилия"),
    ("middle_name", "отчество"),
    ("patronymic", "отчество"),
    ("fio", "ФИО"),
    ("login", "учётная запись"),
    ("username", "учётная запись"),
    ("nickname", "имя пользователя"),
    ("skype", "контакт"),
    ("telegram", "контакт"),
    ("whatsapp", "контакт"),
    ("viber", "контакт"),
    ("postal", "почтовый адрес"),
    ("zip", "почтовый индекс"),
    ("seria", "документ"),
    ("nomer", "документ"),
)


def normalize_column(column_name: str) -> str:
    """Привести имя колонки к виду без разделителей и регистра.

    Нужно потому, что имена пишутся тремя способами, и проверка по подстроке их
    не ловит: `FirstName`, `first_name` и `First-Name` — одно и то же поле.
    Измерено на живой базе: без нормализации `FirstName` и `E-Mail` **не** были
    признаны персональными данными, и имя человека попало бы в пример.
    """
    return (column_name or "").lower().replace("_", "").replace("-", "").replace(" ", "")


def classify(column_name: str, data_type: str = "") -> tuple[bool, str]:
    """Персональные данные или секрет? Возвращает признак и причину.

    Проверка идёт по ИМЕНИ колонки: типы данных для этого бесполезны (телефон и
    номер заказа выглядят одинаково). Имя нормализуется (см. `normalize_column`),
    поэтому правило находит `First_Name`, `FirstName` и `first-name`.

    Правило намеренно щедрое: лучше скрыть лишнее, чем вытащить чужое. Причина
    возвращается, чтобы в ответе было видно, почему колонка закрыта.
    """
    low = normalize_column(column_name)
    if not low:
        return False, ""
    # Идентификаторы и названия организаций — не персональные данные: `UserID`
    # это ссылка на строку, а `CompanyName` — название юрлица.
    if low in ("userid", "companyid", "companyname", "customerid", "clientid",
               "groupid", "entityid", "objectid", "parentid", "ownerid",
               "sessionid", "fileid", "contractid", "docid", "tableid"):
        return False, ""
    for needle, reason in SENSITIVE_PATTERNS:
        if needle.replace("_", "") in low:
            return True, reason
    return False, ""


# ---------------------------------------------------------------------------
# MySQL
# ---------------------------------------------------------------------------


def _mysql_databases(access: access_mod.DbAccess, client: str) -> list[tuple[str, str, str]]:
    sql = ("SELECT SCHEMA_NAME, DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME "
           "FROM information_schema.SCHEMATA ORDER BY SCHEMA_NAME;")
    cmd = _base_command(access, client) + ["-e", sql]
    out, err, code = _run_cli(cmd, _cli_env(access), access.timeout_seconds)
    if code != 0 and not out.strip():
        raise RuntimeError(err.strip() or "не удалось получить список баз")
    result = []
    for row in _parse_rows(out):
        if len(row) >= 3 and access.accepts_database(row[0]):
            result.append((row[0], row[1], row[2]))
    return result


def _mysql_tables(access: access_mod.DbAccess, client: str,
                  database: str) -> dict[str, Table]:
    sql = (
        "SELECT TABLE_NAME, TABLE_TYPE, COALESCE(ENGINE,''), "
        "COALESCE(TABLE_ROWS,0), COALESCE(DATA_LENGTH,0), COALESCE(INDEX_LENGTH,0), "
        f"COALESCE(TABLE_COMMENT,'') FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA = '{database}' ORDER BY TABLE_NAME;"
    )
    out, err, code = _run_cli(_base_command(access, client) + ["-e", sql],
                              _cli_env(access), access.timeout_seconds)
    if code != 0 and not out.strip():
        raise RuntimeError(err.strip() or f"не удалось получить таблицы {database}")
    tables: dict[str, Table] = {}
    for row in _parse_rows(out):
        if len(row) < 7:
            continue
        name, table_type = row[0], row[1]
        kind = "view" if "VIEW" in table_type.upper() else "table"
        tables[name] = Table(
            name=name, database=database, kind=kind, engine=row[2],
            row_estimate=int(row[3] or 0), data_bytes=int(row[4] or 0),
            index_bytes=int(row[5] or 0), comment=row[6],
        )
    return tables


def pg_mark_fields(identity: str | None, generated: str | None) -> tuple[str, str]:
    """Ключ и подсказка колонки PostgreSQL по системным признакам.

    Зачем отдельная функция. У MySQL подсказка СУБД (`EXTRA`) и ключ
    (`COLUMN_KEY`) приходят готовыми строками, а у PostgreSQL то же самое надо
    вывести из двух односимвольных полей каталога:

      * `attidentity`: `a` — `GENERATED ALWAYS AS IDENTITY`, `d` — `BY DEFAULT`;
      * `attgenerated`: `s` — `STORED` генерируемая колонка (в PG 18 бывает и
        `v` — виртуальная).

    Без этого преобразования на PostgreSQL не было НИ ОДНОГО из признаков
    `auto_increment`/`generated`: замерено на коде до правки — `_pg_columns`
    заполнял только имя, тип, NULL и DEFAULT, поэтому `write_check` на PG
    считал генерируемые колонки обычными и разрешал в них запись.

    Возврат: (key, extra) — в том же виде, что приходит от MySQL, чтобы
    `db_marks.marks` разбирал оба диалекта ОДНИМ правилом.
    """
    extra_parts: list[str] = []
    if (identity or "").strip():
        # Идентичность — это значение, которое ведёт СУБД: смысл тот же, что
        # у AUTO_INCREMENT, и подача идёт тем же словом, иначе `db_marks`
        # пришлось бы учить второму словарю.
        extra_parts.append("auto_increment")
    kind = (generated or "").strip().lower()
    if kind == "s":
        extra_parts.append("STORED GENERATED")
    elif kind:
        # `v` и любое будущее значение: генерируемая колонка, вид — виртуальный.
        extra_parts.append("VIRTUAL GENERATED")
    return "", " ".join(extra_parts)


def _mysql_columns(access: access_mod.DbAccess, client: str,
                   database: str, tables: dict[str, Table]) -> None:
    sql = (
        "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, "
        "COALESCE(COLUMN_DEFAULT,''), COALESCE(COLUMN_KEY,''), "
        "COALESCE(EXTRA,''), COALESCE(COLUMN_COMMENT,''), "
        "COALESCE(NUMERIC_PRECISION,0), COALESCE(NUMERIC_SCALE,0), "
        "COALESCE(CHARACTER_MAXIMUM_LENGTH,0), ORDINAL_POSITION "
        f"FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = '{database}' "
        "ORDER BY TABLE_NAME, ORDINAL_POSITION;"
    )
    out, _err, _code = _run_cli(_base_command(access, client) + ["-e", sql],
                                _cli_env(access), access.timeout_seconds)
    for row in _parse_rows(out):
        if len(row) < 12:
            continue
        table = tables.get(row[0])
        if table is None or len(table.columns) >= access.max_columns:
            continue
        sensitive, reason = classify(row[1], row[2])
        table.columns.append(Column(
            name=row[1], data_type=row[2], nullable=(row[3].upper() == "YES"),
            default=row[4], key=row[5], extra=row[6], comment=row[7],
            precision=int(row[8] or 0) or None, scale=int(row[9] or 0) or None,
            length=int(row[10] or 0) or None, ordinal=int(row[11] or 0),
            sensitive=sensitive, sensitive_reason=reason,
        ))


def _mysql_indexes(access: access_mod.DbAccess, client: str,
                   database: str, tables: dict[str, Table]) -> None:
    sql = (
        "SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME, "
        "COALESCE(INDEX_TYPE,'') FROM information_schema.STATISTICS "
        f"WHERE TABLE_SCHEMA = '{database}' ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX;"
    )
    out, _err, _code = _run_cli(_base_command(access, client) + ["-e", sql],
                                _cli_env(access), access.timeout_seconds)
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in _parse_rows(out):
        if len(row) < 6:
            continue
        table = tables.get(row[0])
        if table is None:
            continue
        key = (row[0], row[1])
        entry = grouped.setdefault(key, {
            "table": row[0], "name": row[1],
            "unique": row[2] == "0", "type": row[5], "columns": [],
        })
        entry["columns"].append(row[4])
    for (table_name, _idx), data in grouped.items():
        table = tables.get(table_name)
        if table is not None:
            table.indexes.append(data)


def _mysql_relations(access: access_mod.DbAccess, client: str,
                     database: str, tables: dict[str, Table]) -> None:
    sql = (
        "SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, "
        "REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
        f"WHERE TABLE_SCHEMA = '{database}' AND REFERENCED_TABLE_NAME IS NOT NULL "
        "ORDER BY TABLE_NAME, COLUMN_NAME;"
    )
    out, _err, _code = _run_cli(_base_command(access, client) + ["-e", sql],
                                _cli_env(access), access.timeout_seconds)
    for row in _parse_rows(out):
        if len(row) < 4:
            continue
        table = tables.get(row[0])
        if table is not None:
            table.relations.append(
                (row[1], database, row[2], row[3], "stated", 1.0, "внешний ключ"))


def _mysql_view_deps(access: access_mod.DbAccess, client: str,
                     database: str, tables: dict[str, Table]) -> None:
    sql = ("SELECT TABLE_NAME, REFERENCED_TABLE_NAME FROM "
           "information_schema.VIEW_TABLE_USAGE "
           f"WHERE VIEW_SCHEMA = '{database}' ORDER BY TABLE_NAME;")
    out, _err, _code = _run_cli(_base_command(access, client) + ["-e", sql],
                                _cli_env(access), access.timeout_seconds)
    for row in _parse_rows(out):
        if len(row) < 3:
            continue
        table = tables.get(f"{row[2]}.{row[0]}") or tables.get(row[0])
        if table is not None and row[1] not in table.depends_on:
            table.depends_on.append(row[1])


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------


def _pg_databases(access: access_mod.DbAccess, client: str) -> list[tuple[str, str, str]]:
    sql = ("SELECT datname, pg_encoding_to_char(encoding), datcollate "
           "FROM pg_database WHERE NOT datistemplate ORDER BY datname;")
    cmd = _base_command(access, client, "postgres") + ["-c", sql]
    out, err, code = _run_cli(cmd, _cli_env(access), access.timeout_seconds)
    if code != 0 and not out.strip():
        raise RuntimeError(err.strip() or "не удалось получить список баз")
    result = []
    for row in _parse_rows(out):
        if len(row) >= 3 and access.accepts_database(row[0]):
            result.append((row[0], row[1], row[2]))
    return result


def _pg_tables(access: access_mod.DbAccess, client: str,
               database: str) -> dict[str, Table]:
    # Схема читается ИЗ КАТАЛОГА, а не предполагается. Первая редакция
    # подставляла `public`, и таблицы из другой схемы (`ragraf_probe`) не
    # находились: «отношение public.departments не существует» — измерено.
    sql = (
        "SELECT c.relname, CASE c.relkind WHEN 'v' THEN 'VIEW' "
        "WHEN 'm' THEN 'MATERIALIZED VIEW' ELSE 'BASE TABLE' END, "
        "COALESCE(c.reltuples::bigint, 0), "
        "COALESCE(pg_total_relation_size(c.oid), 0), "
        "COALESCE(obj_description(c.oid, 'pg_class'), ''), n.nspname "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relkind IN ('r','v','m') AND n.nspname NOT IN "
        "('pg_catalog','information_schema') ORDER BY n.nspname, c.relname;"
    )
    cmd = _base_command(access, client, database) + ["-c", sql]
    out, err, code = _run_cli(cmd, _cli_env(access), access.timeout_seconds)
    if code != 0 and not out.strip():
        raise RuntimeError(err.strip() or f"не удалось получить таблицы {database}")
    tables: dict[str, Table] = {}
    for row in _parse_rows(out):
        if len(row) < 6:
            continue
        kind = "view" if "VIEW" in row[1].upper() else "table"
        # Имя с схемой: в одной базе могут лежать одноимённые таблицы разных схем.
        key = f"{row[5]}.{row[0]}"
        tables[key] = Table(
            name=row[0], database=database, kind=kind, schema=row[5],
            row_estimate=int(float(row[2] or 0)), data_bytes=int(row[3] or 0),
            comment=row[4],
        )
    return tables


def _pg_columns(access: access_mod.DbAccess, client: str,
                database: str, tables: dict[str, Table]) -> None:
    sql = (
        "SELECT c.relname, a.attname, "
        "format_type(a.atttypid, a.atttypmod), CASE WHEN a.attnotnull THEN 'NO' ELSE 'YES' END, "
        "COALESCE(pg_get_expr(d.adbin, d.adrelid), ''), "
        "COALESCE(col_description(a.attrelid, a.attnum), ''), a.attnum, n.nspname, "
        # P1-4: признаки колонки. Без них на PostgreSQL не было ни
        # auto_increment, ни генерируемых колонок, и write_check разрешал
        # запись туда, куда СУБД её запрещает.
        "COALESCE(a.attidentity, ''), COALESCE(a.attgenerated, '') "
        "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
        "WHERE a.attnum > 0 AND NOT a.attisdropped AND c.relkind IN ('r','v','m') "
        "AND n.nspname NOT IN ('pg_catalog','information_schema') "
        "ORDER BY n.nspname, c.relname, a.attnum;"
    )
    out, _err, _code = _run_cli(_base_command(access, client, database) + ["-c", sql],
                                _cli_env(access), access.timeout_seconds)
    for row in _parse_rows(out):
        if len(row) < 10:
            continue
        # Ключ таблицы включает схему: в одной базе бывают одноимённые таблицы
        # разных схем, и поиск по одному имени их смешал бы.
        table = tables.get(f"{row[7]}.{row[0]}") or tables.get(row[0])
        if table is None or len(table.columns) >= access.max_columns:
            continue
        sensitive, reason = classify(row[1], row[2])
        key, extra = pg_mark_fields(row[8], row[9])
        table.columns.append(Column(
            name=row[1], data_type=row[2], nullable=(row[3].upper() == "YES"),
            default=row[4], comment=row[5], ordinal=int(row[6] or 0),
            key=key, extra=extra,
            sensitive=sensitive, sensitive_reason=reason,
        ))


def _pg_indexes(access: access_mod.DbAccess, client: str,
                database: str, tables: dict[str, Table]) -> None:
    sql = (
        "SELECT t.relname, i.relname, ix.indisunique, "
        "COALESCE(a.attname, ''), am.amname, n.nspname, ix.indisprimary "
        "FROM pg_index ix JOIN pg_class t ON t.oid = ix.indrelid "
        "JOIN pg_class i ON i.oid = ix.indexrelid "
        "JOIN pg_am am ON am.oid = i.relam "
        "LEFT JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(ix.indkey) "
        "JOIN pg_namespace n ON n.oid = t.relnamespace "
        "WHERE n.nspname NOT IN ('pg_catalog','information_schema') "
        "ORDER BY n.nspname, t.relname, i.relname;"
    )
    out, _err, _code = _run_cli(_base_command(access, client, database) + ["-c", sql],
                                _cli_env(access), access.timeout_seconds)
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in _parse_rows(out):
        if len(row) < 7:
            continue
        table = tables.get(f"{row[5]}.{row[0]}") or tables.get(row[0])
        if table is None:
            continue
        entry = grouped.setdefault((row[5], row[0], row[1]), {
            "table": row[0], "schema": row[5], "name": row[1],
            "unique": row[2].lower() in ("t", "true"),
            "type": row[4], "columns": [],
            "primary": row[6].lower() in ("t", "true"),
        })
        if row[3]:
            entry["columns"].append(row[3])
    for (_schema, table_name, _i), data in grouped.items():
        table = tables.get(f"{_schema}.{table_name}") or tables.get(table_name)
        if table is not None:
            table.indexes.append(data)
            # Признак колонки в PG выражается индексом, а не полем колонки:
            # первичный ключ — `indisprimary`, уникальность — `indisunique`
            # (кроме самого первичного). До этой правки ключи не заполнялись
            # вовсе, поэтому `db_marks.marks` на PG не видел ни `pk`, ни
            # `unique`, и `write_check` не знал ни одного ключа.
            for column_name in data["columns"]:
                for column in table.columns:
                    if column.name != column_name:
                        continue
                    if data["primary"]:
                        column.key = "PRI"
                    elif data["unique"] and column.key != "PRI":
                        column.key = "UNI"


def _pg_relations(access: access_mod.DbAccess, client: str,
                  database: str, tables: dict[str, Table]) -> None:
    sql = (
        "SELECT ct.relname, a.attname, pt.relname, pa.attname, "
        "CASE con.confdeltype WHEN 'c' THEN 'CASCADE' WHEN 'n' THEN 'SET NULL' "
        "WHEN 'd' THEN 'SET DEFAULT' WHEN 'r' THEN 'RESTRICT' ELSE 'NO ACTION' END, "
        "n.nspname "
        "FROM pg_constraint con JOIN pg_class ct ON ct.oid = con.conrelid "
        "JOIN pg_class pt ON pt.oid = con.confrelid "
        "JOIN pg_namespace n ON n.oid = ct.relnamespace "
        "JOIN pg_attribute a ON a.attrelid = ct.oid AND a.attnum = ANY(con.conkey) "
        "JOIN pg_attribute pa ON pa.attrelid = pt.oid AND pa.attnum = ANY(con.confkey) "
        "WHERE con.contype = 'f' ORDER BY n.nspname, ct.relname, a.attname;"
    )
    out, _err, _code = _run_cli(_base_command(access, client, database) + ["-c", sql],
                                _cli_env(access), access.timeout_seconds)
    for row in _parse_rows(out):
        if len(row) < 6:
            continue
        table = tables.get(f"{row[5]}.{row[0]}") or tables.get(row[0])
        if table is not None:
            table.relations.append(
                (row[1], database, row[2], row[3], "stated", 1.0,
                 f"внешний ключ, ON DELETE {row[4]}"))


def _pg_view_deps(access: access_mod.DbAccess, client: str,
                  database: str, tables: dict[str, Table]) -> None:
    sql = (
        "SELECT DISTINCT v.relname, t.relname, n.nspname FROM pg_depend d "
        "JOIN pg_rewrite r ON r.oid = d.objid "
        "JOIN pg_class v ON v.oid = r.ev_class "
        "JOIN pg_namespace n ON n.oid = v.relnamespace "
        "JOIN pg_class t ON t.oid = d.refobjid "
        "WHERE v.relkind = 'v' AND v.oid <> t.oid ORDER BY n.nspname, v.relname;"
    )
    out, _err, _code = _run_cli(_base_command(access, client, database) + ["-c", sql],
                                _cli_env(access), access.timeout_seconds)
    for row in _parse_rows(out):
        if len(row) < 2:
            continue
        table = tables.get(row[0])
        if table is not None and row[1] not in table.depends_on:
            table.depends_on.append(row[1])


# ---------------------------------------------------------------------------
# Эвристические связи
# ---------------------------------------------------------------------------


# Виды имён, которые считаются ссылкой на другую таблицу.
#
# Измерено на живых базах проекта: имена колонок бывают в трёх видах —
#   `user_id`      (змеиный регистр, как в Laravel),
#   `UserID`       (сокращение, как в этой базе: ActiveUserID, AuthorUserID),
#   `User_ID`      (смешанный).
# Первая редакция искала только `_id` и нашла ОДНУ колонку на 892, тогда как
# ссылочных оказалось 173 — то есть связи в индексе отсутствовали почти целиком.
RELATION_NAME = __import__("re").compile(r"(?i)(?:^|_)([A-Za-z][A-Za-z0-9]*?)(?:_?id)$")

# Окончания имён таблиц: единственное и множественное число, а также формы,
# которые дают сокращения (`UserID` → `users`, `AuthorUserID` → `users`).
PLURAL_SUFFIXES = ("", "s", "es", "ies", "y", "i")


def relation_base(column_name: str) -> str:
    """Основная часть имени колонки-ссылки: `UserID` → `user`, `user_id` → `user`.

    Пустая строка означает «это не ссылка» (например, просто `id`).
    """
    name = (column_name or "").strip()
    if not name or name.lower() in ("id", "userid", "user_id"):
        # `userid` — это ИДЕНТИФИКАТОР пользователя, но имя таблицы (`users`)
        # из него выводится ненадёжно, поэтому такие имена разбираются общим
        # правилом ниже, а сюда попадает только голый `id`.
        if name.lower() == "id":
            return ""
    match = RELATION_NAME.search(name)
    if not match:
        return ""
    return match.group(1).lower()


def infer_relations(tables: dict[str, Table]) -> int:
    """Дополнить связи выводом по именам. Возвращает число добавленных.

    Связь помечается `derived` и достоверностью 0,4: это ВЫВОД, а не факт, и в
    ответе это видно отдельным полем. Почему вывод нужен: в живых базах внешние
    ключи часто не объявлены вовсе (измерено: 0 связей из FK на 86 таблиц), и без
    вывода агент не увидит связей вообще.

    Почему нельзя выдавать за факт: в этом проекте уже был ложный `full`, из-за
    которого клиент получал ошибку вместо честного «недоступно». Связь, которой
    нет, ломает вывод о влиянии правки — поэтому она помечается и ограничивается
    по достоверности.

    Сопоставление идёт по трём кандидатам сразу и берётся только ОДНОЗНАЧНЫЙ:
      * точное имя колонки без `id` (`users` → `users`, `user` → `users`);
      * имя с окончанием множественного числа;
      * особая форма: `AuthorUserID` → `users` (по последнему слову имени).
    Неоднозначность не разрешается: связь не создаётся.
    """
    added = 0
    # Ключ таблицы может содержать схему (`public.employees`, `ragraf_probe.users`).
    # Для сопоставления по имени берётся ЧАСТЬ БЕЗ СХЕМЫ, иначе ссылка
    # `company_id → companies` не находилась бы: измерено на живом PostgreSQL,
    # где ключи пришли со схемой и вывод дал ноль связей вместо одной.
    lowered: dict[str, str] = {}
    for name in tables:
        plain = name.split(".", 1)[1] if "." in name else name
        lowered.setdefault(plain.lower(), name)
    by_word: dict[str, str] = {}
    for name in tables:
        plain = (name.split(".", 1)[1] if "." in name else name).lower()
        for suffix in PLURAL_SUFFIXES:
            if suffix and plain.endswith(suffix) and len(plain) > len(suffix) + 2:
                by_word.setdefault(plain[: -len(suffix)], name)
        by_word.setdefault(plain, name)

    def resolve(candidates: list[str], own: str) -> str:
        """Годный кандидат ровно один? Возвращает имя таблицы или пустую строку."""
        found = {lowered[c] for c in candidates
                 if c in lowered and lowered[c] != own}
        return next(iter(found)) if len(found) == 1 else ""

    for table in tables.values():
        known = {(rel[0], rel[2]) for rel in table.relations}
        for column in table.columns:
            base = relation_base(column.name)
            if not base:
                continue
            candidates = [base + suffix for suffix in PLURAL_SUFFIXES]
            #Хвостовая часть имени в CamelCase: `AuthorUserID` → `user`.
            words = __import__("re").findall(r"[A-Z][a-z0-9]*|[a-z0-9]+",
                                            column.name)
            if words and words[-1].lower() in ("id",):
                words = words[:-1]
            if words:
                tail = words[-1].lower()
                candidates += [tail + suffix for suffix in PLURAL_SUFFIXES]
            target = resolve(candidates, table.name)
            if not target:
                continue
            if (column.name, target) in known:
                continue
            table.relations.append(
                (column.name, table.database, target, "id", "derived", 0.4,
                 f"вывод по имени колонки {column.name}"))
            added += 1
    return added


# ---------------------------------------------------------------------------
# Отпечаток структуры
# ---------------------------------------------------------------------------


def schema_fingerprint(result: IntrospectionResult) -> str:
    """Отпечаток структуры для сторожа свежести.

    В отпечаток входит ТОЛЬКО структура: базы, таблицы, колонки с типами, ключи,
    индексы, представления, связи. Данные в него не входят намеренно: изменилась
    строка в таблице — это не повод переиндексировать корпус, а примеры строк
    имеют собственную пометку устаревания.

    Порядок обхода отсортирован: отпечаток обязан быть одинаковым при двух
    прогонах на неизменной базе, иначе сторож свежести зациклится — ровно так же,
    как это устроено для корпуса кода.
    """
    parts: list[str] = [f"dialect={result.dialect}"]
    for database in sorted(result.databases, key=lambda d: d.name):
        parts.append(f"db:{database.name}:{database.charset}:{database.collation}")
        for table in sorted(database.tables, key=lambda t: t.name):
            parts.append(f"t:{database.name}.{table.name}:{table.kind}")
            for column in sorted(table.columns, key=lambda c: c.name):
                parts.append(
                    f"c:{column.name}:{column.data_type}:{int(column.nullable)}:"
                    f"{column.key}:{column.extra}:{column.default}")
            for index in sorted(table.indexes, key=lambda i: i["name"]):
                cols = ",".join(sorted(index["columns"]))
                parts.append(f"i:{index['name']}:{int(index['unique'])}:{cols}")
            for rel in sorted(table.relations, key=lambda r: (r[0], r[2])):
                parts.append(f"r:{rel[0]}->{rel[1]}.{rel[2]}.{rel[3]}:{rel[4]}")
            for dep in sorted(table.depends_on):
                parts.append(f"d:{dep}")
    joined = "\n".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


# DB-FINGERPRINT-STORE
# ---------------------------------------------------------------------------
# Учёт отпечатка структуры: запись и сравнение (актуализация «как код»)
# ---------------------------------------------------------------------------


def record_fingerprint(control_conn, rev_id: str, fingerprint: str,
                       dialect: str) -> None:
    """Запомнить отпечаток структуры баз для этой ревизии.

    Хранится в `meta` control-хранилища — там же, где прочие ключи ревизии:
    отдельная таблица ради одной строки усложнила бы обслуживание, а `meta` уже
    есть и переживает ротацию ревизий.
    """
    try:
        control_conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                             (f"db_fingerprint:{rev_id}",
                              f"{dialect}:{fingerprint}"))
        control_conn.commit()
    except Exception:
        # Неудача записи не должна ронять сбор: отпечаток нужен сторожу, а не
        # самому слою структуры.
        pass


def stored_fingerprint(control_conn, rev_id: str) -> str:
    """Прочитать записанный отпечаток. Пустая строка — не записан."""
    try:
        row = control_conn.execute("SELECT value FROM meta WHERE key = ?",
                                   (f"db_fingerprint:{rev_id}",)).fetchone()
    except Exception:
        return ""
    if not row:
        return ""
    try:
        return str(row["value"])
    except Exception:
        return str(row[0])


def db_structure_changed(cfg, *, enabled: bool | None = None) -> dict:
    """Изменилась ли структура баз с момента последней индексации.

    Возвращает вердикт и ПРИЧИНУ. Три случая различаются намеренно, и путать их
    нельзя:

      * `checked: False` — сбор выключен или отпечаток не записан: сравнивать
        нечего. Это НЕ повод переиндексировать корпус;
      * `error: True` — база недоступна. Тоже не повод: корпус кода от
        недоступности СУБД не устарел, и переиндексация была бы ложной реакцией;
      * `changed: True` — структура действительно другая, и только тогда корпус
        нужно пересобрать.

    Без такого разделения сторож свежести переиндексировал бы корпус на каждой
    проверке при любой недоступной базе — то есть ровно тот дефект, ради которого
    в проекте заведено правило про ложный `full`.
    """
    from . import db_access as access_mod
    from . import db_collect as collect_mod

    access = access_mod.DbAccessStore().load()
    if enabled is not None:
        access.enabled = enabled
    if not access.enabled:
        return {"checked": False, "changed": False, "reason": "сбор баз выключен"}
    if access.dialect not in access_mod.DIALECTS:
        return {"checked": False, "changed": False, "reason": "диалект не задан"}

    probe = collect_mod.probe(access)
    if not probe.get("ok"):
        return {"checked": False, "changed": False, "error": True,
                "reason": f"база недоступна: {probe.get('reason')}"}

    result = collect_mod.introspect(access)
    if result.errors:
        return {"checked": False, "changed": False, "error": True,
                "reason": "; ".join(result.errors[:2])}
    current = f"{access.dialect}:{schema_fingerprint(result)}"

    from .revisions import ControlStore

    control = ControlStore(cfg)
    try:
        row = control.conn.execute(
            "SELECT rev_id FROM rev_history ORDER BY rev_id DESC LIMIT 1").fetchone()
    except Exception:
        row = None
    rev = str(row["rev_id"]) if row else ""
    stored = stored_fingerprint(control.conn, rev) if rev else ""
    if not stored:
        return {"checked": False, "changed": False, "rev_id": rev,
                "reason": "отпечаток структуры не записан: выполните команду db"}
    return {
        "checked": True,
        "changed": stored != current,
        "rev_id": rev,
        "stored": stored[:24],
        "current": current[:24],
        "reason": ("структура баз изменилась" if stored != current
                   else "структура баз не изменилась"),
    }
