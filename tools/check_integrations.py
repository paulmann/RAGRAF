# -*- coding: utf-8 -*-
"""Проверка агентских интеграций: каталоги, шаблоны, уровни, границы.

Зачем отдельный инструмент, а не проверка «на глаз». Поддержка продукта — это
артефакты, которые можно проверить машиной: шаблон конфигурации разбирается,
в нём нет секретов, смоук существует там, где заявлен уровень L2, а таблица
уровней совпадает с фактическим набором каталогов. Без этого «поддержано»
означало бы «упомянуто в README».

Что проверяется (каждый пункт — отдельная проверка с названием):

  1. `integrations/index.json` разбирается, схема и обязательные поля на месте;
  2. каталог есть у каждого объявленного продукта, и лишних каталогов нет;
  3. README каталога содержит разделы «Подключение», «Проверка», «Границы»;
  4. шаблон разбирается тем же разборщиком, что продукт (JSON/YAML/TOML),
     и называет сервер `ragraf`;
  5. в шаблонах нет значений, похожих на секреты (ключи, токены, пароли);
  6. уровень L2 подтверждён смоук-скриптом, а где смоука нет — назван чек-лист;
  7. таблица уровней в `docs/03_implementation/AGENT-INTEGRATIONS.md` совпадает
     с объявлением: продукт, slug и набор уровней;
  8. смоук-скрипт объявляет требования PS 7.6 и проверяет ссылку `path:line`.

Коды возврата: 0 — нарушений нет, 1 — есть, 2 — ошибка аргументов.
Ключи: `--templates` (только шаблоны и README), `--json`, `--root <каталог>`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

#: Обязательные поля объявления продукта.
REQUIRED = ("slug", "title", "transport", "config", "levels", "template")
#: Разделы README, которые обязаны быть: подключение, проверка, границы.
SECTIONS = ("Подключение", "Проверка", "Границы")
#: Ключи, значения которых не имеют права появляться в шаблоне.
SECRET_KEY = re.compile(
    r"(?i)(?:^|[\"'\s])(api[_-]?key|token|secret|password|passwd|authorization|"
    r"credential|client[_-]?secret|private[_-]?key)[\"']?\s*[:=]\s*"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)")
#: Значение-заглушка: пустое, в угловых скобках, из окружения, «впишите сами».
PLACEHOLDER = re.compile(
    r"(?i)^(\"\"|''|<>)$|^<[^>]+>$|^\$\{[^}]+\}$|^\$[A-Z_][A-Z0-9_]*$|"
    r"^(your|replace|changeme|example|none|null|todo|xxx)")


def load_index(root: Path) -> tuple[dict[str, Any] | None, list[str]]:
    path = root / "integrations" / "index.json"
    if not path.is_file():
        return None, [f"нет объявления {path.relative_to(root).as_posix()}"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return None, [f"объявление не разбирается как JSON: {exc}"]
    return data, []


def parse_template(path: Path) -> tuple[bool, str]:
    """Разобрать шаблон тем же разборщиком, что продукт."""
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            json.loads(text)
        except ValueError as exc:
            return False, f"JSON не разбирается: {exc}"
        return True, ""
    if suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            return False, "PyYAML не установлен: YAML-шаблон нечем разобрать"
        try:
            yaml.safe_load(text)
        except Exception as exc:  # noqa: BLE001 — причина называется
            return False, f"YAML не разбирается: {type(exc).__name__}: {exc}"
        return True, ""
    if suffix == ".toml":
        try:
            import tomllib
        except ImportError:
            return False, "tomllib недоступен (нужен Python 3.11+)"
        try:
            tomllib.loads(text)
        except Exception as exc:  # noqa: BLE001
            return False, f"TOML не разбирается: {type(exc).__name__}: {exc}"
        return True, ""
    return False, f"разборщика для расширения {suffix!r} нет"


def scan_secrets(path: Path) -> list[str]:
    """Найти в шаблоне строки вида «ключ: значение» с непустым значением."""
    problems: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for match in SECRET_KEY.finditer(line):
            value = match.group(2).strip().strip(",")
            if PLACEHOLDER.match(value):
                continue
            problems.append(f"{path.name}:{number}: похоже на секрет "
                            f"({match.group(1)} = {value[:12]}…)")
    return problems


def check_index(data: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if data.get("schema") != 1:
        problems.append(f"schema: ожидается 1, получено {data.get('schema')!r}")
    products = data.get("products")
    if not isinstance(products, list) or not products:
        return problems + ["products: пустой или не список"]
    seen: set[str] = set()
    for item in products:
        if not isinstance(item, dict):
            problems.append("products: элемент не объект")
            continue
        for key in REQUIRED:
            if not str(item.get(key) or "").strip():
                problems.append(f"{item.get('slug') or '?'}: нет поля {key}")
        slug = str(item.get("slug") or "")
        if slug in seen:
            problems.append(f"{slug}: объявлен дважды")
        seen.add(slug)
        levels = item.get("levels") or []
        if not isinstance(levels, list) or not levels:
            problems.append(f"{slug}: уровни не объявлены")
        unknown = [level for level in levels if level not in ("L0", "L1", "L2", "L3", "L4")]
        if unknown:
            problems.append(f"{slug}: неизвестные уровни {unknown}")
        if "L2" in (levels or []) and not str(item.get("smoke") or "").strip():
            problems.append(f"{slug}: заявлен L2, но смоук не назван")
        if "L2" not in (levels or []) and str(item.get("smoke") or "").strip():
            problems.append(f"{slug}: смоук есть, но уровень L2 не заявлен")
    return problems


def check_dirs(root: Path, products: list[dict]) -> list[str]:
    problems: list[str] = []
    base = root / "integrations"
    declared = {str(item.get("slug")) for item in products}
    actual = {path.name for path in base.iterdir()
              if path.is_dir() and not path.name.startswith(".")}
    for slug in sorted(declared - actual):
        problems.append(f"{slug}: объявлен, а каталога integrations/{slug} нет")
    for slug in sorted(actual - declared):
        problems.append(f"{slug}: каталог есть, а в index.json не объявлен")
    return problems


def check_readme(root: Path, item: dict) -> list[str]:
    """README каталога: три раздела, которые обещают уровни L1/L4."""
    problems: list[str] = []
    slug = str(item["slug"])
    readme = root / "integrations" / slug / "README.md"
    if not readme.is_file():
        return [f"{slug}: нет README.md"]
    text = readme.read_text(encoding="utf-8")
    for section in SECTIONS:
        if f"## {section}" not in text:
            problems.append(f"{slug}: в README нет раздела «{section}»")
    return problems


def check_template(root: Path, item: dict) -> list[str]:
    """Шаблон: разбирается, называет сервер ragraf, не содержит секретов."""
    problems: list[str] = []
    slug = str(item["slug"])
    template = str(item.get("template") or "")
    if not template:
        return [f"{slug}: шаблон не объявлен"]
    path = root / "integrations" / slug / template
    if not path.is_file():
        return [f"{slug}: нет шаблона {template}"]
    ok, reason = parse_template(path)
    if not ok:
        problems.append(f"{slug}: шаблон не разбирается — {reason}")
    if "ragraf" not in path.read_text(encoding="utf-8"):
        problems.append(f"{slug}: в шаблоне не назван сервер ragraf")
    problems.extend(f"{slug}: {line}" for line in scan_secrets(path))
    return problems


def check_artifact(root: Path, item: dict) -> list[str]:
    """Смоук либо чек-лист: у уровня L2 обязателен смоук с кодом возврата."""
    problems: list[str] = []
    slug = str(item["slug"])
    directory = root / "integrations" / slug
    smoke = str(item.get("smoke") or "")
    checklist = str(item.get("checklist") or "")
    if smoke:
        path = directory / smoke
        if not path.is_file():
            return [f"{slug}: заявлен смоук {smoke}, файла нет"]
        text = path.read_text(encoding="utf-8")
        for requirement in ("#Requires -Version 7.6", "#Requires -PSEdition Core"):
            if requirement not in text:
                problems.append(f"{slug}: смоук без {requirement}")
        if "path:line" not in text:
            problems.append(f"{slug}: смоук не проверяет ссылку path:line")
        if "exit " not in text:
            problems.append(f"{slug}: смоук не возвращает код")
        return problems
    if checklist:
        if not (directory / checklist).is_file():
            problems.append(f"{slug}: нет чек-листа {checklist}")
        return problems
    return [f"{slug}: нет ни смоука, ни чек-листа — проверять нечем"]


def parse_doc_table(root: Path) -> tuple[dict[str, list[str]], list[str]]:
    """Разобрать таблицу уровней из документа интеграций."""
    path = root / "docs" / "03_implementation" / "AGENT-INTEGRATIONS.md"
    if not path.is_file():
        return {}, [f"нет документа {path.relative_to(root).as_posix()}"]
    rows: dict[str, list[str]] = {}
    problems: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        # Slug — вторая колонка, в обратных кавычках и без пробелов: так строка
        # «| продукт | `slug` | уровни |» отличается от заголовка и от текста.
        slug_cell = cells[1]
        if not (slug_cell.startswith("`") and slug_cell.endswith("`")):
            continue
        slug = slug_cell.strip("`")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug or ""):
            continue
        levels = re.findall(r"L[0-4]", cells[2])
        if levels:
            rows[slug] = levels
    if not rows:
        problems.append("таблица уровней не найдена: ожидались строки «| продукт | `slug` | L1… |»")
    return rows, problems


def check_doc(root: Path, products: list[dict]) -> list[str]:
    problems: list[str] = []
    rows, doc_problems = parse_doc_table(root)
    problems.extend(doc_problems)
    declared = {str(item["slug"]): sorted(item.get("levels") or []) for item in products}
    for slug, levels in declared.items():
        if slug not in rows:
            problems.append(f"{slug}: нет строки в таблице уровней документа")
        elif sorted(rows[slug]) != levels:
            problems.append(f"{slug}: в документе уровни {sorted(rows[slug])}, "
                            f"а в объявлении {levels}")
    for slug in sorted(set(rows) - set(declared)):
        problems.append(f"{slug}: строка в документе есть, продукта в объявлении нет")
    return problems


def run(root: Path, *, templates_only: bool = False) -> dict[str, Any]:
    """Выполнить проверки и вернуть результат одним словарём."""
    checks: list[dict[str, Any]] = []

    def record(name: str, problems: list[str], examined: int) -> None:
        checks.append({"name": name, "ok": not problems, "examined": examined,
                       "problems": problems})

    data, index_problems = load_index(root)
    products = (data or {}).get("products") or []
    if data is not None:
        # Правила объявления (дубли слагов, закрытый перечень уровней, связка
        # «L2 → смоук») проверяются здесь же: иначе они остались бы мёртвым
        # кодом, и L2 без смоука проходил бы молча.
        index_problems = list(index_problems) + check_index(data)
    record("объявление интеграций разбирается и полно", index_problems, len(products))
    record("каталог есть у каждого продукта и лишних нет",
           check_dirs(root, products), len(products))

    items = [item for item in products if isinstance(item, dict)]
    record("README каталогов содержит подключение, проверку и границы",
           [problem for item in items for problem in check_readme(root, item)],
           len(items))
    record("шаблоны разбираются, называют ragraf и не содержат секретов",
           [problem for item in items for problem in check_template(root, item)],
           len(items))
    if not templates_only:
        record("у каждого продукта есть смоук либо чек-лист",
               [problem for item in items for problem in check_artifact(root, item)],
               len(items))
        record("таблица уровней совпадает с объявлением", check_doc(root, products),
               len(items))

    failed = sum(1 for check in checks if not check["ok"])
    return {"checks": len(checks), "failed": failed,
            "problems": [f"{check['name']}: {problem}"
                         for check in checks for problem in check["problems"]],
            "detail": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="проверка агентских интеграций")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--templates", action="store_true",
                        help="только шаблоны и README каталогов")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = run(Path(args.root), templates_only=args.templates)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for check in result["detail"]:
            mark = "ok  " if check["ok"] else "FAIL"
            print(f"  [{mark}] {check['name']}")
            for problem in check["problems"]:
                print(f"         {problem}")
        print(f"проверок: {result['checks']}, провалов: {result['failed']}")
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
