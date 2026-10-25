"""Диагностика: почему один и тот же вызов даёт 0,3 с и 25,9 с.

Противоречие:
  * изолированно: SentenceTransformer на cuda, 717 текстов — 0,301 с;
  * в реальном потоке через Embedder: те же 717 текстов — 25,89 с.

Разница в 85 раз при одинаковых данных и одинаковой модели. Значит дело
в окружении вызова, а не в данных. Проверяются гипотезы последовательно,
с замером каждой:

  1. на каком устройстве оказывается модель внутри TorchBackend;
  2. не сбивается ли модель на процессор после загрузки ONNX-модели;
  3. сколько времени занимает каждый шаг внутри Embedder.embed;
  4. не влияет ли конкуренция за потоки процессора.

Ничего не меняет: только замеряет.
"""

from __future__ import annotations

import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import torch  # noqa: E402

from ragraf import corpus  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.extract.css_html import MultiExtractor  # noqa: E402

print("=" * 76)
print("1. Настройки потоков и окружения")
print("=" * 76)
print(f"  torch.get_num_threads(): {torch.get_num_threads()}")
print(f"  torch.cuda.is_available(): {torch.cuda.is_available()}")
import os  # noqa: E402

for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "CUDA_VISIBLE_DEVICES",
            "TOKENIZERS_PARALLELISM", "RAGRAF_GPU"):
    print(f"  {var} = {os.environ.get(var)!r}")

# Реальные тексты чанков.
cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
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
# Уникальные тексты, как в реальном потоке.
uniq = list(dict.fromkeys(texts))[:717]
print(f"\n  реальных уникальных текстов: {len(uniq)}")
lengths = [len(t) for t in uniq]
print(f"  длина: минимум {min(lengths)}, среднее {sum(lengths) // len(lengths)}, "
      f"максимум {max(lengths)}")

print()
print("=" * 76)
print("2. ЧИСТЫЙ случай: модель на cuda напрямую (как в изолированном тесте)")
print("=" * 76)
from sentence_transformers import SentenceTransformer  # noqa: E402

model_name = cfg.embedding.model
t0 = time.perf_counter()
direct = SentenceTransformer(model_name, device="cuda")
print(f"  загрузка: {time.perf_counter() - t0:.2f} с")
print(f"  устройство модели: {direct.device}")
direct.encode(uniq[:16], batch_size=128, show_progress_bar=False)
torch.cuda.synchronize()
t0 = time.perf_counter()
direct.encode(uniq, batch_size=128, show_progress_bar=False)
torch.cuda.synchronize()
direct_seconds = time.perf_counter() - t0
print(f"  кодирование {len(uniq)} текстов: {direct_seconds:.3f} с "
      f"({direct_seconds / len(uniq) * 1000:.2f} мс на текст)")

print()
print("=" * 76)
print("3. ЧЕРЕЗ Embedder: тот же вызов, но через штатный путь проекта")
print("=" * 76)
from ragraf.embeddings import Embedder  # noqa: E402

embedder = Embedder(cfg, allow_download=False)
# Именно так вызывает индексатор: сначала проверка доступности.
t0 = time.perf_counter()
status = embedder.status()
print(f"  embedder.status(): {time.perf_counter() - t0:.2f} с "
      f"(доступен={status.available})")
print(f"  после status() вычислитель: {embedder.compute_status().backend}")

t0 = time.perf_counter()
vectors = embedder.embed(uniq)
embedder_seconds = time.perf_counter() - t0
print(f"  кодирование {len(uniq)} текстов: {embedder_seconds:.3f} с "
      f"({embedder_seconds / len(uniq) * 1000:.2f} мс на текст)")
print(f"  вычислитель после: {embedder.compute_status().backend}")

# Проверка, где реально лежит модель внутри backend.
backend = getattr(embedder, "_backend", None)
if backend is not None:
    model = getattr(backend, "_model", None)
    if model is not None:
        devices = {str(p.device) for p in model.parameters()}
        print(f"  устройства параметров модели в backend: {devices}")
        print(f"  backend.device (настройка): {backend.device}")
        print(f"  backend.batch_size: {backend.batch_size}")
        print(f"  время загрузки модели в backend: {backend.load_seconds:.2f} с")
else:
    print("  backend не создан — значит считалось через ONNX")

print()
print("=" * 76)
print("СРАВНЕНИЕ")
print("=" * 76)
print(f"  напрямую:  {direct_seconds:.3f} с")
print(f"  Embedder:  {embedder_seconds:.3f} с")
ratio = embedder_seconds / direct_seconds if direct_seconds else 0
print(f"  отношение: {ratio:.1f}x")
if ratio > 3:
    print("  ВЫВОД: путь через Embedder медленнее. Причина — в его устройстве.")
else:
    print("  ВЫВОД: пути сопоставимы, прежнее расхождение было случайным.")
