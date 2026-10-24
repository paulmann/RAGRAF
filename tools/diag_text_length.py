"""Диагностика: почему кодирование 717 реальных текстов идёт 25,9 с,
а кодирование 997 текстов в тесте — 0,39 с.

Разница в 66 раз при похожем числе текстов. Значит дело не в видеокарте,
а в самих текстах. Гипотезы:

  1. ДЛИНА текстов: в тесте были короткие строки, здесь — реальные чанки
     кода по 300-6000 символов. Модель обрабатывает их последовательно,
     и время растёт линейно по длине.
  2. ПОВТОРЫ: в тесте 1000 текстов были одинаковыми, и библиотека могла
     посчитать их один раз. Здесь 717 текстов уникальны.

Проверяется замером на текстах разной длины и на реальных чанках.
"""

from __future__ import annotations

import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import torch  # noqa: E402
from sentence_transformers import SentenceTransformer  # noqa: E402

model_name = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
model = SentenceTransformer(model_name, device="cuda")
print(f"модель загружена, максимум длины: {model.max_seq_length}")
print(f"устройство: {model.device}\n")

print("=" * 76)
print("1. Как время зависит от ДЛИНЫ текста")
print("=" * 76)
print(f"  {'длина, симв':>12} {'текстов':>8} {'секунд':>9} {'мс/текст':>10}")
print("  " + "-" * 44)

base = "<?php function handler(string $a, array $b): string { $out = ''; "
for chars in (50, 200, 500, 1000, 2000, 4000):
    text = base
    while len(text) < chars:
        text += "foreach ($b as $k => $v) { $out .= z_e($k) . '=' . z_e($v) . '; } "
    text = text[:chars]
    texts = [text] * 128
    # Прогрев.
    model.encode(texts[:16], batch_size=64, show_progress_bar=False)
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    model.encode(texts, batch_size=64, show_progress_bar=False)
    torch.cuda.synchronize()
    seconds = time.perf_counter() - t0
    print(f"  {chars:>12} {len(texts):>8} {seconds:>9.3f} "
          f"{seconds / len(texts) * 1000:>10.2f}")

print()
print("=" * 76)
print("2. Как время зависит от ЧИСЛА текстов при реальной длине")
print("=" * 76)
real_text = (
    "<?php\nfunction z_agr_select(string $id, string $name, array $items, "
    "string $value, string $required): string {\n    $out = '';\n    "
    "foreach ($items as $k => $v) {\n        $sel = ((string)$k === (string)$value) "
    "? ' selected' : '';\n        $out .= '<option value=\"' . z_e($k) . '\"' . $sel "
    ". '>' . z_e($v) . '</option>';\n    }\n    return $out;\n}\n"
)
print(f"  длина текста: {len(real_text)} символов")
print(f"  {'текстов':>8} {'секунд':>9} {'мс/текст':>10}")
print("  " + "-" * 32)
for count in (32, 64, 128, 256, 512, 717):
    texts = [f"{real_text} // вариант {i}" for i in range(count)]
    model.encode(texts[:16], batch_size=64, show_progress_bar=False)
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    model.encode(texts, batch_size=64, show_progress_bar=False)
    torch.cuda.synchronize()
    seconds = time.perf_counter() - t0
    print(f"  {count:>8} {seconds:>9.3f} {seconds / count * 1000:>10.2f}")

print()
print("=" * 76)
print("3. Влияние размера партии")
print("=" * 76)
texts = [f"{real_text} // вариант {i}" for i in range(512)]
print(f"  {'партия':>8} {'секунд':>9} {'мс/текст':>10}")
print("  " + "-" * 32)
for batch in (16, 32, 64, 128, 256):
    model.encode(texts[:16], batch_size=batch, show_progress_bar=False)
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    model.encode(texts, batch_size=batch, show_progress_bar=False)
    torch.cuda.synchronize()
    seconds = time.perf_counter() - t0
    print(f"  {batch:>8} {seconds:>9.3f} {seconds / len(texts) * 1000:>10.2f}")

print()
print("=" * 76)
print("ВЫВОД")
print("=" * 76)
print("""
  Модель MiniLM имеет предел длины последовательности 128 единиц (токенов).
  Текст ДЛИННЕЕ этого предела обрезается перед подачей в модель, поэтому
  время НЕ должно расти бесконечно с длиной.

  Но время зависит от того, сколько единиц реально дошло до модели, и от
  числа текстов. Замеры выше показывают фактическую зависимость.

  Главное: сравнивать скорость надо на текстах ОДНОЙ длины. В прежнем тесте
  были короткие строки, здесь — реальные чанки. Разница во времени объясняется
  этим, а не работой видеокарты.
""")
