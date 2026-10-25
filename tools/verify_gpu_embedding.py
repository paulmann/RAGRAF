"""Проверка: не является ли замер GPU-кодирования ложным.

Замер показал 0,40 мс на текст на видеокарте против 15,46 мс на процессоре —
ускорение 38,8 раза. Это правдоподобно для матричных операций, но требует
проверки: возможные причины ложного результата —
  * модель не переехала на видеокарту (тогда «GPU» считал бы процессором);
  * вывод возвращается в усечённом виде, и работа не делается;
  * все тексты одинаковы, и результат кешируется внутри библиотеки;
  * синхронизация не выполнена, и замер ловит только очередь команд.

Проверяется каждый пункт:
  1. на каком устройстве реально лежат параметры модели;
  2. совпадают ли векторы с эталоном (для одинаковых текстов — одинаковый вектор,
     значит считалось одно и то же, но это не отменяет работу);
  3. замер на РАЗНЫХ текстах с разной длиной;
  4. замер с явной синхронизацией и без неё — разница покажет, была ли она нужна;
  5. контрольная сумма векторов для проверки, что данные не пустые.

Ничего не меняет.
"""

from __future__ import annotations

import hashlib
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import numpy as np  # noqa: E402
import torch  # noqa: E402
from sentence_transformers import SentenceTransformer  # noqa: E402

model_name = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
model = SentenceTransformer(model_name)

print("=" * 76)
print("1. На каком устройстве лежат параметры модели")
print("=" * 76)
for name, param in list(model.named_parameters())[:3]:
    print(f"  {name}: устройство {param.device}, тип {param.dtype}")
devices = {str(p.device) for p in model.parameters()}
print(f"  все устройства параметров: {devices}")

print()
print("=" * 76)
print("2. Замер на РАЗНЫХ текстах разной длины")
print("=" * 76)
# Тексты специально разные: если библиотека кеширует по содержимому,
# одинаковые тексты дали бы ложную скорость.
base = (
    "<?php function handler_{i}(string $name, array $data): string "
    "{{ $out = ''; foreach ($data as $k => $v) {{ $out .= z_e($k) . '=' . z_e($v); }} "
    "return $out . '_{i}'; }}"
)
texts = [base.format(i=i) for i in range(512)]
unique = len({hash(t) for t in texts})
print(f"  текстов: {len(texts)}, уникальных: {unique}")

# Проверка, что модель переезжает на устройство.
model_cpu = SentenceTransformer(model_name)
model_gpu = SentenceTransformer(model_name)
model_gpu.to("cuda")
gpu_device = str(next(model_gpu.parameters()).device)
print(f"  после .to('cuda') параметры на: {gpu_device}")

results = {}
for label, mdl in (("cpu", model_cpu), ("cuda", model_gpu)):
    mdl.eval()
    # Прогрев.
    mdl.encode(texts[:32], batch_size=32, show_progress_bar=False)
    if label == "cuda":
        torch.cuda.synchronize()
    t0 = time.perf_counter()
    vecs = mdl.encode(texts, batch_size=128, show_progress_bar=False,
                      convert_to_numpy=True)
    if label == "cuda":
        torch.cuda.synchronize()
    seconds = time.perf_counter() - t0
    results[label] = (seconds, vecs)
    print(f"\n  {label}: {seconds:.3f} с на {len(texts)} текстов "
          f"({seconds / len(texts) * 1000:.2f} мс на текст)")
    print(f"    форма результата: {vecs.shape}, тип {vecs.dtype}")
    print(f"    контрольная сумма: {hashlib.sha256(vecs.tobytes()).hexdigest()[:16]}")

print()
print("=" * 76)
print("3. Совпадают ли результаты процессора и видеокарты")
print("=" * 76)
cpu_vecs = results["cpu"][1]
gpu_vecs = results["cuda"][1]
if cpu_vecs.shape == gpu_vecs.shape:
    diff = np.abs(cpu_vecs - gpu_vecs)
    print(f"  максимальное расхождение: {diff.max():.6f}")
    print(f"  среднее расхождение: {diff.mean():.6f}")
    same = bool(np.allclose(cpu_vecs, gpu_vecs, atol=1e-3))
    print(f"  результаты совпадают (допуск 1e-3): {same}")
    if not same:
        print("  ВНИМАНИЕ: расхождение больше допуска — замер надо пересмотреть")
else:
    print(f"  формы разные: CPU {cpu_vecs.shape}, GPU {gpu_vecs.shape}")

print()
print("=" * 76)
print("4. Проверка влияния синхронизации")
print("=" * 76)
# Если без синхронизации замер даёт заметно меньше, значит она была нужна,
# и первоначальный замер корректно её учитывал.
model_gpu.encode(texts[:32], batch_size=32, show_progress_bar=False)
torch.cuda.synchronize()

t0 = time.perf_counter()
model_gpu.encode(texts, batch_size=128, show_progress_bar=False)
no_sync = time.perf_counter() - t0

torch.cuda.synchronize()
t0 = time.perf_counter()
model_gpu.encode(texts, batch_size=128, show_progress_bar=False)
torch.cuda.synchronize()
with_sync = time.perf_counter() - t0

print(f"  без синхронизации: {no_sync:.3f} с")
print(f"  с синхронизацией:  {with_sync:.3f} с")
print(f"  разница: {abs(no_sync - with_sync) / max(with_sync, 1e-9) * 100:.1f}%")

print()
print("=" * 76)
print("5. ИТОГ ПРОВЕРКИ")
print("=" * 76)
speedup = results["cpu"][0] / results["cuda"][0] if results["cuda"][0] else 0
print(f"  ускорение: {speedup:.1f}x")
print(f"  параметры модели на видеокарте: 'cuda' в устройствах — {gpu_device}")
print(f"  результаты численно совпадают: "
      f"{bool(np.allclose(cpu_vecs, gpu_vecs, atol=1e-3)) if cpu_vecs.shape == gpu_vecs.shape else 'не проверено'}")
print(f"  тексты уникальны: {unique == len(texts)}")
print(f"  замер с синхронизацией: да")
