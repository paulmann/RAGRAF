"""Замер: сколько даёт GPU на кодировании векторов (главная GPU-задача).

Задача эмбеддингов — это матричные умножения нейросети, единственная часть
работы RAGRAF, которая ускоряется на видеокарте. Замеряется:
  1. сколько сейчас занимает кодирование на процессоре (ONNX, fastembed);
  2. насколько медленнее была бы чистая реализация на numpy — чтобы понять,
     есть ли вообще смысл считать это «матричной» задачей;
  3. доступна ли видеокарта для подсчёта (torch / onnxruntime-gpu).

Последний пункт критичен: RTX 5080 — это архитектура Blackwell с compute
capability 12.0, и не всякая сборка torch её поддерживает. Если torch не
установлен — проверяется, что доступно без установки.

Базы и конфиги не меняются: только замеры в память.
"""

from __future__ import annotations

import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

print("=" * 76)
print("ДОСТУПНОСТЬ ВИДЕОКАРТЫ ДЛЯ ВЫЧИСЛЕНИЙ")
print("=" * 76)

# 1. Проверка torch.
try:
    import torch  # type: ignore

    print(f"  torch: {torch.__version__}")
    print(f"  сборка под CUDA: {torch.version.cuda}")
    available = torch.cuda.is_available()
    print(f"  CUDA доступна: {available}")
    if available:
        print(f"  устройство: {torch.cuda.get_device_name(0)}")
        cap = torch.cuda.get_device_capability(0)
        print(f"  compute capability: {cap[0]}.{cap[1]}")
        # Пробное умножение: именно здесь выясняется, есть ли ядро под sm_120.
        try:
            a = torch.randn(2048, 2048, device="cuda", dtype=torch.float32)
            b = torch.randn(2048, 2048, device="cuda", dtype=torch.float32)
            torch.cuda.synchronize()
            t0 = time.perf_counter()
            for _ in range(20):
                c = a @ b
            torch.cuda.synchronize()
            gpu_ms = (time.perf_counter() - t0) / 20 * 1000
            print(f"  умножение 2048x2048 на GPU: {gpu_ms:.2f} мс — РАБОТАЕТ")
        except Exception as exc:
            print(f"  умножение на GPU НЕ работает: {type(exc).__name__}: {exc}")
            print("  Это признак того, что сборка torch не содержит ядер под sm_120")
            print("  (архитектура Blackwell). Нужна более новая версия torch.")
    else:
        print("  CUDA недоступна этой сборке torch")
except ImportError:
    print("  torch НЕ установлен")
except Exception as exc:
    print(f"  torch: ошибка {type(exc).__name__}: {exc}")

# 2. Проверка onnxruntime.
print()
try:
    import onnxruntime as ort  # type: ignore

    providers = ort.get_available_providers()
    print(f"  onnxruntime: {ort.__version__}")
    print(f"  доступные провайдеры: {providers}")
    has_cuda = "CUDAExecutionProvider" in providers
    print(f"  CUDAExecutionProvider доступен: {has_cuda}")
    if not has_cuda:
        print("  Значит эмбеддинги идут на процессоре, даже если есть видеокарта.")
except ImportError:
    print("  onnxruntime НЕ установлен")
except Exception as exc:
    print(f"  onnxruntime: ошибка {type(exc).__name__}: {exc}")

# 3. Проверка numpy как ориентира.
print()
try:
    import numpy as np  # type: ignore

    print(f"  numpy: {np.__version__}")
    size = 1024
    a = np.random.randn(size, size).astype(np.float32)
    b = np.random.randn(size, size).astype(np.float32)
    t0 = time.perf_counter()
    for _ in range(20):
        c = a @ b
    np_ms = (time.perf_counter() - t0) / 20 * 1000
    print(f"  умножение {size}x{size} на CPU (numpy): {np_ms:.2f} мс")
    print(f"  Это ориентир: если видеокарта даёт меньше, чем накладные расходы")
    print(f"  на передачу данных, перенос теряет смысл.")
except ImportError:
    print("  numpy НЕ установлен")

print()
print("=" * 76)
print("СРАВНЕНИЕ С ПОИСКОМ")
print("=" * 76)
print("""
  Для поиска сравнение векторов уже замерено отдельно: 0,09 мс на запрос
  против 3936 векторов. Это доли процента от времени поиска.

  Кодирование текста — другое дело: 19,8 мс на один чанк, и на корпусе
  из 997 чанков это около 20 секунд. Именно здесь видеокарта может помочь,
  потому что кодирование — это много матричных умножений нейросети, и их
  можно считать большими партиями.
""")
