"""Проверка сверки «код ↔ база»: разбор SQL и выводы `cross_check`.

Зачем отдельная проверка. Операция `db_cross` отвечает на вопрос «можно ли
писать в эту таблицу и кто в неё пишет», поэтому её выводы обязаны быть
обоснованными, а не «похоже на имя таблицы». Замер 14.10.2026 на корпусе BWO
показал, из чего складывается ложная находка: из прозы (`// On create update
all the other records` → `all`), из служебного слова (`INSERT INTO` → `into`,
3701 упоминание) и из текста без кавычек. Все три случая здесь закреплены.

Проверяются ДВЕ части:
  1. разбор SQL по тексту — чистые функции, без базы и без индекса;
  2. выводы `cross_check` — на синтетической базе в памяти.

Стиль — самостоятельный скрипт с `check`, как остальные проверки проекта.
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import analysis, db_ops, util  # noqa: E402

PASS = 0
FAIL = 0


def check(condition: bool, title: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK ] {title}")
    else:
        FAIL += 1
        print(f"  [FAIL] {title}" + (f"\n      {detail}" if detail else ""))


def names(text: str, start_line: int = 1, lang: str = "") -> list[str]:
    return [n["name"] for n in analysis.sql_table_names(text, start_line, lang=lang)]


print("=== 1. Полные конструкции SQL распознаются ===")
php = """<?php
$q1 = "SELECT Nominal FROM StringData WHERE FileID = 1";
$q2 = "INSERT INTO cellvoltagedata (A) VALUES (1)";
$q3 = "UPDATE TestConfig SET A=1";
$q4 = "DELETE FROM TestData WHERE x=1";
$q5 = "SELECT a FROM Users u JOIN Contracts c ON c.id = u.id";
"""
got = names(php, 10)
for expected in ("StringData", "cellvoltagedata", "TestConfig", "TestData",
                 "Users", "Contracts"):
    check(expected in got, f"найдено имя {expected}", str(got))
check(got.count("Users") == 1, "имя не дублируется", str(got))

print("\n=== 2. Написание сохраняется, строки считаются ===")
items = analysis.sql_table_names(php, 10)
by_name = {i["name"]: i for i in items}
check(by_name["StringData"]["line"] == 11,
      "строка первой конструкции верна", str(by_name["StringData"]["line"]))
check(by_name["TestConfig"]["line"] == 13,
      "строка третьей конструкции верна", str(by_name["TestConfig"]["line"]))
check("SELECT" in by_name["StringData"]["sql"],
      "доказательство содержит сам запрос", by_name["StringData"]["sql"][:60])

print("\n=== 3. Ложные имена не берутся ===")
junk = """// On create update all the other records to make room
/* update the existing rows in the joined table */
* update any row that matches
# update all
-- update the
$log = "Deleted 12 records from $table";
"""
check(names(junk, 1) == [], "проза и комментарии не дают имён", str(names(junk, 1)))
check(names('$sql = "SELECT * FROM (SELECT 1) x";', 1) == [],
      "выражение вместо имени таблицы не даёт")
check("into" not in names('$sql = "INSERT INTO Users (a) VALUES (1)";', 1),
      "служебное слово INTO не становится таблицей",
      str(names('$sql = "INSERT INTO Users (a) VALUES (1)";', 1)))
check("__future__" not in names("from __future__ import annotations", 1),
      "имена с подчёркиванием отбрасываются")
check(names('$sql = "SELECT a FROM Users";', 1),
      "FROM без SELECT впереди не берётся (нет SELECT — нет имени)",
      str(names('$sql = "FROM Users";', 1)))
check(names('$sql = "UPDATE Users SET a=1";', 1) == ["Users"],
      "UPDATE с SET распознан")
check(names('$sql = "UPDATE Users WHERE a=1";', 1) == [],
      "UPDATE без SET не распознан")

print("\n=== 4. Блочный литерал без кавычек на строке ===")
heredoc = """<?php
$sql = <<<SQL
SELECT Id FROM Chargers WHERE Active = 1
SQL;
"""
check("Chargers" in names(heredoc, 1, lang="php"),
      "SQL из HEREDOC распознан", str(names(heredoc, 1, lang="php")))
check(names("SELECT Id FROM Chargers", 1, lang="markdown") == [],
      "тот же текст в документации именем не считается",
      str(names("SELECT Id FROM Chargers", 1, lang="markdown")))

print("\n=== 5. Слепые зоны: имя вычисляется во время выполнения ===")
dyn = analysis.dynamic_sql_sites("""<?php
$a = "SELECT * FROM " . $t . " WHERE 1";
$b = "DELETE FROM {$tbl} WHERE FileID=?";
$c = "SELECT * FROM Users WHERE id = ?";
""", 20)
kinds = [d["kind"] for d in dyn]
check(len(dyn) >= 2, "найдены места с вычисляемым именем", str(dyn))
check(all("Users" not in d["fragment"] for d in dyn),
      "литеральное имя в слепые зоны не попадает", str(dyn))
check(any(d["line"] == 21 for d in dyn), "строка слепой зоны верна",
      str([d["line"] for d in dyn]))
check(analysis.dynamic_sql_sites("обычный текст без SQL", 1) == [],
      "текст без SQL ничего не даёт")

print("\n=== 6. Правило строки чанка: заголовок не сдвигает адрес ===")
symbol = "function f (src/x.php:30)\nSELECT a FROM Users\n"
check(util.chunk_header_lines(symbol) == 1, "заголовок распознан")
# Текст[1] — первая строка КОДА, и она соответствует `start_line`: заголовок
# занимает строку 0 и адреса не сдвигает.
check(util.text_line(symbol, symbol.find("SELECT"), 30) == 30,
      "строка внутри функции считается без заголовка",
      str(util.text_line(symbol, symbol.find("SELECT"), 30)))
check(util.text_line("SELECT a FROM Users\n", 0, 30) == 30,
      "строка файла без заголовка считается от start_line")


def make_graph(live: list[tuple], migrations: list[tuple] = ()) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE db_live_table (rev_id TEXT, table_name TEXT,"
                 " database_name TEXT, row_estimate INTEGER, data_bytes INTEGER)")
    conn.execute("CREATE TABLE db_table (rev_id TEXT, table_name TEXT, source TEXT)")
    for table, database, rows, size in live:
        conn.execute("INSERT INTO db_live_table VALUES ('rev-1',?,?,?,?)",
                     (table, database, rows, size))
    for table, source in migrations:
        conn.execute("INSERT INTO db_table VALUES ('rev-1',?,?)", (table, source))
    conn.commit()
    return conn


def facts(*items) -> dict:
    """Факты по имени: (имя, доступ, путь, строка, через что)."""
    out: dict[str, dict] = {}
    for name, access, path, line, via in items:
        rec = out.setdefault(name.lower(), {"name": name, "edges": [], "text": []})
        entry = {"path": path, "line": line, "access": access, "via": via, "sql": ""}
        (rec["edges"] if via == "edge" else rec["text"]).append(entry)
    return out


print("\n=== 7. cross_check: находки с доказательствами ===")
conn = make_graph(
    live=[("users", "app", 100, 4096), ("orders", "app", 0, 0),
          ("legacy_big", "other", 38260, 4734976), ("chargers", "app", 5, 100),
          ("stringdata", "app", 10, 50)],
    migrations=[("future_table", "migrations/001.sql")])
data = db_ops.cross_check(conn, "rev-1", facts(
    ("Users", "select", "src/a.php", 10, "edge"),
    ("Missing", "", "src/b.php", 20, "text"),
    ("stringdata", "insert", "src/c.php", 30, "edge"),
    ("future_table", "select", "src/d.php", 40, "edge"),
))
missing = {m["name"]: m for m in data["used_missing"]}
check("Missing" in missing, "имя без таблицы попало в used_missing", str(list(missing)))
check("Users" not in missing, "имя, совпавшее по регистру, не считается отсутствующим")
check(missing["Missing"]["evidence"] == [
    {"path": "src/b.php", "line": 20, "via": "text", "access": "", "sql": ""}],
    "у находки есть доказательство path:line", str(missing["Missing"]["evidence"]))
check("future_table" not in missing,
      "таблица из миграций не считается отсутствующей")
check(data["checked_migrations"] == 1, "миграции учтены", str(data["checked_migrations"]))
check(data["checked_live"] == 5, "живые таблицы учтены", str(data["checked_live"]))

print("\n=== 8. Регистр и базы, с которыми работает код ===")
check(data["case_mismatch"] == [{"code": "Users", "db": "users"}],
      "расхождение регистра названо", str(data["case_mismatch"]))
check(data["databases_in_use"] == ["app"],
      "названы базы, с которыми код работает", str(data["databases_in_use"]))
unused_names = [u["name"] for u in data["unused"]]
check("legacy_big" in unused_names and "orders" in unused_names,
      "неиспользуемые таблицы перечислены", str(unused_names))
in_use_flags = [u["database_in_use"] for u in data["unused"]]
check(in_use_flags == sorted(in_use_flags, reverse=True),
      "таблицы рабочих баз идут раньше чужих", str(data["unused"]))
check(all(u["database_in_use"] for u in data["unused"][:2]),
      "признак «база используется» проставлен", str(data["unused"][:2]))
check(data["unused"][-1]["database_in_use"] is False,
      "таблица чужой базы уходит в конец", str(data["unused"][-1]))

print("\n=== 9. write_paths: кто пишет ===")
check(list(data["write_paths"]) == ["stringdata"],
      "пишущие таблицы названы", str(list(data["write_paths"])))
check(data["write_paths"]["stringdata"][0]["access"] == "insert",
      "вид доступа записан")
check("Users" not in data["write_paths"],
      "чтение не выдаётся за запись", str(list(data["write_paths"])))

print("\n=== 10. Плаузибельность: код и база одного проекта? ===")
alien = db_ops.cross_check(make_graph(live=[("таблица_чужого_проекта", "other", 1, 1)]),
                           "rev-1", facts(("Только_в_коде", "", "src/x.php", 1, "text")))
check(alien["plausible"] is False, "несовпадение названо неправдоподобным")
check("ДРУГОМУ проекту" in alien["note"], "предупреждение объясняет причину",
      alien["note"][:80])

print("\n=== 11. Совместимость: множество имён вместо фактов ===")
legacy = db_ops.cross_check(make_graph(live=[("users", "app", 1, 1)]),
                            "rev-1", {"Users", "Ghost"})
check([m["name"] for m in legacy["used_missing"]] == ["Ghost"],
      "множество имён принимается", str(legacy["used_missing"]))
check(legacy["used_missing"][0]["evidence"] == [],
      "без фактов доказательств нет — и это не выдумка", str(legacy["used_missing"]))

print("\n=== 12. Живого слоя нет — честный отказ, а не пустые списки ===")
empty_conn = sqlite3.connect(":memory:")
empty_conn.row_factory = sqlite3.Row
no_layer = db_ops.cross_check(empty_conn, "rev-1", facts(("X", "", "a.php", 1, "text")))
check(no_layer["checked"] == 0 and no_layer["plausible"] is False,
      "отсутствие слоя названо", str(no_layer["note"]))
check("не собран" in no_layer["note"], "причина названа словами", no_layer["note"])

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
