#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка: платная возможность объявлена так же, как исполнена.

Зачем отдельный инструмент. Возможность объявляется данными
(`src/ragraf/edition_baseline.json`), исполняется кодом (`facade.py`), отдаётся
агенту (`mcp_server.py`) и запирается изданием (`pro_only` в манифесте
Enterprise). Расхождение любой из четырёх частей не даёт ошибки само по себе:
объявленная операция без кода выглядит работающей, а платный модуль, забытый в
`pro_only`, уезжает в публичную сборку — ровно так в 0.11.0 уехал
`contract_diff.py`.

Проверяется СВОЙСТВО, а не написание:

  1. объявление: имя, издание, статус, непустой список операций;
  2. у каждой объявленной операции есть код в фасаде — сверка через
     `ragraf.surface.ops_from_file` (второго разбора фасада нет);
  3. у каждой операции есть инструмент в MCP — через `surface.tools_from_file`;
  4. у каждой операции есть путь в CLI — список `OPS` читается разбором AST,
     а не регуляркой по тексту;
  5. модуль объявлен в `pro_only` манифеста Enterprise и ОТСУТСТВУЕТ в
     манифесте Community: граница издания — отсутствие файла;
  6. тест возможности существует и не пуст.

Запуск:
    python tools/check_pro_features.py --feature memory-facts
    python tools/check_pro_features.py --feature skills-catalog

Коды возврата: 0 — расхождений нет; 1 — найдены; 2 — проверить нечем.
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

#: Каждая платная возможность: имя в базовой линии, издание, модуль, тест.
FEATURES: dict[str, dict[str, str]] = {
    "memory-facts": {
        "edition": "pro",
        "module": "src/ragraf/facts.py",
        "test": "tests/test_facts.py",
    },
    "skills-catalog": {
        "edition": "pro",
        "module": "src/ragraf/skills_catalog.py",
        "test": "tests/test_skills_catalog.py",
    },
    # Слой пиннинга: объявлен с 0.9.0, но его модуль сравнения подписей лежал
    # в общем дереве и в 0.11.0 уехал в публичную сборку. Запись добавлена не
    # ради полноты списка, а потому что именно её отсутствие скрыло дефект.
    "contract-pin": {
        "edition": "enterprise",
        "module": "src/ragraf/contract_diff.py",
        "test": "tests/test_contract_pin.py",
    },
}


def fail(message: str, code: int = 1) -> int:
    print(f"FAIL: {message}")
    return code


def cli_ops(path: pathlib.Path) -> set[str]:
    """Список OPS из разбора AST: свойство, а не совпадение текста."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "OPS":
                    return {str(item.value) for item in node.value.elts
                            if isinstance(item, ast.Constant)}
    return set()


def manifest_pro_only(path: pathlib.Path) -> set[str]:
    """Значения pro_only из манифеста: только это поле и только его строки.

    Поле живёт в разделе `[release]` (манифест описывает выпуск, а не дерево),
    поэтому читается оттуда; верхний уровень оставлен запасным вариантом, чтобы
    смена раскладки была видна как пустое множество, а не как тихий ноль.
    """
    text = path.read_text(encoding="utf-8")
    try:
        import tomllib
    except ModuleNotFoundError:  # Python < 3.11
        return set()
    data = tomllib.loads(text)
    release = data.get("release") if isinstance(data.get("release"), dict) else data
    return {str(item) for item in release.get("pro_only") or []}


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка объявления платной возможности")
    parser.add_argument("--feature", required=True, choices=sorted(FEATURES))
    args = parser.parse_args()

    feature = args.feature
    plan = FEATURES[feature]
    module, test, expected_edition = plan["module"], plan["test"], plan["edition"]

    baseline_path = ROOT / "src/ragraf/edition_baseline.json"
    facade_path = ROOT / "src/ragraf/facade.py"
    server_path = ROOT / "src/ragraf/mcp_server.py"
    cli_path = ROOT / "src/ragraf/cli.py"
    ent_manifest = ROOT / "tools/release/manifest-enterprise.toml"
    community_manifest = ROOT / "tools/release/manifest.toml"

    for path in (baseline_path, facade_path, server_path, cli_path, ent_manifest,
                 community_manifest):
        if not path.exists():
            return fail(f"нет {path.relative_to(ROOT).as_posix()}: проверить нечем", 2)

    from ragraf import surface  # noqa: PLC0415 — путь к src добавлен выше

    problems: list[str] = []
    examined = 0

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    entry = next((item for item in baseline.get("features") or []
                  if str(item.get("name")) == feature), None)
    examined += 1
    if entry is None:
        problems.append(f"возможность {feature!r} не объявлена в базовой линии изданий")
        operations: list[str] = []
    else:
        operations = [str(item) for item in entry.get("operations") or []]
        if str(entry.get("edition")) != expected_edition:
            problems.append(f"{feature}: издание {entry.get('edition')!r}, "
                            f"ожидалось {expected_edition!r}")
        if str(entry.get("status")) != "shipped":
            problems.append(f"{feature}: статус {entry.get('status')!r}, ожидалось 'shipped'")
        if not operations:
            problems.append(f"{feature}: список операций пуст — объявление ничего не обещает")

    ops_in_code = {item["name"] for item in surface.ops_from_file(facade_path)}
    tools_in_code = {item["name"] for item in surface.tools_from_file(server_path)}
    ops_in_cli = cli_ops(cli_path)

    for op in operations:
        examined += 3
        if op not in ops_in_code:
            problems.append(f"операция {op!r} объявлена, но в фасаде её нет")
        if f"ragraf_{op}" not in tools_in_code:
            problems.append(f"инструмента ragraf_{op} нет в поверхности MCP")
        if op not in ops_in_cli:
            problems.append(f"операции {op!r} нет в списке OPS командной строки")

    examined += 3
    ent_files = manifest_pro_only(ent_manifest)
    if module not in ent_files:
        problems.append(f"{module} не объявлен в pro_only: граница издания не проведена файлом")
    if test not in ent_files:
        problems.append(f"{test} не объявлен в pro_only: тест платной возможности уедет в Community")
    if module in manifest_pro_only(community_manifest):
        problems.append(f"{module} оказался в манифесте Community: платный модуль в публичной сборке")

    examined += 1
    test_path = ROOT / test
    if not test_path.exists() or test_path.stat().st_size < 1000:
        problems.append(f"{test} отсутствует или пуст: поведение возможности не доказано")

    for item in problems:
        print(f"FAIL: {item}")
    print(f"осмотрено: {examined}, находок: {len(problems)}")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
