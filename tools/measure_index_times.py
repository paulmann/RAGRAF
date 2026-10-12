"""Замеры для пункта 1.3.5: автоподбор порогов по времени индексации.

Идея: порог накопления в 5 и 20 правок — это константа, подобранная для корпуса
из 124 файлов. На корпусе в 10 000 файлов полный пересбор займёт минуты, и тот же
порог станет вредным. Правильный критерий — не число правок, а ОЖИДАЕМАЯ ЦЕНА
пересбора для конкретного корпуса.

Здесь замеряется фактическое время полной и быстрой индексации из журнала реестра,
чтобы на этих числах построить автоподбор порогов.

Скрипт ничего не меняет: читает реестр и базы.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.catalog import Catalog  # noqa: E402
from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

cat = Catalog()
data = cat.load()
entries = data.get("entries") or {}
print(f"разработок в реестре: {len(entries)}\n")

for name, entry in entries.items():
    files = entry.get("file_count") or 0
    print(f"=== {name} ===")
    print(f"  файлов при индексации: {files}")
    print(f"  ревизия: {entry.get('rev_id')}  capability={entry.get('capability')}")

    runs = entry.get("runs") or []
    print(f"  записей о прогонах: {len(runs)}")
    for r in runs[-8:]:
        secs = r.get("seconds") or 0
        trig = r.get("trigger") or "?"
        fc = r.get("file_count") or 0
        print(f"    {r.get('rev_id'):>10}  {secs:>7.2f} с  файлов {fc:>5}  триггер {trig}")

    # Фактическое время по ревизиям: берётся из стадий индексации.
    cfg_path = entry.get("config")
    if not cfg_path:
        print()
        continue
    try:
        cfg = load_config(cfg_path)
    except Exception as exc:
        print(f"  конфиг не прочитан: {type(exc).__name__}\n")
        continue

    conn = store.connect(cfg.graph_db, readonly=True)
    try:
        cols = [str(r[1]) for r in conn.execute("PRAGMA table_info(rev)")]
        print(f"  колонки rev: {cols}")
        rows = conn.execute(
            "SELECT * FROM rev ORDER BY rev_id DESC LIMIT 3"
        ).fetchall()
        for r in rows:
            d = dict(r)
            print(f"    ревизия {d.get('rev_id')}: файлов {d.get('file_count')}, "
                  f"узлов {d.get('node_count')}, рёбер {d.get('edge_count')}")
        # Стадии по времени: где тратится время.
        for table in ("stage", "index_stage", "rev_stage"):
            try:
                srows = conn.execute(
                    f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT 12"
                ).fetchall()
            except Exception:
                continue
            if not srows:
                continue
            print(f"  стадии из таблицы {table}:")
            for sr in srows:
                sd = dict(sr)
                print(f"    {sd.get('stage') or sd.get('name')}: "
                      f"{sd.get('seconds')} с, элементов {sd.get('items')}")
            break
    finally:
        conn.close()
    print()
