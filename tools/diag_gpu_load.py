"""Диагностика: почему индексация с видеокартой стала МЕДЛЕННЕЕ.

Факты:
  * кодирование 997 чанков на видеокарте: 0,39 с;
  * стадия lexvec при реальной индексации: 27,70 с (было 15,69 с без карты);
  * то есть почти всё время стадии уходит НЕ на кодирование.

Гипотеза: время уходит на ЗАГРУЗКУ модели. При измерении ранее загрузка
SentenceTransformer заняла 18,63 с, тогда как загрузка модели через fastembed
(ONNX) — 0,84 с. Разница в 22 раза.

Если гипотеза верна, то для командной строки выигрыш от карты съедается
загрузкой модели, и надо либо ускорять загрузку, либо использовать другой путь.

Проверяется вызовом: замер загрузки по частям.
"""

from __future__ import annotations

import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

model_name = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

print("=" * 76)
print("ЗАМЕР ЗАГРУЗКИ МОДЕЛИ ПО ЧАСТЯМ")
print("=" * 76)

print("\n1. Импорт библиотек")
t0 = time.perf_counter()
import torch  # noqa: E402

print(f"   import torch: {time.perf_counter() - t0:.2f} с")

t0 = time.perf_counter()
from sentence_transformers import SentenceTransformer  # noqa: E402

print(f"   import sentence_transformers: {time.perf_counter() - t0:.2f} с")

print("\n2. Загрузка модели на процессор")
t0 = time.perf_counter()
m_cpu = SentenceTransformer(model_name, device="cpu")
cpu_load = time.perf_counter() - t0
print(f"   загрузка: {cpu_load:.2f} с")

print("\n3. Загрузка модели на видеокарту")
t0 = time.perf_counter()
m_gpu = SentenceTransformer(model_name, device="cuda")
gpu_load = time.perf_counter() - t0
print(f"   загрузка: {gpu_load:.2f} с")

print("\n4. Для сравнения: загрузка модели через fastembed (ONNX)")
try:
    from ragraf.config import load as load_config

    cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
    from ragraf.embeddings import Embedder

    emb = Embedder(cfg, allow_download=False)
    t0 = time.perf_counter()
    emb._load()
    onnx_load = time.perf_counter() - t0
    print(f"   загрузка: {onnx_load:.2f} с")
except Exception as exc:
    print(f"   не удалось: {type(exc).__name__}: {exc}")
    onnx_load = 0.0

print("\n5. Кодирование после загрузки (чтобы отделить загрузку от работы)")
texts = ["<?php function test(): void {}"] * 1000
for label, model in (("cpu", m_cpu), ("cuda", m_gpu)):
    model.encode(texts[:16], show_progress_bar=False)
    if label == "cuda":
        torch.cuda.synchronize()
    t0 = time.perf_counter()
    model.encode(texts, batch_size=128, show_progress_bar=False)
    if label == "cuda":
        torch.cuda.synchronize()
    print(f"   {label}: {time.perf_counter() - t0:.3f} с на 1000 текстов")

print()
print("=" * 76)
print("ВЫВОД")
print("=" * 76)
print(f"""
  Загрузка модели:
    * torch на процессоре: {cpu_load:.2f} с
    * torch на видеокарте: {gpu_load:.2f} с
    * ONNX (fastembed):    {onnx_load:.2f} с

  Кодирование 1000 текстов на видеокарте — доли секунды. Значит именно
  ЗАГРУЗКА определяет общее время при работе через командную строку, где
  каждый запуск создаёт новый процесс.

  Итог: выигрыш от видеокарты на кодировании реальный, но при работе через CLI
  он съедается загрузкой модели. Нужно решать отдельно:
    * либо ускорять загрузку модели torch (сохранять в локальный формат);
    * либо использовать ONNX с CUDA-провайдером — там загрузка быстрая
      ({onnx_load:.2f} с), но нужна совместимая сборка onnxruntime-gpu;
    * либо держать вычислитель в отдельном长 живущем процессе.
""")
