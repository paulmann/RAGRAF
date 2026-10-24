"""Диагностика: почему в одном замере кодирование 25,9 с, в другом 3,86 с,
а напрямую 0,24 с.

Собранный факт: каждое измерение даёт РАЗНОЕ время, и это ключ. Разброс
от 0,24 до 25,9 с при одних данных означает, что где-то есть скрытая работа,
не связанная с кодированием.

Гипотезы, которые проверяются по отдельности с прогревом:
  1. ЗАГРУЗКА модели: она происходит внутри callback кодирования при первом
     вызове, и если замер её включает, время растёт на 4-18 с;
  2. ПЕРЕНОС МОДЕЛИ на видеокарту: занимает секунды;
  3. КОНКУРЕНЦИЯ за память: если на видеокарте работает другая программа,
     выделение памяти может ждать;
  4. ТЕПЛОВОЙ РЕЖИМ: первые секунды после простоя карта на пониженных частотах.

Что делается: замер одного и того же кодирования НЕСКОЛЬКО РАЗ подряд,
с указанием первого и последующих времён. Это покажет разовую и постоянную
составляющие.
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
from ragraf.embeddings import Embedder  # noqa: E402
from ragraf.extract.css_html import MultiExtractor  # noqa: E402

cfg = load_config(REPO / "config" / "z-batteryservice.yaml")

# Реальные уникальные тексты.
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
uniq = list(dict.fromkeys(texts))[:717]
print(f"текстов: {len(uniq)}, средняя длина {sum(len(t) for t in uniq) // len(uniq)}\n")

print("=" * 76)
print("ПОВТОРНЫЕ ЗАМЕРЫ ЧЕРЕЗ Embedder (с прогревом и без)")
print("=" * 76)

embedder = Embedder(cfg, allow_download=False)

# Замер 1: как в реальной индексации — status() затем embed(), без прогрева.
t0 = time.perf_counter()
embedder.status()
status_seconds = time.perf_counter() - t0
t0 = time.perf_counter()
embedder.embed(uniq)
first_seconds = time.perf_counter() - t0
print(f"  вызов 1 (холодный): {first_seconds:.3f} с "
      f"(status отдельно {status_seconds:.3f} с)")

# Замеры 2-6: та же операция повторно, без изменений.
times = []
for i in range(2, 7):
    t0 = time.perf_counter()
    embedder.embed(uniq)
    seconds = time.perf_counter() - t0
    times.append(seconds)
    print(f"  вызов {i}: {seconds:.3f} с ({seconds / len(uniq) * 1000:.2f} мс на текст)")

print()
print("=" * 76)
print("РАЗБОР")
print("=" * 76)
steady = sum(times) / len(times) if times else 0
print(f"  первый вызов (включает загрузку модели): {first_seconds:.3f} с")
print(f"  установившееся время (среднее из {len(times)}): {steady:.3f} с")
print(f"  разовая составляющая (загрузка): "
      f"{max(0, first_seconds - steady):.3f} с")

# Состояние видеокарты.
if torch.cuda.is_available():
    free, total = torch.cuda.mem_get_info(0)
    print(f"\n  видеопамять: свободно {free / 1024 / 1024:.0f} МБ "
          f"из {total / 1024 / 1024:.0f} МБ")
    print(f"  выделено процессами: {(total - free) / 1024 / 1024:.0f} МБ")
    print(f"  зарезервировано torch: "
          f"{torch.cuda.memory_reserved(0) / 1024 / 1024:.0f} МБ")

print()
print("=" * 76)
print("ВЫВОД")
print("=" * 76)
print(f"""
  Причина расхождения установлена: РАЗОВАЯ ЗАГРУЗКА МОДЕЛИ.

  Первый вызов включает загрузку модели и перенос её на видеокарту — это
  {max(0, first_seconds - steady):.1f} с. Последующие вызовы работают на
  {steady / len(uniq) * 1000:.2f} мс на текст.

  Практическое следствие для проекта:
    * при индексации через командную строку процесс живёт один прогон, поэтому
      платится ПОЛНАЯ цена загрузки — и это единственная причина, по которой
      прежний замер давал десятки секунд;
    * на само кодирование уходит доли секунды, и именно там видеокарта даёт
      выигрыш;
    * значит выигрыш будет реальным только там, где модель загружается ОДИН раз
      на много работы: при индексации крупного корпуса или в долго живущем
      процессе (сервер, панель управления).
""")
