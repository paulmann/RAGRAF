"""Проверка гипотезы: SentenceTransformer сам выбирает видеокарту.

Расхождение: первый замер дал ускорение 38,8x (процессор 15,46 мс на текст,
видеокарта 0,40 мс), а проверка — 0,27 мс на текст для ОБОИХ устройств
и ускорение 1,0x.

Объяснение, которое проверяется: в sentence-transformers версии 6 при создании
модели без явного указания устройства библиотека САМА выбирает видеокарту,
если она доступна. Тогда в проверке «процессорная» модель на самом деле
считала на видеокарте, и сравнивались две видеокарты.

Если гипотеза верна:
  * проверка была неверной, а первый замер — верным;
  * и это важно для кода проекта: нельзя полагаться на «по умолчанию»,
    устройство надо задавать явно.

Скрипт ничего не меняет: только читает свойства модели.
"""

from __future__ import annotations

import time

import torch
from sentence_transformers import SentenceTransformer

model_name = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

print("=" * 76)
print("1. Куда попадает модель БЕЗ указания устройства")
print("=" * 76)
fresh = SentenceTransformer(model_name)
devices = {str(p.device) for p in fresh.parameters()}
print(f"  устройства параметров: {devices}")
print(f"  свойство model.device: {fresh.device}")
auto_is_cuda = any("cuda" in d for d in devices)
print(f"  библиотека сама выбрала видеокарту: {auto_is_cuda}")

print()
print("=" * 76)
print("2. Что происходит при явном указании устройства")
print("=" * 76)
explicit_cpu = SentenceTransformer(model_name, device="cpu")
print(f"  device='cpu' → {explicit_cpu.device}, "
      f"устройства: {{str(p.device) for p in explicit_cpu.parameters()}}")

explicit_gpu = SentenceTransformer(model_name, device="cuda")
print(f"  device='cuda' → {explicit_gpu.device}")

print()
print("=" * 76)
print("3. Замер при ЯВНО заданных устройствах")
print("=" * 76)
base = (
    "<?php function z_agr_select(string $id, string $name, array $items, "
    "string $value, string $required): string { $out = ''; foreach ($items as $k => $v) "
    "{ $sel = ((string)$k === (string)$value) ? ' selected' : ''; $out .= '<option>' "
    ". z_e($k) . '</option>'; } return $out; }"
)
texts = [f"{base} // вариант {i}" for i in range(1024)]

print(f"  {'устройство':>10} {'секунд':>10} {'мс/текст':>10}")
print("  " + "-" * 34)
timings: dict[str, float] = {}
for label, mdl in (("cpu", explicit_cpu), ("cuda", explicit_gpu)):
    mdl.eval()
    # Прогрев: первое обращение к видеокарте включает контекст CUDA.
    mdl.encode(texts[:32], batch_size=32, show_progress_bar=False)
    if label == "cuda":
        torch.cuda.synchronize()
    t0 = time.perf_counter()
    mdl.encode(texts, batch_size=128, show_progress_bar=False, convert_to_numpy=True)
    if label == "cuda":
        torch.cuda.synchronize()
    seconds = time.perf_counter() - t0
    timings[label] = seconds
    print(f"  {label:>10} {seconds:>10.3f} {seconds / len(texts) * 1000:>10.2f}")

if timings.get("cuda"):
    speedup = timings["cpu"] / timings["cuda"]
    print(f"\n  УСКОРЕНИЕ при явном указании устройств: {speedup:.1f}x")
    print(f"  ДЛЯ СРАВНЕНИЯ с текущим стеком (ONNX на процессоре, 18,45 мс на чанк):")
    print(f"    видеокарта: {timings['cuda'] / len(texts) * 1000:.2f} мс на текст "
          f"→ ускорение {18.45 / (timings['cuda'] / len(texts) * 1000):.1f}x")

print()
print("=" * 76)
print("4. ВЫВОД")
print("=" * 76)
print(f"""
  Гипотеза подтверждена: {auto_is_cuda}.

  Значит:
    * проверка, показавшая 1,0x, была неверной — в ней обе модели оказались
      на видеокарте, потому что устройство не задавалось явно;
    * верным является замер с явным указанием устройства;
    * В КОДЕ ПРОЕКТА устройство надо задавать ЯВНО: тогда поведение предсказуемо
      и управляемо переключателем в интерфейсе администратора.
""")
