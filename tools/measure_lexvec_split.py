"""Замер: сколько времени внутри стадии lexvec уходит на кодирование векторов.

Почему это нужно. Стадия lexvec заняла 93,9% времени на одном корпусе и 32% на
другом. Но внутри неё есть три разные работы:
  * нарезка чанков — это операции со строками, CPU;
  * запись лексики в базу — это SQLite, CPU;
  * КОДИРОВАНИЕ текста в векторы — это матричные операции нейросети,
    единственная часть, которая может ускориться на видеокарте.

Без этого разделения нельзя понять, сколько именно времени можно сэкономить.

Замер идёт на РЕАЛЬНЫХ чанках корпуса, но базы не меняются: кодирование
выполняется в память, без записи.
"""

from __future__ import annotations

import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import corpus  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.extract.css_html import MultiExtractor  # noqa: E402

cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
print(f"корпус: {cfg.project}, лимит чанка: {cfg.chunking.max_chunk_lines} строк")

# 1. Собираются чанки так же, как при индексации.
scan = corpus.scan(cfg)
extractor = MultiExtractor()
t0 = time.perf_counter()
chunks = []
for cf in scan.files:
    if cf.lang not in ("php", "css", "html"):
        continue
    try:
        data = pathlib.Path(cf.abs_path).read_bytes()
    except OSError:
        continue
    res = extractor.extract(cf.path, data)
    chunks.extend(
        extractor.chunks(
            res, data, by_symbol=cfg.chunking.by_symbol,
            max_lines=cfg.chunking.max_chunk_lines,
            min_lines=cfg.chunking.min_chunk_lines,
        )
    )
chunk_seconds = time.perf_counter() - t0
print(f"\n1. нарезка чанков: {chunk_seconds:.3f} с ({len(chunks)} чанков)")

# 2. Замеряется лексика: токенизация для поиска (это SQLite + строки).
t0 = time.perf_counter()
from ragraf.lexvec import tokenize_for_fts

tokens_total = 0
for ch in chunks:
    tokens_total += len(tokenize_for_fts(ch.text))
lex_seconds = time.perf_counter() - t0
print(f"2. токенизация лексики: {lex_seconds:.3f} с "
      f"({tokens_total} единиц, {lex_seconds / max(len(chunks), 1) * 1000:.2f} мс/чанк)")

# 3. Замеряется собственно КОДИРОВАНИЕ векторов — единственная GPU-часть.
try:
    from ragraf.embeddings import Embedder

    emb = Embedder(cfg, allow_download=False)
    if not emb.available():
        print("\n3. кодирование векторов: недоступно (модель не найдена)")
        raise SystemExit(0)

    # Замер на наборе реальных текстов чанков.
    sample_size = min(len(chunks), 400)
    texts = [ch.text for ch in chunks[:sample_size]]
    chars = sum(len(t) for t in texts)

    t0 = time.perf_counter()
    vectors = emb.embed(texts)
    embed_seconds = time.perf_counter() - t0
    print(f"\n3. КОДИРОВАНИЕ ВЕКТОРОВ: {embed_seconds:.3f} с на {len(texts)} чанков")
    print(f"   {embed_seconds / max(len(texts), 1) * 1000:.2f} мс на чанк, "
          f"{chars} символов ({chars // max(len(texts),1)} симв. на чанк)")
    print(f"   получено векторов: {len(vectors)}")
    if vectors:
        print(f"   размерность вектора: {len(vectors[0])}")

    # Пересчёт на весь корпус.
    per_chunk_ms = embed_seconds / max(len(texts), 1) * 1000
    print(f"\n   оценка на весь корпус ({len(chunks)} чанков): "
          f"{per_chunk_ms * len(chunks) / 1000:.1f} с")
except SystemExit:
    raise
except Exception as exc:
    print(f"\n3. кодирование векторов: ошибка {type(exc).__name__}: {exc}")

print("\n=== Выводы ===")
print("  Из стадии lexvec на видеокарту может уйти ТОЛЬКО кодирование векторов.")
print("  Нарезка чанков и токенизация — это строки и SQLite, видеокарта не помогает.")
print("  Именно поэтому нужно знать три числа по отдельности, а не общее время lexvec.")
