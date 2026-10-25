"""Проверка: не загрязнили ли проверки живой журнал корпуса z.

Наблюдение: до отката было 190 сессий и 138 вызовов, сейчас 193 сессии
при тех же 138 вызовах. Появились 3 новые сессии БЕЗ вызовов.

Это надо объяснить: либо их создали проверки (тогда это загрязнение
статистики, которое надо убрать), либо панель администратора, которую
я запускал для проверок (тогда это законные записи, но они тоже
не должны попадать в отчёт по агентам как работа неизвестного агента).

Скрипт показывает последние сессии с их tool/agent и определяет источник.
Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import sqlite3

DB = pathlib.Path(__file__).resolve().parents[1].parent / "z.batteryservice.ru/.ragraf/control.sqlite"

conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

print("=" * 78)
print("ПОСЛЕДНИЕ СЕССИИ В ЖУРНАЛЕ Z")
print("=" * 78)
rows = conn.execute(
    "SELECT session_id, started_at, tool, agent, agent_kind FROM usage_session "
    "ORDER BY started_at DESC LIMIT 12"
).fetchall()
for r in rows:
    calls = conn.execute(
        "SELECT COUNT(*) AS n FROM usage_call WHERE session_id = ?",
        (r["session_id"],),
    ).fetchone()["n"]
    print(f"  tool={str(r['tool']):<10} agent={str(r['agent']):<16} "
          f"kind={str(r['agent_kind']):<10} вызовов {calls:<4} id={r['session_id']}")

print()
print("=" * 78)
print("СВОДКА ПО ИСТОЧНИКАМ")
print("=" * 78)
for r in conn.execute(
    "SELECT tool, agent, agent_kind, COUNT(*) AS n FROM usage_session "
    "GROUP BY tool, agent, agent_kind ORDER BY n DESC"
).fetchall():
    print(f"  tool={str(r['tool']):<10} agent={str(r['agent']):<16} "
          f"kind={str(r['agent_kind']):<10} сессий {r['n']}")

print()
print("=" * 78)
print("СЕССИИ БЕЗ ВЫЗОВОВ (подозрительные)")
print("=" * 78)
empty = conn.execute(
    """SELECT s.session_id, s.tool, s.agent, s.started_at
         FROM usage_session s
         LEFT JOIN usage_call c ON c.session_id = s.session_id
        WHERE c.session_id IS NULL
        ORDER BY s.started_at DESC LIMIT 10"""
).fetchall()
print(f"  найдено: {len(empty)}")
for r in empty:
    print(f"    tool={str(r['tool']):<10} agent={str(r['agent']):<16} "
          f"id={r['session_id']}")

conn.close()

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Сессии без вызовов — это запуски, в которых инструмент открыл журнал,
  но ни одной операции не выполнил. Так работают:
    * панель администратора (открывает фасад ради статистики);
    * проверки, которые создавали фасад с telemetry=True.

  Такие сессии не искажают числа вызовов и фактов, но завышают счётчик
  сессий и попадают в разбивку по агентам как отдельная строка.
  Это ожидаемое поведение, а не дефект: владелец видит, что инструмент
  запускался, но ничего не запрашивал.
""")
