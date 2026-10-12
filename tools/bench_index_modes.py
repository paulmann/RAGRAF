"""Замер стоимости индексации: обоснование порогов авто-реиндексации.

Зачем замер. Сторож сейчас выполняет ПОЛНУЮ индексацию (с CPG) при каждом
изменении кода. На этом корпусе это 22 с, из которых 16 с — построение графа
свойств кода. При работе агента, который правит файлы десятки раз за сессию,
это недопустимо дорого: время тратится на пересборку того, что не изменилось.

Здесь замеряется фактическая стоимость трёх режимов, чтобы выбрать пороги
не на глаз:
  1. полная индексация с CPG;
  2. индексация без CPG (только AST, схема, лексика);
  3. сканирование корпуса без индексации (проверка контрольной суммы).

По результатам выбирается логика: когда достаточно быстрого режима, а когда
нужен полный.
"""

from __future__ import annotations

import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import corpus, store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
print(f"корпус: {cfg.project}, режим Joern: {cfg.index.joern_enabled}\n")

# 1. Сканирование (то, что делает сторож при каждой проверке).
t0 = time.perf_counter()
scan = corpus.scan(cfg)
scan_time = time.perf_counter() - t0
print(f"1. сканирование корпуса: {scan_time:.3f} с ({len(scan.files)} файлов)")

# 2. Индексация без CPG: временно выключаем Joern.
original_joern = cfg.index.joern_enabled
try:
    cfg.index.joern_enabled = False
    from ragraf.indexer import Indexer

    idx = Indexer(cfg)
    try:
        t0 = time.perf_counter()
        report = idx.run(with_vectors=True)
        fast_time = time.perf_counter() - t0
    finally:
        idx.close()
    print(f"2. индексация БЕЗ CPG:  {fast_time:.2f} с "
          f"({report.files} файлов, {report.nodes} узлов, {report.edges} рёбер)")
finally:
    cfg.index.joern_enabled = original_joern

# 3. Полная индексация (как сейчас делает сторож).
from ragraf.indexer import Indexer  # noqa: E402

idx = Indexer(cfg)
try:
    t0 = time.perf_counter()
    report_full = idx.run(with_vectors=True)
    full_time = time.perf_counter() - t0
finally:
    idx.close()
print(f"3. индексация С CPG:    {full_time:.2f} с "
      f"({report_full.files} файлов, {report_full.nodes} узлов, {report_full.edges} рёбер)")

# Насколько CPG расширяет граф — это цена, которую платим за полный режим.
print("\n=== ВЫВОДЫ ДЛЯ ПОРОГОВ ===")
print(f"проверка суммы (сканирование):  {scan_time:.2f} с — допустимо при каждом обращении с кэшем")
print(f"быстрая индексация без CPG:     {fast_time:.2f} с — годится для правок в ходе сессии")
print(f"полная индексация с CPG:        {full_time:.2f} с — только по накоплению или периодически")
ratio = full_time / fast_time if fast_time else 0
print(f"отношение полной к быстрой:     {ratio:.1f}×")
print(f"\nзначит: локальная правка файла требует быстрого режима ({fast_time:.1f} с),")
print(f"а полный пересбор оправдан реже — когда меняется структура вызовов")
print(f"или накопилось много правок за сессию.")
