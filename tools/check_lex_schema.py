"""Схема хранилища lexvec: где лежат чанки и их поля вида.

Чанки не в графовой базе, а в lex/lexvec.sqlite. Пометку документации надо
положить туда, откуда её видно в ответе поиска.
"""

from __future__ import annotations

import pathlib
import sqlite3

LEX = pathlib.Path(__file__).resolve().parents[1].parent.parent / "1st_RdpMON" / "Service/.ragraf/index/lex/lexvec.sqlite"
print(f"  файл: {LEX}  ({LEX.stat().st_size / 1024 / 1024:.1f} МБ)")

conn = sqlite3.connect(f"file:{LEX}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

print()
print("=" * 78)
print("ТАБЛИЦЫ И ПОЛЯ")
print("=" * 78)
tables = [
    r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
]
for table in tables:
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    n = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
    print(f"\n  {table}  (строк {n})")
    print(f"    {', '.join(c['name'] + ':' + c['type'] for c in cols)}")

print()
print("=" * 78)
print("ЗНАЧЕНИЯ ПОЛЕЙ, ПОХОЖИХ НА ВИД ЧАНКА")
print("=" * 78)
for table in tables:
    cols = [c["name"] for c in conn.execute(f"PRAGMA table_info({table}")] \
        if False else [c["name"] for c in conn.execute(f"PRAGMA table_info({table})")]
    for col in ("source_kind", "text_kind", "label", "kind", "lang"):
        if col not in cols:
            continue
        rows = conn.execute(
            f"SELECT {col} AS v, COUNT(*) AS n FROM {table} "
            f"GROUP BY {col} ORDER BY n DESC LIMIT 10"
        ).fetchall()
        print(f"\n  {table}.{col}:")
        for r in rows:
            print(f"    {r['v']!r}: {r['n']}")

# Пример записи: что именно хранится про чанк документации.
print()
print("=" * 78)
print("ПРИМЕР ЗАПИСИ ДЛЯ ФАЙЛА .md")
print("=" * 78)
target = None
for table in tables:
    cols = [c["name"] for c in conn.execute(f"PRAGMA table_info({table})")]
    if "path" in cols:
        row = conn.execute(
            f"SELECT * FROM {table} WHERE path LIKE '%.md' LIMIT 1"
        ).fetchone()
        if row:
            target = (table, cols)
            print(f"\n  таблица: {table}")
            for key in row.keys():
                value = row[key]
                text = str(value)
                if len(text) > 70:
                    text = text[:70] + "…"
                print(f"    {key:<14} = {text}")
            break
if not target:
    print("  записи с .md не найдены")

conn.close()
