#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Внешние адреса и якоря переводов README: каждый адрес основного документа — поимённо.

Задача, из которой проверка выросла (этап И29, 27.10.2026). К этому моменту
перевод проверялся четырьмя способами: состояние (`tools/check_translations.py`:
признак неполноты, шапка, равенство числа разделов, профиль раздела), ссылки,
числа индекса и терминология, проза (`tools/check_prose_profile.py`). И у всех
четырёх граница названа честно — смысл фраз не проверяется. Но между смыслом и
структурой остался слой, который поддаётся проверке ПОЛНОСТЬЮ и который не
сторожил никто, — АДРЕСА.

функция `relative_targets` в `tools/check_translations.py` (строка 227) отбрасывает всё, что
начинается с `http://`, `https://`, `mailto:`, `ftp://` и `#`, а покрытие ссылок
(`check_link_coverage`, строка 342 того файла) сравнивает только ОТНОСИТЕЛЬНЫЕ
цели. Значит внешний адрес и якорь в переводе не проверял никто: перевод, где
ссылка на GitHub заменена на «см. репозиторий», а якорь `#installation` ведёт в
никуда, проходил зелёным. Это не гипотеза: замер 27.10.2026 показал, что
свойство СЕГОДНЯ выполняется (8 внешних адресов, 5 изображений, 13 якорей — в
каждом из пяти переводов, лишних нет), но выполняется оно случайно, потому что
проверять его нечем.

Что считается адресом и как проверяется (объявлено ДАННЫМИ —
`docs/03_implementation/addresses.json`, а не выведено кодом):

* **внешний адрес** — цель ссылки или источник изображения со схемой `http://`,
  `https://`, `mailto:`, `ftp://`. Требуется тем же ТЕКСТОМ цели: сравнивается
  точное написание, поэтому `http` вместо `https`, другой хост или пропавший
  параметр запроса — находка. Число повторов сверяется по кратности (допуск
  объявлен по языку и равен нулю);
* **изображение** — разметка `![подпись](источник)` независимо от того, внешний
  источник или относительный: обложка `assets/social-preview.png` относительная,
  и прежний замер ссылок этапа И20 не видел её вовсе, потому что шаблон исключал
  изображения. Битая обложка у читателя при зелёной проверке — измеренный дефект;
* **якорь** — цель вида `#…`. Текст якоря с основным документом НЕ сравнивается:
  заголовки перевода переведены, и слаг у них свой (немецкий `#inhalt` против
  английского `#contents`). Проверяются два свойства: якорь указывает на
  существующий заголовок СВОЕГО файла, и порядок якорей повторяет порядок
  основного документа — k-й якорь ведёт в раздел с тем же номером, что k-й якорь
  `README.md`. Второе свойство и есть проверка «ведёт туда, а не в никуда»: якорь,
  указывающий на чужой раздел, разрешается, но стоит не на своём месте.

Правило слага названо в шапке здесь, потому что от него зависит результат: текст
заголовка без HTML-тегов и разметки, в нижнем регистре; буквы, знаки письма,
цифры, дефис и подчёркивание сохраняются; пробелы становятся дефисом; прочая
пунктуация удаляется; совпадающие слаги в одном файле получают суффиксы `-1`,
`-2`. Знаки письма сохранены отдельно от букв не из педантизма: в деванагари
огласовка — категория Mn, и без неё слаг `पहले-कदम` не совпал бы ни с одним
заголовком (замерено: 13 ложных находок в хинди на первом прогоне).

Разбор вложенной разметки обязателен, и это тоже измерено: на бейдже
`[![CI](badge.svg)](url)` простой шаблон `!?\\[[^\\]]*\\]\\(([^)]+)\\)` совпадает с
ВНУТРЕННИМ изображением и теряет цель внешней ссылки. Черновой замер поэтому
назвал шесть внешних адресов вместо восьми. Ссылки и изображения разбираются
отдельно, а ссылка допускает внутри подписи один уровень вложенного изображения.

Границы, названные честно:

* **доступность адреса по сети не проверяется** — сети у проверки нет.
  Проверяется совпадение текста цели и существование якоря в своём файле.
  `https://example.org/removed` пройдёт зелёным, если он назван так же, как в
  основном документе: это проверка ПОЛНОТЫ перевода, а не живости ссылки;
* правило слагов повторено по документации GitHub, а не вызовом его кода; якорь
  сверяется с заголовками своего файла (там обе стороны считаются одним
  правилом), а совпадение с отрисовкой GitHub — граница, а не проверенное
  свойство;
