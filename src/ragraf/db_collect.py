"""Полный сбор слоя баз: структура → примеры → запись → отпечаток.

Зачем единая точка
    Сбор состоит из четырёх шагов, и они должны идти строго вместе: структура без
    записи бесполезна, отпечаток без структуры бессмыслен, а примеры нельзя
    собирать раньше классификации персональных колонок. Одна точка входа исключает
    «полусобранное» состояние, из-за которого индекс выглядел бы полным, будучи
    частичным — этот класс дефектов в проекте уже был (ложный `full`).

Порядок шагов и почему такой
    1. **доступ** — читается из окружения и файла; если выключен, сбор не идёт;
    2. **структура** — системные каталоги, только чтение;
    3. **связи** — факты из внешних ключей, затем вывод по именам колонок;
    4. **отпечаток** — считается по структуре и нужен сторожу свежести;
    5. **примеры** — только если включены отдельно: это единственная часть,
       которая касается содержимого, и классификация персональных колонок к этому
       моменту уже выполнена;
    6. **запись** — граф (структура и связи) и лексика с векторами (описания и
       примеры).

Деградация вместо падения
    Любой шаг может отказать (нет клиента, нет доступа, нет прав, пустая база), и
    это возвращается причиной, а не исключением: индексация корпуса не должна
    падать из-за того, что база недоступна. Отсутствие таблиц — НЕ ошибка:
    измерено на живом PostgreSQL, где пользовательских таблиц ноль, и это
    нормальное состояние, а не сбой.

Автор: Михаил Дейнекин
Сайт: https://Deynekin.com
Почта: Mikhail@Deynekin.com
"""

from __future__ import annotations

import time
from typing import Any

from . import db_access as access_mod
from . import db_index as index_mod
from . import db_samples as samples_mod
from . import db_writer as writer_mod


def introspect(access: access_mod.DbAccess) -> index_mod.IntrospectionResult:
    """Собрать структуру: базы, таблицы, колонки, индексы, представления, связи."""
    result = index_mod.IntrospectionResult(
        dialect=access.dialect, host=access.host, port=access.port)
    client = index_mod.find_client(access.dialect)
    if not client:
        result.errors.append(
            f"клиент {access.dialect} не найден: установите его или добавьте в PATH")
        return result
    result.via = "cli"
    result.notes.append(f"клиент: {client}")

    collector = (_mysql_database if access.dialect == "mysql"
                 else _postgres_database)
    try:
        databases = collector(access, client)
    except Exception as exc:
        result.errors.append(f"{type(exc).__name__}: {exc}")
        return result

    if not databases:
        result.notes.append(
            "ни одной базы не выбрано: проверьте фильтры databases_include/exclude")
        return result

    for name, charset, collation in databases:
        if len(result.databases) >= 200:
            result.notes.append("достигнут предел числа баз (200)")
            break
        try:
            tables = _tables(access, client, name)
        except Exception as exc:
            result.errors.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        if len(tables) > access.max_tables:
            result.notes.append(
                f"{name}: таблиц больше предела ({len(tables)} > {access.max_tables}), "
                "взята только часть")
            tables = dict(list(sorted(tables.items()))[: access.max_tables])
        _fill_columns(access, client, name, tables)
        _fill_indexes(access, client, name, tables)
        _fill_relations(access, client, name, tables)
        _fill_view_deps(access, client, name, tables)
        derived = index_mod.infer_relations(tables)
        if derived:
            result.notes.append(
                f"{name}: связей выведено по именам {derived} (это вывод, не факт)")
        result.databases.append(index_mod.Database(
            name=name, charset=charset, collation=collation,
            tables=list(tables.values())))
    return result


def _tables(access: access_mod.DbAccess, client: str, database: str):
    if access.dialect == "mysql":
        return index_mod._mysql_tables(access, client, database)
    return index_mod._pg_tables(access, client, database)


def _fill_columns(access, client, database, tables) -> None:
    if access.dialect == "mysql":
        index_mod._mysql_columns(access, client, database, tables)
    else:
        index_mod._pg_columns(access, client, database, tables)


def _fill_indexes(access, client, database, tables) -> None:
    if access.dialect == "mysql":
        index_mod._mysql_indexes(access, client, database, tables)
    else:
        index_mod._pg_indexes(access, client, database, tables)


def _fill_relations(access, client, database, tables) -> None:
    if access.dialect == "mysql":
        index_mod._mysql_relations(access, client, database, tables)
    else:
        index_mod._pg_relations(access, client, database, tables)


def _fill_view_deps(access, client, database, tables) -> None:
    if access.dialect == "mysql":
        index_mod._mysql_view_deps(access, client, database, tables)
    else:
        index_mod._pg_view_deps(access, client, database, tables)


