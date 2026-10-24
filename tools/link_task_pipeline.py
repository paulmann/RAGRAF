"""Связка задачи: окружение → CLI → фасад → MCP → вывод.

Задача передаётся тем же способом, что имя агента: переменной окружения
`RAGRAF_TASK`. Причина та же — она работает для ЛЮБОГО способа вызова
(CLI, скрипт, кастомная функция) и не требует менять синтаксис команды.

Что правится:
  1. `cli._agent_from_env` → читает ещё и задачу (функция переименована);
  2. `cli.cmd_usage` → флаг `--task` и раздел «по задачам» в выводе;
  3. `facade.usage_summary` → проброс параметра task;
  4. `mcp_server` → параметр task в схеме `ragraf_usage`.

Вывод команды делается так, чтобы его было удобно показать в интерфейсе:
сначала общие числа, затем вклад по агентам и по задачам.
"""

from __future__ import annotations

import pathlib

REPO = pathlib.Path(r"C:\AutoClaw\RAGRAF")

# --- 1. CLI: чтение задачи из окружения ---
print("=" * 78)
print("1. CLI: ЗАДАЧА ИЗ ОКРУЖЕНИЯ")
print("=" * 78)
target = REPO / "src" / "ragraf" / "cli.py"
text = target.read_text(encoding="utf-8")

OLD_FUNC = '''def _agent_from_env() -> tuple[str, str]:
    """Кто вызвал RAGRAF: имя агента и его семейство из окружения.'''
NEW_FUNC = '''def _task_from_env() -> str:
    """Метка задачи, к которой относится вызов.

    Читается из переменной окружения `RAGRAF_TASK`. Нужна команде `/RAGRAF`
    в SagaAI: там каждый вызов инструмента запускает ОТДЕЛЬНЫЙ процесс,
    поэтому «сессия RAGRAF» (один запуск) с задачей пользователя
    не совпадает. Одна метка на всю задачу собирает все вызовы вместе.

    Пустая строка означает «метка не задана» — это нормальный случай
    для обычной работы из командной строки.
    """
    import os as _os

    return (_os.environ.get("RAGRAF_TASK") or "").strip()


def _agent_from_env() -> tuple[str, str]:
    """Кто вызвал RAGRAF: имя агента и его семейство из окружения.'''
if OLD_FUNC in text:
    text = text.replace(OLD_FUNC, NEW_FUNC, 1)
    print("  добавлено чтение задачи из окружения")

OLD_FACADE = '''    fc = Facade(cfg, tool="cli", agent=_agent_from_env()[0],
                agent_kind=_agent_from_env()[1])'''
NEW_FACADE = '''    fc = Facade(cfg, tool="cli", agent=_agent_from_env()[0],
                agent_kind=_agent_from_env()[1], task=_task_from_env())'''
if OLD_FACADE in text:
    text = text.replace(OLD_FACADE, NEW_FACADE, 1)
    print("  фасад получает задачу")

# --- 2. CLI: фильтр и вывод ---
OLD_SUMMARY = """        summary = facade.usage_summary(
            agent=getattr(args, "agent", None),
            agent_kind=getattr(args, "agent_kind", None),
        )"""
NEW_SUMMARY = """        summary = facade.usage_summary(
            agent=getattr(args, "agent", None),
            agent_kind=getattr(args, "agent_kind", None),
            task=getattr(args, "task", None),
        )"""
if OLD_SUMMARY in text:
    text = text.replace(OLD_SUMMARY, NEW_SUMMARY, 1)
    print("  сводка вызывается с фильтром по задаче")

OLD_FILTER_PRINT = """        flt = summary.get("filter") or {}
        if flt.get("agent") or flt.get("agent_kind"):
            parts = []
            if flt.get("agent"):
                parts.append(f"агент={flt['agent']}")
            if flt.get("agent_kind"):
                parts.append(f"семейство={flt['agent_kind']}")
            print(f"фильтр: {', '.join(parts)}")
        else:
            print("фильтр: нет (все агенты)")"""
NEW_FILTER_PRINT = """        flt = summary.get("filter") or {}
        if flt.get("agent") or flt.get("agent_kind") or flt.get("task"):
            parts = []
            if flt.get("agent"):
                parts.append(f"агент={flt['agent']}")
            if flt.get("agent_kind"):
                parts.append(f"семейство={flt['agent_kind']}")
            if flt.get("task"):
                parts.append(f"задача={flt['task']}")
            print(f"фильтр: {', '.join(parts)}")
        else:
            print("фильтр: нет (все агенты, все задачи)")"""
if OLD_FILTER_PRINT in text:
    text = text.replace(OLD_FILTER_PRINT, NEW_FILTER_PRINT, 1)
    print("  применённый фильтр печатается с задачей")

# Раздел «по задачам» после раздела «по семействам».
OLD_KIND_PRINT = """        by_kind = summary.get("by_agent_kind") or []
        if by_kind:
            print()
            print("по семействам агентов:")
            for row in by_kind:
                print(f"  {row['agent_kind']:<11} агентов {row['agents']}, "
                      f"сессий {row['sessions']}, вызовов {row['calls']}, "
                      f"~{row['est_lines_not_read']} строк не читалось")"""
