"""Честный замер: серверный путь против прежнего при РЕАЛЬНОЙ индексации.

Первый прогон через сервер дал стадию CPG 19,2 с — больше, чем ожидалось
по изолированным замерам (7,42 с построение + 4,13 с запрос ≈ 11,5 с).
Расхождение требует объяснения, иначе решение по серверу принимать нельзя.

Возможные причины:
  * старт сервера (6,5 с) входит в стадию — он платится ОДИН раз на процесс;
  * загрузка классов при первом обращении;
  * чтение и разбор большого файла результата (32 619 записей).

Сравниваются два прогона ПОЛНОЙ индексации: с сервером и без.
Чтобы сравнение было честным, обе стадии делают одну и ту же работу.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.indexer import Indexer  # noqa: E402


def run_index(use_server: bool) -> tuple[float, dict]:
    """Прогон индексации. Возвращает (общее время, стадии)."""
    cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
    cfg.index.joern_server = use_server
    idx = Indexer(cfg)
    try:
        t0 = time.perf_counter()
        report = idx.run(with_vectors=False)   # векторы не нужны: они одинаковы
        total = time.perf_counter() - t0
        stages = {s["stage"]: s["seconds"] for s in report.stages}
        notes = list(report.notes)
        capability = report.capability
    finally:
        idx.close()
        # Сервер останавливается: иначе он живёт и влияет на следующий прогон.
        try:
            idx.close()
        except Exception:
            pass
    return total, {"stages": stages, "notes": notes, "capability": capability}


print("=" * 78)
print("СРАВНЕНИЕ ИНДЕКСАЦИИ: СЕРВЕР JOERN ПРОТИВ ОТДЕЛЬНЫХ ПРОЦЕССОВ")
print("=" * 78)
print("Векторы отключены: они одинаковы в обоих прогонах и не влияют на вывод.")

print("\n--- Прогон 1: прежний путь (два отдельных процесса) ---")
plain_total, plain = run_index(use_server=False)
print(f"  всего: {plain_total:.1f} с")
print(f"  стадия cpg: {plain['stages'].get('cpg', 0):.2f} с")
print(f"  режим: {plain['capability']}")
for note in plain["notes"]:
    if "CPG" in note or "сервер" in note:
        print(f"  · {note[:150]}")

# Небольшая пауза: освобождение портов и завершение JVM.
time.sleep(3)

print("\n--- Прогон 2: серверный путь ---")
server_total, server = run_index(use_server=True)
print(f"  всего: {server_total:.1f} с")
print(f"  стадия cpg: {server['stages'].get('cpg', 0):.2f} с")
print(f"  режим: {server['capability']}")
for note in server["notes"]:
    if "CPG" in note or "сервер" in note:
        print(f"  · {note[:150]}")

print()
print("=" * 78)
print("РАЗНИЦА")
print("=" * 78)
print(f"  {'стадия':<12} {'процессы, с':>12} {'сервер, с':>11} {'разница':>10}")
print("  " + "-" * 50)
for stage in sorted(set(plain["stages"]) | set(server["stages"])):
    p = plain["stages"].get(stage, 0)
    s = server["stages"].get(stage, 0)
    mark = " ← быстрее" if s < p * 0.8 else (" → медленнее" if s > p * 1.2 else "")
    print(f"  {stage:<12} {p:>12.2f} {s:>11.2f} {s - p:>+10.2f}{mark}")
print(f"  {'ВСЕГО':<12} {plain_total:>12.2f} {server_total:>11.2f} "
      f"{server_total - plain_total:>+10.2f}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
cpg_plain = plain["stages"].get("cpg", 0)
cpg_server = server["stages"].get("cpg", 0)
gain = cpg_plain - cpg_server
print(f"""
  Стадия графа свойств кода:
    прежний путь: {cpg_plain:.2f} с
    серверный:    {cpg_server:.2f} с
    разница:      {gain:+.2f} с
""")
if gain > 1:
    print(f"  Серверный путь быстрее на {gain:.1f} с на индексацию.")
elif gain > -1:
    print("  Разница в пределах погрешности: серверный путь не хуже и не быстрее.")
    print("  Но у него есть запас: построение в памяти переиспользуется, если")
    print("  в той же сессии понадобится второй граф.")
else:
    print(f"  Серверный путь МЕДЛЕННЕЕ на {abs(gain):.1f} с.")
    print("  Причина скорее всего в том, что старт сервера (около 6,5 с) входит")
    print("  в стадию, и он не окупается на одном прогоне.")
    print("  Выигрыш появится при НЕСКОЛЬКИХ прогонах в одной сессии.")
