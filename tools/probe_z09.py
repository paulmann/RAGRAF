"""Диагностика двух мест замера: промах Z-09 и низкая проверяемость.

Z-09 (table_usage для DocApproval) не дал результата. Возможные причины:
  * таблица создаётся миграцией и не упоминается в PHP-коде проекта;
  * в индексе нет рёбер queries к этой таблице.

Проверяемость 30% — надо понять, у каких операций факты без path:line и
является ли это дефектом или нормой для конкретной операции.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store, util  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.facade import Facade  # noqa: E402

TABLES = ["DocApproval", "DocPayments", "DocDepartment", "DocSetting", "Users", "OutgoingLetters"]


def main() -> int:
    cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
    facade = Facade(cfg)
    rev = util.read_json(cfg.rev_file, default={}).get("rev_id")
    print(f"ревизия: {rev}")

    print("\n=== есть ли таблицы в графе (schema) ===")
    data = facade.op_schema()
    rows = data.get("result") or []
    print(f"  таблиц в графе: {len(rows)}")
    names = [str(r.get("table_name")) for r in rows]
    for want in TABLES:
        print(f"    {want:18} {'ЕСТЬ' if want in names else 'нет'}")

    print("\n=== использование таблиц (queries) ===")
    for t in TABLES:
        d = facade.op_table_usage(t)
        res = d.get("result") or {}
        refs = res.get("references") or []
        lits = res.get("sql_literals") or []
        print(f"  {t:18} found={d.get('found')} ссылок={len(refs)} литералов={len(lits)}")

    print("\n=== рёбра queries в графе по таблицам ===")
    conn = store.connect(cfg.graph_db, readonly=True)
    rows = conn.execute(
        """SELECT dst, COUNT(*) AS c FROM edge
            WHERE rev_id = ? AND kind = 'queries' GROUP BY dst ORDER BY c DESC LIMIT 15""",
        (rev,),
    ).fetchall()
    for r in rows:
        print(f"    {r['c']:>4}  {r['dst']}")
    conn.close()

    print("\n=== Z-09: упоминается ли DocApproval в PHP-коде корпуса ===")
    import subprocess

    proc = subprocess.run(
        ["rg", "--files-with-matches", "-e", "DocApproval", "."],
        cwd=str(cfg.root), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    files = [f for f in (proc.stdout or "").splitlines() if f.strip()]
    print(f"  rg 'DocApproval': {files if files else '(в коде не упоминается)'}")

    print("\n=== проверяемость по операциям: где факты без path:line ===")
    probes = {
        "symbol": lambda: facade.op_symbol("z_agr_setting", limit=5),
        "search": lambda: facade.op_search("реестр настроек шифрование", limit=5),
        "table_usage": lambda: facade.op_table_usage("Users"),
        "repo_map": lambda: facade.op_repo_map(budget_tokens=800),
        "callers": lambda: facade.op_callers("z_agr_setting", depth=1),
    }
    for name, fn in probes.items():
        d = fn()
        res = d.get("result")
        total = 0
        with_path = 0
        with_line = 0
        if isinstance(res, list):
            items = [x for x in res if isinstance(x, dict)]
        elif isinstance(res, dict):
            items = []
            for k, v in res.items():
                if isinstance(v, list):
                    items.extend(x for x in v if isinstance(x, dict))
        else:
            items = []
        for it in items:
            total += 1
            if it.get("path"):
                with_path += 1
            if it.get("path") and (it.get("start_line") is not None or it.get("line") is not None):
                with_line += 1
        print(f"  {name:12} фактов={total:>3}  с path={with_path:>3}  с path+строкой={with_line:>3}")

    facade.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
