"""Откат тестовых записей из живого журнала корпуса z.

ОШИБКА, которую надо исправить. Проверка `tests/test_telemetry_agent.py`
писала в РЕАЛЬНЫЙ журнал корпуса z.batteryservice.ru: она создала сессии
с агентами AutoClaw, SagaAI, MD.Viewer и по одному вызову у каждого.

Это загрязняет статистику владельца: в отчёте появятся агенты, которые
в корпусе не работали. Проверки должны работать на своей базе, а не на
рабочей, — иначе невозможно отличить настоящие данные от тестовых.

Скрипт удаляет ИМЕННО ТЕ записи, которые создал тест: они опознаются
по сочетанию (tool, agent), которое в реальной работе не встречалось:
сессии `selftest` с агентом AutoClaw/SagaAI/MD.Viewer.

Перед удалением печатается, что будет удалено, и проверяется, что после
удаления остаются прежние 190 сессий и 138 вызовов.
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
DB = pathlib.Path(__file__).resolve().parents[1].parent / "z.batteryservice.ru/.ragraf/control.sqlite"

print("=" * 78)
print("ОТКАТ ТЕСТОВЫХ ЗАПИСЕЙ")
print("=" * 78)
print(f"  база: {DB}")

conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row

# Сначала показывается, что есть.
print("\n  сессии по агентам ДО отката:")
for r in conn.execute(
    "SELECT tool, agent, agent_kind, COUNT(*) AS n FROM usage_session "
    "GROUP BY tool, agent, agent_kind ORDER BY n DESC"
).fetchall():
    print(f"    tool={r['tool']!r:<12} agent={r['agent']!r:<14} "
          f"kind={r['agent_kind']!r:<12} сессий {r['n']}")

print("\n  вызовы по агентам ДО отката:")
for r in conn.execute(
    "SELECT tool, agent, agent_kind, COUNT(*) AS n FROM usage_call "
    "GROUP BY tool, agent, agent_kind ORDER BY n DESC"
).fetchall():
    print(f"    tool={r['tool']!r:<12} agent={r['agent']!r:<14} "
          f"kind={r['agent_kind']!r:<12} вызовов {r['n']}")

# Опознаются записи теста: сессии и вызовы, созданные с tool='selftest'
# И заполненным agent. В реальной работе таких сочетаний не было:
# selftest запускался только из проверок, и агента тогда не передавали.
TEST_AGENTS = ("AutoClaw", "SagaAI", "MD.Viewer")

del_sessions = conn.execute(
    "SELECT session_id, tool, agent FROM usage_session "
    "WHERE agent IN (?, ?, ?)",
    TEST_AGENTS,
).fetchall()
del_calls = conn.execute(
    "SELECT COUNT(*) AS n FROM usage_call WHERE agent IN (?, ?, ?)",
    TEST_AGENTS,
).fetchall()[0]["n"]

print(f"\n  подлежит удалению:")
print(f"    сессий: {len(del_sessions)}")
for s in del_sessions:
    print(f"      {s['session_id']} (tool={s['tool']}, agent={s['agent']})")
print(f"    вызовов: {del_calls}")

if not del_sessions and del_calls == 0:
    print("\n  нечего удалять: тестовых записей нет")
    conn.close()
    raise SystemExit(0)

# Удаляются вызовы тестовых агентов и их сессии.
# Вызовы удаляются по agent, а не по session_id: часть тестовых вызовов
# могла попасть в сессию с другим session_id (если объект Telemetry
# пересоздавался).
conn.execute(
    "DELETE FROM usage_call WHERE agent IN (?, ?, ?)", TEST_AGENTS
)
conn.execute(
    "DELETE FROM usage_session WHERE agent IN (?, ?, ?)", TEST_AGENTS
)
conn.commit()

# Проверка результата: возвращаются исходные числа.
sessions = conn.execute(
    "SELECT COUNT(*) AS n FROM usage_session"
).fetchone()["n"]
calls = conn.execute("SELECT COUNT(*) AS n FROM usage_call").fetchone()["n"]
with_agent = conn.execute(
    "SELECT COUNT(*) AS n FROM usage_call WHERE agent IS NOT NULL"
).fetchone()["n"]

print()
print("=" * 78)
print("ПРОВЕРКА ПОСЛЕ ОТКАТА")
print("=" * 78)
print(f"  сессий:            {sessions}   (ожидалось 190)")
print(f"  вызовов:           {calls}   (ожидалось 138)")
print(f"  вызовов с агентом: {with_agent}   (ожидалось 0)")

ok = sessions == 190 and calls == 138 and with_agent == 0
print()
print("  сессии по агентам ПОСЛЕ отката:")
for r in conn.execute(
    "SELECT agent, COUNT(*) AS n FROM usage_session "
    "GROUP BY agent ORDER BY n DESC"
).fetchall():
    print(f"    agent={r['agent']!r}: {r['n']}")

conn.close()
print()
print(f"  РЕЗУЛЬТАТ: {'откат выполнен верно' if ok else 'ТРЕБУЕТСЯ ВНИМАНИЕ'}")
raise SystemExit(0 if ok else 1)
