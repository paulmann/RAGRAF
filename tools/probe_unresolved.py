"""Диагностика неразрешённых вызовов CPG: какие имена и пути не совпали."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store, util  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.cpg import parse_calls_export  # noqa: E402
from ragraf.joern_export import (  # noqa: E402
    calls_with_owner, load_export, normalize_joern_name, normalize_joern_path,
)


def main() -> int:
    cfg = load_config(REPO / "config" / "revoap.yaml")
    rev = util.read_json(cfg.rev_file, default={}).get("rev_id")
    exp = cfg.ragraf_dir / "cpg" / f"export_{rev}_neo4jcsv"
    print(f"ревизия {rev}, экспорт {exp.name}")
    g = load_export(exp)
    calls = parse_calls_export(calls_with_owner(g))
    print(f"вызовов после фильтрации: {len(calls)}")

    conn = store.connect(cfg.graph_db, readonly=True)
    our_fqns = {
        str(r["fqn"]) for r in conn.execute(
            "SELECT fqn FROM node WHERE rev_id = ? AND kind IN ('method','function')"
            " AND fqn IS NOT NULL",
            (rev,),
        ).fetchall()
    }
    our_files = {
        str(r["path"]) for r in conn.execute(
            "SELECT DISTINCT path FROM file WHERE rev_id = ?", (rev,)
        ).fetchall()
    }
    conn.close()
    print(f"наших методов/функций: {len(our_fqns)}, файлов: {len(our_files)}")

    missing_owner: list[str] = []
    missing_callee: list[str] = []
    missing_files: list[str] = []
    for c in calls:
        owner = normalize_joern_name(c.get("owner_method"))
        callee = normalize_joern_name(c.get("callee"))
        f = normalize_joern_path(c.get("owner_file"))
        if owner not in our_fqns:
            missing_owner.append(owner)
        if callee not in our_fqns:
            missing_callee.append(callee)
        if f and f not in our_files:
            missing_files.append(f)

    print(f"\nвладелец не найден: {len(missing_owner)} из {len(calls)}")
    print("  топ отсутствующих владельцев:")
    for n, cnt in Counter(missing_owner).most_common(8):
        print(f"    {cnt:>5}  {n}")
    print(f"\nцель не найдена: {len(missing_callee)}")
    print("  топ отсутствующих целей:")
    for n, cnt in Counter(missing_callee).most_common(10):
        print(f"    {cnt:>5}  {n}")
    print(f"\nфайл не найден в индексе: {len(missing_files)}")
    for n, cnt in Counter(missing_files).most_common(8):
        print(f"    {cnt:>5}  {n}")

    print("\nпримеры неразрешённых владельцев и целей в паре:")
    shown = 0
    for c in calls:
        owner = normalize_joern_name(c.get("owner_method"))
        callee = normalize_joern_name(c.get("callee"))
        if owner not in our_fqns and callee not in our_fqns and shown < 6:
            print(f"  owner={owner!r} file={normalize_joern_path(c.get('owner_file'))!r}")
            print(f"    callee={callee!r} dispatch={c.get('dispatch')}")
            shown += 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
