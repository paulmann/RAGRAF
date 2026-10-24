"""Замер: кодирование эмбеддингов на видеокарте против процессора.

Это решающий замер для решения по GPU. Установлен sentence-transformers 6.1.0,
torch 2.11.0+cu128 работает с RTX 5080 (compute capability 12.0), принося
собственные cuDNN 9 и cuBLAS 12.

Замеряется на текстах того же вида и размера, что чанки RAGRAF:
  * загрузка модели;
  * кодирование партии 256 и 1024 текстов на процессоре;
  * то же на видеокарте, с прогревом и синхронизацией;
  * выигрыш.

Ничего не меняет: модель берётся из кэша, конфиги и базы не трогаются.
"""

from __future__ import annotations

import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import torch  # noqa: E402

print("=" * 76)
print("1. Окружение")
print("=" * 76)
print(f"  torch: {torch.__version__}, CUDA: {torch.version.cuda}")
print(f"  CUDA доступна: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    cap = torch.cuda.get_device_capability(0)
    print(f"  устройство: {torch.cuda.get_device_name(0)} (sm_{cap[0]}{cap[1]})")
print(f"  версия cuDNN: {torch.backends.cudnn.version()}")

import importlib.metadata as md  # noqa: E402

for pkg in ("sentence-transformers", "transformers"):
    try:
        print(f"  {pkg}: {md.version(pkg)}")
    except Exception:
        print(f"  {pkg}: не установлен")

print()
print("=" * 76)
print("2. Загрузка модели")
print("=" * 76)

from sentence_transformers import SentenceTransformer  # noqa: E402

model_name = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
t0 = time.perf_counter()
model = SentenceTransformer(model_name)
print(f"  модель загружена за {time.perf_counter() - t0:.2f} с")
print(f"  максимум длины последовательности: {model.max_seq_length}")

# Тексты того же вида, что реальные чанки корпуса.
sample = (
    "<?php function z_agr_select(string $id, string $name, array $items, "
    "string $value, string $required): string { $out = ''; foreach ($items as $k => $v) "
    "{ $sel = ((string)$k === (string)$value) ? ' selected' : ''; $out .= '<option value=\"' "
    ". z_e($k) . '\"' . $sel . '>' . z_e($v) . '</option>'; } return $out; }"
)

print()
print("=" * 76)
print("3. Замер: процессор против видеокарты")
print("=" * 76)
print(f"  {'партия':>8} {'CPU, с':>10} {'GPU, с':>10} {'ускорение':>11} "
      f"{'CPU мс/текст':>13} {'GPU мс/текст':>13}")
print("  " + "-" * 72)

results: dict[str, dict[int, float]] = {"cpu": {}, "cuda": {}}
for size in (256, 512, 1024, 2048):
    texts = [sample] * size
    for device in ("cpu", "cuda"):
        if device == "cuda" and not torch.cuda.is_available():
            continue
        try:
            model.to(device)
            # Прогрев: первое обращение к видеокарте включает контекст CUDA.
            model.encode(texts[:16], batch_size=16, show_progress_bar=False,
                         convert_to_numpy=True)
            if device == "cuda":
                torch.cuda.synchronize()
            t0 = time.perf_counter()
            model.encode(texts, batch_size=64, show_progress_bar=False,
                         convert_to_numpy=True)
            if device == "cuda":
                torch.cuda.synchronize()
            results[device][size] = time.perf_counter() - t0
        except Exception as exc:
            print(f"    {device} партия {size}: ошибка {type(exc).__name__}: "
                  f"{str(exc)[:120]}")

for size in sorted(results["cpu"]):
    cpu_s = results["cpu"][size]
    gpu_s = results["cuda"].get(size, 0)
    speedup = cpu_s / gpu_s if gpu_s else 0
    print(f"  {size:>8} {cpu_s:>10.3f} {gpu_s:>10.3f} {speedup:>10.2f}x "
          f"{cpu_s / size * 1000:>13.2f} {gpu_s / size * 1000 if gpu_s else 0:>13.2f}")

print()
print("=" * 76)
print("4. Сравнение с текущим стеком проекта (ONNX на процессоре)")
print("=" * 76)
print("  Замерено ранее: 18,45 мс на чанк через fastembed (ONNX, CPU).")
if results["cuda"]:
    biggest = max(results["cuda"])
    gpu_ms = results["cuda"][biggest] / biggest * 1000
    cpu_ms = results["cpu"][biggest] / biggest * 1000
    print(f"  torch на видеокарте: {gpu_ms:.2f} мс на текст")
    print(f"  torch на процессоре: {cpu_ms:.2f} мс на текст")
    print(f"  выигрыш против текущего стека: {18.45 / gpu_ms:.1f}x"
          if gpu_ms else "")