NEW_KIND_PRINT = """        by_kind = summary.get("by_agent_kind") or []
        if by_kind:
            print()
            print("по семействам агентов:")
            for row in by_kind:
                print(f"  {row['agent_kind']:<11} агентов {row['agents']}, "
                      f"сессий {row['sessions']}, вызовов {row['calls']}, "
                      f"~{row['est_lines_not_read']} строк не читалось")

        # --- разбивка по задачам ---
        # Это ответ на вопрос «сколько RAGRAF сделал для этой задачи»:
        # в SagaAI один вызов инструмента = отдельный процесс, поэтому
        # по сессиям задачу не собрать — нужна общая метка.
        by_task = summary.get("by_task") or []
        if by_task:
            print()
            print("по задачам:")
            print(f"  {'задача':<28} {'вызовов':>8} {'процессов':>10} "
                  f"{'фактов':>7} {'символов':>9} {'строк':>7}")
            for row in by_task:
                name = str(row["task"])[:28]
                print(f"  {name:<28} {row['calls']:>8} "
                      f"{row['processes']:>10} {row['facts']:>7} "
                      f"{row['chars']:>9} "
                      f"{row['est_lines_not_read']:>7}")"""
if OLD_KIND_PRINT in text:
    text = text.replace(OLD_KIND_PRINT, NEW_KIND_PRINT, 1)
    print("  добавлен раздел «по задачам» в вывод")

# Флаг --task в парсере.
OLD_FLAG = """    pu.add_argument("--agent-kind", default=None,
                    help="только это семейство: autoclaw, external, internal")
    pu.set_defaults(func=cmd_usage)"""
NEW_FLAG = """    pu.add_argument("--agent-kind", default=None,
                    help="только это семейство: autoclaw, external, internal")
    # Метка задачи: собирает статистику по ОДНОЙ задаче, хотя каждый вызов
    # инструмента запускает отдельный процесс. Значение по умолчанию пустое —
    # тогда показываются все задачи.
    pu.add_argument("--task", default=None,
                    help="только эта задача (метка RAGRAF_TASK)")
    pu.set_defaults(func=cmd_usage)"""
if OLD_FLAG in text:
    text = text.replace(OLD_FLAG, NEW_FLAG, 1)
    print("  добавлен флаг --task")

target.write_text(text, encoding="utf-8", newline="\n")
print(f"\n  файл: {target.relative_to(REPO)}, {len(text.encode('utf-8'))} б")

# --- 3. Фасад: проброс параметра ---
print()
print("=" * 78)
print("2. ФАСАД: ПРОБРОС ЗАДАЧИ")
print("=" * 78)
facade = REPO / "src" / "ragraf" / "facade.py"
ftext = facade.read_text(encoding="utf-8")

OLD_FACADE_SIG = """    def __init__(self, cfg: ProjectConfig, tool: str = "unknown",
                 telemetry: bool = True, watch: bool = True,
                 agent: str = "unknown", agent_kind: str = "unknown") -> None:"""
NEW_FACADE_SIG = """    def __init__(self, cfg: ProjectConfig, tool: str = "unknown",
                 telemetry: bool = True, watch: bool = True,
                 agent: str = "unknown", agent_kind: str = "unknown",
                 task: str = "") -> None:"""
if OLD_FACADE_SIG in ftext:
    ftext = ftext.replace(OLD_FACADE_SIG, NEW_FACADE_SIG, 1)
    print("  сигнатура фасада дополнена задачей")

OLD_FACADE_ASSIGN = """        self.agent = agent
        self.agent_kind = agent_kind"""
NEW_FACADE_ASSIGN = """        self.agent = agent
        self.agent_kind = agent_kind
        # Метка задачи: нужна, чтобы собрать вклад RAGRAF в одну задачу
        # пользователя (см. команду `/RAGRAF` в SagaAI).
        self.task = task"""
if OLD_FACADE_ASSIGN in ftext:
    ftext = ftext.replace(OLD_FACADE_ASSIGN, NEW_FACADE_ASSIGN, 1)
    print("  задача сохраняется в фасаде")

OLD_TEL = """            Telemetry(self.control, tool=tool, agent=agent,
                      agent_kind=agent_kind)"""
NEW_TEL = """            Telemetry(self.control, tool=tool, agent=agent,
                      agent_kind=agent_kind, task=task)"""
if OLD_TEL in ftext:
    ftext = ftext.replace(OLD_TEL, NEW_TEL, 1)
    print("  телеметрия получает задачу")

OLD_USAGE_SIG = """    def usage_summary(self, since_session: bool = False,
                      agent: str | None = None,
                      agent_kind: str | None = None) -> dict:"""
NEW_USAGE_SIG = """    def usage_summary(self, since_session: bool = False,
                      agent: str | None = None,
                      agent_kind: str | None = None,
                      task: str | None = None) -> dict:"""
if OLD_USAGE_SIG in ftext:
    ftext = ftext.replace(OLD_USAGE_SIG, NEW_USAGE_SIG, 1)
    print("  сигнатура сводки дополнена задачей")

OLD_USAGE_CALL = """            return self.telemetry.summary(
                since_session=since_session, agent=agent,
                agent_kind=agent_kind,
            )"""
NEW_USAGE_CALL = """            return self.telemetry.summary(
                since_session=since_session, agent=agent,
                agent_kind=agent_kind, task=task,
            )"""
if OLD_USAGE_CALL in ftext:
    ftext = ftext.replace(OLD_USAGE_CALL, NEW_USAGE_CALL, 1)
    print("  сводка получает задачу (телеметрия включена)")

OLD_READER_CALL = """        return reader.summary(
            since_session=since_session, agent=agent,
            agent_kind=agent_kind,
        )"""
NEW_READER_CALL = """        return reader.summary(
            since_session=since_session, agent=agent,
            agent_kind=agent_kind, task=task,
        )"""
if OLD_READER_CALL in ftext:
    ftext = ftext.replace(OLD_READER_CALL, NEW_READER_CALL, 1)
    print("  сводка получает задачу (телеметрия выключена)")

facade.write_text(ftext, encoding="utf-8", newline="\n")
print(f"\n  файл: {facade.relative_to(REPO)}, {len(ftext.encode('utf-8'))} б")
