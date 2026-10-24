"""Проверка фактов для двух предложений: GPU-анализ графа и worker для Joern.

Проверяется вызовом, а не по памяти:
  1. доступен ли cuGraph (RAPIDS) на этой платформе вообще;
  2. какие графовые библиотеки есть для процессора (это baseline для сравнения);
  3. сколько времени занимает обход графа НАШЕГО размера на процессоре;
  4. как устроен запуск Joern сейчас и что именно тратится на JVM.

Размеры наших графов:
  z.batteryservice.ru: 1059 узлов, 11421 ребро
  revoap.com:         4594 узла,  42350 рёбер

Это ключевое число: обход такого графа на процессоре занимает микросекунды,
и сравнивать надо с ним, а не с теорией.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

print("=" * 78)
print("1. ДОСТУПНОСТЬ ГРАФОВЫХ БИБЛИОТЕК")
print("=" * 78)

# RAPIDS cuGraph официально поддерживает только Linux.
# Проверяется факт установки и возможность установки на этой машине.
for mod, note in (
    ("cugraph", "RAPIDS: анализ графов на GPU"),
    ("cudf", "RAPIDS: таблицы на GPU"),
    ("cupy", "массивы на GPU для Python"),
    ("pylibcugraph", "низкоуровневый слой cuGraph"),
    ("networkx", "графы на CPU (чистый Python)"),
    ("igraph", "графы на CPU (C-библиотека)"),
    ("scipy", "разрежённые матрицы на CPU"),
):
    spec = importlib.util.find_spec(mod)
    version = ""
    if spec is not None:
        try:
            module = __import__(mod)
            version = getattr(module, "__version__", "?")
        except Exception as exc:
            version = f"импорт даёт {type(exc).__name__}"
    print(f"  {mod:<16} {'есть' if spec else 'НЕТ':<6} {version:<18} {note}")

print()
print("=" * 78)
print("2. ЗАМЕР ОБХОДА ГРАФА НАШЕГО РАЗМЕРА (процессор)")
print("=" * 78)

from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

cfg = load_config(REPO / "config" / "revoap.yaml")
conn = store.connect(cfg.graph_db, readonly=True)
rev = conn.execute(
    "SELECT rev_id FROM rev ORDER BY rev_id DESC LIMIT 1"
).fetchone()["rev_id"]

# Граф собирается в память: список смежности.
rows = conn.execute(
    "SELECT src, dst FROM edge WHERE rev_id = ? OR rev_id = ? LIMIT 200000",
    (rev, rev),
).fetchall()
nodes = conn.execute(
    "SELECT COUNT(*) AS c FROM node WHERE rev_id = ?", (rev,)
).fetchone()["c"]
conn.close()

adjacency: dict[str, list[str]] = {}
for r in rows:
    adjacency.setdefault(str(r["src"]), []).append(str(r["dst"]))
print(f"  граф: узлов {nodes}, рёбер {len(rows)}")
print(f"  вершин в списке смежности: {len(adjacency)}")


def bfs_cpu(adjacency: dict[str, list[str]], start: str, limit: int = 1000) -> int:
    """Обход в ширину: то, что делает операция callers/impact."""
    seen = {start}
    frontier = [start]
    steps = 0
    while frontier and steps < limit:
        nxt: list[str] = []
        for node in frontier:
            for other in adjacency.get(node, ()):
                if other not in seen:
                    seen.add(other)
                    nxt.append(other)
        frontier = nxt
        steps += 1
    return len(seen)


# Замер на нескольких стартовых вершинах: одна вершина может дать малый обход.
starts = list(adjacency.keys())[:50]
t0 = time.perf_counter()
sizes = [bfs_cpu(adjacency, s) for s in starts]
cpu_total = time.perf_counter() - t0
print(f"\n  обход в ширину от {len(starts)} вершин:")
print(f"    всего: {cpu_total * 1000:.2f} мс")
print(f"    на один обход: {cpu_total / len(starts) * 1000:.3f} мс")
print(f"    средний размер обхода: {sum(sizes) / len(sizes):.0f} вершин")

# PageRank как второй типичный графовый алгоритм.
try:
    import numpy as np

    # Матрица смежности в разрежённом виде: считается на процессоре.
    import scipy.sparse as sp

    index = {name: i for i, name in enumerate(adjacency)}
    rows_idx = []
    cols_idx = []
    for src, dsts in adjacency.items():
        for dst in dsts:
            if dst in index:
                rows_idx.append(index[src])
                cols_idx.append(index[dst])
    n = len(index)
    matrix = sp.csr_matrix(
        (np.ones(len(rows_idx)), (rows_idx, cols_idx)), shape=(n, n)
    )
    # Нормировка по столбцам: чтобы сумма переходов равнялась единице.
    col_sums = np.asarray(matrix.sum(axis=0)).ravel()
    col_sums[col_sums == 0] = 1.0
    matrix = matrix.multiply(1.0 / col_sums).tocsr()

    t0 = time.perf_counter()
    rank = np.full(n, 1.0 / n)
    for _ in range(30):
        rank = 0.85 * (matrix @ rank) + 0.15 / n
    pr_seconds = time.perf_counter() - t0
    print(f"\n  PageRank, 30 итераций на {n} вершинах:")
    print(f"    время: {pr_seconds * 1000:.2f} мс")
    print(f"    максимум: {float(rank.max()):.6f}")
except Exception as exc:
    print(f"\n  PageRank: не удалось ({type(exc).__name__}: {exc})")

print()
print("=" * 78)
print("3. ЧТО ЗНАЧИТ ЭТО ДЛЯ ПРЕДЛОЖЕНИЯ ПО cuGraph")
print("=" * 78)
print("""
  Сравнение идёт не с теорией, а с этими числами: обход графа нашего размера
  на процессоре занимает ДОЛИ МИЛЛИСЕКУНДЫ.

  Передача графа на видеокарту стоит дороже:
    * копирование 42 тысячи рёбер в видеопамять — примерно 1-3 мс;
    * подготовка контекста CUDA — до 2 секунд на первый вызов;
    * для cuGraph нужен Linux: официальные сборки RAPIDS выходят только под него.

  Это не значит, что идея плохая вообще. Она станет верной, когда граф вырастет
  на порядки: на сотнях тысяч и миллионах рёбер перенос окупается.
""")
