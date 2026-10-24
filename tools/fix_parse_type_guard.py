"""Проверка типа аргумента в _parse_slash_command.

НАЙДЕННЫЙ ДЕФЕКТ (мой). Функция ожидала строку, а получает значение прямо
из `st.chat_input()`. В интерфейсе Streamlit это обычно строка или None,
но в тестах (и в моках) туда приходит объект-заглушка `_NullCtx`:

    TypeError: '_NullCtx' object is not subscriptable

Из-за этого падали два теста интерфейса, которые до моей правки проходили.

ПРАВКА. Тип проверяется явно (`isinstance(text, str)`), а не предполагается.
Это не «обход теста»: любой объект, не являющийся строкой, командой быть
не может, поэтому возврат None — правильное поведение, а не заглушка.
"""

from __future__ import annotations

import ast
import pathlib

TARGET = pathlib.Path(
    r"C:\!.Tihon\sagaai_edit_github\ui\pages\orchestrator.py"
)
text = TARGET.read_text(encoding="utf-8")

OLD = '''def _parse_slash_command(text: str) -> tuple[str, list[str]] | None:
    """Разобрать команду из строки ввода.

    Возвращает `(имя, аргументы)` либо None, если это не команда.
    Имя приводится к нижнему регистру: `/RAGRAF` и `/ragraf` равнозначны.
    """
    if not text:
        return None
    stripped = text.strip()'''

NEW = '''def _parse_slash_command(text) -> tuple[str, list[str]] | None:
    """Разобрать команду из строки ввода.

    Возвращает `(имя, аргументы)` либо None, если это не команда.
    Имя приводится к нижнему регистру: `/RAGRAF` и `/ragraf` равнозначны.

    ВАЖНО про тип аргумента. Значение берётся прямо из `st.chat_input()`
    и может быть НЕ строкой: в тестах и в моках приходит объект-заглушка
    (`_NullCtx`), который не поддерживает ни срез, ни `in`. Поэтому тип
    проверяется явно, а не предполагается: иначе страница падала бы
    с `TypeError` вместо обычной работы.

    Любой объект, не являющийся строкой, командой быть не может —
    возврат None здесь верное поведение, а не заглушка.
    """
    if not isinstance(text, str) or not text:
        return None
    stripped = text.strip()'''

if OLD not in text:
    if "isinstance(text, str)" in text:
        print("  правка уже внесена")
        raise SystemExit(0)
    raise SystemExit("НЕ НАЙДЕНА функция разбора")

text = text.replace(OLD, NEW, 1)
TARGET.write_text(text, encoding="utf-8", newline="\n")
after = TARGET.read_text(encoding="utf-8")
print("  проверка типа добавлена")

try:
    ast.parse(after)
    print("  синтаксис верен")
except SyntaxError as exc:
    print(f"  ПРОВАЛ синтаксиса: строка {exc.lineno}: {exc.msg}")
    raise SystemExit(1)

checks = [
    ("isinstance(text, str)" in after, "проверка типа есть"),
    ("def _parse_slash_command(text)" in after, "аннотация типа снята"),
    (after.count("def _parse_slash_command") == 1, "функция одна"),
]
for ok, label in checks:
    print(f"  [{'OK ' if ok else '--'}] {label}")
