"""Проверка: можно ли включить GPU в текущем стеке эмбеддингов (fastembed).

Стек сейчас: fastembed 0.8.1 поверх onnxruntime 1.30.0, провайдеры только
CPU и Azure. Видеокарта при этом не используется, хотя она есть.

Прежде чем выбирать способ ускорения, нужно выяснить три вещи:
  1. принимает ли TextEmbedding параметр для выбора провайдера или cuda;
  2. что именно за модель используется и где лежат её файлы;
  3. сколько времени занимает кодирование на реальном корпусе — это база
     для сравнения с любым ускорением.

Ничего не меняет: только читает сигнатуры и замеряет.
"""

from __future__ import annotations

import inspect
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

print("=" * 76)
print("1. СИГНАТУРА FASTEDGE TextEmbedding")
print("=" * 76)
try:
    from fastembed import TextEmbedding  # type: ignore

    sig = inspect.signature(TextEmbedding.__init__)
    for name, param in sig.parameters.items():
        if name == "self":
            continue
        default = "" if param.default is inspect.Parameter.empty else f" = {param.default!r}"
        print(f"  {name}{default}")
except Exception as exc:
    print(f"  ошибка: {type(exc).__name__}: {exc}")

print()
print("=" * 76)
print("2. МОДЕЛЬ: какая используется и сколько весит")
print("=" * 76)
from ragraf.config import load as load_config  # noqa: E402

cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
print(f"  модель из конфига: {cfg.embedding.model}")
print(f"  размерность: {cfg.embedding.dim}")

# Каталог кэша модели.
try:
    from ragraf.embeddings import Embedder

    emb = Embedder(cfg, allow_download=False)
    cache = emb.cache_dir
    print(f"  каталог кэша: {cache}")
    if cache.exists():
        files = [p for p in cache.rglob("*") if p.is_file()]
        total = sum(p.stat().st_size for p in files)
        print(f"  файлов в кэше: {len(files)}, объём: {total / 1024 / 1024:.1f} МБ")
        for p in sorted(files, key=lambda x: -x.stat().st_size)[:6]:
            print(f"    {p.name:<48} {p.stat().st_size / 1024 / 1024:>7.1f} МБ")
except Exception as exc:
    print(f"  кэш модели: {type(exc).__name__}: {exc}")

print()
print("=" * 76)
print("3. ЗАМЕР: кодирование на реальном корпусе (база для сравнения)")
print("=" * 76)

from ragraf import corpus  # noqa: E402
from ragraf.extract.css_html import MultiExtractor  # noqa: E402

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
print(f"  чанков для кодирования: {len(texts)}")

try:
    from ragraf.embeddings import Embedder

    emb = Embedder(cfg, allow_download=False)
    if not emb.available():
        print("  модель недоступна")
        raise SystemExit(0)

    # Разные размеры партии: интересно, как скорость зависит от партии.
    print(f"\n  {'партия':>8} {'секунд':>9} {'мс/чанк':>9}")
    for size in (32, 64, 128, 256):
        sample = texts[:size]
        if len(sample) < size:
            continue
        t0 = time.perf_counter()
        emb.embed(sample)
        seconds = time.perf_counter() - t0
        print(f"  {size:>8} {seconds:>9.3f} {seconds / size * 1000:>9.2f}")

    # Полный прогон на всём корпусе.
    t0 = time.perf_counter()
    vectors = emb.embed(texts)
    total_seconds = time.perf_counter() - t0
    print(f"\n  ВЕСЬ КОРПУС ({len(texts)} чанков): {total_seconds:.2f} с "
          f"({total_seconds / len(texts) * 1000:.2f} мс на чанк)")
    print(f"  получено векторов: {len(vectors)}")

    # Во сколько раз надо ускорить, чтобы это перестало быть узким местом.
    print(f"\n  Доля в индексации: стадия lexvec — 15,7 с из 16,7 с (93,9%)")
    print(f"  кодирование из них: {total_seconds:.1f} с")
except SystemExit:
    raise
except Exception as exc:
    print(f"  ошибка: {type(exc).__name__}: {exc}")
