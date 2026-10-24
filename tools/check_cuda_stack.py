"""Проверка: какие CUDA-библиотеки уже установлены и что подойдёт для ONNX GPU.

Проблема: свежий onnxruntime-gpu 1.30 требует CUDA 13, а torch использует
CUDA 12.8. Драйвер 617.14 (CUDA UMD 13.4) поддерживает обе ветки, но
библиотеки должны совпадать по поколению. Нужно понять, что уже есть,
чтобы выбрать рабочую комбинацию и не ломать torch.

Ничего не устанавливает и не меняет: только осматривает окружение.
"""

from __future__ import annotations

import importlib.metadata as md
import importlib.util
import pathlib
import sys

print("=" * 76)
print("1. Что уже установлено из CUDA-библиотек")
print("=" * 76)

found_any = False
try:
    packages = sorted(
        (d.metadata["Name"], d.version)
        for d in md.distributions()
        if d.metadata and d.metadata.get("Name")
    )
except Exception:
    packages = []

for name, version in packages:
    low = name.lower()
    if low.startswith("nvidia") or low in ("onnxruntime", "onnxruntime-gpu", "torch",
                                           "fastembed", "cupy", "numba"):
        print(f"  {name:<38} {version}")
        found_any = True
if not found_any:
    print("  CUDA-пакетов не найдено")

print()
print("=" * 76)
print("2. Какие модули nvidia.* реально импортируются")
print("=" * 76)
for module in ("nvidia.cudnn", "nvidia.cublas", "nvidia.cuda_runtime",
               "nvidia.cuda_nvrtc", "nvidia.cufft", "nvidia.curand"):
    spec = importlib.util.find_spec(module)
    mark = "есть" if spec else "нет"
    print(f"  {module:<28} {mark}")

print()
print("=" * 76)
print("3. Что требует onnxruntime-gpu разных версий")
print("=" * 76)
# Смотрим метаданные доступных колес через pip (без установки).
import subprocess  # noqa: E402

for version in ("1.30.0", "1.22.0", "1.20.2"):
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "download",
             f"onnxruntime-gpu=={version}", "--no-deps",
             "--dest", str(pathlib.Path(__file__).resolve().parent / "_probe"),
             "--dry-run"],
            capture_output=True, text=True, timeout=240,
        )
        text = (result.stdout or "") + (result.stderr or "")
        first = [ln.strip() for ln in text.splitlines() if "Would install" in ln or "Saved" in ln
                 or "onnxruntime" in ln.lower()][:3]
        print(f"  onnxruntime-gpu=={version}:")
        for ln in first:
            print(f"    {ln[:100]}")
        if not first:
            print(f"    вывод не распознан (код {result.returncode})")
    except subprocess.TimeoutExpired:
        print(f"  onnxruntime-gpu=={version}: превышено время ожидания")
    except Exception as exc:
        print(f"  onnxruntime-gpu=={version}: {type(exc).__name__}")

# Убираем временный каталог, если он появился.
probe = pathlib.Path(__file__).resolve().parent / "_probe"
if probe.exists():
    import shutil

    shutil.rmtree(probe, ignore_errors=True)
    print(f"\n  временный каталог проверки удалён")

print()
print("=" * 76)
print("4. Вывод по совместимости")
print("=" * 76)
print("""
  torch 2.11.0+cu128 приносит библиотеки ветки CUDA 12.8.
  onnxruntime-gpu 1.30 требует ветку CUDA 13.

  Драйвер это допускает (617.14 = CUDA UMD 13.4), но смешивать ветки в одном
  процессе рискованно. Поэтому для эмбеддингов проверяется вариант БЕЗ
  onnxruntime-gpu: кодирование через torch напрямую. Это исключает конфликт
  версий CUDA и использует уже работающую сборку под sm_120.
""")