* относительные цели здесь не проверяются: их сторожит
  `tools/check_translations.py` (существует ли файл поставки). Проверка,
  повторяющая чужую, расходится с ней — в этом проекте так уже было;
* ссылки внутри строк кода (в обратных кавычках) и ссылки справочного вида
  `[текст][метка]` не разбираются: первые — часть цитируемой команды, вторые в
  README не используются. Это названная граница, а не забытая;
* якорь в заголовке другого файла (`README.md#installation`) — относительная
  цель, его проверяет `check_translations.py` как существование файла;
* сравнение идёт по точному тексту цели, поэтому законная правка вида
  `http://` → `https://` станет находкой. Это осознанно: адрес перевода обязан
  быть тем же адресом, а не похожим.

Одна порча — одна находка. Если у адреса разошлось число повторов, находка
называется один раз на адрес, с обеими кратностями; если разошлось число
якорей, сравнение их порядка по этому файлу не выполняется: позиции съехали, и
вторая находка была бы счётом копий одного дефекта.

Запуск:
    python tools/check_document_addresses.py [--root <дерево>]
Коды возврата: 0 — все адреса основного документа названы и все якоря на месте,
1 — есть расхождения (включая «ноль осмотренных адресов»: это провал, а не
PASS), 2 — не прочитаны аргументы.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MAIN = "README.md"
ADDRESSES = "docs/03_implementation/addresses.json"
STATES = "docs/03_implementation/translations.json"

FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
#: Изображение: подпись без вложенных скобок, цель без закрывающей скобки.
IMAGE = re.compile(r"!\[[^\]\n]*\]\(([^)\n]+)\)")
#: Ссылка: в подписи допускается ОДИН уровень вложенного изображения (бейдж).
LINK = re.compile(r"(?<!!)\[(?:[^\[\]\n]|\[[^\]\n]*\]\([^)\n]*\))*\]\(([^)\n]+)\)")
#: Заголовок цели в кавычках: `[т](цель "подсказка")`.
TITLE = re.compile(r'\s+"[^"]*"$')
SCHEMES = ("http://", "https://", "mailto:", "ftp://")

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


# --- разбор адресов ----------------------------------------------------------


def strip_title(target: str) -> str:
    """`цель "подсказка"` → `цель`: подсказка не адрес."""
    return TITLE.sub("", target).strip()


def items(text: str) -> list[tuple[int, str, str]]:
    """Адреса файла: [(строка, цель, вид)], вид — `image` или `link`.

    Строки блоков команд пропускаются: адрес внутри примера кода — часть
    команды, а не ссылка документа.
    """
    found: list[tuple[int, str, str]] = []
    in_fence = False
    for index, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for match in IMAGE.finditer(line):
            found.append((index, strip_title(match.group(1)), "image"))
        for match in LINK.finditer(line):
            found.append((index, strip_title(match.group(1)), "link"))
    found.sort(key=lambda item: item[0])
    return found


def kind_of(target: str) -> str:
    """Вид цели: `external`, `anchor` или `relative`."""
    lowered = target.lower()
    if lowered.startswith(SCHEMES):
        return "external"
    if target.startswith("#"):
        return "anchor"
    return "relative"


def slugify(title: str) -> str:
    """Слаг заголовка по правилу GitHub, названному в шапке файла."""
    text = re.sub(r"<[^>]+>", "", title).strip().lower()
    out: list[str] = []
    for char in text:
        category = unicodedata.category(char)
        if category[0] in "LMN" or char in "-_":
            out.append(char)
        elif char in " \t":
            out.append("-")
    return "".join(out)


def headings(text: str) -> list[tuple[int, str]]:
    """Заголовки файла: [(строка, текст)] — все уровни, в порядке появления."""
    found: list[tuple[int, str]] = []
    in_fence = False
    for index, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = HEADING.match(line)
        if match:
            found.append((index, match.group(2)))
    return found


def slug_index(found: list[tuple[int, str]]) -> list[str]:
    """Слаги заголовков в порядке появления, с суффиксами `-1`, `-2` при совпадении."""
    repeats: dict[str, int] = {}
    out: list[str] = []
    for _, title in found:
        base = slugify(title)
        if base in repeats:
            repeats[base] += 1
            out.append(f"{base}-{repeats[base]}")
        else:
            repeats[base] = 0
            out.append(base)
    return out


