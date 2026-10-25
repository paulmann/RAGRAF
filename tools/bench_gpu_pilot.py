"""Проверка выигрыша GPU на пилотном корпусе (текстов больше порога).

Полигон z.batteryservice.ru имеет около 1078 новых векторов при полной
индексации — это НИЖЕ уточнённого порога 1800, и видеокарта там правильно
не включается.

Пилот revoap.com имеет около 4000 векторов — это ВЫШЕ порога. Здесь выигрыш
должен быть заметен: расчёт даёт около 45 с экономии.

Что делается: сравниваются два прогона индексации пилота — с видеокартой и без.
Векторы между прогонами сбрасываются, чтобы оба считали одинаковую работу.
"""

from __future__ import annotations

import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.indexer import Indexer  # noqa: E402

cfg = load_config(REPO / "config" / "revoap.yaml")
print(f"корпус: {cfg.project}")
print(f"порог окупаемости: {cfg.embedding.gpu_min_texts} текстов\n")


def reset_vectors() -> int:
    conn = store.connect(cfg.lexvec_db)
    try:
        before = conn.execute("SELECT COUNT(*) AS n FROM vec_meta").fetchone()["n"]
        conn.execute("DELETE FROM vec_meta")
        conn.commit()
        return int(before)
    finally:
        conn.close()


def run_index(gpu: bool) -> tuple[float, dict]:
    cfg.embedding.use_gpu = gpu
    idx = Indexer(cfg)
    try:
        t0 = time.perf_counter()
        report = idx.run(with_vectors=True)
        seconds = time.perf_counter() - t0
        stages = {s["stage"]: s["seconds"] for s in report.stages}
        data = report.to_dict()
    finally:
        idx.close()
    return seconds, {
        "stages": stages,
        "vectors": data.get("vectors", 0),
        "reused": data.get("vectors_reused", 0),
        "notes": data.get("notes", []),
    }


print("--- Прогон 1: видеокарта (по умолчанию) ---")
reset_vectors()
gpu_seconds, gpu_info = run_index(gpu=True)
print(f"  всего: {gpu_seconds:.1f} с")
print(f"  стадия lexvec: {gpu_info['stages'].get('lexvec', 0):.1f} с")
print(f"  новых векторов: {gpu_info['vectors'] - gpu_info['reused']}")
for note in gpu_info["notes"]:
    if "видеокарт" in note or "вычислител" in note or "граф свойств" in note:
        print(f"  · {note[:130]}")

print("\n--- Прогон 2: процессор (переключатель выключен) ---")
reset_vectors()
cpu_seconds, cpu_info = run_index(gpu=False)
print(f"  всего: {cpu_seconds:.1f} с")
print(f"  стадия lexvec: {cpu_info['stages'].get('lexvec', 0):.1f} с")
print(f"  новых векторов: {cpu_info['vectors'] - cpu_info['reused']}")

print()
print("=" * 76)
print("РЕЗУЛЬТАТ")
print("=" * 76)
print(f"  {'показатель':<22} {'CPU, с':>9} {'GPU, с':>9} {'разница':>10}")
print("  " + "-" * 54)
for stage in sorted(set(cpu_info["stages"]) | set(gpu_info["stages"])):
    c = cpu_info["stages"].get(stage, 0)
    g = gpu_info["stages"].get(stage, 0)
    print(f"  {stage:<22} {c:>9.2f} {g:>9.2f} {g - c:>+10.2f}")
print(f"  {'ВСЕГО':<22} {cpu_seconds:>9.2f} {gpu_seconds:>9.2f} "
      f"{gpu_seconds - cpu_seconds:>+10.2f}")

lex_cpu = cpu_info["stages"].get("lexvec", 0)
lex_gpu = gpu_info["stages"].get("lexvec", 0)
print()
print(f"  стадия векторов: {lex_cpu:.1f} с → {lex_gpu:.1f} с")
if lex_gpu:
    print(f"  ускорение стадии: {lex_cpu / lex_gpu:.2f}x")
gain = cpu_seconds - gpu_seconds
print(f"  экономия всей индексации: {gain:+.1f} с")
if gain > 0:
    print("\n  ВЫВОД: на этом корпусе видеокарта выгодна — она ускорила индексацию.")
else:
    print("\n  ВЫВОД: видеокарта не дала выигрыша. Нужно проверить, включилась ли она:")
    print("  возможно, новых текстов оказалось меньше порога.")
