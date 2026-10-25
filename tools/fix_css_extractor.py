"""Правка extract/css_html.py: убрать аргумент path из вызовов SymbolRec.

Причина. `SymbolRec` не содержит поля `path` — путь берётся из `FileExtraction`.
Первая версия модуля передавала path в конструктор, из-за чего разбор CSS падал
с TypeError, а индексатор молча считал файл неразобранным (статус skipped).

Правка выполняется с сопоставлением скобок: удаляются строки вида
`path=...,` только внутри вызовов SymbolRec(...), и ничего больше.
`ChunkRec` тоже принимает path — его вызовы не трогаются.
"""

from __future__ import annotations

import pathlib
import re

path = pathlib.Path(__file__).resolve().parents[1] / "src" / "ragraf" / "extract" / "css_html.py"
text = path.read_text(encoding="utf-8")


def strip_path_in_call(src: str, call: str) -> tuple[str, int]:
    """Удалить аргумент path=... внутри вызовов call(...).

    Идёт поиск открывающей скобки вызова и её парной закрывающей; внутри
    диапазона удаляются строки, начинающиеся с path=. Вложенные вызовы
    учитываются счётчиком скобок, поэтому вложенный ChunkRec не пострадает,
    если он окажется внутри.
    """
    out = []
    i = 0
    removed = 0
    marker = call + "("
    while True:
        start = src.find(marker, i)
        if start < 0:
            out.append(src[i:])
            break
        out.append(src[i:start])
        # Ищем парную закрывающую скобку.
        depth = 0
        pos = start + len(call)
        while pos < len(src):
            ch = src[pos]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            pos += 1
        block = src[start: pos + 1]
        # Удаляются строки, где аргумент path задаётся литералом или переменной.
        cleaned_lines = []
        for line in block.split("\n"):
            if re.match(r"\s*path\s*=", line):
                removed += 1
                continue
            cleaned_lines.append(line)
        out.append("\n".join(cleaned_lines))
        i = pos + 1
    return "".join(out), removed


text, n1 = strip_path_in_call(text, "SymbolRec")
print(f"SymbolRec: удалено аргументов path: {n1}")

path.write_text(text, encoding="utf-8", newline="\n")

# Проверка: синтаксис и отсутствие path в вызовах SymbolRec.
import ast  # noqa: E402

ast.parse(path.read_text(encoding="utf-8"))
print("синтаксис OK")

body = path.read_text(encoding="utf-8")
left = len(re.findall(r"SymbolRec\(\s*\n(?:\s*\w+=.*\n)*?\s*path=", body))
print(f"осталось вызовов SymbolRec с path=: {left}")