def profile(path: Path) -> dict:
    """Профиль адресов файла: что в нём считается адресом и куда ведут якоря."""
    text = read(path)
    found = items(text)
    head = headings(text)
    slugs = slug_index(head)
    positions = {slug: number for number, slug in enumerate(slugs)}
    external = Counter(target for _, target, _ in found if kind_of(target) == "external")
    images = Counter(target for _, target, kind in found if kind == "image")
    anchors = [(line, target) for line, target, _ in found if kind_of(target) == "anchor"]
    lines_of: dict[str, list[int]] = {}
    for line, target, _ in found:
        lines_of.setdefault(target, []).append(line)
    return {
        "text": text,
        "items": found,
        "external": external,
        "images": images,
        "anchors": anchors,
        "headings": head,
        "slugs": slugs,
        "positions": positions,
        "lines": lines_of,
    }


def compare_addresses(declared_path: str, label: str, note_label: str, measured: Counter,
                     declared: Counter, measured_lines: dict[str, list[int]],
                     declared_lines: dict[str, list[int]], tolerance: int,
                     consequence: str = "") -> None:
    """Один адрес — одна находка: подмена называется парой, а не двумя счетами.

    Измеренный дефект первого прогона: замена адреса давала ДВЕ находки — «нет адреса
    основного документа» и «есть лишний». Это один дефект, посчитанный дважды, и
    требование «одна порча — одна находка» он нарушал. Поэтому недостача и излишек
    одного вида сводятся в пары: каждая пара — одно место, названное обоими адресами.
    """
    missing = [
        (address, declared[address] - measured.get(address, 0))
        for address in declared
        if measured.get(address, 0) < declared[address] - tolerance
    ]
    extra = [
        (address, measured[address] - declared.get(address, 0))
        for address in measured
        if measured[address] > declared.get(address, 0) + tolerance
    ]

    while missing and extra:
        lost, deficit = missing.pop(0)
        added, surplus = extra.pop(0)
        expect(
            False,
            f"{declared_path}:{where(measured_lines.get(added))}: {label} {added} назван вместо "
            f"{lost} (в {MAIN}:{where(declared_lines.get(lost))}): цель подменена"
            + (f"; {consequence}" if consequence else "")
            + (
                f"; кратность разошлась: {added} × {surplus} против {lost} × {declared[lost]} в {MAIN}"
                if deficit > 1 or surplus > 1
                else ""
            ),
        )
    for address, deficit in missing:
        expect(
            False,
            f"{declared_path}: {label} {address} основного документа не назван "
            f"(не хватает {deficit} раз(а); в {MAIN}:{where(declared_lines.get(address))}): "
            f"перевод заменил его словами или другим адресом"
            + (f"; {consequence}" if consequence else ""),
        )
    for address, surplus in extra:
        expect(
            False,
            f"{declared_path}:{where(measured_lines.get(address))}: {label} {address} есть в переводе, "
            f"но его нет в {MAIN} (× {surplus}): ссылка добавлена или подменена",
        )
    if not missing and not extra:
        expect(
            True,
            "",
            ok_note=f"{declared_path}: {note_label} {len(declared)} — все названы, лишних нет",
        )


def where(lines: list[int] | None) -> str:
    """Строка файла или их перечень — для текста находки."""
    if not lines:
        return "нет строки"
    if len(lines) == 1:
        return f"строка {lines[0]}"
    return "строки " + ", ".join(str(item) for item in lines)


# --- объявления ---------------------------------------------------------------


def load_addresses(root: Path) -> tuple[dict | None, list[str]]:
    """Строгость из данных. None — объявления нет или оно не читается."""
    path = root / ADDRESSES
    if not path.is_file():
        return None, [
            f"нет {ADDRESSES} — что считается адресом и с какой строгостью его "
            f"требовать, не объявлено (строгость в коде запрещена: иначе языковое "
            f"различие нельзя назвать числом, не правя проверку)"
        ]
    try:
        data = json.loads(read(path))
    except json.JSONDecodeError as error:
        return None, [f"{ADDRESSES} не разобран: {error}"]

    problems: list[str] = []
    if data.get("main") != MAIN:
        problems.append(
            f"{ADDRESSES}: основным документом назван {data.get('main')!r}, "
            f"а проверка считает адреса {MAIN}"
        )
    kinds = data.get("kinds")
    declared_kinds = {item.get("name") for item in kinds if isinstance(item, dict)} if isinstance(kinds, list) else set()
    for required in ("external", "image", "anchor"):
        if required not in declared_kinds:
            problems.append(f"{ADDRESSES}: вид адреса {required} не объявлен — проверка не знает, что считать")
    baseline = data.get("baseline")
    if not isinstance(baseline, dict):
        problems.append(f"{ADDRESSES}: перечень основного документа (baseline) не объявлен")
    else:
        for key in ("external", "images", "anchors", "distinct_anchors", "headings"):
            value = baseline.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                problems.append(f"{ADDRESSES}: baseline.{key} не объявлен целым неотрицательным числом")
    languages = data.get("languages")
    if not isinstance(languages, dict) or not languages:
        problems.append(f"{ADDRESSES}: список языков пуст — проверять нечего")
    else:
        for language, entry in languages.items():
            if not isinstance(entry, dict):
                problems.append(f"{ADDRESSES}: язык {language} объявлен не записью со строгостью")
                continue
            tolerance = entry.get("repeat_tolerance")
            if not isinstance(tolerance, int) or isinstance(tolerance, bool) or tolerance < 0:
                problems.append(
                    f"{ADDRESSES}: у языка {language} допуск repeat_tolerance не объявлен "
                    f"целым неотрицательным числом"
                )
            if not isinstance(entry.get("require_order"), bool):
                problems.append(f"{ADDRESSES}: у языка {language} не объявлено require_order (да/нет)")
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


