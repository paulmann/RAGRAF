"""Итоговый замер: выигрыш GPU на реальной индексации.

Что сравнивается:
  * индексация с видеокартой (по умолчанию);
  * индексация с выключенной видеокартой (переключатель).

Чтобы оба прогона выполняли одинаковую работу, векторы между прогонами
сбрасываются: иначе второй прогон переиспользует уже посчитанное и время
будет несовместимым.

Сброс касается только таблицы векторов текущего корпуса: узлы, рёбра и чанки
не трогаются, а сами векторы восстанавливаются при индексации.
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

cfg = load_config(REPO / "config" / "z-batteryservice.yaml")


def reset_vectors() -> int:
    """Сбросить векторы, чтобы индексатор посчитал их заново.

    Удаляются только записи о векторах (vec_meta). Сами векторы лежат в
    виртуальной таблице vec_chunk: их связь восстанавливается при индексации,
    потому что признаком переиспользования служит text_hash в vec_meta.
    Если убрать только vec_meta, индексатор посчитает тексты заново.
    """
    conn = store.connect(cfg.lexvec_db)
    try:
        before = conn.execute("SELECT COUNT(*) AS n FROM vec_meta").fetchone()["n"]
        conn.execute("DELETE FROM vec_meta")
        conn.commit()
        return int(before)
    finally:
        conn.close()


def run_index(gpu: bool) -> tuple[float, dict]:
    """Прогон индексации с заданным вычислителем. Возвращает (секунды, стадии)."""
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
    }


print("=" * 78)
print("СРАВНЕНИЕ ИНДЕКСАЦИИ: ВИДЕОКАРТА ПРОТИВ ПРОЦЕССОРА")
print("=" * 78)
print(f"порог окупаемости: {cfg.embedding.gpu_min_texts} текстов")

print("\n--- Прогон 1: процессор (переключатель выключен) ---")
removed = reset_vectors()
print(f"  векторов сброшено: {removed}")
cpu_seconds, cpu_info = run_index(gpu=False)
print(f"  всего: {cpu_seconds:.1f} с")
print(f"  стадия lexvec: {cpu_info['stages'].get('lexvec', 0):.1f} с")
print(f"  векторов посчитано заново: {cpu_info['vectors'] - cpu_info['reused']}")

print("\n--- Прогон 2: видеокарта (по умолчанию) ---")
removed = reset_vectors()
print(f"  векторов сброшено: {removed}")
gpu_seconds, gpu_info = run_index(gpu=True)
print(f"  всего: {gpu_seconds:.1f} с")
print(f"  стадия lexvec: {gpu_info['stages'].get('lexvec', 0):.1f} с")
print(f"  векторов посчитано заново: {gpu_info['vectors'] - gpu_info['reused']}")

print()
print("=" * 78)
print("РАЗНИЦА ПО СТАДИЯМ")
print("=" * 78)
print(f"  {'стадия':<12} {'CPU, с':>9} {'GPU, с':>9} {'разница':>10}")
print("  " + "-" * 44)
all_stages = sorted(set(cpu_info["stages"]) | set(gpu_info["stages"]))
for stage in all_stages:
    c = cpu_info["stages"].get(stage, 0)
    g = gpu_info["stages"].get(stage, 0)
    mark = "← видеокарта" if g < c * 0.7 else ("→ медленнее" if g > c * 1.3 else "")
    print(f"  {stage:<12} {c:>9.2f} {g:>9.2f} {g - c:>+10.2f}  {mark}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
total_gain = cpu_seconds - gpu_seconds
lexvec_cpu = cpu_info["stages"].get("lexvec", 0)
lexvec_gpu = gpu_info["stages"].get("lexvec", 0)
print(f"  вся индексация: {cpu_seconds:.1f} с → {gpu_seconds:.1f} с "
      f"({total_gain:+.1f} с)")
print(f"  стадия векторов: {lexvec_cpu:.1f} с → {lexvec_gpu:.1f} с "
      f"({lexvec_cpu - lexvec_gpu:+.1f} с)")
if lexvec_gpu:
    print(f"  ускорение стадии векторов: {lexvec_cpu / lexvec_gpu:.1f}x")
print(f"""
  Что ещё замедляет индексацию и НЕ ускоряется видеокартой:
    граф свойств кода (Joern, Java)     {gpu_info['stages'].get('cpg', 0):.1f} с
    разбор дерева (tree-sitter)          {gpu_info['stages'].get('ast', 0):.1f} с
    обход файлов и запись в базы        {gpu_info['stages'].get('scan', 0) + gpu_info['stages'].get('load', 0):.1f} с
""")
