"""Фактическое состояние индекса sagaai: прямо из базы.

`op_index_status` вернул поля, которых я ожидал, поэтому проверяю напрямую
через базу: она источник истины, а не обёртка операции.
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402

cfg = load_config(REPO / "config" / "sagaai.yaml")
db = pathlib.Path(str(cfg.graph_db))
print(f"  база: {db}")
print(f"  размер: {db.stat().st_size / 1024 / 1024:.1f} МБ")

conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

print()
print("=" * 78)
print("РЕВИЗИИ")
print("=" * 78)
for r in conn.execute(
    "SELECT rev_id, state, capability, created_at, files, nodes, edges "
    "FROM rev ORDER BY created_at DESC LIMIT 5"
).fetchall():
    keys = r.keys()
    parts = []
    for k in ("rev_id", "state", "capability"):
        if k in keys:
            parts.append(f"{k}={r[k]}")
    print(f"  {' '.join(str(p) for p in parts)}")

print()
print("=" * 78)
print("СОДЕРЖИМОЕ ПО РЕВИЗИЯМ")
print("=" * 78)
for r in conn.execute(
    "SELECT rev_id, COUNT(*) AS n FROM node GROUP BY rev_id "
    "ORDER BY n DESC LIMIT 5"
).fetchall():
    rev = r["rev_id"]
    edges = conn.execute(
        "SELECT COUNT(*) AS n FROM edge WHERE rev_id = ?", (rev,)
    ).fetchone()["n"]
    print(f"  {rev}: узлов {r['n']}, рёбер {edges}")

    kinds = conn.execute(
        "SELECT kind, COUNT(*) AS n FROM edge WHERE rev_id = ? "
        "GROUP BY kind ORDER BY n DESC LIMIT 6", (rev,)
    ).fetchall()
    for k in kinds:
        print(f"      {k['kind']:<20} {k['n']}")

print()
print("=" * 78)
print("ЧТО ВИДИТ ФАСАД")
print("=" * 78)
from ragraf.facade import Facade  # noqa: E402

fac = Facade(cfg, tool="selftest", telemetry=False, watch=False)
try:
    try:
        rev = fac.current_rev()
        print(f"  current_rev: {rev}")
    except Exception as exc:
        print(f"  current_rev: ошибка {type(exc).__name__}: {exc}")

    status = fac.op_index_status()
    print(f"  ключи op_index_status: {sorted(status)[:16]}")
    for key in ("rev_id", "capability", "files", "nodes", "edges", "coverage"):
        if key in status:
            value = status[key]
            if isinstance(value, dict):
                value = f"{{...{len(value)} полей}}"
            print(f"    {key} = {str(value)[:90]}")

    # Поиск: сколько фактов находит.
    try:
        result = fac.op_search("class", limit=5)
        facts = result.get("facts") or result.get("results") or []
        print(f"\n  поиск 'class': found={result.get('found')}, "
              f"фактов {len(facts) if isinstance(facts, list) else '?'}")
        if isinstance(facts, list) and facts:
            for f in facts[:3]:
                print(f"    {str(f)[:110]}")
    except Exception as exc:
        print(f"\n  поиск: ошибка {type(exc).__name__}: {exc}")
finally:
    fac.close()
conn.close()
