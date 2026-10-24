#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка обвязки поставки: `community/**` и таблица источников.

Проверка формулируется через СВОЙСТВО, а не через написание строк. Задача,
из которой она выросла (этап 2 модернизации, 24.10.2026): продуктовая обвязка
(`CHANGELOG`, `CONTRIBUTING`, `SECURITY`, `ROADMAP`, `README.ru`, `CITATION`,
`CODE_OF_CONDUCT`, `mkdocs.yml`, `.github/**`) жила ТОЛЬКО в опубликованном
репозитории и в старой сборочной копии. Сборка «из живого дерева» поэтому
теряла её всю, а перенос «на глаз» не отличим от переноса с правкой.

Что проверяется (каждый пункт — отдельное наблюдение):

1. девять позиций обвязки существуют в `community/**` и не пусты — список
   обязательных позиций задан здесь, он же белый список требований;
2. у КАЖДОГО файла `community/**` (включая `.github/**` и любые добавленные
   позже) есть строка в `docs/03_implementation/RELEASE-SOURCES.md`, и хэш
   в строке совпадает с фактическим: это и есть защита от «перенесли и забыли»;
3. признак «совпадает с публичным main» в строке не врёт: при `да` хэши
   равны, при `нет` — различаются. Иначе «намеренное отличие» стало бы
   способом скрыть любую правку;
4. каждая строка таблицы указывает на существующий файл: нет строк от файлов,
   которых уже нет;
5. файлы обвязки не ссылаются на служебные и секретоносные пути рабочего
   дерева (`.staging/`, `.zwork/`, `.pi/`, `.dsh/`, `harness/`, `db_access.json`,
   `secrets.key`, `config/*.local.*`) и на абсолютные пути машины владельца;
6. старая сборочная копия `.staging/release-v0.2/RAGRAF` не изменена: хэши её
   файлов совпадают с зафиксированными в том же документе;
7. параметры установщика, которые называет CI (например `-SummaryJson`),
   объявлены самим установщиком. Измеренный дефект: живой `ci.yml:125` вызывал
   `-SummaryJson`, которого в живом `scripts/install.ps1` не было вовсе.

Границы проверки названы честно:

* `.ragraf/`, `.venv`, `.tools/`, `node_modules` НЕ считаются нарушением: это
  документированные пути продукта и потребителя, а не служебные каталоги этой
  машины (замер: `README.ru.md:156` — каталог корпуса, `:226` — venv,
  `CONTRIBUTING.md:50,56`, `SECURITY.md:12`, `ROADMAP.md:106`). Запрещать их
  буквально значило бы объявлять исправные файлы дефектными — ровно тот класс
  ложного FAIL, из-за которого проверки этого проекта уже дважды ошибались;
* проверяется ФОРМУЛИРОВКА путей, а не смысл текста: файл, описывающий
  служебный каталог словами, нарушением не считается;
* нарушением считается ПУТЬ ВНУТРЬ служебного каталога или секретоносного
  файла, а не упоминание имени: оба измеренных случая (см. выше) —
  опубликованные тексты без значений;
* имя проекта публично, поэтому пример каталога исходников в
  `community/install.ps1:90` не считается утечкой раскладки машины;
* хэши с публичным `main` сверяются при переносе, а не здесь: у проверки нет
  сети, и делать вид, что она сверяет с GitHub, было бы ложью. Здесь
  проверяется согласие трёх вещей — файла, строки таблицы и признака.

Запуск: `python tools/check_community_wrapper.py`
Коды возврата: 0 — свойства выполняются, 1 — есть нарушения.
Молчаливого пропуска нет: отсутствие документа или каталога — это провал,
а не «нечего проверять».
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMMUNITY = REPO / "community"
SOURCES_DOC = REPO / "docs/03_implementation/RELEASE-SOURCES.md"
STAGING = REPO / ".staging/release-v0.2/RAGRAF"

FAILURES: list[str] = []
EXAMINED = 0

# Обязательные позиции обвязки. Каталог `.github` требуется как каталог:
# его состав задаётся объединением наборов и проверяется строками таблицы.
REQUIRED_FILES = (
    "CITATION.cff",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "ROADMAP.md",
    "README.ru.md",
    "CHANGELOG.md",
    "mkdocs.yml",
)
REQUIRED_DIRS = (".github",)

# Служебные и секретоносные пути РАБОЧЕГО ДЕРЕВА. Список намеренно не включает
# `.ragraf/`, `.venv`, `.tools/`, `node_modules` — обоснование в docstring.
# Секретоносные ИМЕНА файлов: совпадение по имени — уже нарушение.
FILENAME_TOKENS = (
    ("secrets.key", "мастер-ключ хранилища секретов"),
    ("RAGRAF_SECRETS_KEY", "переменная мастер-ключа"),
    ("AGENTS-TRAPS.md", "внутренний документ ловушек"),
    ("AGENTS-context.md", "внутренний документ контекста"),
)

