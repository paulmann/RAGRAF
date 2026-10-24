"""Проверка: реальное ускорение индексации на видеокарте.

Что проверяется:
  1. включается ли видеокарта и видно ли это в состоянии вычислителя;
  2. выключается ли она переключателем и возвращается ли прежний путь;
  3. сколько времени занимает кодирование корпуса на видеокарте против процессора;
  4. совпадают ли результаты на реальном корпусе (а не на пяти текстах);
  5. что происходит при недоступной видеокарте — переход на процессор,
     а не падение.

Замер идёт на РЕАЛЬНЫХ чанках корпуса, но базы не меняются: кодирование
выполняется в память.
"""

from __future__ import annotations

import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import numpy as np  # noqa: E402

from ragraf import corpus  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.embeddings import Embedder  # noqa: E402
from ragraf.extract.css_html import MultiExtractor  # noqa: E402

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

# Собираются реальные тексты чанков.
scan = corpus.scan(cfg)
extractor = MultiExtractor()
texts: list[str] = []
for cf in scan.files:
    if cf.lang not in ("php", "css", "html"):
        continue
    try:
        data = pathlib.Path(cf.abs_path).read_bytes()
    except OSError:
        continue
    res = extractor.extract(cf.path, data)
    for ch in extractor.chunks(
        res, data, by_symbol=cfg.chunking.by_symbol,
        max_lines=cfg.chunking.max_chunk_lines,
        min_lines=cfg.chunking.min_chunk_lines,
    ):
        texts.append(ch.text[:4000])
print(f"чанков для кодирования: {len(texts)}\n")

print("=== 1. Кодирование на видеокарте (по умолчанию) ===")
# ВАЖНО: используется партия ВЫШЕ порога окупаемости. Видеокарта включается
# только когда работы достаточно: разовая подготовка карты (около 26 с) должна
# окупиться. На малой партии вычислитель вернётся к процессору — это правильное
# поведение, а не отказ. Проверка порога — в tests/test_gpu_threshold.py.
cfg.embedding.use_gpu = True
cfg.embedding.device = "auto"
threshold = int(cfg.embedding.gpu_min_texts)

gpu_texts = [f"{t} #вариант {i}" for i, t in enumerate(texts)]
if len(gpu_texts) < threshold:
    # Партия добирается повторами до порога: для замера вычислителя этого
    # достаточно, а содержимое текстов на выбор вычислителя не влияет.
    gpu_texts = (gpu_texts * ((threshold // max(len(gpu_texts), 1)) + 1))[:threshold + 500]
print(f"  текстов для проверки видеокарты: {len(gpu_texts)} "
      f"(порог {threshold})")

gpu_embedder = Embedder(cfg, allow_download=False)
# Прогрев: первая загрузка модели не входит в замер кодирования.
_ = gpu_embedder.embed(gpu_texts[:threshold + 100])
t0 = time.perf_counter()
gpu_vectors = np.array(gpu_embedder.embed(gpu_texts), dtype=np.float32)
gpu_seconds = time.perf_counter() - t0
gpu_status = gpu_embedder.compute_status()
print(f"  вычислитель: {gpu_status.backend} на {gpu_status.device}")
print(f"  время: {gpu_seconds:.2f} с ({gpu_seconds / len(gpu_texts) * 1000:.2f} мс на чанк)")
print(f"  получено: {gpu_vectors.shape}")
check(gpu_status.backend == "torch-gpu", "используется видеокарта",
      f"получено {gpu_status.backend}")

print("\n=== 2. Кодирование на процессоре (выключатель) ===")
cfg.embedding.use_gpu = False
cpu_embedder = Embedder(cfg, allow_download=False)
_ = cpu_embedder.embed(texts[:16])
t0 = time.perf_counter()
cpu_vectors = np.array(cpu_embedder.embed(gpu_texts), dtype=np.float32)
cpu_seconds = time.perf_counter() - t0
cpu_status = cpu_embedder.compute_status()
print(f"  вычислитель: {cpu_status.backend} на {cpu_status.device}")
print(f"  время: {cpu_seconds:.2f} с ({cpu_seconds / len(gpu_texts) * 1000:.2f} мс на чанк)")
check(cpu_status.backend == "onnx-cpu", "переключатель выключает видеокарту",
      f"получено {cpu_status.backend}")

print("\n=== 3. Выигрыш на самом кодировании (без разовой подготовки) ===")
# Сравнивается ВТОРОЙ прогон на каждом вычислителе: разовая подготовка
# видеокарты исключается, поэтому видно чистое ускорение счёта.
t0 = time.perf_counter()
gpu_embedder.embed(gpu_texts)
gpu_warm = time.perf_counter() - t0
t0 = time.perf_counter()
cpu_embedder.embed(gpu_texts)
cpu_warm = time.perf_counter() - t0
speedup = cpu_warm / gpu_warm if gpu_warm else 0
print(f"  видеокарта (прогрета): {gpu_warm:.2f} с")
print(f"  процессор (прогрет):   {cpu_warm:.2f} с")
print(f"  ускорение:  {speedup:.1f}x")
check(speedup > 3, "видеокарта заметно быстрее на самом счёте",
      f"получено {speedup:.1f}x")

print("\n=== 4. Совпадают ли результаты на реальном корпусе ===")
if gpu_vectors.shape == cpu_vectors.shape:
    # Сравниваются косинусные близости: они и используются при поиске.
    norms_a = np.linalg.norm(gpu_vectors, axis=1, keepdims=True)
    norms_b = np.linalg.norm(cpu_vectors, axis=1, keepdims=True)
    cos = ((gpu_vectors / norms_a) * (cpu_vectors / norms_b)).sum(axis=1)
    print(f"  векторов сравнено: {len(cos)}")
    print(f"  минимальная близость: {cos.min():.6f}")
    print(f"  средняя близость:     {cos.mean():.6f}")
    check(cos.min() > 0.999, "результаты совпадают", f"минимум {cos.min():.6f}")
    bad = int((cos < 0.999).sum())
    check(bad == 0, "нет чанков с расхождением", f"расхождений: {bad}")
else:
    check(False, "формы результатов совпадают",
          f"{gpu_vectors.shape} и {cpu_vectors.shape}")

print("\n=== 5. Поведение при недоступной видеокарте ===")
# Проверяется откат: настройка разрешает видеокарту, но вычислитель её не видит.
cfg.embedding.use_gpu = True
cfg.embedding.device = "cuda:99"   # заведомо несуществующее устройство
fallback = Embedder(cfg, allow_download=False)
try:
    vecs = fallback.embed(gpu_texts[:threshold + 100])
    st = fallback.compute_status()
    print(f"  вычислитель после отказа: {st.backend}")
    print(f"  причина: {st.reason[:120]}")
    check(len(vecs) == threshold + 100,
          "кодирование выполнено несмотря на отказ видеокарты")
    check(st.backend == "onnx-cpu", "произошёл переход на процессор",
          f"получено {st.backend}")
except Exception as exc:
    check(False, "откат на процессор", f"исключение {type(exc).__name__}: {exc}")

print("\n=== ИТОГ ===")
print(f"пройдено: {PASS}, провалено: {FAIL}")
raise SystemExit(0 if FAIL == 0 else 1)