def _mysql_database(access, client):
    return index_mod._mysql_databases(access, client)


def _postgres_database(access, client):
    return index_mod._pg_databases(access, client)


def probe(access: access_mod.DbAccess | None = None) -> dict[str, Any]:
    """Проба подключения: реальное обращение, а не проверка настроек.

    Проверка обязана получить ответ от сервера. Иначе «подключение работает» —
    догадка: на этой машине PostgreSQL отвечал, но пользовательских таблиц в нём
    ноль, и отличить одно от другого можно только обращением.
    """
    access = access or access_mod.DbAccessStore().load()
    started = time.time()
    client = index_mod.find_client(access.dialect)
    if not client:
        return {"ok": False, "reason":
                f"клиент {access.dialect} не найден: установите его или добавьте в PATH",
                "action": "проверьте установку СУБД"}
    if not access.password:
        return {"ok": False,
                "reason": "доступ не задан: ни в настройках, ни в окружении",
                "action": "заполните доступ в панели или задайте RAGRAF_DB_PASSWORD"}

    try:
        if access.dialect == "mysql":
            cmd = index_mod._base_command(access, client) + [
                "-e", "SELECT VERSION(), (SELECT COUNT(*) FROM information_schema.SCHEMATA);"]
        else:
            cmd = index_mod._base_command(access, client, "postgres") + [
                "-c", "SELECT version(), (SELECT count(*) FROM pg_database WHERE NOT datistemplate);"]
        out, err, code = index_mod._run_cli(
            cmd, index_mod._cli_env(access), access.timeout_seconds)
    except Exception as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}",
                "action": "проверьте доступ и адрес"}

    if code != 0 and not out.strip():
        last = err.strip().splitlines()[-1][:200] if err.strip() else "нет ответа"
        return {"ok": False, "reason": last,
                "action": "проверьте пользователя, значение доступа и права на чтение"}
    cells = index_mod._parse_rows(out)
    version = cells[0][0] if cells and cells[0] else ""
    count = cells[0][1] if cells and len(cells[0]) > 1 else "?"
    return {
        "ok": True,
        "dialect": access.dialect,
        "server": version[:80],
        "databases": count,
        "via": "cli",
        "client": client,
        "source": access.source(),
        "seconds": round(time.time() - started, 2),
        "note": ("проверка получила ответ сервера; число баз — из системного каталога, "
                 "пустой список баз не означает ошибку"),
    }


def collect(access: access_mod.DbAccess | None = None,
            *, with_samples: bool | None = None,
            tables_limit: int = 0) -> dict[str, Any]:
    """Полный сбор: структура, отпечаток и (по решению) примеры.

    Возвращает результат для записи и отчёт. Запись в индекс выполняет вызывающий:
    так сбор можно проверить, не меняя состояние.
    """
    access = access or access_mod.DbAccessStore().load()
    started = time.time()
    if not access.enabled:
        return {"ok": False, "reason": "сбор выключен в настройках",
                "action": "включите сбор в разделе «Базы данных» панели"}

    result = introspect(access)
    fingerprint = index_mod.schema_fingerprint(result)

    want_samples = access.samples_enabled if with_samples is None else with_samples
    samples: dict[str, samples_mod.TableSample] = {}
    sample_errors: list[str] = []
    if want_samples and result.databases and not result.errors:
        client = index_mod.find_client(access.dialect)
        try:
            collected = samples_mod.collect_samples(access, result, client, tables_limit)
            samples = collected["samples"]
            sample_errors = collected["errors"]
        except Exception as exc:
            sample_errors.append(f"{type(exc).__name__}: {exc}")
    elif want_samples:
        sample_errors.append("примеры не собирались: структура не получена")

    return {
        "ok": not result.errors,
        "access": access.public(),
        "result": result,
        "fingerprint": fingerprint,
        "samples": samples,
        "samples_wanted": want_samples,
        "errors": list(result.errors) + sample_errors,
        "notes": result.notes,
        "seconds": round(time.time() - started, 2),
    }


def write(access, rev_id: str, collected: dict[str, Any],
          graph_writer, lexvec, embedder) -> dict[str, Any]:
    """Записать собранное в граф и в лексику. Возвращает отчёт."""
    result: index_mod.IntrospectionResult = collected["result"]
    samples = collected.get("samples") or {}
    graph_stats = writer_mod.write_introspection(graph_writer, rev_id, result, samples)
    lex_stats = writer_mod.write_lexvec(lexvec, embedder, rev_id, result, samples)
    return {"graph": graph_stats, "lexvec": lex_stats,
            "fingerprint": collected.get("fingerprint", "")}
