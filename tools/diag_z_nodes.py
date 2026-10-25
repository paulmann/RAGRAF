"""Проверка: не потерялись ли узлы при переиндексации z.batteryservice.ru.

Сигнал тревоги: в отчёте ранее было 2022 узла, а после переиндексации прямой
запрос показывает 1290. Файлов при этом стало больше (147 → 153). Увеличение
числа файлов не может уменьшать число узлов — значит, надо разобраться.

Проверяется:
  1. что показывает таблица rev (поле node_count) по ревизиям;
  2. что показывает фактический подсчёт строк в node;
  3. какие виды узлов есть в последней ревизии и в предыдущей.

Это диагностика, ничего не меняет.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
conn = store.connect(cfg.graph_db, readonly=True)

print("=" * 78)
print("1. ЧТО ЗАПИСАНО В ЖУРНАЛЕ РЕВИЗИЙ")
print("=" * 78)
print(f"\n  {'ревизия':<10} {'состояние':<12} {'файлов':>7} {'узлов':>8} "
      f"{'рёбер':>8}  родитель")
print("  " + "-" * 66)
revs = conn.execute(
    "SELECT rev_id, state, file_count, node_count, edge_count, parent_rev "
    "FROM rev ORDER BY rev_id DESC LIMIT 8"
).fetchall()
for r in revs:
    print(f"  {r['rev_id']:<10} {str(r['state'] or ''):<12} "
          f"{r['file_count'] or 0:>7} {r['node_count'] or 0:>8} "
          f"{r['edge_count'] or 0:>8}  {r['parent_rev'] or '—'}")

print()
print("=" * 78)
print("2. ФАКТИЧЕСКИЙ ПОДСЧЁТ СТРОК")
print("=" * 78)
print(f"\n  {'ревизия':<10} {'файлов':>8} {'узлов':>8} {'рёбер':>8}")
print("  " + "-" * 40)
for r in revs:
    rid = r["rev_id"]
    f = conn.execute("SELECT COUNT(*) AS n FROM file WHERE rev_id = ?",
                     (rid,)).fetchone()["n"]
    n = conn.execute("SELECT COUNT(*) AS n FROM node WHERE rev_id = ?",
                     (rid,)).fetchone()["n"]
    e = conn.execute("SELECT COUNT(*) AS n FROM edge WHERE rev_id = ?",
                     (rid,)).fetchone()["n"]
    print(f"  {rid:<10} {f:>8} {n:>8} {e:>8}")

print()
print("=" * 78)
print("3. ВИДЫ УЗЛОВ: СРАВНЕНИЕ ДВУХ ПОСЛЕДНИХ РЕВИЗИЙ")
print("=" * 78)
latest = revs[0]["rev_id"]
prev = revs[1]["rev_id"] if len(revs) > 1 else None
kinds_latest = {
    r["kind"]: r["n"]
    for r in conn.execute(
        "SELECT kind, COUNT(*) AS n FROM node WHERE rev_id = ? GROUP BY kind",
        (latest,),
    ).fetchall()
}
kinds_prev = {}
if prev:
    kinds_prev = {
        r["kind"]: r["n"]
        for r in conn.execute(
            "SELECT kind, COUNT(*) AS n FROM node WHERE rev_id = ? GROUP BY kind",
            (prev,),
        ).fetchall()
    }
print(f"\n  {'вид узла':<14} {prev or '—':>10} {latest:>10}  разница")
print("  " + "-" * 50)
for kind in sorted(set(kinds_latest) | set(kinds_prev)):
    a = kinds_prev.get(kind, 0)
    b = kinds_latest.get(kind, 0)
    diff = b - a
    mark = f"{diff:+d}" if diff else "0"
    print(f"  {kind:<14} {a:>10} {b:>10}  {mark}")

print()
print("=" * 78)
print("4. ГИПОТЕЗЫ")
print("=" * 78)
print("""
  Если node_count в журнале больше фактического числа строк, значит:
    * либо счётчик писался по всем ревизиям сразу, а выборка идёт по одной;
    * либо node_count считает и то, что в node не пишется.

  Если в последней ревизии пропали виды узлов (например css_class), значит
  переиндексация что-то потеряла — и это надо разбирать.

  Что именно произошло, покажет сравнение последних двух ревизий:
  какие виды узлов исчезли и на сколько.
""")
conn.close()