def check_main(base: dict, declared: dict, declared_path: str = MAIN) -> None:
    """Основной документ: его перечень адресов сверяется с объявлением, якоря — с собой."""
    baseline = declared["baseline"]
    measured = {
        "external": sum(base["external"].values()),
        "images": sum(base["images"].values()),
        "anchors": len(base["anchors"]),
        "distinct_anchors": len({target for _, target in base["anchors"]}),
        "headings": len(base["headings"]),
    }
    diverged = [
        f"{key}: объявлено {baseline[key]}, измерено {measured[key]}"
        for key in ("external", "images", "anchors", "distinct_anchors", "headings")
        if baseline[key] != measured[key]
    ]
    expect(
        not diverged,
        f"{ADDRESSES}: перечень адресов основного документа разошёлся с объявленным "
        f"({'; '.join(diverged)}): изменение адресов README.md записывается объявлением, "
        f"а не остаётся незамеченным",
    )
    if not diverged:
        expect(
            True,
            "",
            ok_note=(
                f"{MAIN}: внешних адресов {measured['external']}, изображений "
                f"{measured['images']}, якорей {measured['anchors']} на "
                f"{measured['distinct_anchors']} заголовков, заголовков {measured['headings']} — "
                f"как объявлено"
            ),
        )

    dangling = [
        (line, target) for line, target in base["anchors"] if target[1:] not in base["positions"]
    ]
    expect(
        not dangling,
        f"{MAIN}: якорь «{dangling[0][1]}» (строка {dangling[0][0]}) не указывает ни на один "
        f"заголовок самого документа: сверять переводы не с чем"
        if dangling
        else "",
    )


