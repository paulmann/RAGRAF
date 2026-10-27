#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ячейки таблиц переводов README: те же ЯЧЕЙКИ, а не только их число.

Задача, из которой проверка выросла (этап И31, 27.10.2026). К этому моменту
перевод был проверяем на семи уровнях: состояние (`tools/check_translations.py`),
ссылки на файлы поставки, числа индекса, терминология (`tools/check_glossary.py`),
проза (`tools/check_prose_profile.py`), адреса
(`tools/check_document_addresses.py`) и блоки команд
(`tools/check_command_blocks.py`, этап И30). У всех названа честная граница, но
внутри таблиц оставался слой той же природы, что и команды, и его не сторожил
никто: СОДЕРЖИМОЕ ЯЧЕЙКИ.

Функция `section_profile` (`tools/check_translations.py`, строка 193) считает
строки таблиц на каждый заголовок `##`, то есть таблица проверяется ЧИСЛОМ.
Поэтому перевод, где в таблице метрик ячейка `2.0 / 3.1` стала `3,1 / 2,0`, а
идентификатор операции `search` заменён переводом, проходил зелёным: число строк
совпало. Замер 27.10.2026 до правки: таблиц 8 в каждом из шести файлов,
содержательных строк 83, ячеек 197, столбцов от 2 до 6; расхождений защищённой
части 5 в четырёх переводах (ru 1, de 1, es 1, hi 1, zh 2), и оба семейства
названы в объявлении исключениями с причиной.

Что считается ячейкой и что в ней защищено (объявлено ДАННЫМИ —
`docs/03_implementation/tables.json`, а не выведено кодом):

* строки таблицы — подряд идущие строки, начинающиеся с `|`, вне блоков
  ограждения; строка-разделитель (`|---|---|`) в содержательные не входит;
* СТРОКА ЗАГОЛОВКА — не данные: её ячейки переводимы свободно, чем бы ни был
  объявлен столбец. Вид колонки относится к строкам данных: `Operation` →
  `Операция` — правильный перевод, а `search` → `поиск` в том же столбце —
  находка;
* ячейки делятся по `|`, но `|` внутри обратных кавычек и экранированный `\\|`
  ячейку не делят — иначе ячейка с образцом разъехалась бы на две и находка
  указала бы не туда;
* внутри ячейки защищены: обрамлённый код, адрес ссылки (`](…)`), ЧИСЛА и число
  разделителей `<br>`; переводимый текст не сравнивается вовсе. Это и есть
  граница: «число, идентификатор, имя файла или операции, признак» защищены,
  «заголовок столбца и пояснение» свободны;
* числа сравниваются ЧИСЛОМ, а не текстом: `2,0` и `2.0` — одно число, `2 722` и
  `2722` — одно число. Это узкий допуск: он касается ТОЛЬКО разделителя дробной
  части и разделителя разрядов, а не «любого числа» — подмена `2.0` на `3.1`
  остаётся находкой;
* числа внутри ОДНОЙ ячейки сравниваются набором с учётом кратности, а не
  последовательностью: языки меняют порядок (`(12 of 13)` → `(13 में से 12)`).
  Позиции чисел между ячейками и между строками сравниваются, поэтому подмена
  местами значений двух столбцов видна;
* вид ячейки объявлен по столбцу (текст, код, число, идентификатор, признак,
  ссылка), и ОСНОВНОЙ документ обязан соответствовать своему объявлению: столбец,
  названный «число», не может содержать фразу. Иначе объявление описывало бы не
  тот файл.

Границы, названные честно:

* **смысл и правильность ячейки не проверяются**: перевод, который перевёл
  пояснение неверно, пройдёт зелёным — сверяется только защищённая часть;
* отрисовка таблицы GitHub не воспроизводится: ширина строки-разделителя `---`
  не сравнивается, переносы внутри ячейки сверяются по числу `<br>`, а не по
  тому, как их покажет браузер;
