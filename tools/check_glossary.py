#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Терминология переводов README: объявлена ДАННЫМИ, подтверждена вхождениями.

Задача, из которой проверка выросла (этап И27, 27.10.2026). К этому моменту
`tools/check_translations.py` подтверждал, что все пять переводов полные, и честно
называл свою границу: «полный» там означает «приведён к текущему продукту и
подтверждён структурой разделов, ссылками и числами», а не «переведён без потерь».
Смысл фраз машинной проверке не поддаётся, но ТЕРМИНОЛОГИЯ — поддаётся: если
`slice` в основном документе назван, то и в переводе он должен называться одним
словом, а не тремя. Измерено на этом дереве: `stale` в испанском переводе был
передан как «atrasado» (отставание по времени), тогда как в остальных четырёх
языках — состоянием («устаревший», «veraltet», «पुराना», «过期»); подмена слова на
синоним проходила зелёной, потому что ни одна проверка слова не считала.

Что проверяется — три свойства, и ни одно из них не про смысл предложения:

1. **Термин основного документа вообще назван в переводе.** Для термина,
   объявленного обязательным (`required`), перевод, который его не называет ни
   одним из разрешённых слов, — находка. Обязательность ставится только там, где
   термин называет операцию, режим или объект продукта, а не свойство или
   механизм, который язык вправе выразить внутри фразы.
2. **Один термин — одно слово.** Для каждого языка объявлен список допустимых
   переводов, первый из которых канонический. Канонический обязан встречаться не
   реже остальных: перевод, где он вытеснен синонимом, — находка. Несколько
   вариантов разрешены намеренно: у языка есть формы одной леммы («деградация» и
   «деградирует», «Korpus» и «Korpora»), и требовать ровно одну строку значило бы
   ловить морфологию, а не термин.
3. **Одно слово — одно понятие (обратный дрейф).** Слово, объявленное каноническим
   для одного термина, не может быть разрешённым для другого: иначе одно слово
   перевода обозначает два разных понятия продукта, и читатель не может отличить
   режим от состояния. Проверяется по объявлению, а не по тексту: это свойство
   СЛОВАРЯ, и нарушение в нём делает недостоверным всё, что им сторожится.

Объявление лежит в `docs/03_implementation/glossary.json` по той же причине, по
которой там же лежат состояния переводов: добавить термин или язык можно правкой
данных, и ни одна строка этой проверки ради этого не меняется. Проверка не держит
словаря в коде — иначе объявление терминологии стало бы кодом, который надо править
ради каждой новой формулировки.

Границы, названные честно:

* смысл предложений по-прежнему не проверяется: пропущенное предложение при
  сохранённой структуре раздела не видно ни здесь, ни в `check_translations.py`.
  Зелёный результат этой проверки означает «термины названы одним словом», а не
  «перевод верен»;
* слово ищется с ограничением слева по букве: «Schnitt» не считается внутри
  «Ausschnitt», но «корпус» считается внутри «корпуса». К иероглифическим словам
  (китайский) ограничение не применяется — там нет пробелов между словами, и тогда
  сложное слово, содержащее канонический термин, от него не отличается: это
  названное ограничение, а не недосмотр;
* термины, которых нет в основном документе, не проверяются в переводах: находка
  о самом объявлении уже есть, и вторая на каждый язык была бы счётом копий;
* проверяется наличие термина, а не его согласованность по падежам и числам.

Запуск:
    python tools/check_glossary.py [--root <дерево>]
Коды возврата: 0 — свойства выполняются, 1 — есть нарушения (в том числе «ноль
осмотренных объектов»: это провал, а не PASS), 2 — не прочитаны аргументы.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MAIN = "README.md"
GLOSSARY = "docs/03_implementation/glossary.json"
STATES = "docs/03_implementation/translations.json"

EXAMINED = 0
FAILURES: list[str] = []
OBSERVED: list[str] = []
WARNINGS: list[str] = []


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


