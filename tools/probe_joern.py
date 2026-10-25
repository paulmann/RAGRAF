"""Проверка разбора экспорта Joern neo4jcsv на реальном каталоге."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.joern_export import calls_with_owner, load_export  # noqa: E402


def main() -> int:
    default = REPO / ".tools" / "exp_try_j18_neo4jcsv"
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else default
    print(f"каталог экспорта: {path}")
    files = [f for f in path.iterdir() if f.is_file()]
    print(f"файлов в каталоге: {len(files)}")
    total_mb = sum(f.stat().st_size for f in files) / 1_048_576
    print(f"суммарный размер: {total_mb:.1f} МБ")

    import time

    t0 = time.perf_counter()
    g = load_export(path)
    dt = time.perf_counter() - t0
    print(f"\nразбор: {dt:.1f} с")
    print(f"файлов просмотрено: {g.files_seen}, использовано: {g.files_used}")
    print(f"вершин всего: {g.nodes_total}, сохранено: {len(g.nodes)}")
    print(f"рёбер всего: {g.edges_total}, сохранено: {len(g.edges)}")
    print(f"метки вершин: {dict(Counter(g.label_counts()))}")
    print(f"типы рёбер: {dict(Counter(g.edge_label_counts()))}")

    calls = calls_with_owner(g)
    with_owner = [c for c in calls if c.get("owner_method")]
    print(f"\nвызовов: {len(calls)}, с владельцем: {len(with_owner)}")
    print("\nпримеры (владелец -> цель):")
    for c in with_owner[:10]:
        print(f"  {c.get('owner_method')} -> {c.get('callee')} [{c.get('line')}]")
    print("\nтоп целей:")
    for name, cnt in Counter(c.get("callee") for c in with_owner).most_common(8):
        print(f"  {cnt:>5}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
