"""Сравнение ревизий sagaai: где граф построился, а где нет.

Обнаружено: rev-0006 и rev-0007 помечены `capability=full`, а rev-0008 —
`degraded`. Это важно: значит обходной путь РАБОТАЛ, и надо понять,
что именно получилось в успешных ревизиях и почему последняя не смогла.

Проверяется прямо из базы: состав узлов и рёбер по каждой ревизии.
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402

cfg = load_config(REPO / "config" / "sagaai.yaml")
conn = sqlite3.connect(f"file:{cfg.graph_db}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

print("=" * 78)
print("РЕВИЗИИ: СОСТОЯНИЕ И ОБЪЁМ")
print("=" * 78)
revs = conn.execute(
    "SELECT rev_id, created_at, state, capability, file_count, node_count, "
    "edge_count, joern_used FROM rev ORDER BY rev_id"
).fetchall()
print(f"\n  {'ревизия':<10} {'состояние':<8} {'capability':<10} "
      f"{'файлов':>7} {'узлов':>8} {'рёбер':>8} joern")
print("  " + "-" * 68)
for r in revs:
    print(f"  {r['rev_id']:<10} {str(r['state']):<8} {str(r['capability']):<10} "
          f"{r['file_count']:>7} {r['node_count']:>8} {r['edge_count']:>8} "
          f"{str(r['joern_used'])[:6]}")

print()
print("=" * 78)
print("РЁБРА ПО ВИДАМ: УСПЕШНАЯ РЕВИЗИЯ И НЕУДАЧНАЯ")
print("=" * 78)
for rev in ("rev-0006", "rev-0007", "rev-0008"):
    print(f"\n  {rev}:")
    rows = conn.execute(
        "SELECT kind, COUNT(*) AS n FROM edge WHERE rev_id = ? "
        "GROUP BY kind ORDER BY n DESC", (rev,)
    ).fetchall()
    for r in rows:
        print(f"    {r['kind']:<22} {r['n']}")
    # Сколько рёбер calls идут из CPG, а не из разбора деревом.
    cpg_calls = conn.execute(
        "SELECT COUNT(*) AS n FROM edge WHERE rev_id = ? AND kind = 'calls'",
        (rev,)
    ).fetchone()["n"]
    heur = conn.execute(
        "SELECT COUNT(*) AS n FROM edge WHERE rev_id = ? "
        "AND kind = 'calls_heuristic'", (rev,)
    ).fetchone()["n"]
    print(f"    всего: calls={cpg_calls} (из CPG), "
          f"calls_heuristic={heur} (деревом)")

print()
print("=" * 78)
print("УЗЛЫ ПО ВИДАМ")
print("=" * 78)
for rev in ("rev-0006", "rev-0007"):
    print(f"\n  {rev}:")
    rows = conn.execute(
        "SELECT kind, COUNT(*) AS n FROM node WHERE rev_id = ? "
        "GROUP BY kind ORDER BY n DESC LIMIT 10", (rev,)
    ).fetchall()
    for r in rows:
        print(f"    {r['kind']:<22} {r['n']}")

print()
print("=" * 78)
print("ПРИМЕРЫ СВЯЗЕЙ calls (ИЗ CPG) В УСПЕШНОЙ РЕВИЗИИ")
print("=" * 78)
rows = conn.execute(
    """SELECT s.name AS src, s.path AS src_path, d.name AS dst,
              d.path AS dst_path, e.kind
         FROM edge e
         JOIN node s ON s.node_id = e.src_id
         JOIN node d ON d.node_id = e.dst_id
        WHERE e.rev_id = 'rev-0006' AND e.kind = 'calls'
        LIMIT 10"""
).fetchall()
print(f"  найдено: {len(rows)}")
for r in rows:
    print(f"    {str(r['src_path'])[:40]}:{r['src']}")
    print(f"      → {str(r['dst_path'])[:40]}:{r['dst']}")

conn.close()

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Таблица показывает, в какой ревизии граф вызовов построился.
  Если rev-0006 содержит рёбра `calls` из CPG (а не только
  calls_heuristic), значит обход работал, и последнюю ревизию надо
  просто получить тем же путём.
""")
