"""Итоговое состояние корпуса sagaai после всех правок.

Собирается фактическое состояние, без предположений:
  * состав корпуса (файлы, языки);
  * число узлов и рёбер по видам;
  * какие операции работают, какие недоступны и почему;
  * состояние capability (full или degraded).

Это основа для честного отчёта о том, что получилось, а что нет.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.facade import Facade  # noqa: E402

cfg = load_config(REPO / "config" / "sagaai.yaml")
fac = Facade(cfg, tool="selftest", telemetry=False, watch=False)
try:
    print("=" * 78)
    print("СОСТОЯНИЕ КОРПУСА SAGA AI")
    print("=" * 78)
    status = fac.op_index_status()
    print(f"  ревизия:    {status.get('rev_id')}")
    print(f"  capability: {status.get('capability')}")
    print(f"  файлов:     {status.get('files', status.get('files_total'))}")
    print(f"  узлов:      {status.get('nodes')}")
    print(f"  рёбер:      {status.get('edges')}")

    coverage = status.get("coverage") or {}
    if coverage:
        print(f"\n  покрытие:")
        for key in ("files_found", "files_indexed", "nodes", "edges",
                    "functions", "classes", "calls_cpg", "calls_heuristic"):
            if key in coverage:
                print(f"    {key:<20} {coverage[key]}")

    print()
    print("=" * 78)
    print("РЁБРА ПО ВИДАМ")
    print("=" * 78)
    from ragraf import store
    conn = store.connect(cfg.graph_db, readonly=True)
    rev = status.get("rev_id")
    for r in conn.execute(
        "SELECT kind, COUNT(*) AS n FROM edge WHERE rev_id = ? "
        "GROUP BY kind ORDER BY n DESC", (rev,)
    ).fetchall():
        print(f"  {r['kind']:<22} {r['n']}")

    print()
    print("  узлы по видам:")
    for r in conn.execute(
        "SELECT kind, COUNT(*) AS n FROM node WHERE rev_id = ? "
        "GROUP BY kind ORDER BY n DESC LIMIT 12", (rev,)
    ).fetchall():
        print(f"  {r['kind']:<22} {r['n']}")
    conn.close()

    print()
    print("=" * 78)
    print("ЧТО РАБОТАЕТ: ПРОБНЫЕ ЗАПРОСЫ")
    print("=" * 78)
    probes = [
        ("search", lambda: fac.op_search("история чатов", limit=3)),
        ("symbol", lambda: fac.op_symbol(name="ConversationStore")),
        ("repo_map", lambda: fac.op_repo_map(budget=800)),
        ("class_usage", lambda: fac.op_class_usage(mode="all", limit=10)),
    ]
    for label, call in probes:
        try:
            result = call()
            found = result.get("found")
            facts = result.get("facts") or []
            n = len(facts) if isinstance(facts, list) else "-"
            print(f"  {label:<14} found={found} фактов={n}")
            if isinstance(facts, list) and facts:
                first = facts[0]
                print(f"    пример: {str(first)[:100]}")
        except Exception as exc:
            print(f"  {label:<14} ОШИБКА {type(exc).__name__}: {exc}")

    print()
    print("=" * 78)
    print("ЧТО НЕ РАБОТАЕТ")
    print("=" * 78)
    for label, call in (
        ("slice", lambda: fac.op_slice("core/api_errors.py:message")),
        ("impact", lambda: fac.op_impact("core/api_errors.py:message")),
    ):
        try:
            result = call()
            error = result.get("error")
            limits = result.get("limits") or []
            print(f"  {label:<10} error={error!r}")
            for lim in limits[:3]:
                print(f"    ограничение: {str(lim)[:100]}")
        except Exception as exc:
            print(f"  {label:<10} исключение {type(exc).__name__}: {str(exc)[:80]}")
finally:
    fac.close()
