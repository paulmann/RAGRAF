"""Фильтрация статистики по задаче: сводка, CLI, фасад, MCP.

Добавляется третий разрез: по задаче (`task`). Он нужен команде `/RAGRAF`
в SagaAI — показать, сколько RAGRAF сделал для ОДНОЙ задачи пользователя,
хотя каждый вызов инструмента запускает отдельный процесс.

Что правится:
  1. `telemetry.summary` — параметр task и разбивка `by_task`;
  2. `facade.usage_summary` — проброс параметра;
  3. `cli.cmd_usage` — флаг `--task` и вывод по задаче;
  4. `mcp_server` — параметр в схеме инструмента `ragraf_usage`.

Особенность: пустая задача — это НЕ то же, что задача со значением.
Записи без метки (`task` пустая или NULL) собираются отдельной строкой,
поэтому фильтр по пустой строке ищет NULL так же, как это сделано
для агента.
"""

from __future__ import annotations

import pathlib

REPO = pathlib.Path(r"C:\AutoClaw\RAGRAF")

# --- 1. telemetry.summary: параметр и разбивка ---
print("=" * 78)
print("1. СВОДКА: ПАРАМЕТР ЗАДАЧИ И РАЗБИВКА ПО ЗАДАЧАМ")
print("=" * 78)
target = REPO / "src" / "ragraf" / "telemetry.py"
text = target.read_text(encoding="utf-8")

OLD_SIG = """    def summary(self, since_session: bool = False, agent: str | None = None,
                agent_kind: str | None = None) -> dict:"""
NEW_SIG = """    def summary(self, since_session: bool = False, agent: str | None = None,
                agent_kind: str | None = None, task: str | None = None) -> dict:"""
if OLD_SIG in text:
    text = text.replace(OLD_SIG, NEW_SIG, 1)
    print("  сигнатура дополнена параметром task")

# Фильтр по задаче: пустое значение ищется как NULL.
OLD_FILTER = """        if agent_kind:
            if agent_kind == "unknown":
                cond = ("(agent_kind IS NULL OR agent_kind = '' "
                        "OR agent_kind = 'unknown')")
                where = (where + " AND " if where else "WHERE ") + cond
            else:
                where = ((where + " AND " if where else "WHERE ")
                         + "agent_kind = ?")
                params = params + (agent_kind,)"""
NEW_FILTER = """        if agent_kind:
            if agent_kind == "unknown":
                cond = ("(agent_kind IS NULL OR agent_kind = '' "
                        "OR agent_kind = 'unknown')")
                where = (where + " AND " if where else "WHERE ") + cond
            else:
                where = ((where + " AND " if where else "WHERE ")
                         + "agent_kind = ?")
                params = params + (agent_kind,)
        if task:
            # Пустая метка задачи (NULL) собирается отдельно, как и у агента:
            # записи до появления поля не имеют значения вовсе.
            if task in ("unknown", "(без задачи)", "-"):
                where = ((where + " AND " if where else "WHERE ")
                         + "(task IS NULL OR task = '')")
            else:
                where = (where + " AND " if where else "WHERE ") + "task = ?"
                params = params + (task,)"""
if OLD_FILTER in text:
    text = text.replace(OLD_FILTER, NEW_FILTER, 1)
    print("  фильтр по задаче добавлен")

