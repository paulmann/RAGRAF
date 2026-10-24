"""Диагностика: почему в bash команды стали «функциями».

Симптом: для файла run_tests.sh найдено 8 «символов», первый из которых — `cd`,
а для abs.sh — `echo`. В shell-скриптах 212 файлов, и если каждая команда
становится узлом-функцией, индекс забьётся мусором.

Проверяется: какой тип узла грамматики bash соответствует `function_definition`
и что попадает в набор functions профиля.
"""

from __future__ import annotations

import pathlib

from tree_sitter_language_pack import get_parser  # type: ignore

REPO = pathlib.Path(r"C:\AutoClaw\RAGRAF")
sys_path = REPO / "src"

import sys  # noqa: E402

sys.path.insert(0, str(sys_path))
from ragraf.extract.generic import PROFILES  # noqa: E402

SAMPLE = REPO / ".staging" / "bash_probe.sh"
SAMPLE.write_text(
    "#!/bin/bash\n"
    "\n"
    "my_function() {\n"
    "    echo \"inside\"\n"
    "}\n"
    "\n"
    "function other_func {\n"
    "    ls -la\n"
    "}\n"
    "\n"
    "cd /tmp\n"
    "echo \"hello\"\n"
    "my_function\n",
    encoding="utf-8", newline="\n",
)

print("=" * 78)
print("РАЗБОР ПРОБНОГО SHELL-СКРИПТА: какие типы узлов встречаются")
print("=" * 78)

parser = get_parser("bash")
tree = parser.parse(SAMPLE.read_bytes())

print(f"\n  {'тип узла':<28} {'строка':>7}  текст")
print("  " + "-" * 62)


def walk(node, depth: int = 0) -> None:
    text = SAMPLE.read_bytes()[node.start_byte : node.end_byte].decode(
        "utf-8", errors="replace"
    )
    if "\n" in text:
        text = text.split("\n", 1)[0]
    print(f"  {'  ' * depth}{node.type:<26} {node.start_point[0] + 1:>7}  "
          f"{text[:40]}")
    for child in node.children:
        walk(child, depth + 1)


walk(tree.root_node)

print()
print("=" * 78)
print("ЧТО В ПРОФИЛЕ BASH")
print("=" * 78)
profile = PROFILES["bash"]
print(f"  functions:  {profile.functions}")
print(f"  methods:    {profile.methods}")
print(f"  classes:    {profile.classes}")
print(f"  calls:      {profile.calls}")
print()
print("  Проверка: какой узел отвечает за объявление функции в bash")
for node_type in ("function_definition", "command", "declaration_command",
                  "function_declaration"):
    found = []
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type == node_type:
            found.append(n)
        stack.extend(n.children)
    texts = []
    for n in found[:4]:
        t = SAMPLE.read_bytes()[n.start_byte : n.end_byte].decode("utf-8", "replace")
        texts.append(t.split("\n")[0][:40])
    print(f"    {node_type:<24} найдено {len(found)}: {texts}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Если «функциями» становятся команды (cd, echo, ls), значит профиль bash
  включает в functions тип, который на деле означает команду, а не объявление.
  Тогда тип надо убрать из functions и оставить только настоящие
  function_definition.
""")
