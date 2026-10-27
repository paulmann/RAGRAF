#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Состояние переводов README: «полный» или «частичный» — проверяемое свойство.

Задача, из которой проверка выросла (этап И21, 27.10.2026). Этап И20 привёл
переводы к решению «полный либо частичный», но само решение держалось шапкой
файла и записью в `docs/03_implementation/RELEASE-SOURCES.md`, то есть не
проверялось ничем: расхождение видел читатель, а не чекер. Здесь объявленное
состояние превращается в свойство, которое падает при нарушении.

Проверяются свойства, а не написание строк:

1. `community/README.ru.md` — ЗЕРКАЛО основного документа: он обязан совпадать
   с `README.md` плюс ровно пять строк шапки, а шапка обязана называть файл
   копией основного документа. Равенство проверяемо, качество перевода — нет;
   поэтому состояние «полный» выражено равенством.
2. `community/README.de.md`, `.es.md`, `.hi.md`, `.zh.md` — ЧАСТИЧНЫЕ переводы:
   у каждого в шапке свой признак неполноты на своём языке, и шапка обязана
   назвать не менее трёх недостающих пунктов из перечня этапа И20.
3. Ни в одном переводе нет ссылок `docs/ru/**` и `docs/DEFECTS.md` (этих путей
   в поставке нет), нет заполнителя команды `<конфиг>`, и КАЖДАЯ относительная
   ссылка — включая изображения — указывает на файл, который действительно
   попадает в поставку.
4. Кириллица в переводе допустима только в ярлыке «Русский» переключателя
   языков и не встречается внутри блоков команд.

Одна порча — одна находка. Поэтому предусловия (нет файла, не читается
манифест) отделены от наблюдений, зависимые свойства не проверяются при
отсутствии того, от чего зависят, а строка, уже названная русским заполнителем,
не пересчитывается как «кириллица в блоке команд». Иначе разбор прогона
начинается с подсчёта копий одного и того же дефекта.

Границы, названные честно:

* «полный» проверяется равенством с `README.md`, а не качеством текста: язык
  перевода машинной проверке не поддаётся, а расхождение зеркала — поддаётся;
* зеркало русского документа кириллицу содержит по определению, и заполнитель
  `<конфиг>` приходит в него из `README.md` — основного документа, который
  решено писать по-русски. Оба свойства заменены равенством (пункт 1), а не
  отключены молча: наблюдение об этом делается явно и попадает в счёт осмотренного;
* «не менее трёх недостающих пунктов» — порог, а не полный перечень: шапки
  четырёх переводов называют шесть пунктов из семи (в них не названы операции
  SSO, SIEM, офлайн-лицензии и аттестации, хотя `RELEASE-SOURCES.md` их
  перечисляет). Перечень в шапке — текст перевода, и правка его здесь была бы
  правкой содержания; поэтому проверяется порог, а расхождение с документом
  названо в отчёте этапа;
* ссылки разрешаются против СОСТАВА ПОСТАВКИ (`tools/release/manifest.toml`),
  а не против живого дерева: в поставке перевод лежит в корне репозитория, а в
  живом дереве — в `community/`. Сверка с живым деревом дала бы ложную зелень
  на `EULA.md` (он есть в `community/`) и на `assets/**`. Измерено 27.10.2026:
  шапки четырёх переводов ссылаются на `assets/social-preview.png`, которого не
  было НИ в живом дереве, НИ в поставке — прежний замер ссылок
  (`stage-i20/links.py`) изображения не видел, потому что исключал их шаблоном
  `(?<!!)`, и обложка была битой у читателя при зелёном замере. Здесь это первая
  находка проверки, а не повод её ослабить: файл восстановлен из сохранённой
  копии `editions/legacy-v0.1.0/assets/` и внесён в манифест тем же этапом;
* проверяется НАЛИЧИЕ цели, а не её содержимое: картинка может быть устаревшей,
  и это не нарушение состояния перевода.

