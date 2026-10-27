#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Строки списков переводов README: те же ПУНКТЫ в том же порядке.

Задача, из которой проверка выросла (этап И32, 27.10.2026). К этому моменту
перевод был проверяем на восьми уровнях: состояние (`tools/check_translations.py`),
ссылки на файлы поставки, числа индекса, терминология (`tools/check_glossary.py`),
проза (`tools/check_prose_profile.py`), адреса
(`tools/check_document_addresses.py`), блоки команд
(`tools/check_command_blocks.py`, этап И30) и ячейки таблиц
(`tools/check_table_rows.py`, этап И31). У всех названа честная граница, но
последний слой той же природы оставался без сторожа: СТРОКИ СПИСКОВ.

Функция `section_profile` (`tools/check_translations.py`) считает строки
списков на каждый заголовок `##`, то есть список проверяется ЧИСЛОМ. Поэтому
перевод, где пункт списка ПРОПАЛ, а соседний пункт склеен с ним, проходил
зелёным: число пунктов в разделе при этом может совпасть. Замер 27.10.2026 до
правки: списков 4 в каждом из шести файлов, пунктов 22 (нумерованных 3), всего
по шести файлам 132 пункта; пунктов с обрамлённым кодом 8, с числом 3, со
ссылкой 11 — и все 11 ссылок это якоря оглавления.

Что считается пунктом и что в нём защищено (объявлено ДАННЫМИ —
`docs/03_implementation/lists.json`, а не выведено кодом):

* список — подряд идущие строки-пункты вне блоков ограждения; пустая строка,
  заголовок и строка обычного текста список заканчивают, блок ограждения тоже;
* пункт — строка, начинающаяся с маркера (`-`, `*`, `+` или число с точкой либо
  скобкой) и пробела, и непустого текста после него;
* ПЕРЕНОС: непустая строка с отступом сразу после пункта (или после его
  переноса) — продолжение этого пункта, а не новый пункт и не новый список;
  текст пункта склеивается одним пробелом. Именно поэтому пункт в переводе,
  разбитый на три строки вместо двух, находкой не является: сверяется пункт, а
  не разбиение;
* ВЛОЖЕННОСТЬ по отступу: пункт с отступом считается пунктом того же списка, а
  отступ сверяется по позиции — иначе «пункт стал вложенным» прошёл бы молча;
* внутри пункта защищены: обрамлённый код (ТОЧНО и ПО ПОРЯДКУ — по ним читатель
  копирует имя файла или операции), числа (ЗНАЧЕНИЕМ и НАБОРОМ с учётом
  кратности, потому что порядок чисел внутри пункта языки меняют законно) и
  число разделителей `<br>`; переводимый текст не сравнивается вовсе;
* ВИД пункта объявлен по списку: `text` — свободный текст с защищёнными частями,
  `anchor` — пункт оглавления, у которого ровно одна ссылка и её адрес есть
  внутренний якорь `#…`. Адрес якоря сверить с основным документом НЕЛЬЗЯ:
  заголовки переведены, и требовать равенства значило бы требовать
  непереведённого оглавления. Поэтому якоря объявлены по языку, и сверяется
  позиция — объявленный якорь обязан стоять на своём месте. Перестановка двух
  пунктов оглавления видна именно так, а куда якорь ведёт — предмет следующего
  слоя (внутренние ссылки на разделы), и эта проверка его не подменяет;
* вид маркера (маркированный или нумерованный) и номер нумерованного пункта
  сверяются: `2.` вместо `1.` — находка. Сам символ маркера (`-`, `*`, `+`) не
  сверяется: на отрисовку он не влияет, и требовать его равенства значило бы
  требовать косметики.

Границы, названные честно:

* **смысл и правильность пункта не проверяются**: перевод, который перевёл
  пункт неверно, пройдёт зелёным — сверяется только защищённая часть;
* отрисовка списка GitHub не воспроизводится: ширина отступа, вид маркера,
  «плотный» и «свободный» список не сравниваются, вложенность сверяется по
  объявленному правилу отступа;
* куда ведёт якорь оглавления (существует ли такой заголовок) НЕ проверяется:
  это следующий слой, названный в отчёте этапа;
