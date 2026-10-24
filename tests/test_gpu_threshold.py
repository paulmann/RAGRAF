"""Проверка порога окупаемости на трёх режимах работы.

Порог нужен потому, что подготовка видеокарты стоит 15 с разово, а кодирование
на ней — 0,35 мс на текст. То есть:
  * на 100 текстах: видеокарта потратит 15 с на подготовку и 0,04 с на работу
    (итого 15 с), процессор — 1,8 с. Видеокарта ВРЕДНА;
  * на 5000 текстах: видеокарта — 15 с + 1,8 с = 16,8 с, процессор — 89 с.
    Видеокарта ПОЛЕЗНА в 5 раз.

Проверяются три режима:
  1. мало работы — должен остаться процессор;
  2. много работы — должна включиться видеокарта;
  3. выключенный переключатель — процессор при любом объёме.

Ничего не меняет в базах: кодирование в память.
"""

from __future__ import annotations

import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.embeddings import Embedder  # noqa: E402

PASS = 0
FAIL = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
threshold = cfg.embedding.gpu_min_texts
print(f"порог окупаемости по настройкам: {threshold} текстов\n")

text = (
    "<?php function z_agr_select(string $id, string $name, array $items): string "
    "{ $out = ''; foreach ($items as $k => $v) { $out .= z_e($k); } return $out; }"
)

print("=== 1. Мало работы (100 текстов, ниже порога) ===")
cfg.embedding.use_gpu = True
cfg.embedding.device = "auto"
small = Embedder(cfg, allow_download=False)
t0 = time.perf_counter()
small.embed([f"{text} #{i}" for i in range(100)])
small_seconds = time.perf_counter() - t0
st = small.compute_status()
print(f"  вычислитель: {st.backend}")
print(f"  причина: {st.reason[:100]}")
print(f"  время: {small_seconds:.2f} с")
check(st.backend == "onnx-cpu", "при малой работе выбран процессор",
      f"получено {st.backend}")

print(f"\n=== 2. Много работы ({threshold + 500} текстов, выше порога) ===")
big = Embedder(cfg, allow_download=False)
count = threshold + 500
t0 = time.perf_counter()
big.embed([f"{text} #{i}" for i in range(count)])
big_seconds = time.perf_counter() - t0
st_big = big.compute_status()
print(f"  вычислитель: {st_big.backend}")
print(f"  причина: {st_big.reason[:100]}")
print(f"  время: {big_seconds:.2f} с ({big_seconds / count * 1000:.2f} мс на текст)")
check(st_big.backend == "torch-gpu", "при большой работе выбрана видеокарта",
      f"получено {st_big.backend}")
check(st_big.ms_per_text is not None and st_big.ms_per_text < 5,
      "скорость на видеокарте измерена и высока",
      f"получено {st_big.ms_per_text} мс")

print("\n=== 3. Переключатель выключен (много работы) ===")
cfg.embedding.use_gpu = False
off = Embedder(cfg, allow_download=False)
off.embed([f"{text} #{i}" for i in range(count)])
st_off = off.compute_status()
print(f"  вычислитель: {st_off.backend}")
print(f"  причина: {st_off.reason[:100]}")
check(st_off.backend == "onnx-cpu", "выключатель отменяет видеокарту",
      f"получено {st_off.backend}")

print("\n=== 4. Сравнение на одинаковом объёме ===")
# Для честного сравнения оба вычислителя должны быть уже прогреты.
# ВАЖНО: прогрев идёт партией ВЫШЕ порога окупаемости, иначе вычислитель
# останется процессорным и сравнение будет не с чем (на этом была ошибка
# в первой версии проверки).
cfg.embedding.use_gpu = True
warm_gpu = Embedder(cfg, allow_download=False)
texts = [f"{text} #{i}" for i in range(2000)]
warmup_texts = [f"{text} warmup {i}" for i in range(threshold + 100)]
warm_gpu.embed(warmup_texts)   # прогрев: включает подготовку видеокарты
st_warm = warm_gpu.compute_status()
print(f"  прогрев на {len(warmup_texts)} текстах: вычислитель {st_warm.backend}")
if st_warm.backend == "torch-gpu":
    t0 = time.perf_counter()
    warm_gpu.embed(texts)
    gpu_warm = time.perf_counter() - t0
    print(f"  видеокарта (прогрета): {gpu_warm:.2f} с "
          f"({gpu_warm / len(texts) * 1000:.2f} мс на текст)")

    cfg.embedding.use_gpu = False
    warm_cpu = Embedder(cfg, allow_download=False)
    warm_cpu.embed(texts[:100])
    t0 = time.perf_counter()
    warm_cpu.embed(texts)
    cpu_warm = time.perf_counter() - t0
    print(f"  процессор (прогрет):   {cpu_warm:.2f} с "
          f"({cpu_warm / len(texts) * 1000:.2f} мс на текст)")
    speedup = cpu_warm / gpu_warm if gpu_warm else 0
    print(f"  ускорение на самом кодировании: {speedup:.1f}x")
    check(speedup > 3, "на большом объёме видеокарта быстрее",
          f"получено {speedup:.1f}x")
else:
    check(False, "видеокарта включилась на прогреве", st_warm.backend)

print("\n=== 5. Оценка выгоды по объёму ===")
print(f"  {'текстов':>8} {'CPU, с':>8} {'GPU, с':>8} {'выгода':>12}")
print("  " + "-" * 40)
# Расчёт из замеренных скоростей: 0,35 мс на текст на видеокарте,
# 17,9 мс на процессоре, разовая подготовка 15 с.
GPU_MS, CPU_MS, STARTUP = 0.35, 17.9, 15.0
for n in (100, 500, 1000, 2000, 5000, 20000):
    cpu_s = n * CPU_MS / 1000
    gpu_s = n * GPU_MS / 1000 + STARTUP
    verdict = "GPU" if gpu_s < cpu_s else "CPU"
    gain = (cpu_s - gpu_s) / cpu_s * 100 if cpu_s else 0
    print(f"  {n:>8} {cpu_s:>8.1f} {gpu_s:>8.1f} {gain:>10.0f}%  {verdict}")
print(f"\n  Порог окупаемости по расчёту: {STARTUP / (CPU_MS - GPU_MS) * 1000:.0f} текстов")
print(f"  Порог в настройках:           {threshold} текстов")

print("\n=== ИТОГ ===")
print(f"пройдено: {PASS}, провалено: {FAIL}")
raise SystemExit(0 if FAIL == 0 else 1)
