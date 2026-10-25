"""Сбор примеров строк и доменов значений.

Что собирается
    * **сборный пример** на таблицу: по каждой колонке берётся первое НЕПУСТОЕ
      значение из просмотренных строк. Если разные поля заполнены в разных
      строках, пример собирается из нескольких — именно так просил владелец;
    * **домен значений** для колонок с малым числом различных значений (статусы,
      типы, флаги): значения с частотами. Это не содержимое строк, а словарь
      модели данных, и он полезнее примеров для понимания схемы.

Чего не делается НИКОГДА
    Значения колонок, признанных персональными данными или секретами, не
    собираются. На их месте — пометка «скрыто» с причиной. Проверка идёт до
    выборки: колонка исключается из списка полей запроса, а не фильтруется после.
    Причина простая: собирая примеры по сотням баз, легко вытащить чужие
    персональные данные в индекс, который читают агенты.

Только чтение
    Один `SELECT` на таблицу с явным `LIMIT` и списком полей. Ни `ORDER BY` по
    большим таблицам, ни агрегатов по всей таблице, ни записи. Число строк
    ограничено настройкой, размер ответа — тоже.

Автор: Михаил Дейнекин
Сайт: https://Deynekin.com
Почта: Mikhail@Deynekin.com
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from . import db_access as access_mod
from . import db_index as index_mod
from .db_index import FIELD, ROW, Table, _base_command, _cli_env, _parse_rows, _run_cli

# Пределы для домена значений: он нужен для колонок с малым числом вариантов,
# поэтому больше не собирается — иначе домен превращается в выгрузку данных.
DOMAIN_MAX_VALUES = 20
DOMAIN_MIN_ROWS = 5

# Длина значения в примере. Обрезается по символам, а не по байтам: цель — не
# «влезть в лимит», а показать форму значения, не выгружая содержимое.
VALUE_MAX_CHARS = 120

HIDDEN = "скрыто: персональные данные"


@dataclass
class TableSample:
    """Собранный пример одной таблицы."""

    database: str
    table: str
    rows_scanned: int = 0
    fields: dict[str, str] = field(default_factory=dict)   # колонка → значение
    domains: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    hidden_columns: list[dict[str, str]] = field(default_factory=list)
    empty_columns: list[str] = field(default_factory=list)
    error: str = ""
    bytes_used: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "database": self.database, "table": self.table,
            "rows_scanned": self.rows_scanned,
            "fields": self.fields, "domains": self.domains,
            "hidden_columns": self.hidden_columns,
            "empty_columns": self.empty_columns,
            "error": self.error, "bytes_used": self.bytes_used,
            "note": ("снимок на момент индексации: примеры со временем устаревают, "
                     "структура — нет"),
        }


def _quote_ident(dialect: str, name: str) -> str:
    """Экранировать имя объекта. Обратные кавычки у MySQL, двойные у PostgreSQL."""
    if dialect == "mysql":
        return "`" + name.replace("`", "``") + "`"
    return '"' + name.replace('"', '""') + '"'


def _select_sql(access: access_mod.DbAccess, database: str, table: Table,
                columns: list[str], limit: int, schema: str = "") -> str:
    """Собрать запрос примера.

    ВАЖНО про адресацию таблиц. У MySQL база — это и есть схема, поэтому таблица
    называется `база.таблица`. У PostgreSQL это разные вещи: соединение уже
    открыто на нужную базу (`-d`), а схема внутри неё обычно `public`. Первая
    редакция подставляла имя базы как схему, и запрос падал с «отношение
    postgres.employees не существует» — измерено на живой базе.
    """
    quoted = ", ".join(_quote_ident(access.dialect, c) for c in columns)
    if access.dialect == "mysql":
        target = (f"{_quote_ident(access.dialect, database)}."
                  f"{_quote_ident(access.dialect, table.name)}")
    elif schema:
        target = (f"{_quote_ident(access.dialect, schema)}."
                  f"{_quote_ident(access.dialect, table.name)}")
    else:
        # Схема неизвестна — полагаемся на `search_path` соединения.
        target = _quote_ident(access.dialect, table.name)
    where = " OR ".join(f"{_quote_ident(access.dialect, c)} IS NOT NULL"
                        for c in columns)
    return f"SELECT {quoted} FROM {target} WHERE {where} LIMIT {limit};"


def collect_sample(access: access_mod.DbAccess, client: str, database: str,
                   table: Table) -> TableSample:
    """Собрать пример одной таблицы.

    Из выборки берётся КАЖДОЕ непустое значение по колонке: если поле заполнено
    только в третьей строке, его значение всё равно попадёт в пример — это и есть
    «сборный пример из разных записей».
    """
    sample = TableSample(database=database, table=table.name)

    # Чувствительные колонки исключаются ДО запроса: их значения не должны даже
    # попасть в память процесса сбора.
    usable: list[str] = []
    for column in table.columns:
        if column.sensitive:
            sample.hidden_columns.append({
                "column": column.name,
                "reason": column.sensitive_reason or "персональные данные",
            })
            sample.fields[column.name] = HIDDEN
        else:
            usable.append(column.name)

    if not usable:
        sample.error = "все колонки скрыты как персональные данные"
        return sample

    limit = max(1, int(access.samples_max_rows))
    sql = _select_sql(access, database, table, usable, limit,
                      schema=getattr(table, "schema", "") or "")
    command = _base_command(access, client, database) + (
        ["-e", sql] if access.dialect == "mysql" else ["-c", sql])
    out, err, code = _run_cli(command, _cli_env(access), access.timeout_seconds)
    if code != 0 and not out.strip():
        sample.error = (err.strip().splitlines()[-1][:200] if err.strip()
                        else "запрос примера не выполнился")
        return sample

    rows = _parse_rows(out)
    sample.rows_scanned = len(rows)

    # Сборный пример: первое непустое значение по каждой колонке.
    counters: dict[str, dict[str, int]] = {c: {} for c in usable}
    for row in rows:
        for position, column in enumerate(usable):
            if position >= len(row):
                continue
            value = (row[position] or "").strip()
            if not value:
                continue
            if column not in sample.fields:
                cut = value[:VALUE_MAX_CHARS]
                if len(value) > VALUE_MAX_CHARS:
                    cut += "…(обрезано)"
                sample.fields[column] = cut
                sample.bytes_used += len(cut)
                if sample.bytes_used > access.samples_max_bytes:
                    sample.error = "превышен предел размера примера"
                    return sample
            bucket = counters[column]
            if len(bucket) < DOMAIN_MAX_VALUES + 1:
                bucket[value[:VALUE_MAX_CHARS]] = bucket.get(value[:VALUE_MAX_CHARS], 0) + 1

    # Домен значений: только для колонок с малым числом вариантов.
    if len(rows) >= DOMAIN_MIN_ROWS:
        for column, bucket in counters.items():
            distinct = len(bucket)
            if 1 < distinct <= DOMAIN_MAX_VALUES:
                sample.domains[column] = [
                    {"value": value, "count": count}
                    for value, count in sorted(bucket.items(),
                                               key=lambda kv: (-kv[1], kv[0]))
                ]

    sample.empty_columns = [c for c in table.columns
                            if c.name not in sample.fields
                            and not c.sensitive]
    return sample


def collect_samples(access: access_mod.DbAccess,
                    result: index_mod.IntrospectionResult,
                    client: str,
                    tables_limit: int = 0) -> dict[str, Any]:
    """Собрать примеры для всех таблиц результата.

    Таблицы-представления пропускаются по умолчанию: у них нет своих данных, а
    запрос к представлению может оказаться тяжёлым (в нём соединения).
    """
    out: dict[str, TableSample] = {}
    errors: list[str] = []
    scanned = 0
    for database in result.databases:
        for table in sorted(database.tables, key=lambda t: t.name):
            if table.kind != "table":
                continue
            if tables_limit and scanned >= tables_limit:
                errors.append(f"достигнут предел таблиц для примеров: {tables_limit}")
                return {"samples": out, "errors": errors, "tables_scanned": scanned}
            scanned += 1
            sample = collect_sample(access, client, database.name, table)
            out[f"{database.name}.{table.name}"] = sample
            if sample.error:
                errors.append(f"{database.name}.{table.name}: {sample.error}")
    return {"samples": out, "errors": errors, "tables_scanned": scanned}


def sample_text(sample: TableSample) -> str:
    """Текст примера для поиска по смыслу.

    Идёт в лексику и векторы: по нему агент найдёт таблицу вопросами вроде «где
    хранятся заявки». Структура отдельно, примеры отдельно — чтобы поиск по
    структуре не размывался случайными значениями.
    """
    lines = [f"Пример данных: {sample.database}.{sample.table}",
             f"просмотрено строк: {sample.rows_scanned}"]
    for column, value in sorted(sample.fields.items()):
        lines.append(f"  {column} = {value}")
    for column, values in sorted(sample.domains.items()):
        shown = ", ".join(f"{v['value']} ({v['count']})" for v in values[:DOMAIN_MAX_VALUES])
        lines.append(f"  домен {column}: {shown}")
    if sample.hidden_columns:
        names = ", ".join(h["column"] for h in sample.hidden_columns)
        lines.append(f"  скрыто как персональные данные: {names}")
    return "\n".join(lines)
