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

import atexit
import os
import shutil
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


def _drop_tmp(path) -> None:
    """Убрать временный каталог, закрыв SQLite-соединения прогона.

    Измерено 30.10.2026: `shutil.rmtree(..., ignore_errors=True)` молча НЕ убирал каталог.
    Причина не в правах: на Windows нельзя удалить файл, открытый соединением SQLite
    (`bad-graph.sqlite`, `duck.sqlite`, `empty-journal.sqlite` и другие), а флаг превращал
    отказ в тишину. Поэтому уборка ЗАКРЫВАЕТ соединения, затем делает две попытки с
    `gc.collect()` между ними и называет остаток, если он есть.
    """
    import gc
    import sqlite3

    for _attempt in range(2):
        for item in gc.get_objects():
            if isinstance(item, sqlite3.Connection):
                try:
                    item.close()
                except Exception:  # noqa: BLE001 — соединение могло быть закрыто раньше
                    pass
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
        gc.collect()
    if path.exists():
        left = [p.name for p in path.rglob("*") if p.is_file()][:5]
        print(f"  [ВНИМАНИЕ] временный каталог не убран: {path} ({left})")



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


def _fixture_result() -> "index_mod.IntrospectionResult":
    """Небольшая структура в памяти: две базы, таблицы, колонки, связи.

    Живая СУБД для этой части проверки не нужна — и не должна быть нужна:
    проверяется запись отпечатка, а не сбор структуры.
    """
    warehouses = index_mod.Table(
        name="warehouses", database="i35_live", kind="table",
        columns=[
            index_mod.Column(name="id", data_type="int", key="PRI", ordinal=1),
            index_mod.Column(name="title", data_type="varchar", ordinal=2),
        ],
        indexes=[{"name": "PRIMARY", "unique": True, "columns": ["id"]}],
    )
    orders = index_mod.Table(
        name="orders", database="i35_live", kind="table", row_estimate=10,
        columns=[
            index_mod.Column(name="id", data_type="int", key="PRI", ordinal=1),
            index_mod.Column(name="WarehouseID", data_type="int", ordinal=2),
        ],
        relations=[("WarehouseID", "i35_live", "warehouses", "id",
                    "derived", 0.4, "вывод по имени колонки WarehouseID")],
    )
    view = index_mod.Table(
        name="orders_today", database="i35_live", kind="view",
        depends_on=["orders"],
        columns=[index_mod.Column(name="id", data_type="int", ordinal=1)],
    )
    return index_mod.IntrospectionResult(
        dialect="mysql", via="fixture", host="127.0.0.1", port=3306,
        databases=[
            index_mod.Database(name="i35_live", charset="utf8mb4",
                               collation="utf8mb4_general_ci",
                               tables=[warehouses, orders, view]),
            index_mod.Database(name="i35_ref", tables=[index_mod.Table(
                name="currencies", database="i35_ref",
                columns=[index_mod.Column(name="code", data_type="char",
                                          key="PRI", ordinal=1)])]),
        ],
    )


# Метки ХРАНИЛИЩА, а не имён функций: по ним свойство и формулируется.
STRUCTURE_TABLE_MARK = "db_live_table"
FINGERPRINT_KEY_MARK = "db_fingerprint"
# Глаголы записи. Нужны потому, что таблица живой схемы читается теми же
# именами: без них проверка считала бы записью структуры любую выборку.
WRITE_VERBS = ("INSERT", "UPDATE", "DELETE")


def _writes_structure(literal: str) -> bool:
    """Запись ли это в таблицу живой схемы (а не чтение из неё)."""
    if STRUCTURE_TABLE_MARK not in literal:
        return False
    upper = literal.upper()
    return any(verb in upper for verb in WRITE_VERBS)


def _function_facts(text: str) -> dict[str, tuple[set[str], set[str]]]:
    """Для каждой функции модуля: строковые литералы и имена вызываемых функций."""
    import ast as ast_mod

    facts: dict[str, tuple[set[str], set[str]]] = {}
    for node in ast_mod.walk(ast_mod.parse(text)):
        if not isinstance(node, (ast_mod.FunctionDef, ast_mod.AsyncFunctionDef)):
            continue
        literals, calls = facts.setdefault(node.name, (set(), set()))
        for inner in ast_mod.walk(node):
            if isinstance(inner, ast_mod.Constant) and isinstance(inner.value, str):
                literals.add(inner.value)
            elif isinstance(inner, ast_mod.Call):
                target = inner.func
                if isinstance(target, ast_mod.Name):
                    calls.add(target.id)
                elif isinstance(target, ast_mod.Attribute):
                    calls.add(target.attr)
    return facts