def warn(message: str) -> None:
    """Наблюдение, которого нельзя предъявить как находку."""
    WARNINGS.append(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


#: Иероглифы: в китайском нет пробелов между словами, поэтому ограничение слева
#: по букве к ним неприменимо — оно отбрасывало бы почти все вхождения (замер на
#: этом дереве: «切片» 2 против 0, «语料» 24 против 6).
HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def count_of(text: str, word: str) -> int:
    """Сколько раз слово встречается в тексте как самостоятельное начало слова.

    Ограничение слева по букве нужно потому, что иначе «Schnitt» считался бы внутри
    «Ausschnitt», а «деградац» — внутри слова с другой основой; канонический
    вариант выглядел бы употребительнее, чем он есть (измерено на этом дереве
    для немецкого: 6 против 5).

    К иероглифическим словам это правило не применяется: там нет пробелов, и
    левый сосед — тоже буква. Для них считается подстрока, и сложное слово,
    содержащее канонический термин, от него не отличается — ограничение названо
    в шапке проверки.
    """
    if not word:
        return 0
    if HAN.match(word[0]) or HAN.match(word[-1]):
        return text.lower().count(word.lower())
    pattern = re.compile(r"(?<![^\W\d_])" + re.escape(word), re.IGNORECASE)
    return len(pattern.findall(text))


def resolve_source(root: Path, declared: str) -> Path:
    """Файл перевода: в живом дереве `community/…`, в поставке — в корне."""
    direct = root / declared
    if direct.is_file():
        return direct
    return root / Path(declared).name


# --- объявление терминологии ------------------------------------------------


def load_glossary(root: Path) -> tuple[dict | None, list[str]]:
    """Объявление терминов из данных. None — объявления нет или оно не читается."""
    path = root / GLOSSARY
    if not path.is_file():
        return None, [
            f"нет {GLOSSARY} — терминология не объявлена данными, проверять нечего "
            f"(словарь в коде запрещён: иначе новый термин нельзя добавить, не правя проверку)"
        ]
    try:
        data = json.loads(read(path))
    except json.JSONDecodeError as error:
        return None, [f"{GLOSSARY} не разобран: {error}"]
    terms = data.get("terms")
    if not isinstance(terms, list) or not terms:
        return None, [f"{GLOSSARY}: список терминов пуст — проверять нечего"]
    problems: list[str] = []
    seen: set[str] = set()
    for index, term in enumerate(terms, 1):
        if not isinstance(term, dict) or not isinstance(term.get("term_en"), str):
            problems.append(f"{GLOSSARY}: запись {index} не называет термин (term_en)")
            continue
        name = term["term_en"]
        if not name.strip():
            problems.append(f"{GLOSSARY}: запись {index} называет пустой термин")
        if name in seen:
            problems.append(f"{GLOSSARY}: термин {name!r} объявлен дважды")
        seen.add(name)
        if not isinstance(term.get("required"), bool):
            problems.append(f"{GLOSSARY}: у термина {name!r} не объявлено, обязателен ли он (required)")
        forms = term.get("forms_en")
        if not isinstance(forms, list) or not forms or not all(isinstance(form, str) and form for form in forms):
            problems.append(f"{GLOSSARY}: у термина {name!r} не объявлено ни одной формы (forms_en)")
        translations = term.get("translations")
        if not isinstance(translations, dict) or not translations:
            problems.append(f"{GLOSSARY}: у термина {name!r} нет ни одного перевода (translations)")
            continue
        for language, entry in translations.items():
            if not isinstance(entry, dict):
                problems.append(f"{GLOSSARY}: у термина {name!r} перевод на {language} объявлен не записью")
                continue
            allowed = entry.get("allowed")
            if not isinstance(allowed, list) or not allowed:
                problems.append(
                    f"{GLOSSARY}: у термина {name!r} на языке {language} нет допустимых вариантов (allowed)"
                )
                continue
            empty = [item for item in allowed if not isinstance(item, str) or not item.strip()]
            if empty:
                problems.append(
                    f"{GLOSSARY}: у термина {name!r} на языке {language} есть пустой вариант перевода"
                )
            duplicates = {item for item in allowed if isinstance(item, str) and allowed.count(item) > 1}
            if duplicates:
                problems.append(
                    f"{GLOSSARY}: у термина {name!r} на языке {language} вариант "
                    f"{', '.join(sorted(duplicates))} объявлен дважды"
                )
    return (data if not problems else None), problems


def load_languages(root: Path) -> tuple[dict[str, str] | None, list[str]]:
    """Языки и файлы переводов из объявления состояний: второй источник не нужен."""
    path = root / STATES
    if not path.is_file():
        return None, [f"нет {STATES}: перечень языков и файлов переводов брать не из чего"]
    try:
        data = json.loads(read(path))
    except json.JSONDecodeError as error:
        return None, [f"{STATES} не разобран: {error}"]
    states = data.get("states")
    if not isinstance(states, list) or not states:
        return None, [f"{STATES}: список состояний пуст — перечень языков взять не из чего"]
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


# --- свойства ---------------------------------------------------------------


def check_reverse_drift(glossary: dict, languages: dict[str, str]) -> None:
    """Одно слово перевода не может обозначать два разных понятия продукта."""
    terms = glossary["terms"]
    for language in languages:
        owners: dict[str, list[str]] = {}
        for term in terms:
            entry = (term.get("translations") or {}).get(language)
            if not isinstance(entry, dict):
                continue
            for word in entry.get("allowed") or []:
                if not isinstance(word, str) or not word:
                    continue
                owners.setdefault(word.lower(), []).append(term["term_en"])
        shared = {word: names for word, names in owners.items() if len(set(names)) > 1}
        expect(
            not shared,
            f"{language}: одно слово объявлено переводом двух разных терминов — "
            + "; ".join(
                f"{word!r} для {', '.join(sorted(set(names)))}" for word, names in sorted(shared.items())
            ),
            ok_note=f"{language}: ни одно слово не объявлено переводом двух терминов",
        )


def check_term(root: Path, term: dict, languages: dict[str, str], main_text: str) -> None:
    name = term["term_en"]
    forms = [form for form in term.get("forms_en") or [] if isinstance(form, str) and form]
    required = bool(term.get("required"))
    found = sum(count_of(main_text, form) for form in forms)
    expect(
        found > 0,
        f"{GLOSSARY}: термин {name!r} объявлен, но в {MAIN} не встречается ни в одной "
        f"из объявленных форм ({', '.join(forms)}): объявление сторожит слово, которого в "
        f"основном документе нет",
    )
    if found == 0:
        # Зависимые свойства: переводам сверяться не с чем, и находка о самом
        # объявлении уже есть — вторая на каждый язык была бы счётом копий.
        expect(
            True,
            "",
            ok_note=f"термин {name}: переводы не сверялись — термина нет в {MAIN}, "
                    f"об этом одна находка, а не по одной на язык",
        )
        return
    print(f"термин: {name} — вхождений в {MAIN}: {found}"
          + ("" if required else " (обязательным не объявлен)"))

    for language, declared in languages.items():
        path = resolve_source(root, declared)
        if not path.is_file():
            fail(f"нет {declared} — перевод {language} объявлен, а файла нет: термин {name!r} сторожить нечем")
            continue
        content = read(path)
        entry = (term.get("translations") or {}).get(language)
        if not isinstance(entry, dict) or not entry.get("allowed"):
            expect(
                False,
                f"{GLOSSARY}: для термина {name!r} не объявлено ни одного варианта на языке "
                f"{language}, а перевод на этом языке есть: термин остаётся непроверенным",
            )
            continue
        allowed = [word for word in entry["allowed"] if isinstance(word, str) and word]
        canonical = allowed[0]
        counts = {word: count_of(content, word) for word in allowed}
        total = sum(counts.values())
        others = {word: number for word, number in counts.items() if word != canonical}
        top_other = max(others.values()) if others else 0

        if total == 0:
            expect(
                not required,
                f"{declared}: обязательный термин {name!r} не назван ни одним из объявленных "
                f"вариантов ({', '.join(allowed)}): перевод обходит понятие продукта",
                ok_note=f"{declared}: термин {name!r} не назван — он объявлен необязательным, "
                        f"это наблюдение, а не находка",
            )
        elif counts[canonical] == 0:
            worst = max(others, key=lambda word: others[word])
            expect(
                False,
                f"{declared}: канонический вариант термина {name!r} — {canonical!r} — не "
                f"встречается ни разу, а синоним {worst!r} — {others[worst]} раз: термин назван "
                f"другим словом",
            )
        elif counts[canonical] < top_other:
            worst = max(others, key=lambda word: others[word])
            expect(
                False,
                f"{declared}: канонический вариант термина {name!r} — {canonical!r} — "
                f"{counts[canonical]} раз, а синоним {worst!r} — {others[worst]}: канонический "
                f"вытесняется",
            )
        else:
            detail = ", ".join(f"{word!r} {number}" for word, number in counts.items() if number)
            expect(
                True,
                "",
                ok_note=f"{declared}: термин {name!r} назван канонически ({detail})",
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Терминология переводов README")
    parser.add_argument("--root", default=str(REPO), help="корень проверяемого дерева")
    args = parser.parse_args()
    root = Path(args.root)

    glossary, problems = load_glossary(root)
    for problem in problems:
        fail(problem)
    languages, language_problems = load_languages(root)
    for problem in language_problems:
        fail(problem)

    main_path = root / MAIN
    main_text = read(main_path) if main_path.is_file() else None
    if main_text is None:
        fail(f"нет {MAIN} — искать термины негде")

    if glossary is not None and languages is not None:
        declared_languages = {
            language
            for term in glossary["terms"]
            for language in (term.get("translations") or {})
        }
        missing = sorted(set(languages) - declared_languages)
        extra = sorted(declared_languages - set(languages))
        expect(
            not missing,
            f"{GLOSSARY}: для языков {', '.join(missing)} перевод есть, а терминология не "
            f"объявлена: новый перевод остался бы непроверенным",
        )
        expect(
            not extra,
            f"{GLOSSARY}: терминология объявлена для языков {', '.join(extra)}, а перевода на "
            f"них нет: объявление обещает проверку, которой нечего осматривать",
        )
        if not missing and not extra:
            expect(
                True,
                "",
                ok_note=f"языки объявления и перевода совпадают: {', '.join(sorted(languages))}",
            )
        check_reverse_drift(glossary, languages)

    if glossary is not None and main_text is not None:
        for term in glossary["terms"]:
            if languages is None:
                # Без перечня языков проверять переводы не в чем; об этом уже есть
                # находка, а термины основного документа всё равно осматриваются.
                check_term(root, term, {}, main_text)
                continue
            check_term(root, term, languages, main_text)

    if EXAMINED == 0:
        fail("ни одного объекта не осмотрено: «ноль проверенного» — это не PASS")
    report()
    return 1 if FAILURES else 0


def report() -> None:
    print(
        "граница: проверяются СЛОВА терминов, а не смысл фраз; пропущенное предложение "
        "при сохранённой структуре раздела эта проверка не видит"
    )
    print(f"объявление терминологии: {GLOSSARY}")
    for note in OBSERVED:
        print(f"наблюдение: {note}")
    for warning in WARNINGS:
        print(f"WARN: {warning}")
    for failure in FAILURES:
        print(f"FAIL: {failure}")
    print(f"осмотрено: {EXAMINED}")
    if WARNINGS and not FAILURES:
        print(f"предупреждений: {len(WARNINGS)}")
    if FAILURES:
        print(f"нарушений: {len(FAILURES)}")


if __name__ == "__main__":
    sys.exit(main())
