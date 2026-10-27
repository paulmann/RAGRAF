#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Соответствие «раздел → раздел»: проза переводов README считается, а не читается.

Задача, из которой проверка выросла (этап И28, 27.10.2026). К этому моменту
переводы проверялись дважды и оба раза — не прозой:

* `tools/check_translations.py` подтверждал состояние перевода: отсутствие
  признака неполноты, шапку, равенство числа разделов `##` и подразделов `###`,
  профиль раздела — сколько в нём блоков команд и строк таблиц — и наличие всех
  относительных ссылок. Профиль (`section_profile`, строка 192 того файла)
  состоит из двух чисел: блоков команд и строк таблиц. Прозы в нём нет;
* `tools/check_glossary.py` сторожил ТЕРМИНОЛОГИЮ: чтобы `slice` назывался одним
  словом, а не тремя.

Оба зелёные, и оба честно называют границу: смысл предложений им не поддаётся.
Но между смыслом и словом есть слой, который поддаётся счёту, — ПРОЗА. Перевод,
из которого пропал абзац, имеет тот же профиль раздела (ни блоков команд, ни
строк таблиц не убыло), те же ссылки и ту же шапку: структура совпала, а текста
меньше. Пропавшее предложение внутри абзаца не видно вообще ничем — ни числом
разделов, ни профилем, ни ссылками. Это самый вероятный вид потери при переводе:
переводчик пропустил строку, а проверка сказала «полный».

Что считается (границы блоков названы здесь, потому что от них зависит результат):

* **Раздел** — строка, начинающаяся с `## ` ВНЕ блока команд. Заголовок `#`
  третьего уровня и глубже разделом не является: подразделы остаются внутри
  своего раздела, как и в `check_translations.py`. Текст до первого `##`
  (шапка с переключателем языков) считается разделом 1 — в нём живая проза
  преамбулы, и её пропажа так же заметна, как пропажа абзаца в любом разделе;
* **Абзац** — подряд идущие строки, которые не являются разметкой: не блок
  команд, не заголовок любого уровня, не строка таблицы, не пункт списка, не
  цитата, не HTML-блок, не горизонтальная линия. Строки-ПРОДОЛЖЕНИЯ списка,
  цитаты и таблицы (у них нет своего маркера) прозой не считаются: это части тех
  же блоков, и именно на них счёт ломался в первом замере — в английском списке
  продолжение шло отдельной строкой, в переводе та же мысль уместилась в одну, и
  «пропавшим абзацем» выглядело то, чего не теряли;
* **Предложение** — знак конца предложения. Латинские `. ! ? …` считаются
  границей, если дальше пробел, конец строки или закрывающая кавычка/скобка/
  знак выделения (`*`, `_`, обратная кавычка): без последнего условия точка
  внутри `**On the model cache.**` не считалась концом предложения в английском,
  а в переводе считалась, и абзац расходился на единицу; иероглифические `。！？`
  считаются всегда — в китайском пробел после точки не ставится; `।` и `॥`
  (деванагари) — по тому же условию, что латинские;
* **Сокращения** не считаются концом предложения: `e.g.`, `i.e.`, `etc.`, `vs.`,
  `cf.`, `approx.`, `fig.`, `no.`, `т.е.`, `т.д.`, `т.п.`, `др.`, `напр.`, `см.`,
  `z.b.`, `bzw.`, `usw.`, `ggf.`, `ca.`, `sog.`, `evtl.`, `d.h.`, `p.ej.`,
  `aprox.`, `ud.`, `ej.`. Ищутся они ТОЛЬКО целым словом: сокращение `в.` без
  этого ограничения съедало конец любого слова на «в» — «проектов.» превращалось
  в «проектов·», и два английских предложения выглядели одним русским (замерено
  на этом дереве). Поэтому короткие формы `в.` и `г.` из списка убраны вовсе;
* **Абзац без знака конца** считается одним предложением: так же считает и
  человек, и иначе абзац-перечисление весил бы ноль.