* признак «да/нет» в защищённые части не входит: в списках README его нет
  (замер 27.10.2026: пунктов с признаком 0), и объявлять вид под случай, которого
  нет, значило бы описывать не тот документ;
* правило разбора проверено на всех 132 пунктах шести файлов: разъехавшихся
  пунктов нет — поэтому оно принято как рабочее.

Одна порча — одна находка. Пропавший или склеенный пункт — находка на СПИСОК
(позиции съехали, построчное сравнение не выполняется: иначе одна пропажа
давала бы столько находок, сколько пунктов ниже). Разошедшийся пункт при
совпавшем числе — тоже одна находка на список: она называет первое
разошедшееся место, оба текста и число разошедшихся позиций.

Строгость — ДАННЫЕ: `item_tolerance` (допуск на число расходящихся пунктов) и
перечень объявленных исключений с причиной, по языку. Исключение, объявленное и
не использованное, — находка: иначе перечень допусков превратился бы в описание
прошлого и разрешал бы то, чего уже нет.

Запуск:
    python tools/check_list_items.py [--root <дерево>]
Коды возврата: 0 — пункты каждого перевода совпадают с основным документом,
1 — есть расхождения (включая «ноль осмотренных пунктов»: это провал, а не
PASS), 2 — не прочитаны аргументы.
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
DECLARATION = "docs/03_implementation/lists.json"
STATES = "docs/03_implementation/translations.json"

FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
HEADING = re.compile(r"^\s{0,3}#{1,6}(\s|$)")
#: Пункт: отступ, маркер (`-`, `*`, `+` или число до 9 знаков с `.` либо `)`),
#: пробел после маркера и непустой текст. Строка без текста пунктом не считается.
ITEM = re.compile(r"^(?P<indent>[ \t]*)(?P<marker>[-*+]|\d{1,9}[.)])(?P<gap>[ \t]+)(?P<body>\S.*)$")
#: Число: разряды через пробел (в том числе неразрывный), дробная часть — `.` или `,`.
NUMBER = re.compile(r"(?<!\d)\d+(?:[ \u00a0]\d{3})*(?:[.,]\d+)?(?!\d)")
BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
#: Виды пунктов, известные проверке. Словарь закрытый: опечатка не создаёт вид.
KINDS = ("text", "anchor")

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


# --- разбор списков и пунктов -------------------------------------------------


def indent_width(text: str) -> int:
    """Ширина отступа: табуляция считается за четыре пробела."""
    return len(text.replace("\t", "    "))


def scan(text: str) -> list[dict]:
    """Списки файла: {open: номер первой строки, items: [...]} в порядке файла."""
    found: list[dict] = []
    current: dict | None = None
    previous: dict | None = None
    in_fence = False
    for number, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            in_fence = not in_fence
            current = None
            previous = None
            continue
        if in_fence:
            continue
        if not line.strip():
            current = None
            previous = None
            continue
        if HEADING.match(line):
            current = None
            previous = None
            continue
        match = ITEM.match(line)
        if match:
            marker = match.group("marker")
            if current is None:
                current = {"open": number, "items": []}
                found.append(current)
            item = {
                "line": number,
                "indent": indent_width(match.group("indent")),
                "marker": marker,
                "ordered": marker[0].isdigit(),
                "number": int(marker[:-1]) if marker[0].isdigit() else None,
                "body": match.group("body").rstrip(),
                "continuation": [],
            }
            current["items"].append(item)
            previous = item
            continue
        # Не пункт и не пустая строка: продолжение пункта — только с отступом.
        if previous is not None and line.startswith((" ", "\t")):
            previous["continuation"].append((number, line.strip()))
            continue
        current = None
        previous = None
    return found


def item_text(item: dict) -> str:
    """Текст пункта: тело и его переносы, склеенные одним пробелом."""
    parts = [item["body"]]
    for _, piece in item["continuation"]:
        if piece:
            parts.append(piece)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def spans(text: str) -> list[str]:
    """Обрамлённый код — по порядку."""
    return [item.strip() for item in re.findall(r"`([^`]+)`", text)]


def links(text: str) -> list[str]:
    """Адреса ссылок — по порядку."""
    return re.findall(r"\]\(([^)]*)\)", text)