def _reaches(calls: set[str], targets: set[str],
             facts: dict[str, tuple[set[str], set[str]]],
             depth: int = 4) -> bool:
    """Достижим ли кто-то из targets по графу вызовов (в пределах depth)."""
    frontier = set(calls)
    seen: set[str] = set()
    for _ in range(depth):
        if frontier & targets:
            return True
        seen |= frontier
        nxt: set[str] = set()
        for name in frontier:
            nxt |= facts.get(name, (set(), set()))[1]
        frontier = nxt - seen
        if not frontier:
            return False
    return bool(frontier & targets)


def audit_structure_paths(sources: dict[str, str]) -> list[tuple[str, str]]:
    """Пути, которые пишут структуру в индекс и НЕ оставляют отпечаток.

    Свойство, а не написание:

      * «пишет структуру» — в теле функции есть запись в таблицу живой схемы
        (`struct_writers` определяется по метке ХРАНИЛИЩА `db_live_table`);
      * «оставляет отпечаток» — из функции достижим вызов функции, в теле
        которой есть ключ хранилища `db_fingerprint`.

    Имена функций в проверке не участвуют: переименование писателя отпечатка
    ничего не меняет (он опознаётся по ключу в своём теле), а подсаженный путь
    без записи ловится — он пишет `db_live_table` и никуда не доходит.
    """
    facts: dict[str, tuple[set[str], set[str]]] = {}
    modules: dict[str, set[str]] = {}
    for module, text in sources.items():
        for func, (literals, calls) in _function_facts(text).items():
            literals_all, calls_all = facts.setdefault(func, (set(), set()))
            literals_all |= literals
            calls_all |= calls
            modules.setdefault(func, set()).add(module)

    fingerprint_writers = {name for name, (literals, _) in facts.items()
                           if any(FINGERPRINT_KEY_MARK in s for s in literals)}
    structure_writers = {name for name, (literals, _) in facts.items()
                         if any(_writes_structure(s) for s in literals)}
    missing: list[tuple[str, str]] = []
    for name, (_, calls) in facts.items():
        writes_here = name in structure_writers
        if not writes_here and not _reaches(calls, structure_writers, facts):
            continue
        if _reaches(calls, fingerprint_writers, facts):
            continue
        missing.append((sorted(modules.get(name, {"?"}))[0], name))
    return sorted(missing)


def _project_sources() -> dict[str, str]:
    """Исходники ядра: то, что уходит в поставку."""
    src = REPO / "src" / "ragraf"
    return {p.name: p.read_text(encoding="utf-8")
            for p in sorted(src.glob("*.py"))}


