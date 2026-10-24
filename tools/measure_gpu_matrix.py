"""Проверка аномалии: GPU-умножение оказалось МЕДЛЕННЕЕ процессора.

Первый замер дал 366 мс на умножение 2048x2048 на видеокарте против 4 мс на
процессоре для 1024x1024. RTX 5080 должна делать это за единицы миллисекунд,
поэтому 366 мс — почти наверняка ошибка измерения, а не свойство железа.

Вероятные причины:
  1. не было ПРОГРЕВА: первое обращение к видеокарте включает инициализацию
     контекста, загрузку модулей, выделение памяти — это сотни миллисекунд;
  2. синхронизация не на каждой итерации, из-за чего замер ловит очередь;
  3. форма матрицы мала, и время уходит на накладные расходы запуска ядра,
     а не на вычисление.

Здесь замер делается правильно: с прогревом, с синхронизацией, на нескольких
размерах матриц, чтобы увидеть, где накладные расходы перестают быть заметны.
Это важно: если видеокарта не даёт выигрыша на матрицах нужного размера, то
переносить кодирование на неё бессмысленно.
"""

from __future__ import annotations

import sys
import time

import torch

print("=" * 76)
print("ПРАВИЛЬНЫЙ ЗАМЕР УМНОЖЕНИЯ МАТРИЦ: CPU против GPU")
print("=" * 76)
print(f"  torch: {torch.__version__}, CUDA: {torch.version.cuda}")
print(f"  устройство: {torch.cuda.get_device_name(0)}")
cap = torch.cuda.get_device_capability(0)
print(f"  compute capability: {cap[0]}.{cap[1]}")
print()


def bench_cpu(size: int, iters: int = 30) -> float:
    a = torch.randn(size, size)
    b = torch.randn(size, size)
    # Прогрев: первые вызовы включают инициализацию библиотек.
    for _ in range(3):
        a @ b
    t0 = time.perf_counter()
    for _ in range(iters):
        a @ b
    return (time.perf_counter() - t0) / iters * 1000


def bench_gpu(size: int, warmup: int = 10, iters: int = 50) -> float:
    a = torch.randn(size, size, device="cuda")
    b = torch.randn(size, size, device="cuda")
    # Прогрев: критично, первое обращение включает контекст CUDA.
    for _ in range(warmup):
        a @ b
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    for _ in range(iters):
        a @ b
    torch.cuda.synchronize()
    return (time.perf_counter() - t0) / iters * 1000


print(f"  {'размер':>8} {'CPU, мс':>10} {'GPU, мс':>10} {'ускорение':>12}")
print("  " + "-" * 44)
for size in (128, 256, 512, 1024, 2048, 4096):
    cpu_ms = bench_cpu(size, iters=30 if size <= 1024 else 15)
    gpu_ms = bench_gpu(size, warmup=10, iters=50 if size <= 1024 else 25)
    speedup = cpu_ms / gpu_ms if gpu_ms else 0
    mark = "GPU быстрее" if speedup > 1.2 else ("равно" if speedup > 0.8 else "CPU быстрее")
    print(f"  {size:>8} {cpu_ms:>10.3f} {gpu_ms:>10.3f} {speedup:>10.2f}x  {mark}")

print()
print("=" * 76)
print("ЧТО ЭТО ЗНАЧИТ ДЛЯ КОДИРОВАНИЯ ВЕКТОРОВ")
print("=" * 76)
print("""
  Кодирование одного текста моделью MiniLM — это НЕ одно умножение больших
  матриц. Это проход небольшой нейросети: десятки умножений на матрицы вида
  (длина_текста × 384) на (384 × 384), то есть матрицы малой ширины.

  Выигрыш видеокарты проявляется на БОЛЬШИХ партиях: когда таких текстов
  сотни, и все они считаются одним пакетом. Именно поэтому проверяется не
  только скорость умножения, но и выигрыш на пакетной обработке.
""")

# Пакетная обработка: то, что реально делает кодирование.
print("Пакетное умножение (как при кодировании: много коротких строк, одна матрица):")
print(f"  {'партия':>8} {'ширина':>8} {'CPU, мс':>10} {'GPU, мс':>10} {'ускорение':>12}")
print("  " + "-" * 54)
for batch in (64, 256, 512, 1024):
    width = 384
    a_cpu = torch.randn(batch, width)
    b_cpu = torch.randn(width, width)
    for _ in range(3):
        a_cpu @ b_cpu
    t0 = time.perf_counter()
    for _ in range(30):
        a_cpu @ b_cpu
    cpu_ms = (time.perf_counter() - t0) / 30 * 1000

    a_gpu = a_cpu.to("cuda")
    b_gpu = b_cpu.to("cuda")
    for _ in range(10):
        a_gpu @ b_gpu
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    for _ in range(50):
        a_gpu @ b_gpu
    torch.cuda.synchronize()
    gpu_ms = (time.perf_counter() - t0) / 50 * 1000
    speedup = cpu_ms / gpu_ms if gpu_ms else 0
    print(f"  {batch:>8} {width:>8} {cpu_ms:>10.3f} {gpu_ms:>10.3f} {speedup:>10.2f}x")
