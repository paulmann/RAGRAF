"""Проверка логики порога накопления правок.

Запуск: python tools/check_threshold.py

Проверяется, что выбор глубины реиндексации соответствует замерам
и настройкам конфига: при единичной правке — быстрый режим, при накоплении
больше порога — полный, при устаревшем индексе — полный независимо от правок.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import watch  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
category = watch.category_for(cfg)
rev = watch.indexed_hashes(category, cfg)[2]

print(f"корпус: {cfg.project}")
print(f"ревизия: {rev}")
print(f"пороги: быстрая {cfg.index.reindex_after_changes}, "
      f"полная {cfg.index.full_reindex_after_changes}, "
      f"интервал {cfg.index.reindex_interval_minutes} мин\n")

changes, age = watch.measure_accumulation(cfg, rev)
print(f"текущее состояние: правок в журнале {changes}, возраст индекса {age} мин")

# Проверка решения на искусственных значениях: реальные данные зависят
# от того, сколько раз правился код, и их недостаточно для всех ветвей.
print("\n=== решение при разном числе правок ===")
for fake in (0, 1, 4, 5, 10, 19, 20, 25):
    # Подменяется только счётчик: логика порогов остаётся настоящей.
    original = watch.measure_accumulation
    watch.measure_accumulation = lambda c, r, _f=fake, _a=age: (_f, _a)
    try:
        mode, info = watch.choose_mode(cfg, rev, changed=fake > 0)
    finally:
        watch.measure_accumulation = original
    print(f"  правок {fake:>3}: режим={mode:<5} | {info.get('reason', '—')}")

print("\n=== решение при устаревшем индексе (мало правок) ===")
original = watch.measure_accumulation
watch.measure_accumulation = lambda c, r: (2, cfg.index.reindex_interval_minutes + 10)
try:
    mode, info = watch.choose_mode(cfg, rev, changed=True)
finally:
    watch.measure_accumulation = original
print(f"  режим={mode} | {info.get('reason')}")
print(f"  признак достижения интервала: {info.get('interval_minutes')}")

print("\n=== решение без правок ===")
watch.measure_accumulation = lambda c, r: (0, 0)
try:
    mode, info = watch.choose_mode(cfg, rev, changed=False)
finally:
    watch.measure_accumulation = original
print(f"  режим={mode} (пересбор не требуется)")
