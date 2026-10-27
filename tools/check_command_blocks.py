#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Блоки команд переводов README: та же КОМАНДА, а не только её число.

Задача, из которой проверка выросла (этап И30, 27.10.2026). К этому моменту
перевод подтверждался шестью способами: состоянием (`tools/check_translations.py`:
признак неполноты, шапка, равенство числа разделов, профиль раздела), ссылками на
файлы поставки, числами индекса, терминологией (`tools/check_glossary.py`),
прозой (`tools/check_prose_profile.py`) и адресами
(`tools/check_document_addresses.py`). У всех шести граница названа честно —
смысл фраз не проверяется, — но внутри блоков команд оставался слой, который
поддаётся проверке ПОЛНОСТЬЮ и который не сторожил никто: САМА КОМАНДА.

Функция `section_profile` (`tools/check_translations.py`, строка 193) считает
блоки команд и строки таблиц на каждый заголовок `##`, то есть блок проверяется
ЧИСЛОМ. Поэтому перевод, где в команде `ragraf -p <config> query search
"reindex latency"` строка запроса переведена, а подстановка `-p <config>`
заменена на `-p <конфиг>`, проходил зелёным: блок на месте, строк столько же.
Замер 27.10.2026 до правки: 15 расхождений в трёх переводах — русский 13
(тринадцать строк с `-p <конфиг>`, в одной из них ещё и переведённый аргумент
запроса), немецкий 1 (`"Verzögerung der Neuindizierung"`), испанский 1
(`"latencia de reindexación"`); хинди и китайский 0. Свойство СЕГОДНЯ не
выполнялось, и заметить это было нечем.

Что считается командой (объявлено ДАННЫМИ — `docs/03_implementation/commands.json`,
а не выведено кодом):

* строка, начинающаяся с `#` после ведущих пробелов, — КОММЕНТАРИЙ: она
  переводится свободно и в сравнение не входит. Комментарии внутри блоков
  переводить можно и нужно — они не часть команды;
* всякая другая непустая строка — КОМАНДА, но сравнивается её часть до первого
  `#`, которому предшествует пробел: хвостовой комментарий
  (`ragraf -p <config> status   # состояние: ревизия`) переводится вместе с
  остальным текстом и находкой не считается;
* пустые строки не сравниваются: они разделяют примеры, их число сторожит
  профиль разделов в `tools/check_translations.py`;
* строка, оканчивающаяся обратным слешем, — продолжение: склеивается со следующей
  командной строкой и сравнивается как ОДНА команда, находка называется по номеру
  первой строки;
* блок разбирается только по языку ограждения, объявленному в `fence_languages`
  (`powershell`, `bash`, `json`). Блок с другим языком ограждения — находка:
  иначе новый пример кода в README.md остался бы вне осмотра.

Границы, названные честно:

* **смысл команды и её выполнимость не проверяются** — запуска команд нет.
  Проверяется совпадение ТЕКСТА: команда, которая в основном документе неверна,
  пройдёт зелёным, если перевод повторил её символ в символ;
* правило разбора повторено по виду документов, а не выведено из разметки:
  обратные кавычки внутри строки командой не считаются, `#` без пробела перед ним
  (`C:\\work\\my#project`) комментарием не считается, заголовок внутри блока не
  распознаётся;
* сравнение идёт по точному тексту, поэтому законная правка основного документа
  (например `-p <config>` → `--project <config>`) сделает расходящимися все
  переводы сразу — это и есть свойство: команда перевода обязана быть тем же
  текстом, а не похожим;
* число команд внутри блока сравнивается тоже (иначе пропавшая строка выглядела бы
  как сдвиг остальных), но позиционное сравнение по такому блоку не выполняется:
  позиции съехали, и вторая находка была бы счётом копий одного дефекта.

Одна порча — одна находка. Если в блоке разошлось число команд, находка одна на
блок; если разошёлся текст одной команды, находка одна на эту команду, с номером
строки файла и обоими текстами.