* разметка вне защищённых частей не сравнивается: `**14**` и `14` — одно число,
  и это правильно, но курсив вокруг числа сам по себе находкой не станет;
* правило разбора повторено по виду документов: обратные кавычки внутри строки
  таблицы считаются парными, `|` внутри адреса ссылки (`[a](b|c)`) делит ячейку —
  в README такого нет, и объявленное правило это называет.

Одна порча — одна находка. Пропавшая строка таблицы — находка на таблицу
(позиции строк съехали, построчное сравнение не выполняется); разошедшаяся
ячейка — находка на эту ячейку, с номером строки файла и обоими значениями.

Строгость — ДАННЫЕ: `cell_tolerance` (допуск на число расходящихся ячеек) и
перечень объявленных исключений с причиной, по языку. Исключение, объявленное и
не использованное, — находка: иначе перечень допусков превратился бы в описание
прошлого и разрешал бы то, чего уже нет.

Запуск:
    python tools/check_table_rows.py [--root <дерево>]
Коды возврата: 0 — защищённые части ячеек каждого перевода совпадают с основным
документом, 1 — есть расхождения (включая «ноль осмотренных ячеек»: это провал,
а не PASS), 2 — не прочитаны аргументы.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MAIN = "README.md"
DECLARATION = "docs/03_implementation/tables.json"
STATES = "docs/03_implementation/translations.json"

FENCE = re.compile(r"^\s*(```|~~~)")
SEPARATOR = re.compile(r"^\|(?:\s*:?-{2,}:?\s*\|)+\s*$")
#: Число: разряды через пробел (в том числе неразрывный), дробная часть — `.` или `,`.
NUMBER = re.compile(r"(?<!\d)\d+(?:[ \u00a0]\d{3})*(?:[.,]\d+)?(?!\d)")
BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
#: Виды ячеек, известные проверке. Словарь закрытый: опечатка не создаёт вид.
KINDS = ("text", "code", "number", "identifier", "flag", "link")

EXAMINED = 0
FAILURES: list[str] = []
OBSERVED: list[str] = []


def expect(condition: bool, message: str, ok_note: str | None = None) -> None:
    """Учесть одно наблюдение; при нарушении запомнить находку."""
    global EXAMINED
    EXAMINED += 1
    if condition:
        if ok_note:
            OBSERVED.append(ok_note)
    else:
        FAILURES.append(message)