Допуски — не в этом файле: они лежат в `docs/03_implementation/prose.json`
(по языку: сколько абзацев в разделе и сколько предложений в абзаце перевод
вправе разойтись с основным документом) и сняты замером. Нулевой допуск —
законное значение и самое сильное: замер 27.10.2026 дал ровно ноль расхождений в
пяти переводах, и именно ноль ловит и пропавший абзац, и склейку двух абзацев в
один, и потерянное предложение внутри абзаца.

Одна порча — одна находка. Если в разделе разошлось число абзацев, сравнение
предложений по этому разделу НЕ выполняется: они сверяются по позициям, и после
склейки двух абзацев позиции съезжают — вторая находка была бы счётом копий
одного дефекта, а не вторым свойством.

Границы, названные честно:

* смысл предложений по-прежнему не проверяется. Зелёный результат означает
  «пропущенной прозы по счёту нет», а не «перевод верен»: предложение может быть
  переведено наоборот, и счёт этого не увидит;
* проза в списках, таблицах, цитатах и блоках команд не считается — это части
  разметки, и их сохранность сторожат другие проверки (профиль раздела по блокам
  команд и строкам таблиц, наличие ссылок);
* разбиение предложения перестановкой слов не отличается от пропуска: считается
  число границ, а не состав;
* различия, законные для языка (одно английское предложение передано двумя),
  при нулевом допуске становятся находкой. Это осознанно: расширение допуска —
  решение владельца, названное числом в `prose.json`, а не молчаливая правка
  проверки.

Запуск:
    python tools/check_prose_profile.py [--root <дерево>]
Коды возврата: 0 — соответствие держится, 1 — есть расхождения сверх допуска
(включая «ноль осмотренных объектов»: это провал, а не PASS), 2 — не прочитаны
аргументы.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MAIN = "README.md"
PROSE = "docs/03_implementation/prose.json"
STATES = "docs/03_implementation/translations.json"

FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s")
TABLE = re.compile(r"^\s*\|")
LIST = re.compile(r"^\s*([-*+]|\d+[.)])\s")
QUOTE = re.compile(r"^\s*>")
HTML_BLOCK = re.compile(r"^\s*<[A-Za-z/!]")
RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")

#: Знак конца предложения и то, что вправе стоять сразу за ним, не отменяя
#: границы: закрывающая кавычка, скобка, знак выделения.
AFTER_END = r"(?=\s|$|[*_`\"'»”’\)\]])"
LATIN_END = re.compile(r"[.!?…]+" + AFTER_END)
DANDA_END = re.compile(r"[।॥]+" + AFTER_END)
#: Иероглифические знаки — без условия справа: в китайском пробела после
#: точки не ставят, и требовать его значило бы не видеть ни одной границы.
CJK_END = re.compile(r"[。！？]+")

#: Сокращения, точка которых не завершает предложение. Только целым словом.
ABBREVIATIONS = (
    "e.g.", "i.e.", "etc.", "vs.", "cf.", "approx.", "fig.", "no.",
    "т.е.", "т.д.", "т.п.", "др.", "напр.", "см.",
    "z.b.", "bzw.", "usw.", "ggf.", "ca.", "sog.", "evtl.", "d.h.",
    "p.ej.", "aprox.", "ud.", "ej.",
)
ABBREVIATION_RE = re.compile(
    r"(?<![\w])(?:"
    + "|".join(sorted((re.escape(item) for item in ABBREVIATIONS), key=len, reverse=True))
    + r")(?![\w])"
)

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


# --- счёт прозы -------------------------------------------------------------


