"""Обновление функции RAGRAF в SagaAI: передача метки задачи и команда статистики.

Что меняется в `orchestrators/dev_agent/functions/ragraf.py`:

  1. Появляется операция `task_stats` — возвращает статистику по задаче
     в виде готового markdown. Это то, что покажет команда `/RAGRAF`.
  2. Появляется параметр `task` у вызова: если задан, передаётся в RAGRAF
     через переменную `RAGRAF_TASK`, и все вызовы одной задачи собираются
     вместе.
  3. Метка задачи может быть задана снаружи через `RAGRAF_TASK` — тогда
     каждый вызов инструмента автоматически относится к текущей задаче.

Почему нужна именно метка. Разведка показала: каждый вызов инструмента
в SagaAI запускает ОТДЕЛЬНЫЙ процесс RAGRAF, и «сессия RAGRAF» (один запуск)
не совпадает с задачей пользователя. Собрать статистику задачи по сессиям
нельзя в принципе — нужна общая метка.

Также заполняется словарь `OPERATIONS`, чтобы агент видел новую операцию.
"""

from __future__ import annotations

import pathlib
import shutil

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
TARGET = SAGA / "orchestrators" / "dev_agent" / "functions" / "ragraf.py"
text = TARGET.read_text(encoding="utf-8")
print(f"  файл: {TARGET}")
print(f"  размер до правки: {len(text.encode('utf-8'))} б")

backup = TARGET.with_suffix(".py.bak-task")
shutil.copy2(TARGET, backup)
print(f"  копия: {backup.name}")

changes: list[str] = []

# --- 1. Операции: добавляется task_stats ---
OLD_OPS = '''    "usage",         # статистика использования
    "freshness",     # свежесть индекса'''
NEW_OPS = '''    "usage",         # статистика использования (все агенты, все задачи)
    "task_stats",    # статистика по ОДНОЙ задаче — для команды /RAGRAF
    "freshness",     # свежесть индекса'''
if OLD_OPS in text:
    text = text.replace(OLD_OPS, NEW_OPS, 1)
    changes.append("добавлена операция task_stats")

# --- 2. Чтение метки задачи из окружения ---
OLD_VARS = '''# Имя агента для статистики RAGRAF. Видно в отчёте по агентам.
AGENT_NAME = os.environ.get("RAGRAF_AGENT", "SagaAI")
AGENT_KIND = os.environ.get("RAGRAF_AGENT_KIND", "external")'''
NEW_VARS = '''# Имя агента для статистики RAGRAF. Видно в отчёте по агентам.
AGENT_NAME = os.environ.get("RAGRAF_AGENT", "SagaAI")
AGENT_KIND = os.environ.get("RAGRAF_AGENT_KIND", "external")
# Метка задачи: собирает все вызовы ОДНОЙ задачи вместе.
#
# Зачем: каждый вызов этого инструмента запускает отдельный процесс RAGRAF,
# поэтому «сессия RAGRAF» (один запуск процесса) с задачей пользователя
# не совпадает. Метку задаёт тот, кто ставит задачу (команда /RAGRAF
# или сам агент), и тогда статистика собирается по задаче, а не по процессу.
TASK_LABEL = os.environ.get("RAGRAF_TASK", "").strip()'''
if OLD_VARS in text:
    text = text.replace(OLD_VARS, NEW_VARS, 1)
    changes.append("добавлено чтение метки задачи")

# --- 3. Передача метки в дочерний процесс ---
OLD_ENV = '''    env = dict(os.environ)
    env["RAGRAF_AGENT"] = AGENT_NAME
    env["RAGRAF_AGENT_KIND"] = AGENT_KIND
    env["PYTHONIOENCODING"] = "utf-8"'''
NEW_ENV = '''    env = dict(os.environ)
    env["RAGRAF_AGENT"] = AGENT_NAME
    env["RAGRAF_AGENT_KIND"] = AGENT_KIND
    env["PYTHONIOENCODING"] = "utf-8"
    # Метка задачи: своя из аргумента, иначе унаследованная из окружения.
    # Так вызовы одной задачи попадают в одну строку статистики.
    task_value = str(kwargs.get("task") or TASK_LABEL or "").strip()
    if task_value:
        env["RAGRAF_TASK"] = task_value'''
if OLD_ENV in text:
    text = text.replace(OLD_ENV, NEW_ENV, 1)
    changes.append("метка задачи передаётся в RAGRAF")