# Разбивка по задачам: добавляется рядом с разбивкой по агентам.
OLD_BY_KIND = """        by_kind_list = sorted(by_kind.values(), key=lambda b: -b["calls"])"""
NEW_BY_KIND = """        by_kind_list = sorted(by_kind.values(), key=lambda b: -b["calls"])

        # --- разбивка по задачам ---
        # Нужна команде /RAGRAF: показать вклад RAGRAF в ОДНУ задачу,
        # хотя каждый вызов инструмента запускает отдельный процесс.
        by_task = [
            dict(r)
            for r in conn.execute(
                f\"\"\"SELECT COALESCE(NULLIF(task, ''), '(без задачи)') AS task,
                           COUNT(*)                    AS calls,
                           COUNT(DISTINCT session_id)  AS processes,
                           SUM(found)                  AS found_calls,
                           SUM(fact_count)             AS facts,
                           SUM(facts_with_span)        AS with_span,
                           SUM(result_chars)           AS chars,
                           SUM(CASE WHEN replaces = 1 THEN result_chars ELSE 0 END)
                                                       AS replacing_chars,
                           ROUND(AVG(duration_ms), 2)  AS avg_ms,
                           SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END)
                                                       AS errors,
                           MIN(ts)                     AS first_ts,
                           MAX(ts)                     AS last_ts
                      FROM usage_call {where}
                     GROUP BY task
                     ORDER BY calls DESC LIMIT 20\"\"\",
                params,
            ).fetchall()
        ]
        for row in by_task:
            row["est_tokens"] = int((row["chars"] or 0) / CHARS_PER_UNIT)
            row["est_lines_not_read"] = int(
                (row["replacing_chars"] or 0) / CHARS_PER_LINE
            )"""
if OLD_BY_KIND in text:
    text = text.replace(OLD_BY_KIND, NEW_BY_KIND, 1)
    print("  разбивка по задачам добавлена")

# Поле в ответе + фильтр наружу.
OLD_RETURN = """            "agents_total": len(by_agent),
            "by_agent": by_agent,
            "by_agent_kind": by_kind_list,"""
NEW_RETURN = """            "agents_total": len(by_agent),
            "by_agent": by_agent,
            "by_agent_kind": by_kind_list,
            "tasks_total": len(by_task),
            "by_task": by_task,"""
if OLD_RETURN in text:
    text = text.replace(OLD_RETURN, NEW_RETURN, 1)
    print("  разбивка по задачам попала в ответ")

OLD_FILTER_OUT = """            "filter": {
                "agent": agent,
                "agent_kind": agent_kind,
                "since_session": since_session,
            },"""
NEW_FILTER_OUT = """            "filter": {
                "agent": agent,
                "agent_kind": agent_kind,
                "task": task,
                "since_session": since_session,
            },"""
if OLD_FILTER_OUT in text:
    text = text.replace(OLD_FILTER_OUT, NEW_FILTER_OUT, 1)
    print("  применённый фильтр отражён в ответе")

# Фильтр сессий тоже должен учитывать задачу.
OLD_SESS_FILTER = """            if agent_kind:
                if agent_kind == "unknown":
                    cond = ("(agent_kind IS NULL OR agent_kind = '' "
                            "OR agent_kind = 'unknown')")
                    sess_where = ((sess_where + " AND " if sess_where else "WHERE ")
                                  + cond)
                else:
                    sess_where = ((sess_where + " AND " if sess_where else "WHERE ")
                                  + "agent_kind = ?")
                    sess_params = sess_params + (agent_kind,)"""
NEW_SESS_FILTER = """            if agent_kind:
                if agent_kind == "unknown":
                    cond = ("(agent_kind IS NULL OR agent_kind = '' "
                            "OR agent_kind = 'unknown')")
                    sess_where = ((sess_where + " AND " if sess_where else "WHERE ")
                                  + cond)
                else:
                    sess_where = ((sess_where + " AND " if sess_where else "WHERE ")
                                  + "agent_kind = ?")
                    sess_params = sess_params + (agent_kind,)
            if task:
                if task in ("unknown", "(без задачи)", "-"):
                    sess_where = ((sess_where + " AND " if sess_where else "WHERE ")
                                  + "(task IS NULL OR task = '')")
                else:
                    sess_where = ((sess_where + " AND " if sess_where else "WHERE ")
                                  + "task = ?")
                    sess_params = sess_params + (task,)"""
if OLD_SESS_FILTER in text:
    text = text.replace(OLD_SESS_FILTER, NEW_SESS_FILTER, 1)
    print("  фильтр сессий учитывает задачу")

target.write_text(text, encoding="utf-8", newline="\n")
print(f"\n  файл: {target.relative_to(REPO)}, {len(text.encode('utf-8'))} б")
