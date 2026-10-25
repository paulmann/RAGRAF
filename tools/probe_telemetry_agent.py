"""Разведка: как телеметрия RAGRAF учитывает того, кто её использует.

Запрос владельца: собирать статистику по каждому агенту отдельно и общую
по всем агентам AutoClaw.

Прежде чем что-то дорабатывать, надо понять фактическое состояние:
  1. какие поля есть у usage_call и usage_session;
  2. как заполняется поле `tool` — кто его передаёт;
  3. различаются ли агенты между собой в текущих данных;
  4. можно ли уже сейчас отфильтровать по агенту.

Это разведка: ничего не меняет.
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

# Управляющая база: там лежит телеметрия.
cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
print(f"  graph_db:   {cfg.graph_db}")
control_path = pathlib.Path(str(cfg.graph_db)).parent / "control.sqlite"
print(f"  control:    {control_path}")
print(f"  существует: {control_path.exists()}")

# Ищется файл управляющей базы, если имя иное.
index_dir = pathlib.Path(str(cfg.graph_db)).parent
print(f"\n  файлы в каталоге индекса:")
for f in sorted(index_dir.rglob("*.sqlite")):
    print(f"    {f.name} ({f.stat().st_size / 1024:.0f} КБ)")

# Основная база телеметрии — в корне RAGRAF (catalog/).
candidates = [
    REPO / "catalog" / "control.sqlite",
    REPO / "catalog" / "telemetry.sqlite",
    REPO / "control.sqlite",
]
found = [c for c in candidates if c.exists()]
print(f"\n  базы в корне RAGRAF: {[c.name for c in found]}")

# Читается управляющая база первого корпуса.
if not control_path.exists():
    # Пробуются все найденные.
    for c in found:
        control_path = c
        break

if not control_path.exists():
    print("\n  управляющая база не найдена — ищется по всему RAGRAF")
    for f in REPO.rglob("control.sqlite"):
        print(f"    найдена: {f.relative_to(REPO)} ({f.stat().st_size / 1024:.0f} КБ)")
    raise SystemExit(0)

conn = sqlite3.connect(f"file:{control_path}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

print()
print("=" * 78)
print("СХЕМА ТАБЛИЦ ТЕЛЕМЕТРИИ")
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
    for c in cols:
        print(f"    {c['name']:<16} {c['type']}")

print()
print("=" * 78)
print("КАКИЕ ЗНАЧЕНИЯ ПРИНИМАЕТ ПОЛЕ tool")
print("=" * 78)
if "usage_call" in tables:
    rows = conn.execute(
        "SELECT tool, COUNT(*) AS n FROM usage_call GROUP BY tool ORDER BY n DESC"
    ).fetchall()
    print(f"\n  различных значений tool в usage_call: {len(rows)}")
    for r in rows:
        print(f"    {r['tool']!r}: {r['n']} вызовов")

if "usage_session" in tables:
    rows = conn.execute(
        "SELECT tool, COUNT(*) AS n FROM usage_session GROUP BY tool ORDER BY n DESC"
    ).fetchall()
    print(f"\n  различных значений tool в usage_session: {len(rows)}")
    for r in rows:
        print(f"    {r['tool']!r}: {r['n']} сессий")

print()
print("=" * 78)
print("ЕСТЬ ЛИ ЧТО-ТО, КРОМЕ tool: ПОЗВОЛЯЕТ ЛИ ОТЛИЧИТЬ АГЕНТА")
print("=" * 78)
if "usage_session" in tables:
    cols = [c["name"] for c in conn.execute("PRAGMA table_info(usage_session)")]
    print(f"  поля usage_session: {cols}")
    rows = conn.execute(
        "SELECT * FROM usage_session ORDER BY started_at DESC LIMIT 5"
    ).fetchall()
    print(f"\n  последние сессии:")
    for r in rows:
        items = {k: r[k] for k in r.keys()}
        print(f"    {items}")

conn.close()

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Проверка показывает, какие сведения о пользователе RAGRAF уже собираются.
  Ключевой вопрос: различает ли поле `tool` разных АГЕНТОВ, или это просто
  «источник вызова» (mcp, cli, admin).
""")
