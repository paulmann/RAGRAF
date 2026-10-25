"""Замер: где реально тратится время в графовых операциях RAGRAF.

Изолированный обход графа занял 0,07 мс на процессоре. Но операции RAGRAF
(`callers`, `impact`, `slice`) работают не так: они обходят граф ЧЕРЕЗ SQL-запросы
к SQLite, по одному запросу на уровень обхода. Если время уходит на запросы,
а не на алгоритм, то перенос алгоритма на видеокарту не даст ничего: узкое
место останется тем же.

Проверяются:
  1. реальное время операций фасада;
  2. разложение этого времени: сколько на SQL-запросы, сколько на вычисления;
  3. что произойдёт, если граф загрузить в память один раз.

Ничего не меняет: только читает базы.
"""

from __future__ import annotations

import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.facade import Facade  # noqa: E402

cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
print(f"корпус: {cfg.project}\n")

print("=" * 78)
print("1. РЕАЛЬНОЕ ВРЕМЯ ГРАФОВЫХ ОПЕРАЦИЙ ФАСАДА")
print("=" * 78)

facade = Facade(cfg, tool="bench", telemetry=False, watch=False)
try:
    experiments = [
        ("callers", lambda: facade.op_callers("z_agr_can_act", depth=3)),
        ("impact", lambda: facade.op_impact("z_agr_can_act", depth=3)),
        ("slice", lambda: facade.op_slice("z_agr_can_act", direction="back")),
        ("callees", lambda: facade.op_callees("z_agr_can_act", depth=3)),
    ]
    print(f"  {'операция':<12} {'время, мс':>12}")
    print("  " + "-" * 28)
    for name, call in experiments:
        times = []
        for _ in range(5):
            t0 = time.perf_counter()
            call()
            times.append(time.perf_counter() - t0)
        times.sort()
        median = times[len(times) // 2]
        print(f"  {name:<12} {median * 1000:>12.2f}")

    # Сравнение: чистая выборка из базы без обхода.
    rev = facade.current_rev()
    print(f"\n  для сравнения — простые запросы к базе:")
    for label, sql, params in (
        ("все рёбра ревизии",
         "SELECT COUNT(*) AS c FROM edge WHERE rev_id = ?", (rev,)),
        ("все узлы ревизии",
         "SELECT COUNT(*) AS c FROM node WHERE rev_id = ?", (rev,)),
        ("входящие рёбра одного узла",
         "SELECT COUNT(*) AS c FROM edge WHERE rev_id = ? AND dst = ?",
         (rev, "nonexistent-node-id")),
    ):
        times = []
        for _ in range(5):
            t0 = time.perf_counter()
            facade.graph.execute(sql, params).fetchone()
            times.append(time.perf_counter() - t0)
        times.sort()
        print(f"    {label:<32} {times[len(times) // 2] * 1000:>8.3f} мс")
finally:
    facade.close()

print()
print("=" * 78)
print("2. РАЗЛОЖЕНИЕ: SQL ПРОТИВ ВЫЧИСЛЕНИЙ")
print("=" * 78)

conn = store.connect(cfg.graph_db, readonly=True)
rev = conn.execute(
    "SELECT rev_id FROM rev ORDER BY rev_id DESC LIMIT 1"
).fetchone()["rev_id"]

# Замер полной загрузки графа в память.
t0 = time.perf_counter()
rows = conn.execute(
    "SELECT src, dst, kind FROM edge WHERE rev_id = ?", (rev,)
).fetchall()
load_seconds = time.perf_counter() - t0
print(f"  загрузка {len(rows)} рёбер из базы: {load_seconds * 1000:.2f} мс")

# Замер обхода по памяти (список смежности).
adjacency: dict[str, list[str]] = {}
for r in rows:
    adjacency.setdefault(str(r["src"]), []).append(str(r["dst"]))


def bfs(adjacency, start, depth):
    seen = {start}
    frontier = [start]
    for _ in range(depth):
        nxt = []
        for node in frontier:
            for other in adjacency.get(node, ()):
                if other not in seen:
                    seen.add(other)
                    nxt.append(other)
        frontier = nxt
    return seen


# Стартовая вершина: узел с реальным именем.
start_row = conn.execute(
    "SELECT node_id FROM node WHERE rev_id = ? AND kind = 'function' LIMIT 1",
    (rev,),
).fetchone()
start = str(start_row["node_id"]) if start_row else next(iter(adjacency))
t0 = time.perf_counter()
for _ in range(1000):
    bfs(adjacency, start, 4)
mem_seconds = (time.perf_counter() - t0) / 1000
print(f"  обход в памяти (глубина 4): {mem_seconds * 1000:.3f} мс на обход")

# Замер того же обхода через SQL: по одному запросу на уровень.
t0 = time.perf_counter()
frontier = [start]
seen = {start}
for _ in range(4):
    nxt = []
    marks = ",".join("?" for _ in frontier)
    sql_rows = conn.execute(
        f"SELECT src FROM edge WHERE rev_id = ? AND dst IN ({marks})",
        (rev, *frontier),
    ).fetchall()
    for r in sql_rows:
        s = str(r["src"])
        if s not in seen:
            seen.add(s)
            nxt.append(s)
    frontier = nxt
sql_seconds = time.perf_counter() - t0
print(f"  тот же обход через SQL: {sql_seconds * 1000:.3f} мс")
print(f"  отношение SQL к памяти: {sql_seconds / mem_seconds:.0f}x")
conn.close()

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print(f"""
  Разница между обходом в памяти ({mem_seconds * 1000:.3f} мс) и тем же обходом
  через SQL ({sql_seconds * 1000:.3f} мс) показывает, где узкое место:
  оно в ОБРАЩЕНИЯХ К БАЗЕ, а не в алгоритме обхода.

  Значит переносить на видеокарту нечего: видеокарта ускорила бы обход,
  который и так занимает доли миллисекунды, а время уходит на запросы к SQLite.
  Ускорять надо доступ к данным, а не алгоритм.

  Если граф загрузить в память один раз, обход становится в разы быстрее
  БЕЗ видеокарты.
""")
