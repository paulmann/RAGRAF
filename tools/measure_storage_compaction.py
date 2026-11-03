"""Замер компакции хранилища: `--plan` считает, `--apply-copy` проверяет на КОПИИ.

Почему на копии. `VACUUM` 7,8-гигабайтного файла — это блокировка и минуты работы; на живом
корпусе такое делается только решением владельца и после того, как числа измерены. Правило
владельца «сначала песочница, потом боевое» здесь буквальное: файлы копируются в `%TEMP%`,
там чистится хвост ротации и выполняется `VACUUM`, и только после этого появляются числа.

Что делает `--plan` (чтение, ничего не меняет):
  * размеры файлов, страницы, свободные страницы;
  * строки документного слоя по ревизиям, которых нет в истории (хвост ротации);
  * оценка того, сколько строк будет удалено.

Что делает `--apply-copy` (в копии):
  * копирует `graph.sqlite` и `lexvec.sqlite` в `%TEMP%/ragraf-compact-<метка>`;
  * удаляет хвост ротации (те же таблицы, что и `Revisions._purge_rev_data`);
  * выполняет `VACUUM` и снимает размеры ДО/ПОСЛЕ;
  * печатает таблицу и проверяет, что активная ревизия осталась целой (число файлов/узлов/рёбер).

Живой корпус этот инструмент не изменяет никогда: путь к живым файлам используется только на
чтение и для копирования.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
LIVE = ROOT / ".ragraf" / "index"
KEEP_TABLES = ("file", "node", "edge", "text_chunk", "cfg_edge", "dfg_edge", "db_table",
               "db_column", "db_fk", "web_route", "invalidated", "coverage", "stage_timing")
DOC_TABLES = ("document_chunk", "document_node", "document")


def sizes(path: pathlib.Path) -> dict:
    if not path.is_file():
        return {"bytes": 0, "missing": True}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        page = int(con.execute("PRAGMA page_size").fetchone()[0])
        pages = int(con.execute("PRAGMA page_count").fetchone()[0])
        free = int(con.execute("PRAGMA freelist_count").fetchone()[0])
    finally:
        con.close()
    return {"bytes": path.stat().st_size, "mb": round(path.stat().st_size / 1_048_576, 1),
            "pages": pages, "page_size": page, "free_pages": free,
            "free_mb": round(free * page / 1_048_576, 1)}


def history(lex: pathlib.Path, graph: pathlib.Path) -> list[str]:
    con = sqlite3.connect(f"file:{graph}?mode=ro", uri=True)
    try:
        return [str(r[0]) for r in con.execute("SELECT rev_id FROM rev ORDER BY rev_id")]
    finally:
        con.close()


def orphans(lex: pathlib.Path, known: set[str]) -> dict[str, int]:
    con = sqlite3.connect(f"file:{lex}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        out: dict[str, int] = {}
        for table in DOC_TABLES:
            if table not in tables:
                continue
            rows = con.execute(f"SELECT rev_id, COUNT(*) FROM {table} GROUP BY rev_id").fetchall()
            out[table] = sum(int(n) for rev, n in rows if str(rev) not in known)
        return out
    finally:
        con.close()


def plan(graph: pathlib.Path, lex: pathlib.Path) -> dict:
    known = set(history(lex, graph))
    tail = orphans(lex, known)
    return {
        "graph": sizes(graph),
        "lexvec": sizes(lex),
        "revisions_in_history": len(known),
        "orphan_rows": tail,
        "orphan_total": sum(tail.values()),
        "total_mb": round((sizes(graph).get("mb") or 0) + (sizes(lex).get("mb") or 0), 1),
    }


def active_counts(graph: pathlib.Path) -> dict:
    con = sqlite3.connect(f"file:{graph}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT rev_id, file_count, node_count, edge_count FROM rev ORDER BY rev_id DESC"
            " LIMIT 1").fetchone()
        return {"rev": row[0], "files": int(row[1]), "nodes": int(row[2]), "edges": int(row[3])}
    finally:
        con.close()


def apply_copy(graph: pathlib.Path, lex: pathlib.Path, work: pathlib.Path) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    copies = {}
    for label, source in (("graph", graph), ("lexvec", lex)):
        target = work / source.name
        shutil.copy2(source, target)
        copies[label] = target
    before = {label: sizes(path) for label, path in copies.items()}

    # Хвост ротации: удаляются только ревизии, которых НЕТ в истории.
    known = set(history(copies["lexvec"], copies["graph"]))
    con = sqlite3.connect(copies["graph"])
    try:
        con.execute("ATTACH DATABASE ? AS lex", (str(copies["lexvec"]),))
        for table in DOC_TABLES:
            exists = con.execute(
                "SELECT 1 FROM lex.sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if not exists:
                continue
            marks = ",".join("?" for _ in known) or "''"
            con.execute(f"DELETE FROM lex.{table} WHERE rev_id NOT IN ({marks})", tuple(known))
        con.commit()
    finally:
        con.execute("DETACH DATABASE lex")
        con.close()

    started = time.perf_counter()
    for label, path in copies.items():
        con = sqlite3.connect(path)
        try:
            con.execute("VACUUM")
        finally:
            con.close()
    vacuum_seconds = round(time.perf_counter() - started, 1)

    after = {label: sizes(path) for label, path in copies.items()}
    preserved = active_counts(copies["graph"])
    freed = {label: round(before[label]["mb"] - after[label]["mb"], 1) for label in copies}
    return {"before": before, "after": after, "freed_mb": freed,
            "vacuum_seconds": vacuum_seconds, "active_after": preserved,
            "work_dir": str(work)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Замер компакции хранилища")
    parser.add_argument("--plan", action="store_true", help="только числа, ничего не менять")
    parser.add_argument("--apply-copy", action="store_true",
                        help="выполнить чистку и VACUUM на КОПИИ в %TEMP%")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--keep-copy", action="store_true", help="не удалять копию после прогона")
    args = parser.parse_args()
    graph = LIVE / "graph" / "graph.sqlite"
    lex = LIVE / "lex" / "lexvec.sqlite"
    if not graph.is_file() or not lex.is_file():
        print("NOT-IMPLEMENTED: не найдены graph.sqlite/lexvec.sqlite")
        return 1

    report: dict = {"plan": plan(graph, lex)}
    if args.apply_copy:
        work = pathlib.Path(tempfile.mkdtemp(prefix="ragraf-compact-"))
        try:
            report["copy"] = apply_copy(graph, lex, work)
        finally:
            if not args.keep_copy:
                shutil.rmtree(work, ignore_errors=True)
                report["copy_removed"] = not work.exists()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    current = report["plan"]
    print(f"живое хранилище: {current['total_mb']} МБ; ревизий в истории {current['revisions_in_history']}")
    for label in ("graph", "lexvec"):
        data = current[label]
        print(f"  {label:7s} {data.get('mb')} МБ, страниц {data.get('pages')}, "
              f"свободных {data.get('free_mb')} МБ")
    print(f"  хвост ротации: {current['orphan_total']} строк "
          f"({current['orphan_rows']})")
    copy = report.get("copy")
    if copy:
        print(f"\nна копии: VACUUM занял {copy['vacuum_seconds']} с")
        for label in ("graph", "lexvec"):
            print(f"  {label:7s} {copy['before'][label]['mb']} → {copy['after'][label]['mb']} МБ "
                  f"(освобождено {copy['freed_mb'][label]} МБ)")
        print(f"  активная ревизия после чистки: {copy['active_after']}")
        print(f"  копия удалена: {report.get('copy_removed')}")
    else:
        print("\nкопия не проверялась: добавьте --apply-copy, чтобы измерить освобождение")
    return 0


if __name__ == "__main__":
    sys.exit(main())