def numbers(text: str) -> list[Decimal]:
    """Числа пункта, приведённые к значению; код и адреса ссылок не разбираются."""
    without_code = re.sub(r"`[^`]+`", " ", text)
    without_targets = re.sub(r"\]\([^)]*\)", " ", without_code)
    found: list[Decimal] = []
    for token in NUMBER.findall(without_targets):
        try:
            found.append(Decimal(token.replace(" ", "").replace("\u00a0", "").replace(",", ".")))
        except InvalidOperation:  # pragma: no cover — NUMBER таких строк не даёт
            continue
    return found


def describe(number: Decimal) -> str:
    return format(number.normalize(), "f")


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def profile(path: Path) -> dict:
    """Списки файла с их пунктами и общие числа."""
    groups = scan(read(path))
    result: list[dict] = []
    for order, group in enumerate(groups, 1):
        result.append({
            "order": order,
            "open": group["open"],
            "items": group["items"],
            "ordered": bool(group["items"]) and group["items"][0]["ordered"],
        })
    return {
        "path": path,
        "lists": result,
        "counts": [len(group["items"]) for group in result],
        "items": sum(len(group["items"]) for group in result),
        "ordered": sum(
            1 for group in result for item in group["items"] if item["ordered"]
        ),
    }


# --- сравнение ----------------------------------------------------------------


def shape_problems(kind: str, item: dict, where: str) -> list[str]:
    """Соответствие пункта своему виду: объявление не должно быть пустым словом."""
    prefix = f"{where}: " if where else ""
    text = item_text(item)
    problems: list[str] = []
    if not text:
        problems.append(f"{prefix}пункт пуст")
        return problems
    if kind == "anchor":
        found = links(text)
        if len(found) != 1:
            problems.append(f"{prefix}пункт объявлен якорем, а ссылок в нём {len(found)}")
        elif not found[0].startswith("#"):
            problems.append(
                f"{prefix}пункт объявлен якорем, а адрес ссылки {found[0]!r} не внутренний"
            )
        if spans(text):
            problems.append(
                f"{prefix}пункт объявлен якорем, но содержит обрамлённый код {spans(text)}"
            )
    return problems


def divergence(kind: str, base: dict, here: dict) -> dict[str, list[str]]:
    """Расхождения защищённой части пункта по семействам; пустой словарь — в допуске."""
    base_text, here_text = item_text(base), item_text(here)
    found: dict[str, list[str]] = {}
    if base["indent"] != here["indent"]:
        found["indent"] = [f"отступ {here['indent']} вместо {base['indent']}"]
    if base["ordered"] != here["ordered"]:
        found["marker"] = [
            f"вид маркера {'нумерованный' if here['ordered'] else 'маркированный'} вместо "
            f"{'нумерованный' if base['ordered'] else 'маркированный'}"
        ]
    elif base["ordered"] and base["number"] != here["number"]:
        found["marker"] = [f"номер {here['number']} вместо {base['number']}"]
    if kind == "anchor":
        # Адрес якоря сверяется с объявлением по языку, а не с основным документом.
        here_links = links(here_text)
        if len(here_links) != 1 or not here_links[0].startswith("#"):
            found["link"] = [f"адрес {here_links} не является одним внутренним якорем"]
        return found
    base_code, here_code = spans(base_text), spans(here_text)
    if base_code != here_code:
        found["code"] = [f"код {here_code} вместо {base_code}"]
    base_links, here_links = links(base_text), links(here_text)
    if base_links != here_links:
        found["link"] = [f"адрес {here_links} вместо {base_links}"]
    base_numbers, here_numbers = Counter(numbers(base_text)), Counter(numbers(here_text))
    if base_numbers != here_numbers:
        found["number"] = [
            f"числа {[describe(item) for item in sorted(here_numbers.elements())]} вместо "
            f"{[describe(item) for item in sorted(base_numbers.elements())]}"
        ]
    base_br, here_br = len(BR.findall(base_text)), len(BR.findall(here_text))
    if base_br != here_br:
        found["br"] = [f"разделителей <br> {here_br} вместо {base_br}"]
    return found