def check_language(root: Path, language: str, declared_path: str, policy: dict,
                   base: dict) -> None:
    """Адреса одного перевода против основного документа."""
    path = resolve_source(root, declared_path)
    if not path.is_file():
        fail(f"нет {declared_path} — перевод {language} объявлен, а файла нет: адреса сверять не в чем")
        return
    other = profile(path)
    tolerance = int(policy.get("repeat_tolerance") or 0)

    print(f"язык: {language} — внешних {sum(other['external'].values())}, "
          f"изображений {sum(other['images'].values())}, якорей {len(other['anchors'])}, "
          f"заголовков {len(other['headings'])}")

    if not (other["external"] or other["images"] or other["anchors"]):
        fail(f"{declared_path}: ни одного адреса не найдено — «ноль осмотренных адресов» "
             f"это провал, а не PASS")
        return

    # 1. Внешние адреса: каждый адрес основного документа назван тем же текстом.
    compare_addresses(
        declared_path, "внешний адрес", "внешних адресов",
        other["external"], base["external"], other["lines"], base["lines"], tolerance,
    )

    # 2. Изображения: обложка и бейджи — тоже адреса, относительные в том числе.
    compare_addresses(
        declared_path, "изображение", "изображений",
        other["images"], base["images"], other["lines"], base["lines"], tolerance,
        consequence="у читателя перевода картинка пропадёт",
    )
    # 3. Якоря: тот же счёт и тот же порядок разделов, что в основном документе.
    if abs(len(other["anchors"]) - len(base["anchors"])) > tolerance:
        expect(
            False,
            f"{declared_path}: якорей {len(other['anchors'])}, а в {MAIN} — {len(base['anchors'])} "
            f"(допуск {tolerance}): пункт оглавления пропал или добавился; порядок якорей "
            f"по этому файлу не сверяется — позиции съехали",
        )
        return
    expect(
        True,
        "",
        ok_note=f"{declared_path}: якорей {len(other['anchors'])}, как в {MAIN}",
    )

    dangling = [
        (line, target) for line, target in other["anchors"] if target[1:] not in other["positions"]
    ]
    expect(
        not dangling,
        f"{declared_path}:{dangling[0][0] if dangling else 0}: якорь "
        f"«{dangling[0][1] if dangling else ''}» не указывает ни на один заголовок своего файла; "
        f"заголовки файла: {', '.join(other['slugs'][:6])}…"
        if dangling
        else "",
        ok_note=f"{declared_path}: все {len(other['anchors'])} якорей указывают на заголовки своего файла"
        if not dangling else None,
    )
    if dangling:
        return  # зависимое свойство: часть якорей не разрешается, порядок сверять не по чему

    if not policy.get("require_order"):
        return

    orders: list[tuple[int, str, int | None]] = []
    for line, target in other["anchors"]:
        orders.append((line, target, other["positions"].get(target[1:])))
    base_orders = [(line, target, base["positions"].get(target[1:])) for line, target in base["anchors"]]

    wrong = 0
    for number in range(min(len(orders), len(base_orders))):
        line, target, position = orders[number]
        base_line, base_target, base_position = base_orders[number]
        if position == base_position:
            continue
        wrong += 1
        here_title = other["headings"][position][1] if position is not None else "—"
        base_title = base["headings"][base_position][1] if base_position is not None else "—"
        expect(
            False,
            f"{declared_path}:{line}: якорь «{target}» ведёт в раздел {here_title!r} "
            f"(заголовок №{(position or 0) + 1}), а в {MAIN}:{base_line} на этой позиции "
            f"«{base_target}» → {base_title!r} (№{(base_position or 0) + 1}): подпись и цель "
            f"разошлись — читатель перевода придёт не в тот раздел",
        )
    if not wrong:
        expect(
            True,
            "",
            ok_note=f"{declared_path}: порядок {len(orders)} якорей повторяет порядок разделов {MAIN}",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Внешние адреса и якоря переводов README")
    parser.add_argument("--root", default=str(REPO), help="корень проверяемого дерева")
    args = parser.parse_args()
    root = Path(args.root)

    declared, address_problems = load_addresses(root)
    for problem in address_problems:
        fail(problem)
    languages, language_problems = load_languages(root)
    for problem in language_problems:
        fail(problem)

    main_path = root / MAIN
    if not main_path.is_file():
        fail(f"нет {MAIN} — сверять адреса не с чем")
        base: dict | None = None
    else:
        base = profile(main_path)
        print(f"основной документ: {MAIN} — внешних адресов {sum(base['external'].values())}, "
              f"изображений {sum(base['images'].values())}, якорей {len(base['anchors'])} "
              f"(различных {len({target for _, target in base['anchors']})}), "
              f"заголовков {len(base['headings'])}")
        if not (base["external"] or base["images"] or base["anchors"]):
            fail(f"{MAIN}: ни одного адреса не найдено — «ноль осмотренных адресов» это провал, "
                 f"а не PASS")

    if declared is not None and base is not None:
        check_main(base, declared)

    if declared is not None and languages is not None:
        declared_languages = declared["languages"]
        missing = sorted(set(languages) - set(declared_languages))
        extra = sorted(set(declared_languages) - set(languages))
        expect(
            not missing,
            f"{ADDRESSES}: для языков {', '.join(missing)} перевод есть, а строгость не объявлена: "
            f"новый перевод остался бы непроверенным",
        )
        expect(
            not extra,
            f"{ADDRESSES}: строгость объявлена для языков {', '.join(extra)}, а перевода на них нет: "
            f"объявление обещает проверку, которой нечего осматривать",
        )
        if not missing and not extra:
            expect(
                True,
                "",
                ok_note=f"языки объявления и перевода совпадают: {', '.join(sorted(languages))}",
            )

    if declared is not None and languages is not None and base is not None:
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
        "граница: проверяется совпадение ТЕКСТА цели и существование якоря в своём файле; "
        "доступность адреса по сети не проверяется — сети у проверки нет"
    )
    print(f"объявление адресов: {ADDRESSES}")
    for note in OBSERVED:
        print(f"наблюдение: {note}")
    for failure in FAILURES:
        print(f"FAIL: {failure}")
    print(f"осмотрено: {EXAMINED}")
    if FAILURES:
        print(f"нарушений: {len(FAILURES)}")


if __name__ == "__main__":
    sys.exit(main())
