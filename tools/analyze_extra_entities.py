"""Анализ: что уже извлекается из кода и что можно извлекать дополнительно.

Задача: оценить, насколько полезно и насколько дорого добавить отдельную базу
со справочными объектами — переменными, функциями, CSS-стилями, классами,
связями «кто с кем» и «кто в кого входит», с указанием файлов и номеров строк.

Что делает скрипт:
  1. печатает фактический состав индекса (виды узлов и рёбер) — это то, что
     уже собирается;
  2. проверяет, какие СПРАВОЧНЫЕ категории уже есть в узлах, а какие теряются;
  3. измеряет, сколько объектов каждого вида придётся добавить и сколько места
     это займёт.

Ничего не изменяет: только читает базы и считает.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

for name in ("z-batteryservice", "revoap"):
    cfg = load_config(REPO / "config" / f"{name}.yaml")
    conn = store.connect(cfg.graph_db, readonly=True)
    rev = conn.execute("SELECT rev_id FROM rev ORDER BY rev_id DESC LIMIT 1").fetchone()["rev_id"]

    print(f"{'=' * 70}")
    print(f"КОРПУС: {cfg.project}   ревизия: {rev}")
    print(f"{'=' * 70}")

    print("\n--- узлы по видам (что уже есть) ---")
    for r in conn.execute(
        "SELECT kind, COUNT(*) AS n FROM node WHERE rev_id = ? GROUP BY kind ORDER BY n DESC",
        (rev,),
    ).fetchall():
        print(f"  {r['kind']:20} {r['n']:>7}")

    print("\n--- рёбра по видам ---")
    for r in conn.execute(
        "SELECT kind, provenance, COUNT(*) AS n FROM edge WHERE rev_id = ?"
        " GROUP BY kind, provenance ORDER BY n DESC",
        (rev,),
    ).fetchall():
        print(f"  {r['kind']:22} {r['provenance']:9} {r['n']:>7}")

    print("\n--- размеры баз ---")
    total = 0
    for title, path in (("граф", cfg.graph_db), ("лексика", cfg.lexvec_db),
                        ("журнал", cfg.control_db)):
        try:
            size = pathlib.Path(path).stat().st_size
            wal = pathlib.Path(str(path) + "-wal")
            wal_size = wal.stat().st_size if wal.exists() else 0
            total += size + wal_size
            print(f"  {title:10} {size / 1024 / 1024:>8.1f} МБ (+ WAL {wal_size / 1024 / 1024:.1f} МБ)")
        except OSError:
            print(f"  {title:10} нет файла")
    print(f"  {'ИТОГО':10} {total / 1024 / 1024:>8.1f} МБ")

    print("\n--- справочные категории: что можно собрать дополнительно ---")
    # Переменные: сейчас попадают ли в узлы?
    for label, kind in (("переменные", "variable"), ("константы", "constant"),
                        ("свойства", "property"), ("классы", "class"),
                        ("интерфейсы", "interface"), ("трейты", "trait"),
                        ("функции", "function"), ("методы", "method"),
                        ("CSS-классы", "css_class"), ("пространства имён", "namespace")):
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM node WHERE rev_id = ? AND kind = ?", (rev, kind)
        ).fetchone()["n"]
        mark = "есть" if n else "НЕТ"
        print(f"  {label:22} {kind:12} {n:>7}  {mark}")

    conn.close()
    print()
