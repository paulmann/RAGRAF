"""Разведка телеметрии: как учитывается тот, кто использует RAGRAF.

Найдено: управляющая база лежит в `<корень>/.ragraf/control.sqlite`, а не
в каталоге индекса. Проверяется фактическое содержимое:
  1. схема usage_call и usage_session;
  2. значения поля `tool` — различает ли оно АГЕНТОВ или только каналы;
  3. есть ли что-то ещё, позволяющее отделить одного агента от другого;
  4. можно ли сейчас отфильтровать статистику по агенту.

Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]

# Управляющие базы всех корпусов.
BASES = {
    "z.batteryservice.ru": pathlib.Path(
        r"C:\AutoClaw\z.batteryservice.ru\.ragraf\control.sqlite"
    ),
    "revoap.com": pathlib.Path(r"C:\AutoClaw\revoap.com\.ragraf\control.sqlite"),
    "MD.Viewer": pathlib.Path(r"C:\AutoClaw\MD.Viewer\.ragraf\control.sqlite"),
    "RdpMON": pathlib.Path(r"C:\1st_RdpMON\Service\.ragraf\control.sqlite"),
}

for name, path in BASES.items():
    if not path.exists():
        print(f"\n{name}: базы нет ({path})")
        continue
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    print()
    print("=" * 78)
    print(f"{name}   ({path.stat().st_size / 1024:.0f} КБ)")
    print("=" * 78)

    tables = [
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    ]
    print(f"  таблицы: {tables}")

    for table in ("usage_call", "usage_session"):
        if table not in tables:
            continue
        cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
        n = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        print(f"\n  {table}  (строк {n})")
        print(f"    поля: {', '.join(c['name'] for c in cols)}")

        # Значения tool: главный вопрос разведки.
        if n:
            rows = conn.execute(
                f"SELECT tool, COUNT(*) AS n FROM {table} "
                f"GROUP BY tool ORDER BY n DESC"
            ).fetchall()
            print(f"    значения tool ({len(rows)}):")
            for r in rows:
                print(f"      {r['tool']!r}: {r['n']}")

    # Есть ли поля, различающие агента.
    if "usage_session" in tables:
        cols = {c["name"] for c in conn.execute(
            "PRAGMA table_info(usage_session)"
        ).fetchall()}
        print(f"\n  поля сессии: {sorted(cols)}")
        # Кандидаты на «кто использовал».
        for candidate in ("tool", "note", "session_id", "agent", "caller",
                          "source", "client", "user", "actor"):
            mark = "есть" if candidate in cols else "нет"
            print(f"    {candidate:<12}: {mark}")
        rows = conn.execute(
            "SELECT * FROM usage_session ORDER BY started_at DESC LIMIT 4"
        ).fetchall()
        print(f"\n  последние сессии:")
        for r in rows:
            print(f"    {dict(r)}")

    conn.close()

print()
print("=" * 78)
print("ГДЕ ЗАПОЛНЯЕТСЯ tool: ПОИСК ПО КОДУ")
print("=" * 78)
import re  # noqa: E402

src_dir = REPO / "src" / "ragraf"
for path in sorted(src_dir.rglob("*.py")):
    text = path.read_text(encoding="utf-8")
    for i, line in enumerate(text.splitlines(), 1):
        if "tool=" in line or "tool =" in line:
            print(f"  {path.relative_to(REPO)}:{i}: {line.strip()[:88]}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Проверка показывает, что уже собирается про пользователя RAGRAF.
  Дальнейшее зависит от того, что именно лежит в поле tool: канал вызова
  (mcp/cli/admin) или имя агента.
""")
