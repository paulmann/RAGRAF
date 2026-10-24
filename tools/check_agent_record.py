"""Откуда в рабочем журнале revoap появилась запись с агентом.

Проверка test_cli_agent_env.py записала агентов в КОПИЮ журнала, но затем
обнаружила 1 запись с агентом в РАБОЧЕМ журнале revoap. Это надо объяснить:
возможно, она появилась от моего первого пробного вызова CLI с переменной
окружения RAGRAF_AGENT=TestAgentEnv (я делал его до написания проверки),
а возможно — из-за ошибки изоляции.

Скрипт показывает, что именно записано, и ничего не меняет.
"""

from __future__ import annotations

import pathlib
import sqlite3

DB = pathlib.Path(r"C:\AutoClaw\revoap.com\.ragraf\control.sqlite")

conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

print("=" * 78)
print("ЗАПИСИ С АГЕНТОМ В РАБОЧЕМ ЖУРНАЛЕ REVOAP")
print("=" * 78)

rows = conn.execute(
    "SELECT session_id, ts, op, tool, agent, agent_kind FROM usage_call "
    "WHERE agent IS NOT NULL ORDER BY ts DESC"
).fetchall()
print(f"\n  найдено: {len(rows)}")
for r in rows:
    print(f"    op={r['op']!r} tool={r['tool']!r} agent={r['agent']!r} "
          f"kind={r['agent_kind']!r}")
    print(f"      session={r['session_id']}")

print()
print("=" * 78)
print("ВСЕ ВЫЗОВЫ ПО КАНАЛАМ (для сравнения)")
print("=" * 78)
for r in conn.execute(
    "SELECT tool, agent, COUNT(*) AS n FROM usage_call "
    "GROUP BY tool, agent ORDER BY n DESC"
).fetchall():
    print(f"  tool={str(r['tool']):<10} agent={str(r['agent']):<16} {r['n']}")

print()
print("=" * 78)
print("СЕССИИ С АГЕНТОМ")
print("=" * 78)
for r in conn.execute(
    "SELECT session_id, tool, agent, agent_kind, started_at FROM usage_session "
    "WHERE agent IS NOT NULL ORDER BY started_at DESC"
).fetchall():
    print(f"  tool={r['tool']!r} agent={r['agent']!r} "
          f"kind={r['agent_kind']!r} id={r['session_id']}")

conn.close()

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Здесь видно, какая именно запись осталась. Если это TestAgentEnv —
  значит, она появилась от пробного вызова CLI, который я делал
  ДО написания изолированной проверки (в нём переменная окружения была
  задана в текущей оболочке, и вызов пошёл в рабочий журнал).

  Такую запись надо убрать: она тестовая, а не рабочая. Правка делается
  отдельным скриптом с явным указанием, что удаляется.
""")
