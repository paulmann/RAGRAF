"""Диагностика сопоставления имён Joern с нашими узлами.

Проблема: после фильтрации осталось 992 вызова, и ни один не привязался —
причина либо в форме имён Joern, либо в том, что фильтр отсекает слишком много.
Печатаются реальные примеры имён с обеих сторон (без выгрузки кода).
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store, util  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.cpg import is_noise_file, _is_noise_call  # noqa: E402
from ragraf.joern_export import calls_with_owner, extract_method_index, load_export  # noqa: E402


def main() -> int:
    cfg = load_config(REPO / "config" / "revoap.yaml")
    rev = util.read_json(cfg.rev_file, default={}).get("rev_id")
    print(f"наша ревизия: {rev}")

    exp_dir = cfg.ragraf_dir / "cpg" / f"export_{rev}_neo4jcsv"
    if not exp_dir.exists():
        cands = sorted((cfg.ragraf_dir / "cpg").glob("export_*_neo4jcsv"))
        if not cands:
            print("нет каталога экспорта")
            return 1
        exp_dir = cands[-1]
    print(f"экспорт: {exp_dir.name}")

    g = load_export(exp_dir)
    methods = extract_method_index(g)
    calls = calls_with_owner(g)
    print(f"\nметодов в CPG: {len(methods)}")
    print("примеры fullName методов CPG:")
    for m in list(methods.values())[:8]:
        print(f"  {m.get('fullName')}  <- {m.get('filename')}:{m.get('line')}")

    print(f"\nвызовов всего: {len(calls)}")

    # Сколько отсекается на каждом шаге фильтра
    steps = Counter()
    for c in calls:
        owner = c.get("owner_method") or ""
        callee = c.get("callee") or ""
        if not owner or not callee:
            steps["нет владельца/цели"] += 1
        elif owner.endswith("<global>"):
            steps["владелец <global>"] += 1
        elif is_noise_file(c.get("owner_file")):
            steps["служебный путь"] += 1
        elif _is_noise_call(str(callee), c.get("callee_name")):
            steps["оператор/встроенное"] += 1
        else:
            steps["прошло фильтр"] += 1
    for k, v in steps.most_common():
        print(f"  {k}: {v}")

    kept = [
        c for c in calls
        if c.get("owner_method") and not c["owner_method"].endswith("<global>")
        and not is_noise_file(c.get("owner_file"))
        and not _is_noise_call(str(c.get("callee")), c.get("callee_name"))
    ]
    print(f"\nпрошло фильтр: {len(kept)}; примеры:")
    for c in kept[:12]:
        print(f"  owner={c.get('owner_method')} [{c.get('owner_file')}:{c.get('line')}]")
        print(f"    -> callee={c.get('callee')} name={c.get('callee_name')} dispatch={c.get('dispatch')}")

    # Что есть в нашем графе
    conn = store.connect(cfg.graph_db, readonly=True)
    ours = conn.execute(
        "SELECT fqn, name, path FROM node WHERE rev_id = ? AND kind IN ('method','function') LIMIT 12",
        (rev,),
    ).fetchall()
    print("\nнаши fqn (примеры):")
    for r in ours:
        print(f"  {r['fqn']}  <- {r['path']}")
    total = conn.execute(
        "SELECT COUNT(*) AS c FROM node WHERE rev_id = ? AND kind IN ('method','function')", (rev,)
    ).fetchone()["c"]
    print(f"всего наших методов/функций: {total}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
