"""Набор проверок не имеет права прятать своё падение.

Измерено 23.11.2026 на `tests/test_client_report.py`: `finally` заканчивался
`raise SystemExit(0 if FAIL == 0 else 1)`, а исключение, возникшее в теле `try`, этим же
`SystemExit` из `finally` ПОДАВЛЯЛОСЬ. Набор печатал «ИТОГ: пройдено 3, провалено 0» и
выходил с кодом 0, не дойдя до конца: зелёный результат, который ничего не осмотрел.

Свойство (а не написание): если в `finally` возбуждается `SystemExit`/`sys.exit`, у того же
`try` обязан быть `except`, который печатает падение (print/traceback) или увеличивает
счётчик провалов. Иначе исключение исчезает.

Проверяется не текст, а разбор: файл читается как AST, ищутся узлы `try` с `finally`, в
`finally` — возбуждение выхода, и проверяется наличие `handler` с печатью.

Запуск: python tools/check_test_finally.py [--root <каталог>]
Формат для чекера: строки `FAIL:` дают находки, `осмотрено: N` попадает в отчёт.
Код возврата 1 при находке, 2 при ошибке аргументов.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]
PRINT_CALLS = {"print", "traceback.print_exc", "print_exc"}


def _raises_exit(node: ast.AST) -> bool:
    """Возбуждает ли узел выход из процесса."""
    for child in ast.walk(node):
        if isinstance(child, ast.Raise):
            exc = child.exc
            name = ""
            if isinstance(exc, ast.Call):
                func = exc.func
                name = getattr(func, "id", None) or getattr(func, "attr", None) or ""
            elif isinstance(exc, ast.Name):
                name = exc.id
            if name in ("SystemExit", "exit", "quit"):
                return True
        if isinstance(child, ast.Call):
            func = child.func
            name = getattr(func, "id", None) or getattr(func, "attr", None) or ""
            if name in ("exit", "quit", "_exit"):
                return True
    return False


def _prints(node: ast.AST) -> bool:
    """Печатает ли обработчик что-нибудь (значит падение видно)."""
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            name = getattr(func, "id", None) or getattr(func, "attr", None) or ""
            if name in ("print", "print_exc", "write", "format_exc"):
                return True
    return False


def scan_file(path: Path) -> list[tuple[int, str]]:
    """Находки в одном файле: (строка, причина)."""
    problems: list[tuple[int, str]] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return [(exc.lineno or 1, f"файл не разбирается: {exc.msg}")]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or node.finalbody is None:
            continue
        finally_body = ast.Module(body=node.finalbody, type_ignores=[])
        if not _raises_exit(finally_body):
            continue
        # Выход в finally законен, если исключение уже видно: обработчик печатает.
        visible = any(_prints(handler) for handler in node.handlers)
        if not visible:
            problems.append((
                node.lineno,
                "выход в finally без обработчика, который печатает падение: "
                "исключение из try будет подавлено",
            ))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="наборы не прячут своё падение")
    parser.add_argument("--root", default=str(REPO))
    args = parser.parse_args(argv)
    root = Path(args.root)
    if not root.is_dir():
        print(f"FAIL: каталог не найден: {root}")
        return 2

    targets: list[Path] = sorted((root / "tests").glob("test_*.py"))
    targets += sorted((root / "tools").glob("check_*.py"))
    if not targets:
        print("FAIL: ни одного набора не найдено — проверка ничего не осмотрела")
        return 1

    found: list[str] = []
    for path in targets:
        for lineno, reason in scan_file(path):
            found.append(f"FAIL: {path.relative_to(root).as_posix()}:{lineno} — {reason}")

    for line in found:
        print(line)
    print(f"осмотрено: {len(targets)}, находок: {len(found)}")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
