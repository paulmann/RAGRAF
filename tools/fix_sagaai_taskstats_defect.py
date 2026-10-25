"""Исправление дефекта в функции RAGRAF для SagaAI.

НАЙДЕННЫЙ ДЕФЕКТ (мой, внесён предыдущей правкой):
    task_value = ...   # строка 201, ВНУТРИ try
    ...
    _task_report(data, task_value if "task_value" in dir() else "")

`task_value` определяется внутри блока `try` ПОСЛЕ проверки аргументов.
Если вызов завершается раньше (неизвестная операция, ошибка пути),
переменной ещё нет, и `dir()` внутри функции её не покажет — будет
подставлена пустая строка. Но конструкция `"task_value" in dir()` вообще
проверяет не то: `dir()` без аргументов возвращает имена В ТЕКУЩЕЙ области
видимости, и в момент формирования ответа переменная там может быть,
а может и не быть — поведение зависит от порядка выполнения, то есть
непредсказуемо.

ПРАВКА: метка вычисляется заново прямо в месте использования. Это
устраняет зависимость от порядка и убирает проверку через `dir()`.
"""

from __future__ import annotations

import pathlib

TARGET = pathlib.Path(
    r"C:\!.Tihon\sagaai_edit_github\orchestrators\dev_agent\functions\ragraf.py"
)
text = TARGET.read_text(encoding="utf-8")

OLD = '''    if want_markdown:
        return {
            "ok": True,
            "operation": "task_stats",
            "summary": _task_report(data, task_value if "task_value" in dir()
                                    else ""),
            "data": data,
        }'''

NEW = '''    if want_markdown:
        # Метка задачи вычисляется ЗДЕСЬ заново, а не берётся из переменной
        # внутри try: там она определяется только при успешном разборе
        # аргументов, и при раннем выходе её могло не быть (NameError).
        # Проверка через `dir()` не годится: она зависит от порядка
        # выполнения и даёт то значение, то нет.
        label = str(kwargs.get("task") or TASK_LABEL or "").strip()
        return {
            "ok": True,
            "operation": "task_stats",
            "summary": _task_report(data, label),
            "data": data,
        }'''

if OLD not in text:
    if "label = str(kwargs.get(" in text:
        print("  дефект уже исправлен")
    else:
        raise SystemExit("НЕ НАЙДЕН фрагмент для исправления")
else:
    text = text.replace(OLD, NEW, 1)
    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print("  дефект исправлен: метка вычисляется в месте использования")

after = TARGET.read_text(encoding="utf-8")
print()
print("  проверки:")
checks = [
    ('"task_value" in dir()' not in after, "проверка через dir() убрана"),
    ("label = str(kwargs.get(" in after, "метка вычисляется заново"),
    ("_task_report(data, label)" in after, "отчёт получает метку"),
]
for ok, label in checks:
    print(f"    [{'OK ' if ok else '--'}] {label}")

# Синтаксическая проверка.
import ast  # noqa: E402

try:
    ast.parse(after)
    print("    [OK ] синтаксис верен")
except SyntaxError as exc:
    print(f"    [ПРОВАЛ] синтаксис: строка {exc.lineno}: {exc.msg}")
    raise SystemExit(1)