def fingerprint_paths_checks() -> None:
    """Отпечаток структуры: одна точка записи, идемпотентность, классификатор.

    Проверка не требует живой СУБД: структура берётся из фикстуры в памяти.
    Записи делаются под СИНТЕТИЧЕСКИЕ ревизии и убираются в конце — прогон
    теста не должен оставлять следов в индексе.
    """
    import sqlite3

    from ragraf.config import load as load_config
    from ragraf.embeddings import Embedder
    from ragraf.lexvec import LexVecStore
    from ragraf.revisions import ControlStore, GraphWriter
    import ragraf.db_access as db_access_mod
    import ragraf.db_collect as db_collect_mod
    import ragraf.revisions as revisions_mod

    sources = _project_sources()
    check(bool(sources), f"исходники ядра прочитаны ({len(sources)} файлов)")

    # 1. Инвариант на настоящем коде: путей без отпечатка нет.
    missing = audit_structure_paths(sources)
    check(not missing, "каждый путь записи структуры оставляет отпечаток",
          f"без отпечатка: {missing}")

    # 2. Подсаженный путь: пишет структуру, отпечатка не оставляет — ловится.
    sabotage = {
        "sabotage.py": (
            "def write_structure_only(writer, rev_id):\n"
            "    writer.conn.execute(\n"
            "        'INSERT OR REPLACE INTO db_live_table(rev_id) VALUES(?)',\n"
            "        (rev_id,))\n"
            "    return rev_id\n"
        ),
    }
    planted = audit_structure_paths({**sources, **sabotage})
    check(("sabotage.py", "write_structure_only") in planted,
          "подсаженный путь без записи отпечатка пойман",
          f"найдено: {planted}")

    # 3. Переименование писателя отпечатка не даёт ложного FAIL.
    renamed = {name: text.replace("record_fingerprint", "store_db_fingerprint")
               for name, text in sources.items()}
    check(not audit_structure_paths(renamed),
          "переименование вызова не даёт ложного FAIL",
          f"найдено: {audit_structure_paths(renamed)}")

    # 4. Поведение: запись структуры оставляет отпечаток, повтор её не плодит.
    cfg = load_config(REPO / "config" / "ragraf.yaml")
    control = ControlStore(cfg)
    test_revs = ["rev-i35-a", "rev-i35-b", "rev-i35-c"]
    lex = LexVecStore(cfg)
    embedder = Embedder(cfg, allow_download=False)
    fixture = _fixture_result()
    expected = f"{fixture.dialect}:{index_mod.schema_fingerprint(fixture)}"
    try:
        graph = GraphWriter(cfg)
        stats = writer_mod.write_introspection(graph, test_revs[0], fixture, {},
                                               control=control.conn)
        check(stats.get("fingerprint_stored") is True,
              "запись структуры сообщает факт записи отпечатка")
        stored = index_mod.stored_fingerprint(control.conn, test_revs[0])
        check(stored == expected,
              "отпечаток равен schema_fingerprint собранной структуры",
              f"{stored[:30]} vs {expected[:30]}")

        again = writer_mod.write_introspection(graph, test_revs[0], fixture, {},
                                              control=control.conn)
        rows = control.conn.execute(
            "SELECT COUNT(*) FROM meta WHERE key = ?",
            (f"db_fingerprint:{test_revs[0]}",)).fetchone()[0]
        check(index_mod.stored_fingerprint(control.conn, test_revs[0]) == stored
              and rows == 1 and again.get("fingerprint_stored") is True,
              "повторный прогон не меняет значение и не добавляет запись",
              f"записей {rows}")

        # 5. Три пути на одной структуре дают один отпечаток. Соединения
        #    разные — так же, как у индексатора, панели и CLI.
        cli_stats = db_collect_mod.write(
            None, test_revs[1],
            {"result": fixture, "fingerprint": expected.split(":", 1)[1],
             "samples": {}},
            graph, lex, embedder, control=control.conn)
        writer_mod.write_introspection(graph, test_revs[2], fixture, {},
                                       control=ControlStore(cfg).conn)
        values = [index_mod.stored_fingerprint(control.conn, rev)
                  for rev in test_revs]
        check(len(set(values)) == 1 and values[0] == expected,
              "три пути дают один отпечаток на одной структуре",
              f"{[v[:24] for v in values]}")
        check(cli_stats.get("fingerprint") == expected.split(":", 1)[1],
              "CLI-путь передаёт тот же отпечаток, что записывает")

        # Классификатор сравнивает текущую структуру с записанной в индексе,
        # поэтому структура фикстуры предъявляется под ревизию сравнения.
        compare_rev = "rev-i35-cls"
        writer_mod.write_introspection(graph, compare_rev, fixture, {},
                                       control=control.conn)

        # 6. Классификатор: три исхода. Живая СУБД не нужна — проба, сбор и
        #    control подменяются, потому что проверяется вердикт, а не сбор.
        class _FakeAccess:
            enabled = True
            dialect = "mysql"

        class _FakeStore:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def load(self):
                return _FakeAccess()

        class _FakeControl:
            def __init__(self, *args, **kwargs) -> None:
                self.conn = sqlite3.connect(":memory:")
                self.conn.row_factory = sqlite3.Row
                self.conn.execute(
                    "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                self.conn.execute(
                    "CREATE TABLE rev_history(rev_id TEXT PRIMARY KEY)")
                self.conn.execute(
                    "INSERT INTO rev_history(rev_id) VALUES('rev-i35-cls')")
                self.conn.execute(
                    "INSERT INTO meta(key, value) VALUES(?, ?)",
                    ("db_fingerprint:rev-i35-cls", expected))
                self.conn.commit()

            def close(self) -> None:
                self.conn.close()

        saved = (db_access_mod.DbAccessStore, db_collect_mod.probe,
                 db_collect_mod.introspect, revisions_mod.ControlStore)
        verdicts = {}
        try:
            db_access_mod.DbAccessStore = _FakeStore
            db_collect_mod.probe = lambda access=None: {"ok": True}
            revisions_mod.ControlStore = _FakeControl

            def _verdict(probe_result) -> dict:
                db_collect_mod.introspect = lambda access: probe_result
                return index_mod.db_structure_changed(cfg)

            verdicts["same"] = _verdict(fixture)
            data_only = _fixture_result()
            data_only.databases[0].tables[1].row_estimate = 99999
            data_only.databases[0].tables[1].data_bytes = 123456
            verdicts["data"] = _verdict(data_only)
            altered = _fixture_result()
            altered.databases[0].tables[0].columns.append(
                index_mod.Column(name="probe_added", data_type="int"))
            verdicts["structure"] = _verdict(altered)
        finally:
            (db_access_mod.DbAccessStore, db_collect_mod.probe,
             db_collect_mod.introspect, revisions_mod.ControlStore) = saved

        check(verdicts["same"]["changed"] is False
              and "changes" not in verdicts["same"],
              "та же структура: молчание, без перечня изменений",
              str(verdicts["same"]))
        check(verdicts["structure"]["changed"] is True
              and any("probe_added" in item
                      for item in verdicts["structure"].get("changes") or []),
              "изменённая структура: находка называет изменение",
              str(verdicts["structure"].get("changes")))
        check(verdicts["data"]["changed"] is False
              and verdicts["data"]["stored"] == verdicts["data"]["current"],
              "изменены только данные: отпечаток не двигается",
              str(verdicts["data"]))
    finally:
        # Уборка: ни одной строки тестовых ревизий в индексе.
        try:
            for rev in [*test_revs, "rev-i35-cls"]:
                lex.purge_rev(rev)
                graph.conn.execute("DELETE FROM db_live_table WHERE rev_id = ?", (rev,))
                graph.conn.execute("DELETE FROM db_live_column WHERE rev_id = ?", (rev,))
                graph.conn.execute("DELETE FROM db_live_index WHERE rev_id = ?", (rev,))
                graph.conn.execute("DELETE FROM db_live_relation WHERE rev_id = ?", (rev,))
                graph.conn.execute("DELETE FROM db_live_meta WHERE rev_id = ?", (rev,))
                graph.conn.execute("DELETE FROM db_meta WHERE rev_id = ?", (rev,))
                control.conn.execute("DELETE FROM meta WHERE key = ?",
                                     (f"db_fingerprint:{rev}",))
            graph.conn.commit()
            control.conn.commit()
            check(True, "следы тестовых ревизий отпечатка убраны")
        except Exception as exc:
            check(False, "следы тестовых ревизий отпечатка убраны",
                  f"{type(exc).__name__}: {exc}")
        finally:
            try:
                lex.close()
            except Exception:
                pass
            control.close()


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
    print("=== отпечаток структуры: единая точка записи (без живой СУБД) ===")
    fingerprint_paths_checks()
    if not MYSQL_PASSWORD:
        print("ПРОПУСК: проверки живых СУБД не выполнялись — нет RAGRAF_DB_PASSWORD\n"
              "и нет значения в catalog/db_access.json. Это НЕ «пройдено»:\n"
              "код возврата 2 означает, что осмотрено ноль проверок СУБД.")
        # Провал проверок отпечатка не прячется за пропуском живой СУБД.
        return 1 if FAILED else 2
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
    atexit.register(_drop_tmp, tmp)
    # Каталог убирается после прогона: прямой запуск набора иначе
    # оставляет его во временном хранилище (замер 29.10.2026: tmp
    # копился при каждом запуске).
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
    stats = writer_mod.write_introspection(graph, ctl_rev, result, {},
                                          control=ctl.conn)
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
        # Ключ отпечатка живёт в control.sqlite: уборка без него оставила бы
        # запись под ревизией, которой в индексе нет.
        ctl.conn.execute("DELETE FROM meta WHERE key = ?",
                         (f"db_fingerprint:{ctl_rev}",))
        ctl.conn.commit()
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
