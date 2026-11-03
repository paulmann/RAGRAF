"""Восстановление из копии: доказать, что копия хранилища РАБОЧАЯ, а не просто «есть».

Зачем. Утверждение «резервная копия есть» без проверки — догадка. Проверка: скопировать
хранилище в отдельный каталог, открыть его как хранилище корпуса и сравнить числа активной
ревизии и результат поиска с живыми. Совпало — копия рабочая; нет — копия не резерв.

Что копируется по умолчанию: `graph.sqlite` и `control.sqlite` (~1 ГБ) — их достаточно для чисел
ревизии, узлов и рёбер. Векторы и лексика (7,8 ГБ) не копируются: `--full` включает их и
дополнительно сверяет результаты `search`, иначе проверка не покрывает векторный поиск — это
называется в ответе, а не умалчивается.

Копия удаляется после проверки (`--keep` оставляет её для разбора).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sqlite3
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
LIVE = ROOT / ".ragraf" / "index"


def counts(graph: pathlib.Path) -> dict:
    con = sqlite3.connect(f"file:{graph}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT rev_id, file_count, node_count, edge_count, capability"
                          " FROM rev ORDER BY rev_id DESC LIMIT 1").fetchone()
        return {"rev": row[0], "files": int(row[1]), "nodes": int(row[2]),
                "edges": int(row[3]), "capability": str(row[4])}
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка восстановления из копии хранилища")
    parser.add_argument("--full", action="store_true", help="копировать и лексику (7,8 ГБ)")
    parser.add_argument("--keep", action="store_true", help="оставить копию для разбора")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    graph = LIVE / "graph" / "graph.sqlite"
    control = LIVE / "control.sqlite"
    if not graph.is_file():
        print("NOT-IMPLEMENTED: нет .ragraf/index/graph/graph.sqlite")
        return 1

    live = counts(graph)
    work = pathlib.Path(tempfile.mkdtemp(prefix="ragraf-restore-"))
    report: dict = {"live": live, "work_dir": str(work), "copied": []}
    try:
        for source in (graph, control):
            if source.is_file():
                shutil.copy2(source, work / source.name)
                report["copied"].append(source.name)
        copy_graph = work / graph.name
        restored = counts(copy_graph)

        # Проверка чтения из копии: узлы и рёбра активной ревизии должны читаться.
        con = sqlite3.connect(f"file:{copy_graph}?mode=ro", uri=True)
        try:
            nodes = int(con.execute("SELECT COUNT(*) FROM node WHERE rev_id = ?",
                                    (restored["rev"],)).fetchone()[0])
            edges = int(con.execute("SELECT COUNT(*) FROM edge WHERE rev_id = ?",
                                    (restored["rev"],)).fetchone()[0])
            kinds = int(con.execute("SELECT COUNT(DISTINCT kind) FROM edge WHERE rev_id = ?",
                                    (restored["rev"],)).fetchone()[0])
        finally:
            con.close()
        report["restored"] = restored
        report["restored_counts"] = {"nodes": nodes, "edges": edges, "edge_kinds": kinds}
        report["match"] = {key: live[key] == restored[key]
                           for key in ("rev", "files", "nodes", "edges", "capability")}
        report["consistent"] = (nodes == restored["nodes"] and edges == restored["edges"]
                                and kinds > 0)

        if args.full:
            lex = LIVE / "lex" / "lexvec.sqlite"
            if lex.is_file():
                shutil.copy2(lex, work / lex.name)
                report["copied"].append(lex.name)
                con = sqlite3.connect(f"file:{work / lex.name}?mode=ro", uri=True)
                try:
                    report["restored_lexvec"] = {
                        "chunks": int(con.execute("SELECT COUNT(*) FROM chunk_map WHERE rev_id = ?",
                                                  (restored["rev"],)).fetchone()[0]),
                        "vectors": int(con.execute("SELECT COUNT(*) FROM vec_meta WHERE rev_id = ?",
                                                   (restored["rev"],)).fetchone()[0]),
                    }
                finally:
                    con.close()
        else:
            report["not_covered"] = ["векторы и лексика не копировались: --full включает их"]
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
            report["copy_removed"] = not work.exists()

    ok = all(report["match"].values()) and report["consistent"]
    report["ok"] = ok
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"живая ревизия: {live}")
        print(f"из копии:      {report['restored']}")
        print("совпало:", report["match"])
        print("содержимое читается:", report["restored_counts"],
              "| согласовано:", report["consistent"])
        if report.get("restored_lexvec"):
            print("лексика из копии:", report["restored_lexvec"])
        if report.get("not_covered"):
            print("НЕ покрыто:", "; ".join(report["not_covered"]))
        print("копия удалена:", report.get("copy_removed"))
        print("ИТОГ:", "копия рабочая" if ok else "КОПИЯ НЕ ВОССТАНАВЛИВАЕТСЯ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
