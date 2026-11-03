"""Проверка: `python -m <модуль>` В ПРИМЕРАХ КОДА обязан указывать на существующий модуль.

Измеренный дефект (30.10.2026): в `docs/03_implementation/RELEASE-0.5.0-PUBLISH.md` стояла
команда `python -m ragraf --version` внутри блока команд. Модуля `ragraf/__main__.py` в дереве
НЕТ, команда невыполнима — выяснилось только запуском на собранном дереве, хотя документ
описывает именно проверку установки.

Свойство, а не написание: **команда внутри огороженного блока считается обещанием** и обязана
быть выполнимой. Упоминание в обычном тексте обещанием не считается — там документ объясняет,
что команда неверна, и это законное место. Поэтому проверка разбирает структуру (границы
блоков ```), а не ищет слова вроде «невозможно».

Архивные издания (`editions/legacy-*`) не проверяются: там описаны исторические дефекты, а не
обещания текущей поставки.

Запуск:  python tools/check_doc_commands.py [--root <каталог>] [--json]
Код 1 при находках.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

MODULE = re.compile(r"python\s+-m\s+([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)")
FENCE = re.compile(r"^\s*(```|~~~)")
# Стандартная библиотека и внешние инструменты: к нашему дереву отношения не имеют.
FOREIGN = {"pip", "venv", "pytest", "http", "json", "sqlite3", "unittest", "site", "zipfile"}

SKIP_DIRS = {".ragraf", ".git", ".zwork", ".staging", "node_modules", "__pycache__", ".mypy_cache"}
SKIP_PREFIX = ("editions/legacy-",)


def code_blocks(text: str) -> list[tuple[int, str]]:
    """Возвращает (номер строки начала, содержимое) для каждого огороженного блока."""
    blocks: list[tuple[int, str]] = []
    lines = text.splitlines()
    inside = False
    start = 0
    buffer: list[str] = []
    for index, line in enumerate(lines, start=1):
        if FENCE.match(line):
            if inside:
                blocks.append((start, "\n".join(buffer)))
                buffer = []
                inside = False
            else:
                inside = True
                start = index
            continue
        if inside:
            buffer.append(line)
    if inside and buffer:  # незакрытый блок: считаем блоком до конца файла
        blocks.append((start, "\n".join(buffer)))
    return blocks


def resolves(src: pathlib.Path, module: str) -> bool:
    parts = module.split(".")
    # Каталог пакета сам по себе НЕ делает `-m` выполнимым: нужен `__main__.py`.
    return (src.joinpath(*parts).with_suffix(".py").is_file()
            or (src.joinpath(*parts) / "__main__.py").is_file())


def scan(root: pathlib.Path) -> dict:
    src = root / "src"
    missing: list[dict] = []
    quoted_in_text = 0
    checked = 0
    for path in sorted(root.rglob("*.md")):
        relative = str(path.relative_to(root)).replace("\\", "/")
        if set(path.relative_to(root).parts) & SKIP_DIRS:
            continue
        if relative.startswith(SKIP_PREFIX):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        in_block: set[int] = set()
        for start, body in code_blocks(text):
            in_block.update(range(start, start + body.count("\n") + 2))
        for match in MODULE.finditer(text):
            module = match.group(1)
            if module.split(".")[0] in FOREIGN:
                continue
            line = text.count("\n", 0, match.start()) + 1
            if line not in in_block:
                quoted_in_text += 1
                continue
            checked += 1
            if resolves(src, module):
                continue
            missing.append({"doc": relative, "line": line, "module": module})
    return {"ok": not missing, "blocks_checked": checked,
            "text_mentions": quoted_in_text, "missing": missing}


def main() -> int:
    parser = argparse.ArgumentParser(description="Команды в примерах обязаны быть выполнимы")
    parser.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parents[1]))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = scan(pathlib.Path(args.root))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"[check_doc_commands] команд в примерах: {report['blocks_checked']}, "
              f"невыполнимых: {len(report['missing'])} "
              f"(упоминаний в тексте, не считаются обещанием: {report['text_mentions']})")
        for item in report["missing"]:
            print(f"  НАХОДКА {item['doc']}:{item['line']} — `python -m {item['module']}`: "
                  f"модуля нет в src/")
        print("ИТОГ:", "чисто" if report["ok"] else "есть невыполнимые команды")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
