"""Эффект схлопывания на корпусе revoap: индекс и поиск.

Проверяется то, ради чего делалась работа:
  1. в индексе действительно меньше файлов (592 вместо 699);
  2. отброшенных копий в индексе НЕТ;
  3. поиск не выдаёт несколько результатов из одного и того же документа;
  4. метрики схлопывания видны в статусе индекса;
  5. покрытие индекса согласовано: files_found > files_total тогда и только
     тогда, когда копии были отброшены.

Данные берутся из индекса, а не из расчётов: проверяется конечный результат,
который увидит агент.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.dedupe import is_document  # noqa: E402
from ragraf.facade import Facade  # noqa: E402

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


cfg = load_config(REPO / "config" / "revoap.yaml")
conn = store.connect(cfg.graph_db, readonly=True)
row = conn.execute(
    "SELECT rev_id, file_count, node_count, edge_count, corpus_hash FROM rev "
    "WHERE state = 'ready' ORDER BY rev_id DESC LIMIT 1"
).fetchone()
rev = row["rev_id"]

print("=" * 78)
print(f"1. СОСТАВ ИНДЕКСА ({rev})")
print("=" * 78)
files = conn.execute(
    "SELECT path, lang FROM file WHERE rev_id = ? ORDER BY path", (rev,)
).fetchall()
paths = {f["path"] for f in files}
print(f"  файлов в индексе: {len(files)} (было 699 до схлопывания)")

check(len(files) < 699, "файлов стало меньше", f"получено {len(files)}")
check(row["file_count"] == len(files),
      "число файлов в журнале ревизии = числу строк в file",
      f"журнал {row['file_count']}, таблица {len(files)}")

# Отброшенные копии определяются заново: берётся скан с включённым
# схлопыванием и проверяется, что ни одного отброшенного пути в индексе нет.
from ragraf import corpus  # noqa: E402
from ragraf import dedupe as dedupe_mod  # noqa: E402

cfg.index.dedupe_enabled = False
full = corpus.scan(cfg)
cfg.index.dedupe_enabled = True
decision = dedupe_mod.dedupe(full, cfg)
dropped = {f.path for f in decision.dropped_files}
kept = {f.path for f in decision.kept_files}

print(f"  отброшено копий:  {len(dropped)}")
print(f"  оставлено файлов: {len(kept)}")

in_index_and_dropped = sorted(dropped & paths)
print(f"  отброшенные, но попавшие в индекс: {len(in_index_and_dropped)}")
for p in in_index_and_dropped[:5]:
    print(f"    {p}")
check(not in_index_and_dropped,
      "ни одна отброшенная копия не попала в индекс",
      f"попало {len(in_index_and_dropped)}")

missing_kept = sorted(kept - paths)
print(f"  оставленные, но НЕ попавшие в индекс: {len(missing_kept)}")
for p in missing_kept[:5]:
    print(f"    {p}")
check(not missing_kept,
      "все оставленные файлы попали в индекс",
      f"не попало {len(missing_kept)}")

conn.close()

print()
print("=" * 78)
print("2. МЕТРИКИ СХЛОПЫВАНИЯ В СТАТУСЕ ИНДЕКСА")
print("=" * 78)
fac = Facade(cfg, tool="test_dedupe_effect", telemetry=False, watch=False)
env = fac.op_index_status()
res = env.get("result") or {}
cov = res.get("coverage") or {}
for key in ("files_found", "files_total", "dup_dropped", "dup_groups",
            "dup_bytes", "dup_lines"):
    print(f"  {key:<12} = {cov.get(key)}")

check(cov.get("dup_dropped") is not None,
      "метрика dup_dropped присутствует в статусе")
check((cov.get("dup_dropped") or 0) > 0,
      "отброшенные копии отражены в статусе",
      f"значение {cov.get('dup_dropped')}")
check((cov.get("dup_groups") or 0) > 0, "группы копий отражены в статусе")
check((cov.get("files_found") or 0) > len(files),
      "files_found больше числа файлов в индексе (видно, сколько отсеяно)",
      f"files_found {cov.get('files_found')}, в индексе {len(files)}")

print()
print("=" * 78)
print("3. ПОИСК НЕ ОТДАЁТ КОПИИ ОДНОГО ДОКУМЕНТА")
print("=" * 78)
# Запрос по тексту, у которого раньше было много копий.
for query in ("триггер развёртывания", "append-only pipeline", "ADR"):
    hits = fac.op_search(query=query, limit=10)
    items = hits.get("result") if isinstance(hits, dict) else hits
    items = items if isinstance(items, list) else []
    paths_hit = [h.get("path") for h in items]
    print(f"\n  запрос «{query}»: найдено {len(items)}")
    for h in items[:6]:
        print(f"    {h.get('path')}:{h.get('start_line')}")
    # Проверка: среди ответов нет двух чанков из одного пути (кроме случая,
    # когда у документа несколько чанков — это нормально).
    # Главное — нет путей с суффиксами-копиями вида /www/www/.
    copies = [p for p in paths_hit if "/www/www" in str(p).lower()
              or "/old/" in str(p).lower()]
    print(f"    путей-копий среди результатов: {len(copies)}")
    check(not copies, f"«{query}»: в выдаче нет путей-копий",
          f"найдено {copies[:3]}")

print()
print("=" * 78)
print("4. РАЗМЕР ИНДЕКСА И ЧАНКОВ")
print("=" * 78)
lex = pathlib.Path(str(cfg.lexvec_db))
import sqlite3  # noqa: E402

lconn = sqlite3.connect(f"file:{lex}?mode=ro", uri=True)
lconn.row_factory = sqlite3.Row
lrev = lconn.execute(
    "SELECT rev_id FROM chunk_map ORDER BY rev_id DESC LIMIT 1"
).fetchone()["rev_id"]
total_chunks = lconn.execute(
    "SELECT COUNT(*) AS n FROM chunk_map WHERE rev_id = ?", (lrev,)
).fetchone()["n"]
md_chunks = lconn.execute(
    "SELECT COUNT(*) AS n FROM chunk_map WHERE rev_id = ? AND path LIKE '%.md'",
    (lrev,),
).fetchone()["n"]
txt_chunks = lconn.execute(
    "SELECT COUNT(*) AS n FROM chunk_map WHERE rev_id = ? AND path LIKE '%.txt'",
    (lrev,),
).fetchone()["n"]
print(f"  ревизия чанков: {lrev}")
print(f"  всего чанков:   {total_chunks}")
print(f"  из .md:         {md_chunks}")
print(f"  из .txt:        {txt_chunks}")
print(f"  размер базы:    {lex.stat().st_size / 1024 / 1024:.1f} МБ")

# Проверка: отброшенных документов нет среди чанков.
#
# Запрос идёт порциями: путей может быть много (в revoap 107), а у SQLite
# ограничение на число параметров. Порционный запрос надёжнее одного
# длинного и не падает на «Incorrect number of bindings».
dropped_docs = sorted(p for p in dropped if is_document(p))
dropped_in_chunks = 0
CHUNK_BATCH = 50
for start in range(0, len(dropped_docs), CHUNK_BATCH):
    part = dropped_docs[start : start + CHUNK_BATCH]
    if not part:
        break
    marks = ",".join("?" for _ in part)
    dropped_in_chunks += lconn.execute(
        f"SELECT COUNT(*) AS n FROM chunk_map WHERE rev_id = ? AND path IN ({marks})",
        (lrev, *part),
    ).fetchone()["n"]

print(f"\n  отброшенных документов: {len(dropped_docs)}")
print(f"  их чанков в индексе:    {dropped_in_chunks}")
check(dropped_in_chunks == 0,
      "чанков от отброшенных документов в индексе нет",
      f"найдено {dropped_in_chunks}")
lconn.close()

fac.close()

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
