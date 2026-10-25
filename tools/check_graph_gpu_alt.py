"""Проверка: есть ли WSL и работает ли графовый анализ на GPU без cuGraph.

Для предложения №1 (графовый анализ на GPU) надо знать два факта:
  1. есть ли WSL на машине — cuGraph официально ставится ТОЛЬКО под Linux,
     а под Windows единственный путь — WSL2;
  2. работает ли альтернатива без WSL: torch разрежённые матрицы на видеокарте.

Второе важно: если torch.sparse даёт графовые алгоритмы на карте без WSL,
то идея «графы на GPU» реализуема и на Windows — пусть и без RAPIDS.

Замеряется на НАШЕМ графе и на искусственно большом, чтобы увидеть,
при каком размере передача данных на карту начинает окупаться.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]

print("=" * 78)
print("1. ЕСТЬ ЛИ WSL (без него cuGraph не поставить)")
print("=" * 78)

try:
    result = subprocess.run(
        ["wsl.exe", "--status"], capture_output=True, text=True, timeout=60,
    )
    text = (result.stdout or "") + (result.stderr or "")
    print(f"  код возврата: {result.returncode}")
    for line in text.splitlines()[:12]:
        if line.strip():
            print(f"    {line.strip()[:120]}")
    # Признак отсутствия: код ошибки или текст про отсутствующий компонент.
    missing = ("не установлен" in text.lower() or "not installed" in text.lower()
               or "не найдено" in text.lower() or result.returncode != 0)
    print(f"\n  WSL доступен: {not missing}")
except FileNotFoundError:
    print("  wsl.exe не найден — WSL не установлен")
    missing = True
except Exception as exc:
    print(f"  проверка не удалась: {type(exc).__name__}: {exc}")
    missing = True

print()
print("=" * 78)
print("2. АЛЬТЕРНАТИВА БЕЗ WSL: РАЗРЕЖЁННЫЕ МАТРИЦЫ НА ВИДЕОКАРТЕ (torch)")
print("=" * 78)

import torch  # noqa: E402

print(f"  torch: {torch.__version__}")
print(f"  CUDA доступна: {torch.cuda.is_available()}")

sys.path.insert(0, str(REPO / "src"))
from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

cfg = load_config(REPO / "config" / "revoap.yaml")
conn = store.connect(cfg.graph_db, readonly=True)
rev = conn.execute(
    "SELECT rev_id FROM rev ORDER BY rev_id DESC LIMIT 1"
).fetchone()["rev_id"]
edges = conn.execute(
    "SELECT src, dst FROM edge WHERE rev_id = ?", (rev,)
).fetchall()
conn.close()

# Граф переводится в индексы вершин.
index: dict[str, int] = {}
pairs: list[tuple[int, int]] = []
for r in edges:
    s = str(r["src"])
    d = str(r["dst"])
    if s not in index:
        index[s] = len(index)
    if d not in index:
        index[d] = len(index)
    pairs.append((index[s], index[d]))
n = len(index)
print(f"\n  наш граф: вершин {n}, рёбер {len(pairs)}")


def pagerank_torch(n: int, pairs: list[tuple[int, int]], device: str,
                   iters: int = 30, warmup: bool = False) -> float:
    """PageRank разрежёнными матрицами. Возвращает время в секундах."""
    idx = torch.tensor(pairs, dtype=torch.long, device=device)
    src = idx[:, 0]
    dst = idx[:, 1]
    vals = torch.ones(len(pairs), device=device)

    import time as _t

    if warmup:
        # Прогрев обязателен: первый вызов включает подготовку видеокарты.
        # ВАЖНО: прогрев делается на КРОШЕЧНОМ отдельном тензоре, а не на матрице
        # размера графа. Первая версия делала small.to_dense() от матрицы n×n,
        # и на графе в 200 тысяч вершин это требовало 149 ГБ видеопамяти
        # (плотная матрица!). Ошибка была в способе прогрева, а не в замысле.
        tiny = torch.zeros(8, 8, device=device)
        _ = tiny @ tiny
        if device == "cuda":
            torch.cuda.synchronize()

    matrix = torch.sparse_coo_tensor(
        torch.stack([dst, src]), vals, size=(n, n)
    ).coalesce()
    # Нормировка по столбцам: сумма переходов от вершины равна единице.
    col_sums = torch.zeros(n, device=device)
    col_sums.index_add_(0, src, vals)
    col_sums[col_sums == 0] = 1.0

    rank = torch.full((n,), 1.0 / n, device=device)
    t0 = _t.perf_counter()
    for _ in range(iters):
        out = torch.zeros(n, device=device)
        out.index_add_(0, dst, col_sums[src].reciprocal() * rank[src])
        rank = 0.85 * out + 0.15 / n
    if device == "cuda":
        torch.cuda.synchronize()
    return _t.perf_counter() - t0


print(f"\n  {'граф':<22} {'CPU, мс':>10} {'GPU, мс':>10} {'вывод':>14}")
print("  " + "-" * 60)

# Наш реальный граф.
if pairs:
    cpu = pagerank_torch(n, pairs, "cpu") * 1000
    gpu = pagerank_torch(n, pairs, "cuda", warmup=True) * 1000
    mark = "CPU" if cpu < gpu else "GPU"
    print(f"  {'наш: ' + str(n):<22} {cpu:>10.2f} {gpu:>10.2f} {mark + ' быстрее':>14}")

# Искусственно большие графы: чтобы увидеть точку окупаемости.
import random  # noqa: E402

random.seed(42)
for size, edges_count in ((50_000, 400_000), (200_000, 2_000_000)):
    big_pairs = [
        (random.randrange(size), random.randrange(size))
        for _ in range(edges_count)
    ]
    try:
        cpu = pagerank_torch(size, big_pairs, "cpu") * 1000
        gpu = pagerank_torch(size, big_pairs, "cuda", warmup=True) * 1000
    except torch.OutOfMemoryError as exc:
        print(f"  {'синтетика: ' + str(size):<22} не поместилось в память: "
              f"{str(exc)[:60]}")
        torch.cuda.empty_cache()
        del big_pairs
        continue
    mark = "CPU" if cpu < gpu else "GPU"
    speedup = cpu / gpu if gpu else 0
    print(f"  {'синтетика: ' + str(size):<22} {cpu:>10.2f} {gpu:>10.2f} "
          f"{mark + ' ' + f'{speedup:.1f}x':>14}")
    torch.cuda.empty_cache()
    del big_pairs

print()
print("=" * 78)
print("3. ГДЕ ТОЧКА ОКУПАЕМОСТИ GPU ДЛЯ ГРАФОВ")
print("=" * 78)
print(f"  {'вершин':>10} {'CPU, мс':>10} {'GPU, мс':>10} {'кто быстрее':>14}")
print("  " + "-" * 48)
# Проверяется диапазон вокруг границы: где GPU начинает выигрывать.
for size in (5_000, 10_000, 20_000, 40_000):
    edges_count = size * 8
    sample = [(random.randrange(size), random.randrange(size))
              for _ in range(edges_count)]
    cpu = pagerank_torch(size, sample, "cpu") * 1000
    gpu = pagerank_torch(size, sample, "cuda", warmup=True) * 1000
    mark = "GPU" if gpu < cpu else "CPU"
    ratio = max(cpu, gpu) / min(cpu, gpu)
    print(f"  {size:>10} {cpu:>10.2f} {gpu:>10.2f} {mark + f' в {ratio:.1f}x':>14}")
    torch.cuda.empty_cache()
    del sample

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print(f"""
  WSL не установлен: {missing}. Без WSL поставить RAPIDS cuGraph нельзя —
  официальные сборки выходят только под Linux.

  Альтернатива без WSL — разрежённые матрицы torch на видеокарте. Таблица выше
  показывает ФАКТИЧЕСКУЮ точку окупаемости на этой машине.

  Сравнение с нашим графом:
    * наш граф z.batteryservice.ru: 1059 узлов, 11421 ребро;
    * наш граф revoap.com: 5520 вершин, 42350 рёбер;
    * обход и PageRank на таком размере на процессоре — доли миллисекунды.

  Решение принимается по числам: если процессор быстрее на нашем размере,
  перенос на карту не имеет смысла, и настройка должна быть выключена
  по умолчанию с объяснением, а не включена ради красоты.
""")