def fail(message: str) -> None:
    """Предусловие нарушено: находка без наблюдения (осматривать было нечего)."""
    FAILURES.append(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# --- разбор таблиц и ячеек ----------------------------------------------------


def tables(text: str) -> list[dict]:
    """Таблицы файла: {open: номер первой строки, rows: [(номер, строка)]}."""
    found: list[dict] = []
    in_fence = False
    current: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            in_fence = not in_fence
            current = []
            continue
        if in_fence:
            continue
        if line.startswith("|"):
            current.append((number, line.rstrip()))
            continue
        if current:
            found.append({"open": current[0][0], "rows": current})
            current = []
    if current:
        found.append({"open": current[0][0], "rows": current})
    return found


def cells(line: str) -> list[str]:
    """Ячейки строки: `|` внутри обратных кавычек и экранированный ячейку не делят."""
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    out: list[str] = []
    current: list[str] = []
    ticks = 0
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\" and index + 1 < len(body) and body[index + 1] == "|":
            current.append("|")
            index += 2
            continue
        if char == "`":
            ticks += 1
            current.append(char)
            index += 1
            continue
        if char == "|" and ticks % 2 == 0:
            out.append("".join(current).strip())
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    out.append("".join(current).strip())
    return out


def content_rows(table: dict) -> list[tuple[int, str]]:
    """Содержательные строки таблицы: строка-разделитель не считается."""
    return [(number, line) for number, line in table["rows"] if not SEPARATOR.match(line)]


def spans(cell: str) -> list[str]:
    """Обрамлённый код ячейки — по порядку."""
    return [item.strip() for item in re.findall(r"`([^`]+)`", cell)]


def links(cell: str) -> list[str]:
    """Адреса ссылок ячейки — по порядку."""
    return re.findall(r"\]\(([^)]*)\)", cell)


def numbers(cell: str) -> list[Decimal]:
    """Числа ячейки, приведённые к значению; код и адреса ссылок не разбираются."""
    without_code = re.sub(r"`[^`]+`", " ", cell)
    without_targets = re.sub(r"\]\([^)]*\)", " ", without_code)
    found: list[Decimal] = []
    for token in NUMBER.findall(without_targets):
        try:
            found.append(Decimal(token.replace(" ", "").replace("\u00a0", "").replace(",", ".")))
        except InvalidOperation:  # pragma: no cover — NUMBER таких строк не даёт
            continue
    return found


def residual(cell: str) -> str:
    """Что осталось от ячейки после снятия кода, адресов, чисел и разметки."""
    text = re.sub(r"`[^`]+`", " ", cell)
    text = re.sub(r"\]\([^)]*\)", " ", text)
    text = NUMBER.sub(" ", text)
    text = BR.sub(" ", text)
    text = re.sub(r"[\[\]*_`|]", " ", text)
    return text.strip(" \u00a0/\\,+-–—()×%.:;")


def squash(cell: str) -> str:
    return re.sub(r"\s+", " ", cell).strip()


def describe(number: Decimal) -> str:
    return format(number.normalize(), "f")


# --- сравнение ----------------------------------------------------------------


def shape_problems(kind: str, cell: str, where: str) -> list[str]:
    """Соответствие ячейки своему виду: объявление не должно быть пустым словом."""
    prefix = f"{where}: " if where else ""
    problems: list[str] = []
    code = spans(cell)
    targets = links(cell)
    if kind == "code":
        if not code:
            problems.append(f"{prefix}в ячейке нет обрамлённого кода, а столбец объявлен кодом")
        elif residual(cell):
            problems.append(f"{prefix}ячейка объявлена кодом, но кроме кода в ней {residual(cell)!r}")
        if targets:
            problems.append(f"{prefix}ячейка объявлена кодом, но содержит адрес ссылки")
    elif kind == "number":
        if len(numbers(cell)) != 1 or residual(cell) or code or targets:
            problems.append(f"{prefix}ячейка объявлена числом, но это {cell!r}")
    elif kind == "identifier":
        if code or targets:
            problems.append(f"{prefix}ячейка объявлена идентификатором, но содержит код или ссылку")
        elif not re.fullmatch(r"[\x20-\x7E]+", cell):
            problems.append(f"{prefix}ячейка объявлена идентификатором, но содержит {cell!r}")
    elif kind == "link":
        if len(targets) != 1:
            problems.append(f"{prefix}ячейка объявлена ссылкой, но адресов {len(targets)}")
    return problems


def divergence(kind: str, base: str, here: str) -> list[str]:
    """Расхождения защищённой части ячейки; пустой список — ячейка в допуске."""
    if kind in ("identifier", "flag"):
        if squash(base) != squash(here):
            return [f"значение {here!r} вместо {base!r}"]
        return []
    problems: list[str] = []
    base_code, here_code = spans(base), spans(here)
    if base_code != here_code:
        problems.append(f"код {here_code} вместо {base_code}")
    base_links, here_links = links(base), links(here)
    if base_links != here_links:
        problems.append(f"адрес {here_links} вместо {base_links}")
    base_numbers = Counter(numbers(base))
    here_numbers = Counter(numbers(here))
    if base_numbers != here_numbers:
        problems.append(
            f"числа {[describe(item) for item in sorted(here_numbers.elements())]} вместо "
            f"{[describe(item) for item in sorted(base_numbers.elements())]}"
        )
    base_br, here_br = len(BR.findall(base)), len(BR.findall(here))
    if base_br != here_br:
        problems.append(f"разделителей <br> {here_br} вместо {base_br}")
    return problems


# --- объявления ---------------------------------------------------------------


def load_declaration(root: Path) -> tuple[dict | None, list[str]]:
    """Правило разбора и строгость из данных. None — объявления нет или оно нечитаемо."""
    path = root / DECLARATION
    if not path.is_file():
        return None, [
            f"нет {DECLARATION} — что внутри ячейки защищено и с какой строгостью это "
            f"требовать, не объявлено (строгость в коде запрещена: иначе языковое различие "
            f"нельзя назвать числом, не правя проверку)"
        ]
    try:
        data = json.loads(read(path))
    except json.JSONDecodeError as error:
        return None, [f"{DECLARATION} не разобран: {error}"]

    problems: list[str] = []
    if data.get("main") != MAIN:
        problems.append(
            f"{DECLARATION}: основным документом назван {data.get('main')!r}, а проверка "
            f"считает ячейки {MAIN}"
        )

    kinds = data.get("cell_kinds")
    declared_kinds: set[str] = set()
    if not isinstance(kinds, list) or not kinds:
        problems.append(f"{DECLARATION}: виды ячеек не объявлены — проверка не знает, что сверять")
    else:
        for item in kinds:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                problems.append(f"{DECLARATION}: вид ячейки объявлен не записью с именем")
                continue
            declared_kinds.add(item["name"])
            if not isinstance(item.get("about"), str) or not item["about"].strip():
                problems.append(
                    f"{DECLARATION}: у вида ячейки {item['name']} нет описания — по объявлению "
                    f"нечем понять границу"
                )
        unknown = sorted(declared_kinds - set(KINDS))
        if unknown:
            problems.append(
                f"{DECLARATION}: вид {', '.join(unknown)} не известен проверке (словарь "
                f"закрытый: {', '.join(KINDS)})"
            )
        unused_kinds = sorted(set(KINDS) - declared_kinds)
        if unused_kinds:
            problems.append(
                f"{DECLARATION}: вид {', '.join(unused_kinds)} есть в словаре проверки, но не "
                f"объявлен: словарь разошёлся с данными"
            )

    rule = data.get("parse_rule")
    if not isinstance(rule, dict):
        problems.append(f"{DECLARATION}: правило разбора ячейки не объявлено")
    else:
        for key in ("about", "cells", "header", "decimal", "comparison", "boundary"):
            if not isinstance(rule.get(key), str) or not rule.get(key).strip():
                problems.append(
                    f"{DECLARATION}: в правиле разбора не объявлено поле {key} — граница правила "
                    f"осталась бы непроверяемой"
                )

    baseline = data.get("baseline")
    if not isinstance(baseline, dict):
        problems.append(f"{DECLARATION}: перечень основного документа (baseline) не объявлен")
    else:
        for key in ("tables", "rows", "cells"):
            value = baseline.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                problems.append(
                    f"{DECLARATION}: baseline.{key} не объявлен целым неотрицательным числом"
                )
        per_table = baseline.get("rows_per_table")
        if not isinstance(per_table, list) or not all(
            isinstance(item, int) and not isinstance(item, bool) and item >= 0 for item in per_table
        ):
            problems.append(
                f"{DECLARATION}: baseline.rows_per_table не объявлен перечнем целых чисел — "
                f"пропавшую строку основного документа иначе не увидеть"
            )
        columns = baseline.get("columns")
        if not isinstance(columns, list) or not columns:
            problems.append(
                f"{DECLARATION}: baseline.columns не объявлен: вид каждой колонки — это решение, "
                f"и оно записывается данными"
            )
        else:
            for order, column in enumerate(columns, 1):
                if not isinstance(column, list) or not column:
                    problems.append(
                        f"{DECLARATION}: у таблицы №{order} виды колонок объявлены не перечнем"
                    )
                    continue
                for kind in column:
                    if kind not in declared_kinds:
                        problems.append(
                            f"{DECLARATION}: таблица №{order} объявляет вид колонки {kind!r}, "
                            f"которого нет в словаре видов"
                        )

    languages = data.get("languages")
    if not isinstance(languages, dict) or not languages:
        problems.append(f"{DECLARATION}: список языков пуст — проверять нечего")
    else:
        for language, entry in languages.items():
            if not isinstance(entry, dict):
                problems.append(f"{DECLARATION}: язык {language} объявлен не записью со строгостью")
                continue
            tolerance = entry.get("cell_tolerance")
            if not isinstance(tolerance, int) or isinstance(tolerance, bool) or tolerance < 0:
                problems.append(
                    f"{DECLARATION}: у языка {language} допуск cell_tolerance не объявлен целым "
                    f"неотрицательным числом"
                )
            exceptions = entry.get("exceptions")
            if not isinstance(exceptions, list):
                problems.append(
                    f"{DECLARATION}: у языка {language} не объявлен перечень исключений "
                    f"(пустой перечень — это `[]`, а не отсутствие поля)"
                )
                continue
            for item in exceptions:
                if not isinstance(item, dict):
                    problems.append(f"{DECLARATION}: исключение языка {language} объявлено не записью")
                    continue
                for key in ("table", "row", "column"):
                    value = item.get(key)
                    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                        problems.append(
                            f"{DECLARATION}: у исключения языка {language} не названо место "
                            f"({key}) — исключение без места станет забывчивостью"
                        )
                if not isinstance(item.get("main"), str) or not item["main"].strip():
                    problems.append(
                        f"{DECLARATION}: у исключения языка {language} не названа ячейка "
                        f"основного документа"
                    )
                if not isinstance(item.get("reason"), str) or not item["reason"].strip():
                    problems.append(
                        f"{DECLARATION}: у исключения языка {language} нет причины — исключение "
                        f"без причины становится забывчивостью"
                    )
    return (data if not problems else None), problems


def load_languages(root: Path) -> tuple[dict[str, str] | None, list[str]]:
    """Языки и файлы переводов — из объявления состояний, второй источник не нужен."""
    path = root / STATES
    if not path.is_file():
        return None, [f"нет {STATES}: перечень переводов брать не из чего"]
    try:
        data = json.loads(read(path))
    except json.JSONDecodeError as error:
        return None, [f"{STATES} не разобран: {error}"]
    states = data.get("states")
    if not isinstance(states, list) or not states:
        return None, [f"{STATES}: список состояний пуст — перечень переводов взять не из чего"]
    languages: dict[str, str] = {}
    problems: list[str] = []
    for state in states:
        if not isinstance(state, dict):
            continue
        language = state.get("language")
        declared = state.get("path")
        if not language or not declared:
            problems.append(f"{STATES}: запись перевода не называет язык или путь")
            continue
        if language in languages:
            problems.append(f"{STATES}: язык {language} объявлен дважды")
        languages[language] = declared
    return (languages if languages else None), problems


def resolve_source(root: Path, declared: str) -> Path:
    """Файл перевода: в живом дереве `community/…`, в поставке — в корне."""
    direct = root / declared
    if direct.is_file():
        return direct
    return root / Path(declared).name


def profile(path: Path) -> dict:
    """Таблицы файла с их ячейками и общие числа."""
    found = tables(read(path))
    result: list[dict] = []
    for order, table in enumerate(found, 1):
        rows = content_rows(table)
        width = max((len(cells(line)) for _, line in rows), default=0)
        separated = len(table["rows"]) > 1 and bool(SEPARATOR.match(table["rows"][1][1]))
        result.append({
            "order": order,
            "open": table["open"],
            "rows": rows,
            "header": rows[0] if rows else None,
            "data": rows[1:] if rows else [],
            "separated": separated,
            "width": width,
        })
    return {
        "path": path,
        "tables": result,
        "rows": sum(len(table["rows"]) for table in result),
        "cells": sum(sum(len(cells(line)) for _, line in table["rows"]) for table in result),
        "widths": [table["width"] for table in result],
    }


# --- свойства -----------------------------------------------------------------


def check_main(base: dict, declared: dict) -> bool:
    """Основной документ: перечень ячеек и виды колонок сверяются с объявлением."""
    baseline = declared["baseline"]
    measured = {"tables": len(base["tables"]), "rows": base["rows"], "cells": base["cells"]}
    diverged = [
        f"{key}: объявлено {baseline[key]}, измерено {measured[key]}"
        for key in ("tables", "rows", "cells")
        if baseline[key] != measured[key]
    ]
    expect(
        not diverged,
        f"{DECLARATION}: перечень ячеек основного документа разошёлся с объявленным "
        f"({'; '.join(diverged)}): добавленная или пропавшая ячейка README.md записывается "
        f"объявлением, а не остаётся незамеченной",
    )
    if not diverged:
        OBSERVED.append(
            f"{MAIN}: таблиц {measured['tables']}, содержательных строк {measured['rows']}, "
            f"ячеек {measured['cells']}, столбцов по таблицам {base['widths']} — как объявлено"
        )

    declared_rows = baseline["rows_per_table"]
    sizes = [len(table["rows"]) for table in base["tables"]]
    expect(
        declared_rows == sizes,
        f"{DECLARATION}: число строк по таблицам основного документа разошлось с объявленным "
        f"(объявлено {declared_rows}, измерено {sizes}): пропавшая строка таблицы видна только "
        f"так — остальные строки сдвигаются и выглядят как одна замена",
    )

    declared_columns = baseline["columns"]
    widths = [len(column) for column in declared_columns]
    expect(
        widths == base["widths"],
        f"{DECLARATION}: число столбцов по таблицам разошлось с измеренным (объявлено {widths}, "
        f"измерено {base['widths']}): вид колонки, которой нет, ничего не сторожит",
    )
    if widths != base["widths"]:
        # Зависимые свойства: с неверным числом столбцов виды ячеек не сверить.
        return False

    # Виды объявлены для КОЛОНКИ — значит, основной документ обязан им
    # соответствовать. Иначе «столбец число» окажется фразой, и проверка будет
    # сторожить не то.
    shaped = True
    for order, table in enumerate(base["tables"], 1):
        kinds = declared_columns[order - 1]
        for column, kind in enumerate(kinds, 1):
            wrong: list[str] = []
            # Строка заголовка не данные: её ячейки переводимы независимо от вида
            # колонки, поэтому вид проверяется только на строках данных.
            for number, line in table["data"]:
                row_cells = cells(line)
                if column > len(row_cells):
                    continue
                wrong += shape_problems(kind, row_cells[column - 1], f"{MAIN}:{number}")
            expect(
                not wrong,
                f"{DECLARATION}: таблица №{order}, столбец {column} объявлен видом {kind!r}, а "
                f"основной документ этому не соответствует — {wrong[0]}"
                if wrong else "",
            )
            if wrong:
                shaped = False
                break
    return shaped


def check_language(root: Path, language: str, declared_path: str, policy: dict,
                   declared: dict, base: dict) -> None:
    """Ячейки одного перевода против основного документа."""
    path = resolve_source(root, declared_path)
    if not path.is_file():
        fail(f"нет {declared_path} — перевод {language} объявлен, а файла нет: ячейки сверять не в чем")
        return
    other = profile(path)
    tolerance = int(policy.get("cell_tolerance") or 0)
    exceptions = policy.get("exceptions") or []
    columns = declared["baseline"]["columns"]

    if other["cells"] == 0:
        fail(
            f"{declared_path}: ни одной ячейки не найдено — «ноль осмотренных ячеек» это провал, "
            f"а не PASS"
        )
        return

    if len(other["tables"]) != len(base["tables"]):
        expect(
            False,
            f"{declared_path}: таблиц {len(other['tables'])}, а в {MAIN} — {len(base['tables'])}: "
            f"ячейки по этому файлу не сверяются — позиции таблиц съехали",
        )
        print(
            f"язык: {language} — таблиц: {len(other['tables'])}, строк: {other['rows']}, "
            f"ячеек: {other['cells']}, расхождений: 1"
        )
        return

    used: set[int] = set()
    findings: list[tuple[str, str]] = []

    for left, right in zip(base["tables"], other["tables"]):
        expected_rows = left["rows"]
        actual_rows = right["rows"]
        if len(expected_rows) != len(actual_rows):
            findings.append((
                f"{declared_path}:{right['open']}",
                f"в таблице №{right['order']} строк {len(actual_rows)}, а в {MAIN}:{left['open']} — "
                f"{len(expected_rows)}: строка таблицы пропала или добавилась; построчное "
                f"сравнение этой таблицы не выполняется — позиции строк съехали",
            ))
            continue

        kinds = columns[right["order"] - 1] if right["order"] - 1 < len(columns) else []

        for index, ((base_line, base_row), (here_line, here_row)) in enumerate(
                zip(expected_rows, actual_rows), 1):
            base_cells, here_cells = cells(base_row), cells(here_row)
            if len(base_cells) != len(here_cells):
                findings.append((
                    f"{declared_path}:{here_line}",
                    f"в таблице №{right['order']} ячеек {len(here_cells)}, а в {MAIN}:{base_line} — "
                    f"{len(base_cells)}: столбец пропал или добавился",
                ))
                continue
            for column, (base_cell, here_cell) in enumerate(zip(base_cells, here_cells), 1):
                # Заголовок переводится свободно: вид колонки относится к строкам данных.
                kind = "text" if index == 1 else (
                    kinds[column - 1] if column - 1 < len(kinds) else "text")
                problems = shape_problems(kind, here_cell, "")
                problems = problems or divergence(kind, base_cell, here_cell)
                if not problems:
                    continue
                allowance = None
                for position, item in enumerate(exceptions):
                    if (item.get("table") == right["order"] and item.get("row") == index
                            and item.get("column") == column):
                        allowance = position
                        break
                if allowance is not None:
                    declared_cell = exceptions[allowance].get("main")
                    if squash(declared_cell or "") != squash(base_cell):
                        findings.append((
                            f"{declared_path}:{here_line}",
                            f"исключение языка {language} описывает ячейку {declared_cell!r}, а в "
                            f"{MAIN}:{base_line} стоит {base_cell!r}: допуск выписан не на ту ячейку",
                        ))
                        continue
                    used.add(allowance)
                    OBSERVED.append(
                        f"{declared_path}:{here_line}: ячейка таблицы №{right['order']} (строка "
                        f"{index}, столбец {column}) расходится по объявленному исключению "
                        f"({exceptions[allowance].get('reason')})"
                    )
                    continue
                findings.append((
                    f"{declared_path}:{here_line}",
                    f"ячейка таблицы №{right['order']} (строка {index}, столбец {column}, вид "
                    f"{kind}) расходится с {MAIN}:{base_line}: {'; '.join(problems)} — в основном "
                    f"документе {base_cell!r}, в переводе {here_cell!r}",
                ))

    for position, item in enumerate(exceptions):
        if position in used:
            continue
        findings.append((
            DECLARATION,
            f"исключение языка {language} (таблица №{item.get('table')}, строка {item.get('row')}, "
            f"столбец {item.get('column')}) не использовано: ячейка перевода с ним не расходится — "
            f"объявленный допуск описывает прошлое и разрешает то, чего уже нет",
        ))

    for place, message in findings:
        expect(False, f"{place}: {message}")

    print(
        f"язык: {language} — таблиц: {len(other['tables'])}, строк: {other['rows']}, "
        f"ячеек: {other['cells']}, расхождений: {len(findings)}"
    )

    if len(findings) == 0:
        OBSERVED.append(
            f"язык {language}: {other['cells']} ячеек в {len(other['tables'])} таблицах "
            f"({other['rows']} строк) совпадают с {MAIN} по защищённой части (допуск {tolerance})"
        )
    elif len(findings) <= tolerance:
        OBSERVED.append(
            f"язык {language}: расхождений {len(findings)} при объявленном допуске {tolerance} — "
            f"в пределах строгости, названной данными"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Ячейки таблиц переводов README")
    parser.add_argument("--root", default=str(REPO), help="корень проверяемого дерева")
    args = parser.parse_args()
    root = Path(args.root)
    blocked = False

    declared, declaration_problems = load_declaration(root)
    for problem in declaration_problems:
        fail(problem)
    languages, language_problems = load_languages(root)
    for problem in language_problems:
        fail(problem)

    main_path = root / MAIN
    base: dict | None = None
    if not main_path.is_file():
        fail(f"нет {MAIN} — сверять ячейки не с чем")
        blocked = True
    else:
        base = profile(main_path)
        print(
            f"основной документ: {MAIN} — таблиц: {len(base['tables'])}, строк: {base['rows']}, "
            f"ячеек: {base['cells']}; столбцов по таблицам: "
            f"{', '.join(str(item) for item in base['widths'])}"
        )
        if base["cells"] == 0:
            fail(
                f"{MAIN}: ни одной ячейки не найдено — «ноль осмотренных ячеек» это провал, а не PASS"
            )
            blocked = True
        for order, table in enumerate(base["tables"], 1):
            if not table["separated"]:
                fail(
                    f"{MAIN}:{table['open']}: у таблицы №{order} нет строки-разделителя во второй "
                    f"строке — правило делит содержательные строки по ней, и без неё заголовок "
                    f"неотличим от данных"
                )

    shaped = True
    if declared is not None and base is not None and not blocked:
        shaped = check_main(base, declared)

    if declared is not None and languages is not None:
        declared_languages = declared["languages"]
        missing = sorted(set(languages) - set(declared_languages))
        extra = sorted(set(declared_languages) - set(languages))
        expect(
            not missing,
            f"{DECLARATION}: для языков {', '.join(missing)} перевод есть, а строгость не "
            f"объявлена: новый перевод остался бы непроверенным",
        )
        expect(
            not extra,
            f"{DECLARATION}: строгость объявлена для языков {', '.join(extra)}, а перевода на них "
            f"нет: объявление обещает проверку, которой нечего осматривать",
        )
        if not missing and not extra:
            OBSERVED.append(
                f"языки объявления и перевода совпадают: {', '.join(sorted(languages))} "
                f"({len(languages)})"
            )

    if declared is not None and languages is not None and base is not None and not blocked:
        for language, declared_path in languages.items():
            policy = declared["languages"].get(language)
            if not isinstance(policy, dict):
                continue  # уже названо находкой о самом объявлении
            check_language(root, language, declared_path, policy, declared, base)

    if EXAMINED == 0:
        fail("ни одного объекта не осмотрено: «ноль проверенного» — это не PASS")
    report()
    return 1 if FAILURES else 0


def report() -> None:
    print(
        "граница: сверяются защищённые части ячейки — обрамлённый код, адрес ссылки, числа "
        "(значением) и число разделителей <br>; переводимый текст, смысл ячейки и отрисовка "
        "таблицы GitHub не проверяются"
    )
    print(f"объявление ячеек таблиц: {DECLARATION}")
    for note in OBSERVED:
        print(f"наблюдение: {note}")
    for failure in FAILURES:
        print(f"FAIL: {failure}")
    print(f"осмотрено: {EXAMINED}")
    if FAILURES:
        print(f"нарушений: {len(FAILURES)}")


if __name__ == "__main__":
    sys.exit(main())