def anchor_problems(declared: list, position: int, here_text: str, where: str) -> list[str]:
    """Якорь пункта оглавления сверяется с объявленным по языку."""
    here_links = links(here_text)
    if len(here_links) != 1:
        return [f"{where}: адресов в пункте-якоре {len(here_links)}, а объявлено один"]
    if position > len(declared):
        return [
            f"{where}: в объявлении языка нет якоря для пункта №{position} — сверять не с чем"
        ]
    if here_links[0] != declared[position - 1]:
        return [
            f"{where}: якорь {here_links[0]!r}, а объявлен {declared[position - 1]!r} — "
            f"пункты оглавления стоят не в том порядке или переименованы без правки объявления"
        ]
    return []


# --- объявления ---------------------------------------------------------------


def load_declaration(root: Path) -> tuple[dict | None, list[str]]:
    """Правило разбора и строгость из данных. None — объявления нет или оно нечитаемо."""
    path = root / DECLARATION
    if not path.is_file():
        return None, [
            f"нет {DECLARATION} — что внутри пункта защищено и с какой строгостью это "
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
            f"считает пункты {MAIN}"
        )

    kinds = data.get("item_kinds")
    declared_kinds: set[str] = set()
    if not isinstance(kinds, list) or not kinds:
        problems.append(f"{DECLARATION}: виды пунктов не объявлены — проверка не знает, что сверять")
    else:
        for item in kinds:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                problems.append(f"{DECLARATION}: вид пункта объявлен не записью с именем")
                continue
            declared_kinds.add(item["name"])
            if not isinstance(item.get("about"), str) or not item["about"].strip():
                problems.append(
                    f"{DECLARATION}: у вида пункта {item['name']} нет описания — по объявлению "
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
        problems.append(f"{DECLARATION}: правило разбора пункта не объявлено")
    else:
        for key in ("about", "items", "continuation", "nested", "ordered", "protected",
                    "comparison", "boundary"):
            if not isinstance(rule.get(key), str) or not rule.get(key).strip():
                problems.append(
                    f"{DECLARATION}: в правиле разбора не объявлено поле {key} — граница правила "
                    f"осталась бы непроверяемой"
                )

    anchor_rule = data.get("anchor_rule")
    if not isinstance(anchor_rule, dict):
        problems.append(
            f"{DECLARATION}: правило якоря оглавления не объявлено — почему адрес якоря "
            f"сверяется по языку, а не с основным документом, осталось бы непроверяемым"
        )
    else:
        for key in ("about", "comparison", "boundary"):
            if not isinstance(anchor_rule.get(key), str) or not anchor_rule[key].strip():
                problems.append(
                    f"{DECLARATION}: в правиле якоря не объявлено поле {key} — граница правила "
                    f"осталась бы непроверяемой"
                )

    lists = data.get("lists")
    if not isinstance(lists, list) or not lists:
        problems.append(f"{DECLARATION}: перечень списков не объявлен — вид каждого списка не назван")
    else:
        for order, entry in enumerate(lists, 1):
            if not isinstance(entry, dict):
                problems.append(f"{DECLARATION}: список №{order} объявлен не записью")
                continue
            kind = entry.get("kind")
            if kind not in declared_kinds:
                problems.append(
                    f"{DECLARATION}: список №{order} объявлен видом {kind!r}, которого нет в "
                    f"словаре видов"
                )
            count = entry.get("items")
            if not isinstance(count, int) or isinstance(count, bool) or count < 1:
                problems.append(
                    f"{DECLARATION}: у списка №{order} не объявлено целое число пунктов — "
                    f"пропавший пункт основного документа иначе не увидеть"
                )

    baseline = data.get("baseline")
    if not isinstance(baseline, dict):
        problems.append(f"{DECLARATION}: перечень основного документа (baseline) не объявлен")
    else:
        for key in ("lists", "items", "ordered"):
            value = baseline.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                problems.append(
                    f"{DECLARATION}: baseline.{key} не объявлен целым неотрицательным числом"
                )
        per_list = baseline.get("items_per_list")
        if not isinstance(per_list, list) or not all(
            isinstance(item, int) and not isinstance(item, bool) and item >= 0 for item in per_list
        ):
            problems.append(
                f"{DECLARATION}: baseline.items_per_list не объявлен перечнем целых чисел — "
                f"пропавший пункт основного документа иначе не увидеть"
            )

    languages = data.get("languages")
    if not isinstance(languages, dict) or not languages:
        problems.append(f"{DECLARATION}: список языков пуст — проверять нечего")
    else:
        for language, entry in languages.items():
            if not isinstance(entry, dict):
                problems.append(f"{DECLARATION}: язык {language} объявлен не записью со строгостью")
                continue
            tolerance = entry.get("item_tolerance")
            if not isinstance(tolerance, int) or isinstance(tolerance, bool) or tolerance < 0:
                problems.append(
                    f"{DECLARATION}: у языка {language} допуск item_tolerance не объявлен целым "
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
                for key in ("list", "item"):
                    value = item.get(key)
                    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                        problems.append(
                            f"{DECLARATION}: у исключения языка {language} не названо место "
                            f"({key}) — исключение без места станет забывчивостью"
                        )
                if not isinstance(item.get("part"), str) or not item["part"].strip():
                    problems.append(
                        f"{DECLARATION}: у исключения языка {language} не названа часть пункта — "
                        f"исключение «на весь пункт» разрешило бы любое расхождение в нём"
                    )
                if not isinstance(item.get("main"), str) or not item["main"].strip():
                    problems.append(
                        f"{DECLARATION}: у исключения языка {language} не назван фрагмент пункта "
                        f"основного документа"
                    )
                if not isinstance(item.get("reason"), str) or not item["reason"].strip():
                    problems.append(
                        f"{DECLARATION}: у исключения языка {language} нет причины — исключение "
                        f"без причины становится забывчивостью"
                    )
            anchors = entry.get("anchors")
            if not isinstance(anchors, list) or not all(isinstance(one, str) for one in anchors):
                problems.append(
                    f"{DECLARATION}: у языка {language} якоря оглавления не объявлены перечнем "
                    f"строк — перестановку пунктов оглавления иначе не увидеть"
                )
                continue
            if len(set(anchors)) != len(anchors):
                problems.append(
                    f"{DECLARATION}: у языка {language} один и тот же якорь объявлен дважды — "
                    f"два пункта оглавления вели бы в одно место"
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


# --- свойства -----------------------------------------------------------------


def check_main(base: dict, declared: dict) -> bool:
    """Основной документ: перечень пунктов и вид каждого списка сверяются с объявлением."""
    baseline = declared["baseline"]
    measured = {"lists": len(base["lists"]), "items": base["items"], "ordered": base["ordered"]}
    diverged = [
        f"{key}: объявлено {baseline[key]}, измерено {measured[key]}"
        for key in ("lists", "items", "ordered")
        if baseline[key] != measured[key]
    ]
    expect(
        not diverged,
        f"{DECLARATION}: перечень пунктов основного документа разошёлся с объявленным "
        f"({'; '.join(diverged)}): добавленный или пропавший пункт README.md записывается "
        f"объявлением, а не остаётся незамеченным",
    )
    if not diverged:
        OBSERVED.append(
            f"{MAIN}: списков {measured['lists']}, пунктов {measured['items']} "
            f"(нумерованных {measured['ordered']}), пунктов по спискам {base['counts']} — как объявлено"
        )

    declared_counts = baseline["items_per_list"]
    expect(
        declared_counts == base["counts"],
        f"{DECLARATION}: число пунктов по спискам основного документа разошлось с объявленным "
        f"(объявлено {declared_counts}, измерено {base['counts']}): пропавший пункт виден только "
        f"так — остальные пункты сдвигаются и выглядят как одна замена",
    )

    declared_lists = declared["lists"]
    if len(declared_lists) != len(base["lists"]):
        expect(
            False,
            f"{DECLARATION}: перечень объявляет {len(declared_lists)} списков, а в {MAIN} их "
            f"{len(base['lists'])}: у нового списка не объявлен вид, и пункты его не сверяются",
        )
        return False

    shaped = True
    for order, (entry, group) in enumerate(zip(declared_lists, base["lists"]), 1):
        expect(
            entry.get("items") == len(group["items"]),
            f"{DECLARATION}: у списка №{order} объявлено пунктов {entry.get('items')}, а "
            f"измерено {len(group['items'])} ({MAIN}:{group['open']}): вид списка объявлен под "
            f"другое число пунктов",
        )
        kind = entry.get("kind")
        if kind not in KINDS:
            shaped = False
            continue
        wrong: list[str] = []
        for position, item in enumerate(group["items"], 1):
            problems = shape_problems(kind, item, f"{MAIN}:{item['line']}")
            if problems:
                wrong += problems
                break
        expect(
            not wrong,
            f"{DECLARATION}: список №{order} объявлен видом {kind!r}, а основной документ этому "
            f"не соответствует — {wrong[0]}"
            if wrong else "",
        )
        if wrong:
            shaped = False
    return shaped


def check_language(root: Path, language: str, declared_path: str, policy: dict,
                   declared: dict, base: dict) -> None:
    """Пункты одного перевода против основного документа."""
    path = resolve_source(root, declared_path)
    if not path.is_file():
        fail(f"нет {declared_path} — перевод {language} объявлен, а файла нет: пункты сверять не в чем")
        return
    other = profile(path)
    tolerance = int(policy.get("item_tolerance") or 0)
    exceptions = policy.get("exceptions") or []
    anchors = policy.get("anchors") or []
    declared_lists = declared["lists"]

    if other["items"] == 0:
        fail(
            f"{declared_path}: ни одного пункта не найдено — «ноль осмотренных пунктов» это "
            f"провал, а не PASS"
        )
        return

    if len(other["lists"]) != len(base["lists"]):
        expect(
            False,
            f"{declared_path}: списков {len(other['lists'])}, а в {MAIN} — {len(base['lists'])}: "
            f"пункты по этому файлу не сверяются — позиции списков съехали",
        )
        print(
            f"язык: {language} — списков: {len(other['lists'])}, пунктов: {other['items']}, "
            f"расхождений: 1"
        )
        return

    used: set[int] = set()
    findings: list[tuple[str, str]] = []

    for base_group, here_group in zip(base["lists"], other["lists"]):
        order = here_group["order"]
        entry = declared_lists[order - 1] if order - 1 < len(declared_lists) else {}
        kind = entry.get("kind") if entry.get("kind") in KINDS else "text"
        expected, actual = base_group["items"], here_group["items"]

        if len(expected) != len(actual):
            findings.append((
                f"{declared_path}:{here_group['open']}",
                f"в списке №{order} пунктов {len(actual)}, а в {MAIN}:{base_group['open']} — "
                f"{len(expected)}: пункт пропал, добавился или склеился с соседним; сверка по "
                f"позициям этого списка не выполняется — позиции пунктов съехали",
            ))
            continue

        diverged: list[tuple[int, dict, dict, dict[str, list[str]]]] = []
        for position, (base_item, here_item) in enumerate(zip(expected, actual), 1):
            problems = divergence(kind, base_item, here_item)
            if kind == "anchor":
                anchor = anchor_problems(anchors, position, item_text(here_item),
                                         f"{declared_path}:{here_item['line']}")
                if anchor:
                    problems.setdefault("link", []).extend(anchor)
            if not problems:
                continue
            remaining: dict[str, list[str]] = {}
            for part, messages in problems.items():
                allowance = None
                for index, item in enumerate(exceptions):
                    if (item.get("list") == order and item.get("item") == position
                            and item.get("part") == part):
                        allowance = index
                        break
                if allowance is None:
                    remaining[part] = messages
                    continue
                fragment = exceptions[allowance].get("main") or ""
                if fragment not in item_text(base_item):
                    remaining[part] = messages + [
                        f"исключение языка {language} описывает фрагмент {fragment!r}, которого в "
                        f"{MAIN}:{base_item['line']} нет — допуск выписан не на ту часть"
                    ]
                    continue
                used.add(allowance)
                OBSERVED.append(
                    f"{declared_path}:{here_item['line']}: часть пункта {part} списка №{order} "
                    f"(пункт {position}) расходится по объявленному исключению "
                    f"({exceptions[allowance].get('reason')})"
                )
            if remaining:
                diverged.append((position, base_item, here_item, remaining))

        if diverged:
            position, base_item, here_item, remaining = diverged[0]
            detail = "; ".join(
                f"{part}: {' '.join(messages)}" for part, messages in sorted(remaining.items())
            )
            findings.append((
                f"{declared_path}:{here_item['line']}",
                f"список №{order} (вид {kind}), первое расхождение — пункт {position}: {detail} "
                f"— в основном документе {MAIN}:{base_item['line']} {item_text(base_item)!r}, "
                f"в переводе {item_text(here_item)!r}; разошедшихся пунктов {len(diverged)} из "
                f"{len(actual)}",
            ))

    for position, item in enumerate(exceptions):
        if position in used:
            continue
        findings.append((
            DECLARATION,
            f"исключение языка {language} (список №{item.get('list')}, пункт {item.get('item')}, "
            f"часть {item.get('part')}) не использовано: пункт перевода с ним не расходится — "
            f"объявленный допуск описывает прошлое и разрешает то, чего уже нет",
        ))

    for place, message in findings:
        expect(False, f"{place}: {message}")

    print(
        f"язык: {language} — списков: {len(other['lists'])}, пунктов: {other['items']}, "
        f"расхождений: {len(findings)}"
    )

    if len(findings) == 0:
        OBSERVED.append(
            f"язык {language}: {other['items']} пунктов в {len(other['lists'])} списках совпадают "
            f"с {MAIN} по позициям и защищённой части (допуск {tolerance})"
        )
    elif len(findings) <= tolerance:
        OBSERVED.append(
            f"язык {language}: расхождений {len(findings)} при объявленном допуске {tolerance} — "
            f"в пределах строгости, названной данными"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Строки списков переводов README")
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
        fail(f"нет {MAIN} — сверять пункты не с чем")
        blocked = True
    else:
        base = profile(main_path)
        print(
            f"основной документ: {MAIN} — списков: {len(base['lists'])}, пунктов: {base['items']} "
            f"(нумерованных {base['ordered']}); пунктов по спискам: "
            f"{', '.join(str(item) for item in base['counts'])}"
        )
        if base["items"] == 0:
            fail(f"{MAIN}: ни одного пункта не найдено — «ноль осмотренных пунктов» это провал, а не PASS")
            blocked = True
        for order, group in enumerate(base["lists"], 1):
            kind = "—"
            if declared is not None and order - 1 < len(declared["lists"]):
                kind = declared["lists"][order - 1].get("kind") or "—"
            print(f"список {order}: вид {kind}, пунктов {len(group['items'])}, строка {group['open']}")

    if declared is not None and base is not None and not blocked:
        check_main(base, declared)

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

        # Число объявленных якорей сверяется с числом пунктов списка-оглавления:
        # объявление, где якорей меньше, разрешало бы части пунктов не сверяться.
        anchor_lists = [
            index for index, entry in enumerate(declared["lists"], 1)
            if isinstance(entry, dict) and entry.get("kind") == "anchor"
        ]
        anchor_items = sum(
            declared["lists"][index - 1].get("items") or 0 for index in anchor_lists
        )
        for language, entry in declared_languages.items():
            if not isinstance(entry, dict):
                continue
            count = len(entry.get("anchors") or [])
            expect(
                count == anchor_items,
                f"{DECLARATION}: у языка {language} объявлено якорей {count}, а пунктов в списках "
                f"вида anchor — {anchor_items}: остальные пункты оглавления не сверялись бы",
            )
        if anchor_lists:
            OBSERVED.append(
                f"якоря оглавления: списков вида anchor {len(anchor_lists)}, пунктов {anchor_items}, "
                f"объявлены по языку ({len(declared_languages)})"
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
        "граница: сверяются позиции пунктов, отступ, вид и номер маркера, обрамлённый код, "
        "адрес ссылки, числа (значением) и число разделителей <br>; смысл пункта, отрисовка "
        "списка GitHub и то, куда ведёт якорь оглавления, не проверяются"
    )
    print(f"объявление пунктов списков: {DECLARATION}")
    for note in OBSERVED:
        print(f"наблюдение: {note}")
    for failure in FAILURES:
        print(f"FAIL: {failure}")
    print(f"осмотрено: {EXAMINED}")
    if FAILURES:
        print(f"нарушений: {len(FAILURES)}")


if __name__ == "__main__":
    sys.exit(main())