Строгость — ДАННЫЕ: `command_tolerance` (допуск на число расходящихся команд) и
перечень объявленных исключений с причиной, по языку. Исключение, объявленное и не
использованное, — находка: иначе перечень допусков превратился бы в описание
прошлого и разрешал бы то, чего уже нет.

Запуск:
    python tools/check_command_blocks.py [--root <дерево>]
Коды возврата: 0 — команды каждого перевода совпадают с основным документом,
1 — есть расхождения (включая «ноль осмотренных команд»: это провал, а не PASS),
2 — не прочитаны аргументы.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MAIN = "README.md"
DECLARATION = "docs/03_implementation/commands.json"
STATES = "docs/03_implementation/translations.json"

FENCE = re.compile(r"^\s*(```|~~~)\s*([A-Za-z0-9_+-]*)\s*$")
#: Хвостовой комментарий: `#`, которому предшествует пробел (или начало строки).
TAIL_COMMENT = re.compile(r"(?:^|\s)#")
#: Признак продолжения строки команды.
CONTINUATION = "\\"

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


# --- разбор блоков команд -----------------------------------------------------


def blocks(text: str) -> list[dict]:
    """Блоки ограждения файла: [(порядковый номер, строка открытия, язык, строки)]."""
    found: list[dict] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        match = FENCE.match(lines[index])
        if not match:
            index += 1
            continue
        marker = match.group(1)
        language = (match.group(2) or "").lower()
        order = len(found) + 1
        open_line = index + 1
        body: list[tuple[int, str]] = []
        index += 1
        while index < len(lines):
            close = FENCE.match(lines[index])
            if close and close.group(1) == marker and not close.group(2):
                break
            body.append((index + 1, lines[index]))
            index += 1
        found.append({"order": order, "open": open_line, "language": language, "body": body})
        index += 1
    return found


def split_block(body: list[tuple[int, str]]) -> dict:
    """Разбить блок на команды, комментарии и пустые строки по объявленному правилу."""
    commands: list[tuple[int, str]] = []
    comments: list[tuple[int, str]] = []
    blank = 0
    pending = ""
    pending_line = 0
    for number, text in body:
        stripped = text.strip()
        if not stripped:
            blank += 1
            continue
        if stripped.startswith("#"):
            comments.append((number, stripped))
            continue
        match = TAIL_COMMENT.search(text)
        piece = (text[: match.start()] if match else text).rstrip()
        if pending:
            piece = pending + " " + piece.strip()
            number = pending_line
        if piece.endswith(CONTINUATION):
            pending = piece[: -len(CONTINUATION)].rstrip()
            pending_line = number
            continue
        pending = ""
        commands.append((number, piece.strip()))
    if pending:
        commands.append((pending_line, pending.strip()))
    return {"commands": commands, "comments": comments, "blank": blank}


def profile(path: Path) -> dict:
    """Профиль файла: блоки, их команды и общие числа."""
    text = read(path)
    found = blocks(text)
    result: list[dict] = []
    for block in found:
        parts = split_block(block["body"])
        result.append({
            "order": block["order"],
            "open": block["open"],
            "language": block["language"],
            "lines": len(block["body"]),
            "commands": parts["commands"],
            "comments": parts["comments"],
            "blank": parts["blank"],
        })
    return {
        "path": path,
        "blocks": result,
        "lines": sum(block["lines"] for block in result),
        "commands": sum(len(block["commands"]) for block in result),
        "comments": sum(len(block["comments"]) for block in result),
        "blank": sum(block["blank"] for block in result),
        "dialects": sorted({block["language"] for block in result}),
    }


def where(lines: list[int] | None) -> str:
    """Строка файла или их перечень — для текста находки."""
    if not lines:
        return "нет строки"
    if len(lines) == 1:
        return f"строка {lines[0]}"
    return "строки " + ", ".join(str(item) for item in lines)


# --- объявления ---------------------------------------------------------------