def prose_sections(text: str) -> list[tuple[str, list[list[str]]]]:
    """Разделы `##` и абзацы прозы в каждом: [(заголовок, [абзац, ...]), ...]."""
    sections: list[tuple[str, list[list[str]]]] = [("шапка до первого ##", [])]
    mode = "text"  # text | list | quote | table | html | fence
    paragraph: list[str] = []
    for line in text.splitlines():
        if FENCE.match(line):
            mode = "text" if mode == "fence" else "fence"
            paragraph = []
            continue
        if mode == "fence":
            continue
        if not line.strip():
            paragraph = []
            mode = "text"
            continue
        heading = HEADING.match(line)
        if heading:
            paragraph = []
            mode = "text"
            if len(heading.group(1)) == 2:
                sections.append((line[3:].strip(), []))
            continue
        if RULE.match(line):
            paragraph = []
            mode = "text"
            continue
        if HTML_BLOCK.match(line):
            paragraph = []
            mode = "html"
            continue
        if TABLE.match(line):
            paragraph = []
            mode = "table"
            continue
        if LIST.match(line):
            paragraph = []
            mode = "list"
            continue
        if QUOTE.match(line):
            paragraph = []
            mode = "quote"
            continue
        if mode in ("list", "quote", "table", "html"):
            continue  # продолжение блока разметки — не проза
        if not paragraph:
            sections[-1][1].append(paragraph)  # объект добавляется по ссылке
        paragraph.append(line)
    return sections


def sentences_of(paragraph: list[str]) -> int:
    """Сколько предложений в абзаце по объявленным выше границам."""
    text = " ".join(item.strip() for item in paragraph).replace("\u00a0", " ")
    text = ABBREVIATION_RE.sub(lambda found: found.group(0).replace(".", "\u2024"), text)
    count = len(LATIN_END.findall(text)) + len(CJK_END.findall(text)) + len(DANDA_END.findall(text))
    if count == 0 and text.strip():
        return 1  # абзац без знака конца — одно предложение, а не ноль
    return count


def profile(path: Path) -> list[tuple[str, list[int]]]:
    """Профиль файла: по разделу — заголовок и число предложений в каждом абзаце."""
    return [
        (title, [sentences_of(paragraph) for paragraph in paragraphs])
        for title, paragraphs in prose_sections(read(path))
    ]


# --- объявления --------------------------------------------------------------


