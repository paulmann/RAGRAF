#!/usr/bin/env python3
"""Сколько на самом деле: операции фасада, инструменты MCP, подкоманды, языки.

Зачем отдельный инструмент. Документы называют числа поверхности продукта
(ПРОМТ-И13: три документа интеграции отстали на 10 операций, 18 инструментов,
2 подкоманды, 7 проверок, 13 языков дерева и 29 текстовых). Число обязано
считаться по коду, а не переноситься из документа: иначе проверка сверяет
пересказ с пересказом.

Языки считаются через `ragraf.config`, а не регуляркой по файлу: `LANG_BY_EXT`
— это словарь расширение→язык, и число РАЗНЫХ языков из текста регуляркой не
получается без ошибки (проверка стала бы проверкой написания).

Печатает JSON:
    {"ops": 49, "tools": 59, "subcommands": 21, "tree_langs": 29, "text_langs": 40}

Коды возврата: 0 — посчитано, 2 — посчитать не удалось.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"

_TOOLS_DIR = str(Path(__file__).resolve().parent)
sys.path[:] = [p for p in sys.path if str(Path(p or ".").resolve()) != _TOOLS_DIR]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def main() -> int:
    parser = argparse.ArgumentParser(description="числа поверхности продукта по коду")
    parser.add_argument("--root", default=str(REPO_ROOT), help="корень проекта")
    args = parser.parse_args()
    root = Path(args.root)

    try:
        facade = (root / "src/ragraf/facade.py").read_text(encoding="utf-8")
        server = (root / "src/ragraf/mcp_server.py").read_text(encoding="utf-8")
        cli = (root / "src/ragraf/cli.py").read_text(encoding="utf-8")
        from ragraf.config import LANG_BY_EXT, TREE_LANGS  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 — причина отказа важнее типа
        print(json.dumps({"error": f"посчитать не удалось: {type(exc).__name__}: {exc}"},
                         ensure_ascii=False))
        return 2

    ops = len(set(re.findall(r"(?m)^\s*def op_[a-z_]+", facade)))
    tools = len(set(re.findall(r'"name":\s*"(ragraf_[a-z_]+)"', server)))
    subcommands = len(set(re.findall(r'(?m)^\s*p[a-z]+ = sub\.add_parser\(\s*\n?\s*"([a-z_]+)"',
                                     cli)))
    tree = len(TREE_LANGS)
    text = len(set(LANG_BY_EXT.values()) - set(TREE_LANGS))

    if min(ops, tools, subcommands, tree, text) < 1:
        print(json.dumps({"error": "одно из чисел вышло нулём: шаблон разошёлся с кодом",
                          "ops": ops, "tools": tools, "subcommands": subcommands,
                          "tree_langs": tree, "text_langs": text}, ensure_ascii=False))
        return 2

    print(json.dumps({"ops": ops, "tools": tools, "subcommands": subcommands,
                      "tree_langs": tree, "text_langs": text}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