def load_declaration(root: Path) -> tuple[dict | None, list[str]]:
    """Строгость из данных. None — объявления нет или оно не читается."""
    path = root / DECLARATION
    if not path.is_file():
        return None, [
            f"нет {DECLARATION} — что считается командой и с какой строгостью её "
            f"требовать, не объявлено (строгость в коде запрещена: иначе языковое "
            f"различие нельзя назвать числом, не правя проверку)"
        ]
    try:
        data = json.loads(read(path))
    except json.JSONDecodeError as error:
        return None, [f"{DECLARATION} не разобран: {error}"]

    problems: list[str] = []
    if data.get("main") != MAIN:
        problems.append(
            f"{DECLARATION}: основным документом назван {data.get('main')!r}, "
            f"а проверка считает команды {MAIN}"
        )

    dialects = data.get("fence_languages")
    declared_dialects = (
        {item.get("name") for item in dialects if isinstance(item, dict)}
        if isinstance(dialects, list) else set()
    )
    if not declared_dialects:
        problems.append(
            f"{DECLARATION}: языки ограждения не объявлены — проверка не знает, "
            f"какой блок считать командным"
        )

    rule = data.get("parse_rule")
    if not isinstance(rule, dict):
        problems.append(f"{DECLARATION}: правило разбора командной строки не объявлено")
    else:
        for key in ("about", "continuation", "boundary"):
            if not isinstance(rule.get(key), str) or not rule.get(key).strip():
                problems.append(
                    f"{DECLARATION}: в правиле разбора не объявлено поле {key} — "
                    f"граница правила осталась бы непроверяемой"
                )

    baseline = data.get("baseline")
    if not isinstance(baseline, dict):
        problems.append(f"{DECLARATION}: перечень основного документа (baseline) не объявлен")
    else:
        for key in ("blocks", "lines", "comments", "blank", "commands"):
            value = baseline.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                problems.append(f"{DECLARATION}: baseline.{key} не объявлен целым неотрицательным числом")
        per_block = baseline.get("commands_per_block")
        if not isinstance(per_block, list) or not all(
            isinstance(item, int) and not isinstance(item, bool) and item >= 0 for item in per_block
        ):
            problems.append(
                f"{DECLARATION}: baseline.commands_per_block не объявлен перечнем целых чисел — "
                f"пропавшую команду основного документа иначе не увидеть"
            )
        listed = baseline.get("languages")
        if not isinstance(listed, list) or not listed:
            problems.append(f"{DECLARATION}: baseline.languages не объявлен перечнем языков ограждения")

    languages = data.get("languages")
    if not isinstance(languages, dict) or not languages:
        problems.append(f"{DECLARATION}: список языков пуст — проверять нечего")
    else:
        for language, entry in languages.items():
            if not isinstance(entry, dict):
                problems.append(f"{DECLARATION}: язык {language} объявлен не записью со строгостью")
                continue
            tolerance = entry.get("command_tolerance")
            if not isinstance(tolerance, int) or isinstance(tolerance, bool) or tolerance < 0:
                problems.append(
                    f"{DECLARATION}: у языка {language} допуск command_tolerance не объявлен "
                    f"целым неотрицательным числом"
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
                if not isinstance(item.get("block"), int) or isinstance(item.get("block"), bool):
                    problems.append(
                        f"{DECLARATION}: у исключения языка {language} не назван номер блока"
                    )
                if not isinstance(item.get("main"), str) or not item.get("main").strip():
                    problems.append(
                        f"{DECLARATION}: у исключения языка {language} не названа команда "
                        f"основного документа"
                    )
                if not isinstance(item.get("reason"), str) or not item.get("reason").strip():
                    problems.append(
                        f"{DECLARATION}: у исключения языка {language} нет причины — "
                        f"исключение без причины становится забывчивостью"
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


def check_main(base: dict, declared: dict) -> None:
    """Основной документ: его перечень команд сверяется с объявлением."""
    baseline = declared["baseline"]
    measured = {
        "blocks": len(base["blocks"]),
        "lines": base["lines"],
        "comments": base["comments"],
        "blank": base["blank"],
        "commands": base["commands"],
    }
    diverged = [
        f"{key}: объявлено {baseline[key]}, измерено {measured[key]}"
        for key in ("blocks", "lines", "comments", "blank", "commands")
        if baseline[key] != measured[key]
    ]
    expect(
        not diverged,
        f"{DECLARATION}: перечень команд основного документа разошёлся с объявленным "
        f"({'; '.join(diverged)}): добавленная или пропавшая команда README.md "
        f"записывается объявлением, а не остаётся незамеченной",
    )
    if not diverged:
        expect(
            True,
            "",
            ok_note=(
                f"{MAIN}: блоков {measured['blocks']}, строк {measured['lines']}, "
                f"из них комментариев {measured['comments']} и пустых {measured['blank']}, "
                f"команд {measured['commands']} — как объявлено"
            ),
        )

    per_block = baseline["commands_per_block"]
    sizes = [len(block["commands"]) for block in base["blocks"]]
    expect(
        len(per_block) == len(sizes) and per_block == sizes,
        f"{DECLARATION}: число команд по блокам основного документа разошлось с объявленным "
        f"(объявлено {per_block}, измерено {sizes}): пропавшая строка команды видна "
        f"только так — остальные команды блока сдвигаются и выглядят как одна замена"
        if per_block != sizes
        else "",
        ok_note=f"{MAIN}: команд по блокам {sizes} — как объявлено",
    )

    declared_dialects = {item["name"] for item in declared["fence_languages"]}
    measured_dialects = set(base["dialects"])
    unknown = sorted(measured_dialects - declared_dialects)
    expect(
        not unknown,
        f"{MAIN}: блок с языком ограждения {', '.join(unknown)} не объявлен в "
        f"{DECLARATION} — пример кода в нём не осматривается вовсе",
        ok_note=f"{MAIN}: языки ограждения {', '.join(sorted(measured_dialects))} — все объявлены",
    )
    if unknown:
        # Зависимые свойства: необъявленный язык уже назван, и «объявленный и не
        # встреченный» и «перечень baseline разошёлся» — счёт копий того же дефекта.
        return
    unused = sorted(declared_dialects - measured_dialects)
    expect(
        not unused,
        f"{DECLARATION}: язык ограждения {', '.join(unused)} объявлен, но блока с ним в "
        f"{MAIN} нет: объявление обещает осмотр, которого нечего делать",
    )
    listed = set(baseline["languages"])
    expect(
        listed == measured_dialects,
        f"{DECLARATION}: baseline.languages {sorted(listed)} разошёлся с измеренными "
        f"{sorted(measured_dialects)}: перечень основного документа описывает не тот файл",
    )


def check_language(root: Path, language: str, declared_path: str, policy: dict,
                   base: dict) -> None:
    """Команды одного перевода против основного документа."""
    path = resolve_source(root, declared_path)
    if not path.is_file():
        fail(f"нет {declared_path} — перевод {language} объявлен, а файла нет: команды сверять не в чем")
        return
    other = profile(path)
    tolerance = int(policy.get("command_tolerance") or 0)

    print(f"язык: {language} — блоков: {len(other['blocks'])}, строк: {other['lines']}, "
          f"комментариев: {other['comments']}, команд: {other['commands']}")

    if other["commands"] == 0:
        fail(f"{declared_path}: ни одной команды не найдено — «ноль осмотренных команд» "
             f"это провал, а не PASS")
        return

    if len(other["blocks"]) != len(base["blocks"]):
        expect(
            False,
            f"{declared_path}: блоков команд {len(other['blocks'])}, а в {MAIN} — "
            f"{len(base['blocks'])} (допуск {tolerance}): команды внутри блоков по этому "
            f"файлу не сверяются — позиции блоков съехали",
        )
        return

    exceptions = policy.get("exceptions") or []
    used: set[int] = set()
    findings = 0

    for left, right in zip(base["blocks"], other["blocks"]):
        expected = left["commands"]
        actual = right["commands"]
        if len(expected) != len(actual):
            findings += 1
            expect(
                False,
                f"{declared_path}:{right['open']}: в блоке №{right['order']} команд "
                f"{len(actual)}, а в {MAIN}:{left['open']} — {len(expected)}: строка команды "
                f"пропала или добавилась; построчное сравнение этого блока не выполняется — "
                f"позиции съехали",
            )
            continue

        for number in range(len(expected)):
            base_line, base_text = expected[number]
            here_line, here_text = actual[number]
            if base_text == here_text:
                continue
            allowance = None
            for index, item in enumerate(exceptions):
                if item.get("block") == right["order"] and item.get("main") == base_text:
                    allowance = index
                    break
            if allowance is not None:
                used.add(allowance)
                OBSERVED.append(
                    f"{declared_path}:{here_line}: команда блока №{right['order']} расходится "
                    f"по объявленному исключению ({exceptions[allowance].get('reason')})"
                )
                continue
            findings += 1
            expect(
                False,
                f"{declared_path}:{here_line}: команда блока №{right['order']} расходится с "
                f"{MAIN}:{base_line}: в переводе {here_text!r}, в основном документе "
                f"{base_text!r} — команда перевода обязана быть тем же текстом "
                f"(переводится только комментарий после `#`)",
            )

    for index, item in enumerate(exceptions):
        if index in used:
            continue
        findings += 1
        expect(
            False,
            f"{DECLARATION}: исключение языка {language} не использовано — ни одна команда "
            f"блока №{item.get('block')} не расходится с {item.get('main')!r}: объявленный "
            f"допуск описывает прошлое и разрешает то, чего уже нет",
        )

    if findings == 0:
        expect(
            True,
            "",
            ok_note=(
                f"{declared_path}: {other['commands']} команд в {len(other['blocks'])} блоках "
                f"совпадают с {MAIN} символ в символ (допуск {tolerance})"
            ),
        )
    elif findings <= tolerance:
        OBSERVED.append(
            f"{declared_path}: расхождений {findings} при объявленном допуске {tolerance} — "
            f"в пределах строгости, названной данными"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Блоки команд переводов README")
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
    if not main_path.is_file():
        fail(f"нет {MAIN} — сверять команды не с чем")
        base: dict | None = None
        blocked = True
    else:
        base = profile(main_path)
        print(f"основной документ: {MAIN} — блоков: {len(base['blocks'])}, строк: {base['lines']}, "
              f"комментариев: {base['comments']}, пустых: {base['blank']}, команд: {base['commands']}; "
              f"языки ограждения: {', '.join(base['dialects'])}")
        if base["commands"] == 0:
            fail(f"{MAIN}: ни одной команды не найдено — «ноль осмотренных команд» это "
                 f"провал, а не PASS")
            # Зависимые свойства: сверять переводы не с чем, и объявление
            # описывает не этот файл. Одна порча — одна находка.
            blocked = True

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
            f"{DECLARATION}: строгость объявлена для языков {', '.join(extra)}, а перевода "
            f"на них нет: объявление обещает проверку, которой нечего осматривать",
        )
        if not missing and not extra:
            expect(
                True,
                "",
                ok_note=f"языки объявления и перевода совпадают: {', '.join(sorted(languages))}",
            )

    if declared is not None and languages is not None and base is not None and not blocked:
        for language, declared_path in languages.items():
            policy = declared["languages"].get(language)
            if not isinstance(policy, dict):
                continue  # уже названо находкой о самом объявлении
            check_language(root, language, declared_path, policy, base)

    if EXAMINED == 0:
        fail("ни одного объекта не осмотрено: «ноль проверенного» — это не PASS")
    report()
    return 1 if FAILURES else 0


def report() -> None:
    print(
        "граница: проверяется совпадение ТЕКСТА команды до хвостового комментария; "
        "смысл команды и её выполнимость не проверяются — запуска команд нет"
    )
    print(f"объявление блоков команд: {DECLARATION}")
    for note in OBSERVED:
        print(f"наблюдение: {note}")
    for failure in FAILURES:
        print(f"FAIL: {failure}")
    print(f"осмотрено: {EXAMINED}")
    if FAILURES:
        print(f"нарушений: {len(FAILURES)}")


if __name__ == "__main__":
    sys.exit(main())