# Служебные КАТАЛОГИ рабочего дерева: нарушение — путь ВНУТРЬ каталога.
# Названный каталог без сегмента после него остаётся предложением, а не
# указателем. Замер: community/CHANGELOG.md:9 называет `.staging/`,
# описывая, где лежит приватная карта полигона; :50 называет
# `db_access.json` в описании исправления .gitignore. Оба текста
# опубликованы и значений не содержат — объявлять их утечкой значило бы
# ловить формулировку вместо факта.
PATH_TOKENS = (
    (".staging", "старая сборочная копия"),
    (".zwork", "служебный каталог агента"),
    (".pi", "каталог правил агента"),
    (".dsh", "каталог правил второго агента"),
    ("harness", "внутренние материалы, не публикуются"),
)

FORBIDDEN_PATTERNS = (
    # Путь ВНУТРЬ приватного дерева: два и более сегмента после корня. Имя
    # проекта публично (оно и есть имя продукта), поэтому пример
    # `RAGRAF` в community/install.ps1:90 нарушением не
    # считается, а путь внутрь этого дерева — считается.
    (re.compile(r"(?i)\b[A-Z]:\\(?:AutoClaw|1st_RdpMON)\\[^\\\s`\"'|]+\\"), "путь внутрь приватного дерева"),
    (re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s`\"'|]+\\"), "путь внутрь домашнего каталога"),
    (re.compile(r"(?i)catalog[/\\]db_access\.json"), "путь к хранилищу доступа к БД"),
    (re.compile(r"(?i)config[/\\][A-Za-z0-9_.\-]*\.local\."), "локальный конфиг с секретами"),
)

# Таблица источников: ровно шесть столбцов, порядок задан документом.
ROW_COLUMNS = 6

HEADER_COMMIT = re.compile(r"Публичный\s+`main`\s+на\s+момент\s+сверки:[^\n]{0,40}?([0-9a-f]{40})")
ROW = re.compile(r"^\s*\|")


def expect(condition: bool, message: str) -> None:
    """Учесть одно наблюдение и запомнить нарушение."""
    global EXAMINED
    EXAMINED += 1
    if not condition:
        FAILURES.append(message)


def digest(path: Path) -> str:
    """sha256 файла в нижнем регистре — так его и записывает таблица."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def cells_of(line: str) -> list[str]:
    """Разобрать строку markdown-таблицы на ячейки."""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_sources(text: str) -> tuple[list[list[str]], list[list[str]]]:
    """Вернуть (строки источников обвязки, строки контроля старой копии)."""
    wrapper_rows: list[list[str]] = []
    staging_rows: list[list[str]] = []
    for line in text.splitlines():
        if not ROW.match(line):
            continue
        cells = cells_of(line)
        if len(cells) == ROW_COLUMNS and cells[0].startswith("community/"):
            wrapper_rows.append(cells)
        elif len(cells) == 2 and cells[0].startswith(".staging/release-v0.2/RAGRAF/"):
            staging_rows.append(cells)
    return wrapper_rows, staging_rows


def main() -> int:
    if not COMMUNITY.is_dir():
        print("FAIL: каталога community/ нет — обвязка поставки в живом дереве отсутствует")
        print("осмотрено: 0")
        return 1
    if not SOURCES_DOC.is_file():
        relative_doc = SOURCES_DOC.relative_to(REPO).as_posix()
        print(f"FAIL: нет {relative_doc} — источники обвязки не записаны")
        print("осмотрено: 0")
        return 1

    doc = read_text(SOURCES_DOC)
    wrapper_rows, staging_rows = parse_sources(doc)

    commit_match = HEADER_COMMIT.search(doc)
    expect(
        commit_match is not None,
        "документ не называет коммит публичного main, против которого сверялись",
    )
    commit = commit_match.group(1) if commit_match else None
    expect(
        "https://github.com/paulmann/RAGRAF" in doc,
        "документ не называет адрес публичного репозитория",
    )

    # --- 1. Обязательные позиции ------------------------------------------
    for name in REQUIRED_FILES:
        path = COMMUNITY / name
        expect(
            path.is_file() and path.stat().st_size > 0,
            f"обязательная позиция обвязки отсутствует или пуста: community/{name}",
        )
    for name in REQUIRED_DIRS:
        directory = COMMUNITY / name
        has_files = directory.is_dir() and any(p.is_file() for p in directory.rglob("*"))
        expect(bool(has_files), f"обязательная позиция обвязки отсутствует или пуста: community/{name}/")

    # --- 2-4. Таблица источников ------------------------------------------
    files = sorted(p for p in COMMUNITY.rglob("*") if p.is_file())
    expect(len(files) > 0, "в community/ нет ни одного файла — проверять нечего")

    by_path: dict[str, list[str]] = {}
    for cells in wrapper_rows:
        by_path.setdefault(cells[0], cells)

    for path in files:
        relative = path.relative_to(REPO).as_posix()
        actual = digest(path)
        cells = by_path.get(relative)
        expect(cells is not None, f"нет строки в таблице источников: {relative}")
        if cells is None:
            continue
        source, main_hash, tree_hash, same = cells[1], cells[2].lower(), cells[3].lower(), cells[4].lower()
        expect(
            re.fullmatch(r"[0-9a-f]{64}", main_hash) is not None,
            f"{relative}: в таблице не хэш sha256 публичного main ({cells[2]!r})",
        )
        expect(
            tree_hash == actual,
            f"{relative}: хэш в таблице {tree_hash[:12]}… не совпадает с фактическим {actual[:12]}…",
        )
        expect(
            same in ("да", "нет"),
            f"{relative}: признак совпадения должен быть «да» или «нет», а записан {cells[4]!r}",
        )
        if same == "да":
            expect(
                main_hash == actual,
                f"{relative}: записано «совпадает с main», но хэши различные — "
                f"перенос изменён, а решение не названо",
            )
        elif same == "нет":
            expect(
                main_hash != actual,
                f"{relative}: записано «не совпадает с main», но хэши равны — "
                f"признак расхождения не соответствует факту",
            )
        expect(
            source.startswith("https://") and (commit is None or commit in source),
            f"{relative}: источник не содержит адреса и коммита публичного main ({source!r})",
        )

    for relative in by_path:
        expect(
            (REPO / relative).is_file(),
            f"таблица источников ссылается на отсутствующий файл: {relative}",
        )

    # --- 5. Гигиена содержимого -------------------------------------------
    for path in files:
        relative = path.relative_to(REPO).as_posix()
        before = len(FAILURES)
        text = read_text(path)
        file_lines = text.splitlines()
        for token, why in FILENAME_TOKENS:
            hit = next((index for index, line in enumerate(file_lines, 1) if token in line), 0)
            if hit:
                expect(False, f"{relative}:{hit}: имя секретоносного файла {token!r} ({why})")
                break
        for token, why in PATH_TOKENS:
            pattern = re.compile(re.escape(token) + r"[/\\][A-Za-z0-9_]")
            match = pattern.search(text)
            if match is not None:
                line_no = text[: match.start()].count("\n") + 1
                expect(False, f"{relative}:{line_no}: путь внутрь служебного каталога — {match.group(0)!r} ({why})")
                break
        for pattern, why in FORBIDDEN_PATTERNS:
            match = pattern.search(text)
            if match is not None:
                line_no = text[: match.start()].count("\n") + 1
                expect(False, f"{relative}:{line_no}: {why} — {match.group(0)!r}")
                break
        if len(FAILURES) == before:
            expect(True, f"{relative}: служебных путей не найдено")

    # --- 6. Старая сборочная копия не изменена ----------------------------
    expect(len(staging_rows) > 0, "документ не фиксирует хэши старой сборочной копии")
    for relative, expected_hash in staging_rows:
        path = REPO / relative
        actual = digest(path) if path.is_file() else None
        expect(
            actual == expected_hash.lower(),
            f"{relative}: хэш старой сборочной копии изменился "
            f"({(actual or 'файла нет')[:12]}… против зафиксированного {expected_hash[:12]}…)",
        )

    # --- 7. Параметры установщика, которые называет CI --------------------
    installer = REPO / "scripts/install.ps1"
    expect(installer.is_file(), "нет scripts/install.ps1 — единый установщик не найден")
    if installer.is_file():
        installer_text = read_text(installer)
        workflow_dir = COMMUNITY / ".github/workflows"
        workflow_files = sorted(workflow_dir.glob("*.yml")) if workflow_dir.is_dir() else []
        expect(len(workflow_files) > 0, "в community/.github/workflows нет ни одного файла — CI не проверен")
        for workflow in workflow_files:
            for line in read_text(workflow).splitlines():
                if "install.ps1" not in line:
                    continue
                tail = line.split("install.ps1", 1)[1]
                for flag in re.findall(r"(?<!\w)-(?P<name>[A-Za-z][A-Za-z0-9]*)", tail):
                    expect(
                        re.search(rf"\${re.escape(flag)}\b", installer_text) is not None,
                        f"{workflow.name} вызывает -{flag}, а scripts/install.ps1 такой параметр не объявляет",
                    )

    for problem in FAILURES:
        print(f"FAIL: {problem}")
    print(f"осмотрено: {EXAMINED}")
    if FAILURES:
        print(f"нарушений: {len(FAILURES)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())