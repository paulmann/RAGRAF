"""Уборка пробной записи из рабочего журнала revoap.

Что найдено: в рабочем журнале revoap есть ОДНА тестовая запись —
вызов с агентом `TestAgentEnv`, сделанный моим первым пробным вызовом CLI
до того, как проверка была изолирована. Такие записи загрязняют статистику
владельца: он увидит агента, которого у него нет.

Что удаляется: строго эта запись и её сессия. Опознаётся по имени агента
`TestAgentEnv`, которое в реальной работе не встречается (оно придумано
для проверки).

Что НЕ удаляется:
  * записи без агента (NULL) — это реальная история;
  * сессии `RAGRAF-Admin` — это работа панели администратора, она законная;
  * любые другие агенты.
"""

from __future__ import annotations

import pathlib
import sqlite3

DB = pathlib.Path(r"C:\AutoClaw\revoap.com\.ragraf\control.sqlite")
TEST_AGENT = "TestAgentEnv"

conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row

before_calls = conn.execute("SELECT COUNT(*) AS n FROM usage_call").fetchone()["n"]
before_sessions = conn.execute(
    "SELECT COUNT(*) AS n FROM usage_session"
).fetchone()["n"]
print(f"  до уборки: вызовов {before_calls}, сессий {before_sessions}")

# Показывается, что будет удалено.
calls = conn.execute(
    "SELECT session_id, op, tool, agent FROM usage_call WHERE agent = ?",
    (TEST_AGENT,),
).fetchall()
sessions = conn.execute(
    "SELECT session_id, tool, agent FROM usage_session WHERE agent = ?",
    (TEST_AGENT,),
).fetchall()
print(f"\n  подлежит удалению: вызовов {len(calls)}, сессий {len(sessions)}")
for c in calls:
    print(f"    вызов: op={c['op']!r} tool={c['tool']!r} agent={c['agent']!r}")
for s in sessions:
    print(f"    сессия: tool={s['tool']!r} agent={s['agent']!r} id={s['session_id']}")

if not calls and not sessions:
    print("\n  нечего удалять")
    conn.close()
    raise SystemExit(0)

conn.execute("DELETE FROM usage_call WHERE agent = ?", (TEST_AGENT,))
conn.execute("DELETE FROM usage_session WHERE agent = ?", (TEST_AGENT,))
conn.commit()

after_calls = conn.execute("SELECT COUNT(*) AS n FROM usage_call").fetchone()["n"]
after_sessions = conn.execute(
    "SELECT COUNT(*) AS n FROM usage_session"
).fetchone()["n"]
with_agent = conn.execute(
    "SELECT COUNT(*) AS n FROM usage_call WHERE agent IS NOT NULL"
).fetchone()["n"]

print()
print("=" * 78)
print("ПОСЛЕ УБОРКИ")
print("=" * 78)
print(f"  вызовов:  {after_calls} (было {before_calls})")
print(f"  сессий:   {after_sessions} (было {before_sessions})")
print(f"  вызовов с агентом: {with_agent} (ожидается 0)")

# Проверка: законные записи на месте.
print(f"\n  сессии по агентам:")
for r in conn.execute(
    "SELECT agent, COUNT(*) AS n FROM usage_session "
    "GROUP BY agent ORDER BY n DESC"
).fetchall():
    print(f"    {str(r['agent'])}: {r['n']}")

admin_sessions = conn.execute(
    "SELECT COUNT(*) AS n FROM usage_session WHERE agent = 'RAGRAF-Admin'"
).fetchone()["n"]
print(f"\n  сессии панели (RAGRAF-Admin): {admin_sessions} — сохранены, "
      f"это законная работа панели")

ok = (after_calls == before_calls - len(calls)
      and with_agent == 0
      and admin_sessions > 0)
conn.close()
print()
print(f"  РЕЗУЛЬТАТ: {'уборка выполнена верно' if ok else 'ТРЕБУЕТСЯ ВНИМАНИЕ'}")
raise SystemExit(0 if ok else 1)
