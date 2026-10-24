"""Проверка через штатный путь RAGRAF: есть ли вызовы в Python-графе.

Мои диагностические вызовы Joern давали ошибку разбора параметров и на пути
с `!`, и без него — то есть ошибочен был мой вызов, а не путь и не язык.
Отлаживать его заново смысла нет: в RAGRAF уже есть отлаженный метод
`JoernRunner.query()` (проверен тестом test_joern_server, 15/15).

Здесь используется именно он: тот же путь, которым RAGRAF получает вызовы
из графа при индексации. Если через него данные приходят — граф Python
пригоден, и падал только экспорт. Если нет — граф действительно непригоден.

Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf import cpg as cpg_mod  # noqa: E402

CASES = [
    ("sagaai (Python)", REPO / "config" / "sagaai.yaml"),
    ("z (PHP)", REPO / "config" / "z-batteryservice.yaml"),
]

# Запросы берутся те же, что использует RAGRAF при индексации.
QUERIES = [
    ("вызовы (QUERY_CALLS)", cpg_mod.QUERY_CALLS),
    ("методы (QUERY_METHODS)", cpg_mod.QUERY_METHODS),
]

for title, config_path in CASES:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)
    cfg = load_config(config_path)
    runner = cpg_mod.JoernRunner(cfg)
    files = sorted(runner.workdir.glob("rev-*.cpg.bin"),
                   key=lambda p: -p.stat().st_size)
    if not files:
        print("  CPG нет")
        continue
    cpg_path = files[0]
    print(f"  CPG: {cpg_path.name} ({cpg_path.stat().st_size / 1024 / 1024:.1f} МБ)")

    # rev_id извлекается из имени файла: query() его ожидает.
    rev_id = cpg_path.stem.replace(".cpg", "")
    print(f"  ревизия: {rev_id}")

    for label, query_text in QUERIES:
        try:
            ok, err, rows = runner.query(rev_id, query_text, timeout=900)
        except Exception as exc:
            print(f"  {label:<24} ИСКЛЮЧЕНИЕ {type(exc).__name__}: {exc}")
            continue
        count = len(rows) if isinstance(rows, list) else "?"
        print(f"  {label:<24} ok={ok}, строк {count}")
        if not ok:
            print(f"    ошибка: {str(err)[:200]}")
        elif isinstance(rows, list) and rows:
            print(f"    пример: {str(rows[0])[:150]}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Если через штатный query() вызовы приходят — граф Python пригоден,
  и надо исправить только путь получения данных (экспорт), а не
  отказываться от графа.

  Если не приходят — граф Python содержит узлы без обязательных рёбер
  REF на метод (установлено по стеке: SchemaViolationException
  в MethodRef.referencedMethod), и это дефект фронтенда Joern для Python.
  Тогда честный вывод для этого проекта: RAGRAF работает по данным
  разбора деревом, а операция slice недоступна.
""")
