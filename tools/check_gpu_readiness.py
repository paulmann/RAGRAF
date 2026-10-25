"""Проверка двух вопросов, от которых зависит решение по GPU.

ВОПРОС 1. Расхождение в замерах стадии графа свойств кода.
  В одном отчёте стадия cpg для revoap.com показала 63,16 с (62,9% времени),
  в другом — 0,00 с. Нужно понять, какая цифра верна: от этого зависит,
  стоит ли вообще пытаться ускорять эту стадию.

ВОПРОС 2. Доступно ли GPU-ускорение эмбеддингов.
  onnxruntime в системе без GPU-провайдера: есть только CPU и Azure.
  Установлен ли пакет onnxruntime-gpu, и можно ли получить
  CUDAExecutionProvider без ломки текущей работающей установки.

Ничего не меняет: только читает базы и опрашивает установленные пакеты.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

print("=" * 76)
print("ВОПРОС 1. Все метрики покрытия по стадиям")
print("=" * 76)

for name in ("z-batteryservice", "revoap"):
    cfg = load_config(REPO / "config" / f"{name}.yaml")
    conn = store.connect(cfg.graph_db, readonly=True)
    rev = conn.execute(
        "SELECT rev_id FROM rev ORDER BY rev_id DESC LIMIT 1"
    ).fetchone()["rev_id"]
    print(f"\n=== {cfg.project}, ревизия {rev} ===")
    rows = conn.execute(
        "SELECT metric, value, note FROM coverage WHERE rev_id = ? ORDER BY metric",
        (rev,),
    ).fetchall()
    stage_rows = [r for r in rows if "stage" in str(r["metric"]).lower()]
    for r in stage_rows:
        print(f"  {r['metric']:<24} {r['value']:>10}   {(r['note'] or '')[:60]}")
    if not stage_rows:
        print("  метрик стадий нет; есть такие метрики:")
        for r in rows[:20]:
            print(f"    {r['metric']}")
    conn.close()

print()
print("=" * 76)
print("ВОПРОС 2. Пакеты для GPU-ускорения эмбеддингов")
print("=" * 76)

import importlib.util  # noqa: E402
import subprocess  # noqa: E402

for pkg in ("onnxruntime", "onnxruntime_gpu", "onnxruntime-gpu", "torch", "fastembed",
            "transformers", "sentence_transformers"):
    module = pkg.replace("-", "_")
    spec = importlib.util.find_spec(module)
    if spec is None:
        print(f"  {pkg:<24} НЕ установлен")
        continue
    try:
        mod = __import__(module)
        version = getattr(mod, "__version__", "?")
        print(f"  {pkg:<24} установлен, версия {version}")
        if module == "onnxruntime":
            providers = mod.get_available_providers()
            print(f"       провайдеры: {providers}")
    except Exception as exc:
        print(f"  {pkg:<24} установлен, но импорт даёт {type(exc).__name__}")

print("\n  Что предлагает pip (доступность без установки):")
for pkg in ("onnxruntime-gpu",):
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "index", "versions", pkg],
            capture_output=True, text=True, timeout=180,
        )
        text = (result.stdout or "") + (result.stderr or "")
        # Показываются только строки с версиями.
        for line in text.splitlines()[:8]:
            if line.strip():
                print(f"    {line.strip()}")
    except Exception as exc:
        print(f"    {pkg}: проверка не удалась ({type(exc).__name__})")

print("\n  Интерпретация:")
print("    * если onnxruntime-gpu установлен, но провайдера CUDA нет — это")
print("      известный конфликт двух пакетов: они ставятся в один каталог и")
print("      затирают друг друга;")
print("    * если его нет — установка добавит провайдера, но требует")
print("      совместимой версии CUDA-библиотек.")
