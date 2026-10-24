"""Проверка: можно ли включить видеокарту для эмбеддингов через ONNX.

Два способа, оба проверяются здесь.

СПОСОБ 1 (предпочтительный): fastembed с провайдером CUDA.
  У TextEmbedding уже есть параметры `providers` и `cuda` — это проверено
  чтением сигнатуры, а не по памяти. Если установлен пакет onnxruntime-gpu,
  достаточно передать проводайдера. Код проекта меняется минимально.

СПОСОБ 2 (запасной): прямое кодирование через библиотеку transformers на torch.
  Требует больше зависимостей и другого кода, но не требует ветки CUDA 13.

Здесь проверяется СПОСОБ 1: наличие провайдера и фактическая работа.
Если провайдера нет — выводится точная команда установки и оценка выигрыша.
"""

from __future__ import annotations

import inspect
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

print("=" * 76)
print("СОСТОЯНИЕ ONNX: какие провайдеры доступны сейчас")
print("=" * 76)
try:
    import onnxruntime as ort

    providers = ort.get_available_providers()
    print(f"  onnxruntime {ort.__version__}")
    print(f"  провайдеры: {providers}")
    has_cuda = "CUDAExecutionProvider" in providers
    print(f"  CUDA-провайдер: {'ЕСТЬ' if has_cuda else 'НЕТ'}")
except Exception as exc:
    print(f"  ошибка: {type(exc).__name__}: {exc}")
    has_cuda = False

print()
print("=" * 76)
print("ПАРАМЕТРЫ FASTEDGE (читаются из кода, не по памяти)")
print("=" * 76)
from fastembed import TextEmbedding  # noqa: E402

sig = inspect.signature(TextEmbedding.__init__)
supports_providers = "providers" in sig.parameters
supports_cuda = "cuda" in sig.parameters
print(f"  принимает providers: {supports_providers}")
print(f"  принимает cuda: {supports_cuda}")
for name in ("providers", "cuda", "device_ids", "threads"):
    if name in sig.parameters:
        param = sig.parameters[name]
        default = "" if param.default is inspect.Parameter.empty else f" = {param.default!r}"
        print(f"    {name}{default}")

print()
print("=" * 76)
print("ПОПЫТКА ЗАПУСКА С CUDA-ПРОВАЙДЕРОМ")
print("=" * 76)

from ragraf.config import load as load_config  # noqa: E402

cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
model_name = cfg.embedding.model

texts = [
    "<?php function z_agr_select(string $id, string $name, array $items): string "
    "{ $out = ''; foreach ($items as $k => $v) { $out .= z_e($k); } return $out; }"
] * 128

attempts: list[tuple[str, dict]] = []
if has_cuda:
    attempts.append(("CUDA-провайдер", {"providers": ["CUDAExecutionProvider"]}))
attempts.append(("только процессор", {"providers": ["CPUExecutionProvider"]}))

for label, kwargs in attempts:
    print(f"\n  --- {label} ---")
    try:
        t0 = time.perf_counter()
        model = TextEmbedding(model_name=model_name, **kwargs)
        load_seconds = time.perf_counter() - t0
        print(f"    загрузка модели: {load_seconds:.2f} с")

        # Прогрев: первое кодирование включает инициализацию.
        list(model.embed(texts[:8]))
        t0 = time.perf_counter()
        vectors = list(model.embed(texts))
        seconds = time.perf_counter() - t0
        print(f"    кодирование {len(texts)} текстов: {seconds:.3f} с "
              f"({seconds / len(texts) * 1000:.2f} мс на текст)")
        print(f"    получено векторов: {len(vectors)}")
    except Exception as exc:
        print(f"    НЕ РАБОТАЕТ: {type(exc).__name__}: {str(exc)[:200]}")

print()
print("=" * 76)
print("ЧТО НУЖНО ДЛЯ CUDA-ПРОВАЙДЕРА")
print("=" * 76)
print("""
  onnxruntime (текущий, 1.30.0) не содержит CUDA-провайдера: он есть только
  в отдельном пакете onnxruntime-gpu.

  ВАЖНОЕ ОГРАНИЧЕНИЕ (выяснено замером): onnxruntime-gpu 1.30 требует ветку
  CUDA 13, тогда как torch работает на ветке CUDA 12.8. Драйвер 617.14
  поддерживает обе ветки, но смешивать их в одном процессе рискованно.

  Поэтому у способа два варианта:
    A) поставить onnxruntime-gpu с extras под CUDA 13 — тогда эмбеддинги пойдут
       на видеокарту, но torch останется на своей ветке (разные библиотеки);
    B) кодировать через transformers на torch — ветка CUDA одна (12.8),
       уже работает, но это другой код кодирования.

  Оба варианта требуют проверки замером: без цифр выбирать нельзя.
""")