Запуск: `python tools/check_translations.py [--root <дерево>]`
Коды возврата: 0 — свойства выполняются, 1 — есть нарушения (в том числе «ноль
осмотренных объектов»: это провал, а не PASS).
"""

from __future__ import annotations

import argparse
import posixpath
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MAIN = "README.md"
MIRROR = "community/README.ru.md"
MIRROR_HEADER_LINES = 5

#: Признак неполноты в шапке — на языке перевода. Ключ словаря: путь файла.
PARTIAL = {
    "community/README.de.md": ("de", "Diese Übersetzung ist unvollständig"),
    "community/README.es.md": ("es", "Esta traducción está incompleta"),
    "community/README.hi.md": ("hi", "यह अनुवाद अधूरा है"),
    "community/README.zh.md": ("zh", "本译本不完整"),
}

#: Шапка перевода — первые строки после заголовка. Замер 27.10.2026: примечание
#: о неполноте стоит строкой 12 у всех четырёх переводов (после переключателя
#: языков, девиза, ссылок и обложки). Окно шире факта намеренно: шапка может
#: сдвинуться на строку-две без потери смысла, но не уехать в середину файла.
HEADER_WINDOW = 13

#: Недостающие пункты из перечня этапа И20. Проверяется, что шапка называет
#: НЕ МЕНЕЕ ТРЁХ из них — по признакам на всех языках перевода, потому что
#: перевод пишется на своём языке, а не по-русски.
GAPS = (
    ("уровни лицензии", re.compile(
        r"(?i)(Lizenzstufen|niveles de licencia|licen[cs]ias?\b|लाइसेंस\s*स्तर"
        r"|许可证(层级|分层)?|уровн[а-яё]* лицензи)")),
    ("брокер секретов", re.compile(
        r"(?i)(Secret[- ]Broker|br[óo]ker de secretos|ब्रोकर|机密代理|密钥代理"
        r"|брокер[а-яё]* секрет)")),
    ("контур качества и общий индекс", re.compile(
        r"(?i)(Qualitätsschleife|bucle de calidad|control de calidad|गुणवत्ता"
        r"|质量(回路|循环|闭环)|контур[а-яё]* качества"
        r"|gemeinsame[nr]? Index|índice compartido|साझा\s*(इंडेक्स|सूचकांक)|共享索引"
        r"|общ[а-яё]* индекс)")),
    ("снятые connectors и fleet", re.compile(
        r"(?i)(`?connectors`?|`?fleet`?|कनेक्टर|连接器)")),
    ("разбор документов Office/PDF", re.compile(
        r"(?i)(Office|PDF|ऑफ़िस|文档解析|разбор[а-яё]* документ)")),
    ("операции route и neighbors", re.compile(
        r"(?i)(`route`|`neighbors`|\broute\b|\bneighbors\b)")),
    ("операции SSO, SIEM, офлайн-лицензии и аттестации", re.compile(
        r"(?i)(\bSSO\b|\bSIEM\b|Offline[- ]?Lizenz|licencia (sin conexión|offline)"
        r"|ऑफ़लाइन|离线|офлайн|Attestierung|attestation|अटेस्टेशन|认证|аттестаци)")),
)

#: Минимум названных недостающих пунктов у частичного перевода.
MIN_GAPS = 3

#: Пути, которых в поставке нет: ссылка на них — обещание 404.
FORBIDDEN_LINKS = ("docs/ru/", "docs/DEFECTS.md")

#: Заполнитель команды, оставшийся с русского оригинала.
FORBIDDEN_PLACEHOLDERS = ("<конфиг>", "-конфиг-")

#: Ссылка И изображение: `[т](цель)` и `![т](цель)`. Прежний замер исключал
#: изображения (`(?<!!)`), поэтому битая обложка не находилась.
LINK = re.compile(r"!?\[[^\]\n]*\]\(([^)\n]+)\)")
FENCE = re.compile(r"^\s*(```|~~~)")
CYRILLIC = re.compile(r"[\u0400-\u04FF]")
SWITCHER_LABEL = "Русский"
REMOTE_PREFIXES = ("http://", "https://", "mailto:", "#", "ftp://")

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


def header_lines(lines: list[str]) -> list[str]:
    """Шапка перевода: первые строки после заголовка первого уровня."""
    return lines[1 : 1 + HEADER_WINDOW]


def delivered_targets(root: Path) -> tuple[set[str] | None, list[str]]:
    """Пути поставки (цели манифеста). None — состав поставки неизвестен."""
    release = root / "tools" / "release"
    if not (release / "manifest.toml").is_file():
        return None, [f"нет tools/release/manifest.toml — состав поставки неизвестен, "
                      f"ссылки переводов не проверены"]
    sys.path.insert(0, str(release))
    try:
        import relcommon as rel  # noqa: PLC0415 - путь к общему модулю добавлен выше
        manifest = rel.load_manifest(root / "tools/release/manifest.toml", root)
        plan = rel.collect(root, manifest)
    except Exception as error:  # pragma: no cover - сломанный манифест
        return None, [f"манифест поставки не разобран: {error} — ссылки не проверены"]
    return set(plan.files), []


def forbidden_path(target: str) -> bool:
    """Ссылка ведёт на путь, которого в поставке нет по решению (docs/ru, DEFECTS)."""
    clean = posixpath.normpath(target.split("#", 1)[0].strip())
    return any(clean.startswith(item) or clean == item.rstrip("/")
               for item in FORBIDDEN_LINKS)


def resolve(target: str, delivered: set[str]) -> bool:
    """Разрешить относительную ссылку против состава поставки."""
    clean = target.split("#", 1)[0].strip().strip("<>").strip()
    if not clean:
        return True  # ссылка на якорь внутри того же файла
    clean = posixpath.normpath(clean.replace("\\", "/"))
    if clean.startswith("..") or clean.startswith("/"):
        return False
    if clean in delivered:
        return True
    # Ссылка на каталог: цель существует, если в нём есть файлы поставки. Иначе
    # каждая ссылка на `docs/skills/` была бы провалом.
    prefix = clean.rstrip("/") + "/"
    return any(path.startswith(prefix) for path in delivered)


def links_of(text: str) -> list[tuple[int, str]]:
    """Относительные ссылки и изображения файла: (строка, цель)."""
    found: list[tuple[int, str]] = []
    for index, line in enumerate(text.splitlines(), 1):
        for target in LINK.findall(line):
            target = target.strip()
            if target.lower().startswith(REMOTE_PREFIXES):
                continue
            found.append((index, target))
    return found


def check_mirror(root: Path, main_lines: list[str]) -> None:
    path = root / MIRROR
    if not path.is_file():
        fail(f"нет {MIRROR} — зеркало основного документа отсутствует")
        return
    lines = read(path).splitlines()
    print(f"перевод: {MIRROR}")
    expect(
        len(lines) == len(main_lines) + MIRROR_HEADER_LINES,
        f"{MIRROR}: строк {len(lines)}, а должно быть {len(main_lines) + MIRROR_HEADER_LINES} "
        f"({MAIN} {len(main_lines)} + шапка {MIRROR_HEADER_LINES}): зеркало разошлось",
    )
    expect(
        bool(lines) and bool(main_lines) and lines[0] == main_lines[0],
        f"{MIRROR}: первый заголовок не совпадает с {MAIN}",
    )
    body = lines[1 + MIRROR_HEADER_LINES :]
    reference = main_lines[1:]
    differences = [
        (index, left, right)
        for index, (left, right) in enumerate(zip(body, reference), 1)
        if left != right
    ]
    if body == reference:
        expect(True, "")
    else:
        where = (
            f", первое в строке {differences[0][0] + 1 + MIRROR_HEADER_LINES}: "
            f"{differences[0][1][:70]!r} против {differences[0][2][:70]!r}"
            if differences
            else ""
        )
        expect(
            False,
            f"{MIRROR}: текст не совпадает с {MAIN} — расхождений {len(differences)}{where}",
        )
    header = "\n".join(header_lines(lines))
    expect(
        bool(re.search(r"копи(?:я|ей)", header))
        and MAIN in header
        and bool(re.search(r"основн", header)),
        f"{MIRROR}: шапка не называет файл копией основного документа ({MAIN})",
    )
    expect(
        True,
        "",
        ok_note=(
            f"{MIRROR}: кириллица и заполнитель <конфиг> — не нарушение: файл объявлен "
            f"копией русского {MAIN}, их сторожит равенство"
        ),
    )


def check_partial(root: Path, relative: str, language: str, marker: str,
                  delivered: set[str] | None) -> None:
    path = root / relative
    if not path.is_file():
        fail(f"нет {relative} — перевод {language} отсутствует, а состояние объявлено")
        return
    text = read(path)
    lines = text.splitlines()
    header = "\n".join(header_lines(lines))

    declared = marker in header
    expect(
        declared,
        f"{relative}: в шапке нет признака неполноты «{marker}» — частичный перевод "
        f"выдаётся за полный",
    )
    if declared:
        named = [name for name, pattern in GAPS if pattern.search(header)]
        expect(
            len(named) >= MIN_GAPS,
            f"{relative}: шапка называет недостающих пунктов {len(named)} из {len(GAPS)} "
            f"({', '.join(named) or 'ни одного'}), а требуется не менее {MIN_GAPS}: "
            f"частичность без перечня — обещание, а не состояние",
        )
    else:
        # Зависимое свойство: без шапки перечень проверять не в чем. Находка о
        # шапке уже есть, и вторая про тот же дефект была бы счётом копий.
        expect(
            True,
            "",
            ok_note=(
                f"{relative}: перечень недостающего не проверялся — шапки нет, "
                f"об этом одна находка, а не две"
            ),
        )

    for forbidden in FORBIDDEN_LINKS:
        hits = [index for index, line in enumerate(lines, 1) if forbidden in line]
        expect(
            not hits,
            f"{relative}:{hits[0] if hits else 0}: ссылка на {forbidden} — такого пути в "
            f"поставке нет (всего {len(hits)})",
        )

    placeholder_lines = [
        index
        for index, line in enumerate(lines, 1)
        if any(item in line for item in FORBIDDEN_PLACEHOLDERS)
    ]
    expect(
        not placeholder_lines,
        f"{relative}:{placeholder_lines[0] if placeholder_lines else 0}: в примерах команд "
        f"остался русский заполнитель (строк {len(placeholder_lines)})",
    )

    in_fence = False
    code_blocks = 0
    code_cyrillic: list[tuple[int, str]] = []
    outside: list[tuple[int, str]] = []
    for index, line in enumerate(lines, 1):
        if FENCE.match(line):
            in_fence = not in_fence
            if in_fence:
                code_blocks += 1
            continue
        if index in placeholder_lines:
            continue  # уже названо заполнителем: второй раз не считаем
        if not CYRILLIC.search(line) or SWITCHER_LABEL in line:
            continue
        (code_cyrillic if in_fence else outside).append((index, line))

    expect(
        not code_cyrillic,
        f"{relative}:{code_cyrillic[0][0] if code_cyrillic else 0}: кириллица в блоке "
        f"команд ({len(code_cyrillic)} строк) — пример команды остался на русском",
    )
    expect(
        not outside,
        f"{relative}:{outside[0][0] if outside else 0}: кириллица вне ярлыка «Русский» "
        f"в {len(outside)} строках, первая: {outside[0][1][:80]!r}" if outside
        else f"{relative}: кириллица только в ярлыке «Русский»",
    )
    expect(
        code_blocks > 0,
        f"{relative}: не найдено ни одного блока команд — проверять нечего, а это "
        f"перевод с примерами",
    )

    links = links_of(text)
    if delivered is None:
        expect(
            False,
            f"{relative}: ссылки не проверены — состав поставки неизвестен "
            f"(переводов со ссылками {len(links)})",
        )
    else:
        for index, target in links:
            # Запрещённые пути уже названы выше: одна порча — одна находка.
            if forbidden_path(target):
                continue
            expect(
                resolve(target, delivered),
                f"{relative}:{index}: ссылка {target!r} не указывает на файл поставки",
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Состояние переводов README")
    parser.add_argument("--root", default=str(REPO), help="корень живого дерева")
    args = parser.parse_args()
    root = Path(args.root)

    main_path = root / MAIN
    main_lines = read(main_path).splitlines() if main_path.is_file() else None
    if main_lines is None:
        fail(f"нет {MAIN} — сверять зеркало и переводы не с чем")
    else:
        check_mirror(root, main_lines)

    delivered, problems = delivered_targets(root)
    for problem in problems:
        fail(problem)

    for relative, (language, marker) in PARTIAL.items():
        check_partial(root, relative, language, marker, delivered)
        if (root / relative).is_file():
            print(f"перевод: {relative}")

    if EXAMINED == 0:
        FAILURES.append("ни одного объекта не осмотрено: «ноль проверенного» — это не PASS")
    report()
    return 1 if FAILURES else 0


def report() -> None:
    print(
        "граница: зеркало README.ru.md русское по решению, поэтому кириллица и <конфиг> "
        "в нём проверяются равенством с README.md, а не запретом"
    )
    for note in OBSERVED:
        print(f"наблюдение: {note}")
    for failure in FAILURES:
        print(f"FAIL: {failure}")
    print(f"осмотрено: {EXAMINED}")
    if FAILURES:
        print(f"нарушений: {len(FAILURES)}")


if __name__ == "__main__":
    sys.exit(main())
