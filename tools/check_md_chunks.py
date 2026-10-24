"""Значения полей чанков для файлов .md: куда класть пометку роли.

Из прошлой проверки: чанки лежат в chunk_map со полями
rev_id, chunk_id, source_kind, path, node_id, start_line, end_line, lang, tokens.

Нужно посмотреть, какие значения уже используются, чтобы пометка документации
не конфликтовала с существующими и была видна в поиске.
"""

from __future__ import annotations

import pathlib
import sqlite3

ROOTS = {
    "rdpmon": r"C:\1st_RdpMON\Service",
    "z": r"C:\AutoClaw\z.batteryservice.ru",
}

for label, root in ROOTS.items():
    lex = pathlib.Path(root) / ".ragraf" / "index" / "lex" / "lexvec.sqlite"
    if not lex.exists():
        print(f"\n{label}: базы нет ({lex})")
        continue
    conn = sqlite3.connect(f"file:{lex}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rev = conn.execute(
        "SELECT rev_id FROM chunk_map ORDER BY rev_id DESC LIMIT 1"
    ).fetchone()["rev_id"]
    print()
    print("=" * 78)
    print(f"КОРПУС {label}  (ревизия {rev})")
    print("=" * 78)

    print("\n  source_kind по всем чанкам:")
    for r in conn.execute(
        "SELECT source_kind AS v, COUNT(*) AS n FROM chunk_map WHERE rev_id = ? "
        "GROUP BY source_kind ORDER BY n DESC",
        (rev,),
    ).fetchall():
        print(f"    {r['v']!r}: {r['n']}")

    print("\n  lang для чанков из файлов .md:")
    for r in conn.execute(
        "SELECT lang AS v, COUNT(*) AS n FROM chunk_map WHERE rev_id = ? "
        "AND path LIKE '%.md' GROUP BY lang ORDER BY n DESC",
        (rev,),
    ).fetchall():
        print(f"    {r['v']!r}: {r['n']}")

    print("\n  source_kind для чанков из файлов .md:")
    for r in conn.execute(
        "SELECT source_kind AS v, COUNT(*) AS n FROM chunk_map WHERE rev_id = ? "
        "AND path LIKE '%.md' GROUP BY source_kind ORDER BY n DESC",
        (rev,),
    ).fetchall():
        print(f"    {r['v']!r}: {r['n']}")

    print("\n  примеры путей .md (первые 12):")
    for r in conn.execute(
        "SELECT DISTINCT path FROM chunk_map WHERE rev_id = ? AND path LIKE '%.md' "
        "ORDER BY path LIMIT 12",
        (rev,),
    ).fetchall():
        print(f"    {r['path']}")

    total = conn.execute(
        "SELECT COUNT(*) AS n FROM chunk_map WHERE rev_id = ?", (rev,)
    ).fetchone()["n"]
    md = conn.execute(
        "SELECT COUNT(*) AS n FROM chunk_map WHERE rev_id = ? AND path LIKE '%.md'",
        (rev,),
    ).fetchone()["n"]
    print(f"\n  всего чанков: {total}, из них .md: {md} ({md / total * 100:.1f}%)")

    conn.close()