def load_prose(root: Path) -> tuple[dict | None, list[str]]:
    """Допуски из данных. None — объявления нет или оно не читается."""
    path = root / PROSE
    if not path.is_file():
        return None, [
            f"нет {PROSE} — допуски не объявлены данными, проверять нечего "
            f"(допуск в коде запрещён: иначе языковое различие нельзя назвать числом, не правя проверку)"
        ]
    try:
        data = json.loads(read(path))
    except json.JSONDecodeError as error:
        return None, [f"{PROSE} не разобран: {error}"]
    languages = data.get("languages")
    if not isinstance(languages, dict) or not languages:
        return None, [f"{PROSE}: список языков с допусками пуст — проверять нечего"]
    problems: list[str] = []
    for language, entry in languages.items():
        if not isinstance(entry, dict):
            problems.append(f"{PROSE}: язык {language} объявлен не записью с допусками")
            continue
        for key in ("paragraph_tolerance", "sentence_tolerance"):
            value = entry.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                problems.append(
                    f"{PROSE}: у языка {language} допуск {key} не объявлен целым неотрицательным числом"
                )
    declared_main = data.get("main")
    if declared_main != MAIN:
        problems.append(
            f"{PROSE}: основным документом назван {declared_main!r}, а проверка считает прозу {MAIN}"
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


# --- свойства ----------------------------------------------------------------


def check_language(root: Path, language: str, declared: str, tolerances: dict,
                   base: list[tuple[str, list[int]]]) -> None:
    """Соответствие одного перевода основному документу по разделам."""
    path = resolve_source(root, declared)
    if not path.is_file():
        fail(f"нет {declared} — перевод {language} объявлен, а файла нет: прозу считать не в чем")
        return
    other = profile(path)
    paragraph_tolerance = int(tolerances.get("paragraph_tolerance") or 0)
    sentence_tolerance = int(tolerances.get("sentence_tolerance") or 0)

    print(f"язык: {language} — разделов {len(other)}, "
          f"абзацев {sum(len(paragraphs) for _, paragraphs in other)}, "
          f"допуск по абзацам {paragraph_tolerance}, по предложениям {sentence_tolerance}")

    expect(
        len(other) == len(base),
        f"{declared}: разделов {len(other)}, а в {MAIN} их {len(base)}: сверять по разделам нечего, "
        f"пропавший раздел ловится проверкой состояний перевода",
        ok_note=f"{declared}: число разделов совпадает с {MAIN} ({len(base)})",
    )

    for index in range(min(len(base), len(other))):
        title, left = base[index]
        _, right = other[index]
        where = f"{declared}, раздел {index + 1} «{title}»"
        if abs(len(right) - len(left)) > paragraph_tolerance:
            expect(
                False,
                f"{where}: абзацев {len(right)}, а в {MAIN} {len(left)} "
                f"(допуск {paragraph_tolerance}): в разделе пропал или добавился абзац прозы",
            )
            continue  # зависимое свойство: позиции абзацев съехали, сверять по ним нечего
        expect(
            True,
            "",
            ok_note=f"{where}: абзацев {len(right)}, как в {MAIN}",
        )
        differed = False
        for position in range(min(len(left), len(right))):
            if abs(right[position] - left[position]) <= sentence_tolerance:
                continue
            differed = True
            expect(
                False,
                f"{where}, абзац {position + 1}: предложений {right[position]}, а в {MAIN} "
                f"{left[position]} (допуск {sentence_tolerance}): внутри абзаца пропало "
                f"или добавилось предложение",
            )
        if not differed:
            expect(
                True,
                "",
                ok_note=f"{where}: предложений в каждом из {len(right)} абзацев столько же, сколько в {MAIN}",
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Проза переводов README против основного документа")
    parser.add_argument("--root", default=str(REPO), help="корень проверяемого дерева")
    args = parser.parse_args()
    root = Path(args.root)

    prose, prose_problems = load_prose(root)
    for problem in prose_problems:
        fail(problem)
    languages, language_problems = load_languages(root)
    for problem in language_problems:
        fail(problem)

    main_path = root / MAIN
    if not main_path.is_file():
        fail(f"нет {MAIN} — сверять прозу не с чем")
        base: list[tuple[str, list[int]]] = []
    else:
        base = profile(main_path)
        print(f"основной документ: {MAIN} — разделов {len(base)}, "
              f"абзацев {sum(len(paragraphs) for _, paragraphs in base)}")

    if prose is not None and languages is not None:
        declared = prose["languages"]
        missing = sorted(set(languages) - set(declared))
        extra = sorted(set(declared) - set(languages))
        expect(
            not missing,
            f"{PROSE}: для языков {', '.join(missing)} перевод есть, а допуски не объявлены: "
            f"новый перевод остался бы непроверенным",
        )
        expect(
            not extra,
            f"{PROSE}: допуски объявлены для языков {', '.join(extra)}, а перевода на них нет: "
            f"объявление обещает проверку, которой нечего осматривать",
        )
        if not missing and not extra:
            expect(
                True,
                "",
                ok_note=f"языки объявления и перевода совпадают: {', '.join(sorted(languages))}",
            )

    if prose is not None and languages is not None and base:
        for language, declared_path in languages.items():
            entry = prose["languages"].get(language)
            if not isinstance(entry, dict):
                continue  # уже названо находкой о самом объявлении
            check_language(root, language, declared_path, entry, base)

    if EXAMINED == 0:
        fail("ни одного объекта не осмотрено: «ноль проверенного» — это не PASS")
    report()
    return 1 if FAILURES else 0


def report() -> None:
    print(
        "граница: считается ЧИСЛО абзацев в разделе и предложений в абзаце, а не смысл "
        "предложений; проза в списках, таблицах, цитатах и блоках команд не считается"
    )
    print(f"объявление допусков: {PROSE}")
    for note in OBSERVED:
        print(f"наблюдение: {note}")
    for failure in FAILURES:
        print(f"FAIL: {failure}")
    print(f"осмотрено: {EXAMINED}")
    if FAILURES:
        print(f"нарушений: {len(FAILURES)}")


if __name__ == "__main__":
    sys.exit(main())