# --- 4. Обработка операции task_stats ---
OLD_INVOKE = '''    operation = str(kwargs.get("operation") or "search").strip()
    if operation not in OPERATIONS:'''
NEW_INVOKE = '''    operation = str(kwargs.get("operation") or "search").strip()
    # task_stats собирается из обычной сводки, но отдаётся готовым текстом:
    # команда /RAGRAF показывает его пользователю без обработки.
    want_markdown = operation == "task_stats"
    if want_markdown:
        operation = "usage"
    if operation not in OPERATIONS:'''
if OLD_INVOKE in text:
    text = text.replace(OLD_INVOKE, NEW_INVOKE, 1)
    changes.append("операция task_stats обрабатывается")

# --- 5. Формирование markdown-отчёта ---
OLD_RETURN = '''    return {
        "ok": True,
        "operation": operation,
        "summary": ", ".join(summary_parts),
        "data": data,
    }'''
NEW_RETURN = '''    if want_markdown:
        return {
            "ok": True,
            "operation": "task_stats",
            "summary": _task_report(data, task_value if "task_value" in dir()
                                    else ""),
            "data": data,
        }

    return {
        "ok": True,
        "operation": operation,
        "summary": ", ".join(summary_parts),
        "data": data,
    }


def _task_report(data: dict, task: str) -> str:
    """Собрать читаемый отчёт о работе RAGRAF по задаче.

    Возвращается markdown: его можно показать в интерфейсе как есть.
    Числа берутся из сводки, ничего не додумывается: строки, которых
    нет, показываются нулём.
    """
    total = data.get("calls_total") or 0
    if not total:
        return ("RAGRAF по этой задаче ещё не вызывался. "
                "Индекс готов к работе — задайте вопрос по коду проекта.")

    lines: list[str] = []
    lines.append(f"### RAGRAF: работа по задаче「{task or 'все задачи'}」")
    lines.append("")
    lines.append(f"- обращений к индексу: **{total}**")
    lines.append(f"- ошибок: **{data.get('errors') or 0}**")
    facts = data.get("facts_total") or 0
    with_span = data.get("facts_with_span") or 0
    share = (with_span / facts * 100) if facts else 0
    lines.append(f"- фактов получено: **{facts}**, со ссылкой файл:строка — "
                 f"**{with_span}** ({share:.0f}%)")
    chars = data.get("result_chars_total") or 0
    lines.append(f"- объём ответов: **{chars}** символов "
                 f"(~{data.get('est_tokens_total') or 0} токенов, оценка)")
    replacing = data.get("result_chars_replacing_reading") or 0
    if replacing:
        lines.append(f"- заменили чтение файлов: **{replacing}** символов "
                     f"(~{data.get('est_lines_not_read') or 0} строк кода)")
    lines.append("")

    by_op = data.get("by_op") or []
    if by_op:
        lines.append("| операция | вызовов | фактов | среднее |")
        lines.append("|---|---|---|---|")
        for row in by_op[:8]:
            lines.append(
                f"| {row.get('op')} | {row.get('calls')} | "
                f"{row.get('facts') or 0} | "
                f"{(row.get('avg_ms') or 0):.0f} мс |"
            )
        lines.append("")

    by_task = data.get("by_task") or []
    if len(by_task) > 1:
        lines.append("Задачи в журнале: "
                     + ", ".join(f"`{r.get('task')}` ({r.get('calls')})"
                                 for r in by_task[:5]))
        lines.append("")

    return "\\n".join(lines)'''
if OLD_RETURN in text:
    text = text.replace(OLD_RETURN, NEW_RETURN, 1)
    changes.append("добавлен сборщик markdown-отчёта")

TARGET.write_text(text, encoding="utf-8", newline="\n")
after = TARGET.read_text(encoding="utf-8")
print(f"  размер после правки: {len(after.encode('utf-8'))} б")
print()
print("  изменения:")
for c in changes:
    print(f"    - {c}")

print()
print("  проверки:")
checks = [
    ("task_stats" in after, "операция task_stats объявлена"),
    ("TASK_LABEL" in after, "метка задачи читается"),
    ("RAGRAF_TASK" in after, "метка передаётся в RAGRAF"),
    ("_task_report" in after, "сборщик отчёта добавлен"),
    ("def invoke" in after, "главная функция на месте"),
]
for ok, label in checks:
    print(f"    [{'OK ' if ok else '--'}] {label}")
