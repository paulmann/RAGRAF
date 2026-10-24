"""Диагностика качества графа: трактовка связей, покрытие, топ-узлы.

Только чтение индекса, без выгрузки исходного кода.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store, util  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402


def main() -> int:
    cfg = load_config(REPO / "config" / "revoap.yaml")
    rev = util.read_json(cfg.rev_file, default={}).get("rev_id")
    print(f"ревизия: {rev}")
    conn = store.connect(cfg.graph_db, readonly=True)

    print("\n=== входящие доказанные связи (топ-15 методов) ===")
    rows = conn.execute(
        """SELECT n.fqn, n.path, n.start_line, COUNT(*) AS c
           FROM edge e JOIN node n ON n.rev_id = e.rev_id AND n.node_id = e.dst
           WHERE e.rev_id = ? AND e.kind = 'calls' AND e.provenance = 'stated'
           GROUP BY e.dst ORDER BY c DESC LIMIT 15""",
        (rev,),
    ).fetchall()
    for r in rows:
        print(f"  {r['c']:>4}  {r['fqn']}  ({r['path']}:{r['start_line']})")

    print("\n=== топ внешних неразрешённых целей ===")
    rows = conn.execute(
        """SELECT dst, COUNT(*) AS c FROM edge
           WHERE rev_id = ? AND dst LIKE '@ext:%'
           GROUP BY dst ORDER BY c DESC LIMIT 12""",
        (rev,),
    ).fetchall()
    for r in rows:
        print(f"  {r['c']:>4}  {r['dst']}")

    print("\n=== распределение достоверности ===")
    rows = conn.execute(
        """SELECT provenance, kind, COUNT(*) AS c, ROUND(AVG(confidence), 2) AS avg_conf
           FROM edge WHERE rev_id = ? GROUP BY provenance, kind
           ORDER BY c DESC LIMIT 14""",
        (rev,),
    ).fetchall()
    for r in rows:
        print(f"  {r['provenance']:<8} {r['kind']:<20} {r['c']:>6}  avg={r['avg_conf']}")

    print("\n=== SQL-связи с таблицами ===")
    rows = conn.execute(
        """SELECT dst, COUNT(*) AS c FROM edge
           WHERE rev_id = ? AND kind = 'queries' GROUP BY dst ORDER BY c DESC LIMIT 15""",
        (rev,),
    ).fetchall()
    for r in rows:
        print(f"  {r['c']:>4}  {r['dst']}")

    print("\n=== классы с наибольшим числом методов ===")
    rows = conn.execute(
        """SELECT fqn, path, COUNT(*) AS c FROM node
           WHERE rev_id = ? AND kind = 'class' GROUP BY node_id ORDER BY c DESC LIMIT 3""",
        (rev,),
    ).fetchall()
    rows = conn.execute(
        """SELECT substr(fqn, 1, instr(fqn, '::') - 1) AS cls, COUNT(*) AS c
           FROM node WHERE rev_id = ? AND kind = 'method' AND fqn LIKE '%::%'
           GROUP BY cls ORDER BY c DESC LIMIT 12""",
        (rev,),
    ).fetchall()
    for r in rows:
        print(f"  {r['c']:>4}  {r['cls']}")

    print("\n=== покрытие ===")
    rows = conn.execute(
        "SELECT metric, value, note FROM coverage WHERE rev_id = ? ORDER BY metric", (rev,)
    ).fetchall()
    for r in rows:
        note = f"  ({r['note']})" if r["note"] else ""
        print(f"  {r['metric']:<24} {r['value']}{note}")

    print("\n=== чанки без символа-владельца ===")
    r = conn.execute(
        """SELECT COUNT(*) AS c FROM chunk_map m WHERE m.rev_id = ?
           AND (m.node_id IS NULL OR m.node_id = '')""",
        (rev,),
    ).fetchone()
    print(f"  {r['c']} из ", end="")
    r2 = conn.execute("SELECT COUNT(*) AS c FROM chunk_map WHERE rev_id = ?", (rev,)).fetchone()
    print(f"{r2['c']}")

    print("\n=== файлы: разбор ===")
    rows = conn.execute(
        """SELECT parse_status, COUNT(*) AS c, SUM(lines) AS lines FROM file
           WHERE rev_id = ? GROUP BY parse_status ORDER BY c DESC""",
        (rev,),
    ).fetchall()
    for r in rows:
        print(f"  {r['parse_status']:<10} файлов={r['c']:>4}  строк={r['lines']}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
