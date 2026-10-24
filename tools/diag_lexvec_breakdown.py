"""Диагностика: что именно занимает 27,7 с в стадии lexvec.

Факты:
  * кодирование 997 чанков на видеокарте: 0,39 с;
  * загрузка модели torch: 4,4 с;
  * но стадия lexvec занимает 27,7 с.

Значит время уходит на что-то ещё. Подозрение на порядок вызовов:
индексатор СНАЧАЛА вызывает embedder.status(), который грузит модель ONNX
(0,95 с) только чтобы проверить доступность, а затем embed() грузит ещё и
модель torch (4,4 с). Плюс возможна работа с базой.

Проверяется замером по частям: сколько времени занимает каждый шаг.
"""

from __future__ import annotations

import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import corpus  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.embeddings import Embedder  # noqa: E402
from ragraf.extract.css_html import MultiExtractor  # noqa: E402

cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
print(f"use_gpu: {cfg.embedding.use_gpu}, device: {cfg.embedding.device}")
print(f"vec_ok проверяется отдельно (нужен store.connect)\n")

# 1. Нарезка чанков.
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
        extractor.chunks(res, data, by_symbol=cfg.chunking.by_symbol,
                         max_lines=cfg.chunking.max_chunk_lines,
                         min_lines=cfg.chunking.min_chunk_lines)
    )
cut_seconds = time.perf_counter() - t0
print(f"1. нарезка чанков: {cut_seconds:.2f} с ({len(chunks)} чанков)")

# 2. Загрузка лексического хранилища (открытие баз).
from ragraf.lexvec import LexVecStore  # noqa: E402

t0 = time.perf_counter()
lexvec = LexVecStore(cfg)
open_seconds = time.perf_counter() - t0
print(f"2. открытие лексического хранилища: {open_seconds:.2f} с "
      f"(vec_ok={lexvec.vec_ok})")

# 3. Проверка статуса эмбеддера — здесь возможна лишняя загрузка ONNX.
embedder = Embedder(cfg, allow_download=True)
t0 = time.perf_counter()
status = embedder.status()
status_seconds = time.perf_counter() - t0
print(f"3. embedder.status(): {status_seconds:.2f} с (доступен={status.available})")
print(f"   после проверки состояние вычислителя: "
      f"{embedder.compute_status().backend}")

# 4. Поиск переиспользуемых векторов.
hashes = [c.text_hash for c in chunks]
t0 = time.perf_counter()
vectors = lexvec.reusable_vectors(cfg.embedding.model, hashes)
reuse_seconds = time.perf_counter() - t0
print(f"4. поиск переиспользуемых векторов: {reuse_seconds:.2f} с "
      f"(найдено {len(vectors)})")

# 5. Что осталось закодировать.
missing = [c for c in chunks if c.text_hash not in vectors]
uniq: dict[str, str] = {}
for c in missing:
    uniq.setdefault(c.text_hash, c.text[:4000])
print(f"5. кодировать нужно: {len(uniq)} уникальных текстов")

t0 = time.perf_counter()
new_vecs = embedder.embed(list(uniq.values()))
embed_seconds = time.perf_counter() - t0
print(f"6. КОДИРОВАНИЕ: {embed_seconds:.2f} с "
      f"({embed_seconds / max(len(uniq), 1) * 1000:.2f} мс на текст)")
print(f"   вычислитель: {embedder.compute_status().backend}")

# 7. Запись в базу.
t0 = time.perf_counter()
lexvec.add_chunks("test-rev-probe", chunks, dict(zip(uniq.keys(), new_vecs)))
write_seconds = time.perf_counter() - t0
print(f"7. запись чанков в базу: {write_seconds:.2f} с")

print()
print("=" * 76)
print("РАСПРЕДЕЛЕНИЕ 27,7 с ВНУТРИ СТАДИИ")
print("=" * 76)
total = (cut_seconds + open_seconds + status_seconds + reuse_seconds
         + embed_seconds + write_seconds)
print(f"  нарезка чанков:      {cut_seconds:>7.2f} с")
print(f"  открытие хранилища:  {open_seconds:>7.2f} с")
print(f"  status():            {status_seconds:>7.2f} с")
print(f"  поиск векторов:      {reuse_seconds:>7.2f} с")
print(f"  КОДИРОВАНИЕ:         {embed_seconds:>7.2f} с")
print(f"  запись в базу:       {write_seconds:>7.2f} с")
print(f"  ИТОГО:               {total:>7.2f} с")
print()
print("  Если status() занимает секунды — это лишняя загрузка модели ONNX,")
print("  которая идёт ДО выбора видеокарты и только для проверки доступности.")
print("  Это и есть источник замедления: платим за две загрузки.")

# Уборка тестовой записи.
try:
    lexvec.conn.execute("DELETE FROM chunk_map WHERE rev_id = ?", ("test-rev-probe",))
    lexvec.conn.execute("DELETE FROM ft_chunk WHERE rev_id = ?", ("test-rev-probe",))
    lexvec.conn.commit()
    print("\n  тестовая запись удалена")
except Exception as exc:
    print(f"\n  тестовую запись удалить не удалось: {type(exc).__name__}")
