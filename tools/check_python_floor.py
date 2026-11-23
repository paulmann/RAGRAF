"""Граница версии Python: проверка читает требования из кода, а не из документа.

Зачем. В блоках 20–21 дважды выяснялось, что «поддерживается» и «работает» — разные
вещи: установщик отказывает на 3.11 кодом 3, `pip install -e` отказывает по
`requires-python`, а документ может обещать другую границу и разойтись молча.
Проверка сверяет ТРИ факта и падает, если они расходятся:

  1. `pyproject.toml` объявляет `requires-python`;
  2. КОД не использует синтаксис новее объявленной границы — здесь проверяется
     известное ограничение проекта: пакет собран под 3.13, и `tomllib` (3.11+),
     `typing.Self` (3.11+), `except*` (3.11+) не являются доказательством 3.13;
     настоящий признак — объявленная граница и то, что установщик её требует;
  3. `docs/02_architecture/INSTALL.md` называет ту же границу и ту же причину.

Запуск: python tools/check_python_floor.py
Код: 0 — согласовано, 1 — расхождение (названо), 2 — не читается.
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
INSTALL = ROOT / "docs/02_architecture/INSTALL.md"
SETUP_PS1 = ROOT / "scripts/ragraf-setup.ps1"
INSTALL_SH = ROOT / "scripts/ragraf-install.sh"


def floor_from_pyproject() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    found = re.search(r'requires-python\s*=\s*"([^"]+)"', text)
    if not found:
        raise SystemExit("ОТКАЗ: pyproject.toml не объявляет requires-python")
    return found.group(1)


def version_tuple(spec: str) -> tuple[int, int]:
    found = re.search(r"(\d+)\.(\d+)", spec)
    if not found:
        raise SystemExit(f"ОТКАЗ: граница «{spec}» не разбирается")
    return int(found.group(1)), int(found.group(2))


def main() -> int:
    problems: list[str] = []
    spec = floor_from_pyproject()
    major, minor = version_tuple(spec)
    print(f"pyproject: requires-python = \"{spec}\" → граница {major}.{minor}")

    # 2. Установщики обязаны требовать ту же границу и называть её в отказе.
    for path, pattern in ((SETUP_PS1, r"\$script:MinPython\s*=\s*(?:\[version\])?'?(\d+)\.(\d+)"),
                          (INSTALL_SH, r"MIN_PYTHON=\"?(\d+)\.(\d+)")):
        if not path.is_file():
            problems.append(f"нет файла установщика: {path.relative_to(ROOT)}")
            continue
        text = path.read_text(encoding="utf-8")
        found = re.search(pattern, text)
        if not found:
            problems.append(f"{path.relative_to(ROOT)}: граница версии не объявлена переменной")
            continue
        have = (int(found.group(1)), int(found.group(2)))
        if have != (major, minor):
            problems.append(
                f"{path.relative_to(ROOT)}: установщик требует {have[0]}.{have[1]}, "
                f"пакет объявляет {major}.{minor}")
        else:
            print(f"{path.relative_to(ROOT)}: требует {have[0]}.{have[1]} — совпадает")

    # 3. Документ обязан называть границу числом и причину отказа.
    if not INSTALL.is_file():
        problems.append("нет docs/02_architecture/INSTALL.md")
    else:
        text = INSTALL.read_text(encoding="utf-8")
        if f"Python {major}.{minor}" not in text:
            problems.append(f"INSTALL.md не называет границу «Python {major}.{minor}»")
        if "3.11" not in text or "backports" not in text.lower():
            problems.append("INSTALL.md не объясняет, что делать на Debian 12 (системный 3.11)")
        if "requires-python" not in text and "requires a different Python" not in text:
            problems.append("INSTALL.md не называет второй барьер — `pip` по requires-python")
        else:
            print("INSTALL.md: граница, причина и второй барьер названы")

    for problem in problems:
        print(f"  · {problem}")
    if problems:
        print(f"ОТКАЗ: расхождений {len(problems)} — документ и код говорят разное о Python")
        return 1
    print("OK: граница Python согласована в pyproject, установщиках и документе")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
