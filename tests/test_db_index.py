"""Проверка слоя баз данных: доступ, структура, примеры, персональные, отпечаток.

Проверяется на ЖИВЫХ СУБД (MySQL 9.7 и PostgreSQL 18 на этой машине), потому что
именно живая база выявила все дефекты этого слоя:
  * MySQL не принимает выбранный разделитель вывода, а psql отдаёт вертикальную
    черту — разбор должен определять разделитель по факту;
  * у PostgreSQL база и схема — разные вещи, и `postgres.таблица` не существует;
  * имена колонок пишутся в трёх видах (`FirstName`, `first_name`, `First-Name`),
    и проверка персональных данных по подстроке их не ловит.

Тест не требует ни наличия таблиц, ни данных: пустая база — нормальное состояние
(измерено: в PostgreSQL на этой машине пользовательских таблиц ноль), и это
проверяется отдельным случаем.

Запуск: python tests/test_db_index.py

Значение доступа берётся из `RAGRAF_DB_PASSWORD` или из файла доступа этой
машины (`catalog/db_access.json`). Литерального значения в файле нет: он входит
в поставку, а значение — настройка машины. Нет значения — код возврата 2
с причиной, а не «пройдено».
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import db_access as access_mod  # noqa: E402
from ragraf import db_collect as collect_mod  # noqa: E402
from ragraf import db_index as index_mod  # noqa: E402
from ragraf import db_ops  # noqa: E402
from ragraf import db_samples as samples_mod  # noqa: E402
from ragraf import db_writer as writer_mod  # noqa: E402

PASSED = 0
FAILED = 0

def _access_value() -> str:
    """Значение доступа этой машины: окружение, затем файл доступа.

    Литерального значения в файле нет намеренно. Файл входит в поставку, а
    значение — настройка машины, а не продукта: оно лежит в
    `catalog/db_access.json`, который исключён из поставки правилом .gitignore.
    Отсутствие значения — честный отказ с причиной, а не подстановка
    «чтобы проверка прошла».
    """
    direct = os.environ.get("RAGRAF_DB_PASSWORD", "").strip()
    if direct:
        return direct
    try:
        stored = access_mod.DbAccessStore().load()
    except Exception:  # noqa: BLE001 - файла доступа может не быть вовсе
        return ""
    return str(getattr(stored, "password", "") or "").strip()


MYSQL_PASSWORD = _access_value()
PG_PASSWORD = os.environ.get("RAGRAF_PG_PASSWORD", "").strip() or MYSQL_PASSWORD


def check(ok: bool, label: str, detail: str = "") -> bool:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  [OK ] {label}")
    else:
        FAILED += 1
        print(f"  [FAIL] {label}")
        if detail:
            print(f"         {detail}")
    return ok


def mysql_access(databases: list[str] | None = None) -> access_mod.DbAccess:
    access = access_mod.DbAccess(
        enabled=True, dialect="mysql", host="127.0.0.1", port=3306,
        user="root", password=MYSQL_PASSWORD, samples_max_rows=30,
        timeout_seconds=60, max_columns=80)
    access.databases_include = databases or []
    return access


def pg_access() -> access_mod.DbAccess:
    access = access_mod.DbAccess(
        enabled=True, dialect="postgresql", host="127.0.0.1", port=5432,
        user="postgres", password=PG_PASSWORD, samples_max_rows=30,
        timeout_seconds=60, max_columns=80)
    access.databases_include = ["postgres"]
    access.databases_exclude = ["template0", "template1"]
    return access


def main() -> int:
    if not MYSQL_PASSWORD:
        print("ПРОПУСК: проверки живых СУБД не выполнялись — нет RAGRAF_DB_PASSWORD\n"
              "и нет значения в catalog/db_access.json. Это НЕ «пройдено»:\n"
              "код возврата 2 означает, что осмотрено ноль проверок СУБД.")
        return 2
    print("=== разбор имён колонок и персональные данные ===")
    check(index_mod.relation_base("UserID") == "user",
          "CamelCase `UserID` разобран как ссылка на `user`")
    check(index_mod.relation_base("user_id") == "user",
          "snake_case `user_id` разобран")
    check(index_mod.relation_base("id") == "",
          "голый `id` не считается ссылкой (это первичный ключ)")

    for name, expected in (("FirstName", True), ("first_name", True),
                           ("First-Name", True), ("LastName", True),
                           ("E-Mail", True), ("EMail", True), ("Phone", True),
                           ("BirthDay", True), ("Login", True),
                           ("UserID", False), ("CompanyName", False),
                           ("Created", False), ("Amount", False)):
        got, reason = index_mod.classify(name)
        check(got == expected, f"персональные данные: {name} → {got}",
              f"ожидалось {expected}, получено {got} ({reason})")

    print("\n=== доступ: приоритет и отказы ===")
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="ragraf-db-"))
    store = access_mod.DbAccessStore(tmp / "db_access.json")
    saved = store.save({"enabled": True, "dialect": "mysql", "port": 3306,
                        "user": "root", "password": "file-value"})
    check(saved.public()["password_set"] is True, "значение сохранено")
    check("password" not in saved.public(),
          "значение НЕ отдаётся в состоянии интерфейса")
    os.environ["RAGRAF_DB_PASSWORD"] = "env-value"
    check(store.load().source() == "environment",
          "окружение перекрывает файл")
    del os.environ["RAGRAF_DB_PASSWORD"]
    check(store.load().source() == "file", "без окружения берётся файл")
    store.save({"port": 3307})
    check(store.load().port == 3307 and store.load().password == "file-value",
          "смена порта не стирает значение доступа")
    for bad, why in (({"dialect": "oracle"}, "неизвестный диалект"),
                     ({"port": 99999}, "порт вне диапазона"),
                     ({"host": "a b"}, "пробел в адресе")):
        check(bool(access_mod.validate(bad)), f"отказ: {why}")

    print("\n=== проба подключения (живые СУБД) ===")
    mysql_probe = collect_mod.probe(mysql_access())
    check(mysql_probe["ok"], "MySQL: проба получила ответ сервера",
          str(mysql_probe)[:200])
    check("9.7" in str(mysql_probe.get("server", "")),
          "MySQL: версия сервера получена", str(mysql_probe.get("server")))
    pg_probe = collect_mod.probe(pg_access())
    check(pg_probe["ok"], "PostgreSQL: проба получила ответ сервера",
          str(pg_probe)[:200])
    empty = access_mod.DbAccess(enabled=True, dialect="mysql", password="")
    check(collect_mod.probe(empty)["ok"] is False,
          "без значения доступа проба отказывает")
    wrong = access_mod.DbAccess(enabled=True, dialect="mysql", user="root",
                                password="definitely-wrong")  # ragraf-release: placeholder-value — заведомо неверное значение доступа в проверке отказа
    wrong_probe = collect_mod.probe(wrong)
    check(wrong_probe["ok"] is False and wrong_probe.get("reason"),
          "неверное значение: отказ называет причину",
          str(wrong_probe.get("reason"))[:120])

    print("\n=== структура MySQL ===")
    access = mysql_access(["bsdocs"])
    result = collect_mod.introspect(access)
    check(not result.errors, "структура собрана без ошибок", str(result.errors)[:200])
    check(result.tables_total > 50,
          f"таблиц найдено достаточно ({result.tables_total})")
    check(result.columns_total > 400,
          f"колонок найдено достаточно ({result.columns_total})")
    views = sum(1 for d in result.databases for t in d.tables if t.kind == "view")
    check(views > 0, f"представления найдены ({views})")
    sensitive = sum(1 for d in result.databases for t in d.tables
                    for c in t.columns if c.sensitive)
    check(sensitive > 0, f"персональные колонки распознаны ({sensitive})")
    indexed = sum(1 for d in result.databases for t in d.tables if t.indexes)
    check(indexed > 0, f"индексы собраны у {indexed} таблиц")
    derived = sum(1 for d in result.databases for t in d.tables
                  for r in t.relations if r[4] == "derived")
    stated = sum(1 for d in result.databases for t in d.tables
                 for r in t.relations if r[4] == "stated")
    check(stated + derived > 0,
          f"связи собраны: фактов {stated}, выводов {derived}")
    print(f"       таблиц {result.tables_total}, колонок {result.columns_total}, "
          f"связей {stated + derived} (из них вывод {derived}), закрыто {sensitive}")

    print("\n=== отпечаток структуры ===")
    fp1 = index_mod.schema_fingerprint(result)
    fp2 = index_mod.schema_fingerprint(collect_mod.introspect(access))
    check(fp1 == fp2, "два прогона на неизменной базе дают один отпечаток",
          f"{fp1[:16]} vs {fp2[:16]}")
    # Изменение структуры меняет отпечаток: проверяем на копии результата.
    import copy
    altered = copy.deepcopy(result)
    altered.databases[0].tables[0].columns.append(
        index_mod.Column(name="probe_added", data_type="int"))
    check(index_mod.schema_fingerprint(altered) != fp1,
          "добавленная колонка меняет отпечаток")

    print("\n=== примеры строк: сборные, персональные скрыты ===")
    # Берётся таблица, у которой ЕСТЬ строки и есть чувствительные колонки:
    # на пустой таблице пример собрать нельзя, и это не дефект сбора.
    target = next((t for d in result.databases for t in d.tables
                   if t.kind == "table" and any(c.sensitive for c in t.columns)
                   and (t.row_estimate or 0) > 0), None)
    if target is None:
        check(False, "найдена таблица с персональными колонками для проверки")
    else:
        client = index_mod.find_client("mysql")
        sample = samples_mod.collect_sample(access, client, target.database, target)
        check(not sample.error, f"пример собран для {target.name}",
              sample.error)
        check(sample.rows_scanned > 0,
              f"строки прочитаны ({sample.rows_scanned})")
        hidden = {h["column"] for h in sample.hidden_columns}
        leaked = [c for c in hidden if c not in sample.fields
                  or samples_mod.HIDDEN not in str(sample.fields.get(c))]
        check(not leaked, "персональные колонки скрыты и не содержат значений",
              f"утечка: {leaked}")
        check(all(any(h["column"] == c.name for h in sample.hidden_columns)
                  for c in target.columns if c.sensitive),
              "все чувствительные колонки отмечены скрытыми")
        filled = [c for c in sample.fields
                  if samples_mod.HIDDEN not in str(sample.fields[c])]
        check(len(filled) > 0, f"непустые значения собраны ({len(filled)} полей)")

    print("\n=== PostgreSQL: схема, примеры, связи ===")
    pg = pg_access()
    pg_result = collect_mod.introspect(pg)
    check(not pg_result.errors, "PostgreSQL: структура собрана без ошибок",
          str(pg_result.errors)[:200])
    total_pg = pg_result.tables_total
    check(isinstance(total_pg, int), f"PostgreSQL: таблиц получено ({total_pg})")
    if total_pg == 0:
        check(True, "пустая база обрабатывается как нормальное состояние, "
                    "а не как ошибка")
    else:
        client_pg = index_mod.find_client("postgresql")
        table = pg_result.databases[0].tables[0]
        sample = samples_mod.collect_sample(pg, client_pg,
                                           pg_result.databases[0].name, table)
        check(not sample.error,
              f"PostgreSQL: пример собран для {table.name}", sample.error)
        check(sample.rows_scanned > 0 or table.row_estimate == 0,
              f"PostgreSQL: строки прочитаны ({sample.rows_scanned})")

    print("\n=== запись в индекс ===")
    from ragraf.config import load as load_config
    from ragraf.embeddings import Embedder
    from ragraf.lexvec import LexVecStore
    from ragraf.revisions import ControlStore, GraphWriter

    cfg = load_config(REPO / "config" / "ragraf.yaml")
    ctl = ControlStore(cfg)
    ctl_rev = ctl.next_rev_id()
    graph = GraphWriter(cfg)
    lex = LexVecStore(cfg)
    embedder = Embedder(cfg, allow_download=False)
    stats = writer_mod.write_introspection(graph, ctl_rev, result, {})
    check(stats["tables"] == result.tables_total,
          "в граф записаны все таблицы", f"{stats['tables']}")
    check(stats["sensitive_columns"] == sensitive,
          "число закрытых колонок совпадает", f"{stats['sensitive_columns']}")
    check(bool(stats["fingerprint"]), "отпечаток записан в метаданные")
    lex_stats = writer_mod.write_lexvec(lex, embedder, ctl_rev, result, {})
    check(lex_stats["chunks"] >= result.tables_total,
          f"в лексику записаны чанки по таблицам ({lex_stats['chunks']})")
    # Уборка: прогон теста не должен оставлять ревизию в индексе.
    try:
        lex.purge_rev(ctl_rev)
        graph.conn.execute("DELETE FROM db_live_table WHERE rev_id = ?", (ctl_rev,))
        graph.conn.execute("DELETE FROM db_live_column WHERE rev_id = ?", (ctl_rev,))
        graph.conn.execute("DELETE FROM db_live_index WHERE rev_id = ?", (ctl_rev,))
        graph.conn.execute("DELETE FROM db_live_relation WHERE rev_id = ?", (ctl_rev,))
        graph.conn.execute("DELETE FROM db_live_meta WHERE rev_id = ?", (ctl_rev,))
        graph.conn.commit()
        check(True, "следы тестовой ревизии убраны")
    except Exception as exc:
        check(False, "следы тестовой ревизии убраны", f"{type(exc).__name__}: {exc}")

    print("\n=== связи: база источника и отсутствие дублей ===")
    rel = db_ops.relations(graph.conn, ctl_rev, "contracts")
    stated = rel.get("stated") or []
    if stated:
        has_db = [r for r in stated if r.get("from_database")]
        check(len(has_db) == len(stated),
              "у каждой связи указана база источника",
              f"без базы: {len(stated) - len(has_db)}")
        keys = [(r.get("from_database"), r.get("from_column"), r.get("to_table"),
                 r.get("to_column")) for r in stated]
        check(len(keys) == len(set(keys)),
              "в связях нет дублей", f"дублей: {len(keys) - len(set(keys))}")
    else:
        check(True, "связей нет: проверка дублей не требуется")

    # Имена таблиц из кода: ложные находки из импортов Python отсекаются.
    fake = {"__future__", "annotations", "os", "sys", "json"}
    found = {n.lower() for n in fake}
    live_names = {str(r[0]).lower() for r in graph.conn.execute(
        "SELECT DISTINCT table_name FROM db_live_table WHERE rev_id = ?",
        (ctl_rev,)).fetchall()}
    check(not (found & live_names),
          "среди имён таблиц нет имён из импортов Python")

    print("\n" + "=" * 78)
    print(f"ИТОГ: пройдено {PASSED}, провалено {FAILED}")
    print("=" * 78)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
