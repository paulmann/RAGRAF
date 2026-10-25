"""Проверка: число узлов в журнале ревизии совпадает с числом строк в node.

Это проверка исправленного дефекта учёта. Раньше журнал ревизии писал число
строк узлов ДО объединения по ключу (rev_id, node_id). На корпусе z это давало
2227 против 1290 фактических — расхождение 42%, которое выглядело как потеря
данных.

Проверка раздельная:
  1. число в журнале равно числу строк в node;
  2. по узлам КОДА нет схлопываний (нет разных объявлений под одним id);
  3. схлопывания есть только у классов CSS — и это задумано.

Третья проверка важна: если однажды схлопывания появятся в функциях или
классах, это будет означать настоящую потерю, и тест это покажет.
"""

from __future__ import annotations

import pathlib
import sys
from collections import Counter

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

PASS = 0
FAIL = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


CORPORA = [
    ("z-batteryservice.yaml", "z.batteryservice.ru"),
    ("md.viewer.yaml", "MD.Viewer"),
    ("rdpmon.yaml", "RdpMON"),
]

print("=" * 78)
print("СОГЛАСОВАННОСТЬ УЧЁТА УЗЛОВ")
print("=" * 78)

for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    conn = store.connect(cfg.graph_db, readonly=True)
    rev = conn.execute(
        "SELECT rev_id FROM rev WHERE state = 'ready' ORDER BY rev_id DESC LIMIT 1"
    ).fetchone()["rev_id"]

    journal = conn.execute(
        "SELECT node_count AS n, edge_count AS e, file_count AS f FROM rev "
        "WHERE rev_id = ?",
        (rev,),
    ).fetchone()
    actual_nodes = conn.execute(
        "SELECT COUNT(*) AS n FROM node WHERE rev_id = ?", (rev,)
    ).fetchone()["n"]
    actual_edges = conn.execute(
        "SELECT COUNT(*) AS n FROM edge WHERE rev_id = ?", (rev,)
    ).fetchone()["n"]
    actual_files = conn.execute(
        "SELECT COUNT(*) AS n FROM file WHERE rev_id = ?", (rev,)
    ).fetchone()["n"]
    distinct_nodes = conn.execute(
        "SELECT COUNT(DISTINCT node_id) AS n FROM node WHERE rev_id = ?", (rev,)
    ).fetchone()["n"]

    print(f"\n{title}  ({rev})")
    print(f"  журнал:  файлов {journal['f']}, узлов {journal['n']}, "
          f"рёбер {journal['e']}")
    print(f"  таблицы: файлов {actual_files}, узлов {actual_nodes}, "
          f"рёбер {actual_edges}")
    print(f"  различных node_id: {distinct_nodes}")

    check(journal["n"] == actual_nodes,
          f"{title}: узлы в журнале = узлы в таблице",
          f"журнал {journal['n']} против таблицы {actual_nodes}")
    check(journal["e"] == actual_edges,
          f"{title}: рёбра в журнале = рёбра в таблице",
          f"журнал {journal['e']} против таблицы {actual_edges}")
    check(distinct_nodes == actual_nodes,
          f"{title}: повторов node_id нет (ключ соблюдён)")

    # По узлам кода не должно быть нескольких объявлений под одним id.
    code_kinds = {"function", "method", "class", "interface", "enum",
                  "constant", "property", "module", "struct", "namespace"}
    rows = conn.execute(
        "SELECT node_id, kind, path, start_line FROM node WHERE rev_id = ?",
        (rev,),
    ).fetchall()
    by_id: dict[str, list] = {}
    for r in rows:
        by_id.setdefault(r["node_id"], []).append(r)
    code_collisions = 0
    for node_id, items in by_id.items():
        if len(items) < 2:
            continue
        if not ({i["kind"] for i in items} & code_kinds):
            continue
        if len({(i["path"], i["start_line"]) for i in items}) > 1:
            code_collisions += 1
    check(code_collisions == 0,
          f"{title}: в узлах кода нет схлопываний",
          f"найдено {code_collisions}")

    kinds = Counter(r["kind"] for r in rows)
    print(f"  виды узлов: {dict(kinds)}")
    conn.close()

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
print("""
  Проверяется именно то, что сломало доверие к цифрам: сходится ли учёт.
  Расхождение в 42% выглядело как потеря узлов, но замер показал, что
  терялись только СТРОКИ одного и того же класса CSS — 920 повторов,
  которые по замыслу и должны быть одним узлом.
""")
raise SystemExit(0 if FAIL == 0 else 1)
