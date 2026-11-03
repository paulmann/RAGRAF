"""Числа поверхности продукта — из кода, а не из документа.

Зачем отдельный инструмент: чекер обязан сравнивать объявленное число с посчитанным,
и считать надо в ОДНОМ месте. Иначе каждый потребитель заводит свою редакцию счёта,
и документы снова расходятся — ровно так появились 54 / 59 / 61 / 66 при фактических
60 и 67.

Все числа берутся разбором исходников:
  facade_ops      — `def op_*` в `src/ragraf/facade.py`;
  mcp_tools       — объявления `"name": "ragraf_..."` в `src/ragraf/mcp_server.py`;
  cli_subcommands — `add_parser("<имя>")` в `src/ragraf/cli.py`;
  tree_languages  — `TREE_LANGS` из `src/ragraf/config.py`;
  text_languages  — языки `LANG_BY_EXT` минус языки дерева (файлы читаются текстом);
  formats         — `FORMATS` из `src/ragraf/formats.py`;
  capabilities    — записи `src/ragraf/edition_baseline.json`.

Ответ — JSON с полями `source` (какой файл дал число) и счётом. Ошибка разбора
называется, а не подменяется нулём: нулевое число прошло бы проверку «сколько есть —
столько и есть».
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def count_facade_ops() -> tuple[int, str]:
    body = (ROOT / "src/ragraf/facade.py").read_text(encoding="utf-8")
    names = sorted(set(re.findall(r"(?m)^\s*def (op_[a-z0-9_]+)\(", body)))
    return len(names), "src/ragraf/facade.py"


def count_mcp_tools() -> tuple[int, str]:
    body = (ROOT / "src/ragraf/mcp_server.py").read_text(encoding="utf-8")
    names = sorted(set(re.findall(r'"name":\s*"(ragraf_[a-z0-9_]+)"', body)))
    return len(names), "src/ragraf/mcp_server.py"


def count_cli_subcommands() -> tuple[int, str]:
    body = (ROOT / "src/ragraf/cli.py").read_text(encoding="utf-8")
    names = sorted(set(re.findall(r'(?m)^\s*p[a-z]+ = sub\.add_parser\(\s*\n?\s*"([a-z_]+)"', body)))
    return len(names), "src/ragraf/cli.py"


def count_languages() -> tuple[int, int, str]:
    from ragraf import config

    tree = set(config.TREE_LANGS)
    languages = set(config.LANG_BY_EXT.values())
    return len(tree), len(languages - tree), "src/ragraf/config.py"


def count_formats() -> tuple[int, str]:
    from ragraf import formats

    return len(formats.FORMATS), "src/ragraf/formats.py"


def count_capabilities() -> tuple[int, str]:
    data = json.loads((ROOT / "src/ragraf/edition_baseline.json").read_text(encoding="utf-8"))
    features = data.get("features")
    if not isinstance(features, list):
        raise ValueError("в edition_baseline.json нет списка features")
    return len(features), "src/ragraf/edition_baseline.json"


def main() -> int:
    result: dict[str, object] = {}
    problems: list[str] = []
    try:
        result["facade_ops"], result["facade_ops_source"] = count_facade_ops()
        result["mcp_tools"], result["mcp_tools_source"] = count_mcp_tools()
        result["cli_subcommands"], result["cli_subcommands_source"] = count_cli_subcommands()
        tree, text, tree_source = count_languages()
        result["tree_languages"], result["text_languages"] = tree, text
        result["tree_languages_source"] = tree_source
        result["formats"], result["formats_source"] = count_formats()
        result["capabilities"], result["capabilities_source"] = count_capabilities()
    except Exception as exc:  # noqa: BLE001
        problems.append(f"счёт чисел поверхности не выполнен: {type(exc).__name__}: {exc}")

    for key in ("facade_ops", "mcp_tools", "cli_subcommands", "tree_languages",
                "text_languages", "formats", "capabilities"):
        if key in result and (not isinstance(result[key], int) or result[key] < 1):
            problems.append(f"число {key} посчитано как {result[key]!r}: образец разошёлся с кодом")

    result["problems"] = problems
    print(json.dumps(result, ensure_ascii=False))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
