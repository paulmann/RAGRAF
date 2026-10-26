#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Документы поставки описывают ОДНУ закрытую поставку и два уровня лицензии.

Задача, из которой выросла проверка (этап И17, 26.10.2026). Решение владельца:
закрытая поставка одна (`RAGRAF Enterprise`), а `Pro` — уровень лицензии внутри
неё; переход Pro → Enterprise не требует установки. Код приведён к этому
решению этапом И15, а ДОКУМЕНТЫ — нет: `community/EULA.md` продолжал говорить
«относится к изданиям Pro и Enterprise» и «сборку соответствующего издания».
Расхождение видит покупатель, а не проверка: страницы сайта лежат вне корпуса,
EULA никто не сверял с базовой линией, и `grep` по «издание» находит десятки
законных упоминаний.

Проверка формулируется через свойства, а не через написание:

1. `community/EULA.md` существует и не пуст;
2. EULA называет ОБА уровня (Pro и Enterprise) и говорит, что поставка одна;
3. EULA описывает переход между уровнями как активацию лицензии, а не установку;
4. EULA описывает, что происходит при истечении срока (действует Community);
5. EULA называет каждую НАПИСАННУЮ платную возможность из
   `src/ragraf/edition_baseline.json` — и не выдаёт объявленные за написанные;
6. EULA не называет Pro отдельным пакетом или сборкой;
7. `tools/release/manifest.toml` действительно кладёт EULA в поставку;
8. переводы README (de, es, hi, zh) не заявляют двух пакетов — если заявляют,
   это находка, а не редакторская правка;
9. `editions/editions.toml` называет закрытую поставку единственной;
10. `docs/AGENTS-context.md` объявляет границу: страницы сайта живут в отдельном
    репозитории и проверяются его гейтом (`tools/check-editions.php`), иначе
    следующий агент посчитает расхождение проверенным.

Границы, названные честно:

* проверяется СМЫСЛ по ключевым утверждениям, а не литературный текст: EULA —
  юридический документ, и требовать в нём дословных формулировок значило бы
  ловить редакцию вместо факта;
* страницы сайта здесь не читаются: у них свой гейт и свой репозиторий (см.
  пункт 10). Проверка смотрит только на объявленную границу;
* объявленные и не написанные возможности (`secret-broker`, `connectors`,
  `fleet`) не обязаны быть в EULA; если они названы, рядом обязано стоять, что
  кода нет.

Запуск: `python tools/check_single_delivery.py [--root <дерево>] [--verbose]`
Коды возврата: 0 — свойства выполняются (есть осмотр), 1 — есть провалы,
2 — ошибка стенда (нет файлов, ноль осмотра).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

FAILURES: list[str] = []
EXAMINED = 0


def observe(ok: bool, what: str, why: str = "") -> None:
    global EXAMINED
    EXAMINED += 1
    if not ok:
        FAILURES.append(f"{what} — {why}" if why else what)


def read(root: Path, relative: str) -> str | None:
    path = root / relative
    if not path.is_file():
        observe(False, f"файл {relative}", "файла нет")
        return None
    text = path.read_text(encoding="utf-8")
    observe(True, f"файл {relative}")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    root = Path(args.root)

    # --- 1. EULA ---------------------------------------------------------
    eula = read(root, "community/EULA.md")
    if eula is None:
        print_result()
        return 1
    observe(len(eula.strip()) > 500, "EULA не пуст", f"длина {len(eula)} символов")

    # --- 2. оба уровня и одна поставка -----------------------------------
    observe("Pro" in eula and "Enterprise" in eula, "EULA называет оба уровня")
    observe(
        re.search(r"поставка\s+одн|одн\w* и тот же набор файлов|поставка\s*—?\s*одна", eula, re.I) is not None,
        "EULA говорит, что поставка одна",
        "нет утверждения «поставка одна» / «один и тот же набор файлов»",
    )
    observe(
        re.search(r"уровен\w*\s+лицензи\w*", eula, re.I) is not None,
        "EULA говорит про уровни лицензии",
    )

    # --- 3. переход без установки ----------------------------------------
    observe(
        re.search(r"установк\w*[^.]{0,120}не\s+выполня\w*", eula, re.I) is not None,
        "EULA: переход не требует установки",
        "нет утверждения, что установка при смене уровня не выполняется",
    )
    observe(
        re.search(r"pro\s*(→|->)\s*enterprise", eula, re.I) is not None,
        "EULA называет прямой переход Pro → Enterprise",
    )

    # --- 4. истечение срока ----------------------------------------------
    observe(
        re.search(r"по\s+истечении\s+срока", eula, re.I) is not None
        and re.search(r"действует\s+Community|уровень\s+Community", eula, re.I) is not None,
        "EULA: при истечении срока действует Community",
    )
    observe(
        re.search(r"данн\w*[^.]{0,80}оста\w*|оста\w*[^.]{0,60}данн\w*", eula, re.I) is not None,
        "EULA: данные при понижении остаются",
    )

    # --- 5. возможности против базовой линии ------------------------------
    baseline_path = root / "src/ragraf/edition_baseline.json"
    if not baseline_path.is_file():
        observe(False, "файл src/ragraf/edition_baseline.json", "базовой линии нет — сверять состав не с чем")
    else:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        observe(True, "файл src/ragraf/edition_baseline.json")
        features = baseline.get("features", [])
        paid_shipped = [f for f in features if f.get("edition") in ("pro", "enterprise") and f.get("status") == "shipped"]
        paid_declared = [f for f in features if f.get("edition") in ("pro", "enterprise") and f.get("status") != "shipped"]
        observe(len(paid_shipped) >= 1, "в базовой линии есть написанные платные возможности", f"их {len(paid_shipped)}")
        for feature in paid_shipped:
            name = str(feature.get("name", ""))
            observe(name in eula, f"EULA называет возможность {name}", "написанная возможность в EULA не названа")
        for feature in paid_declared:
            name = str(feature.get("name", ""))
            if name not in eula:
                continue
            window = eula[max(0, eula.find(name) - 400) : eula.find(name) + 400]
            observe(
                re.search(r"не\s+написан|объявлен|нет\s+кода|кода\s+не\s+име", window, re.I) is not None,
                f"EULA не выдаёт {name} за написанную",
                "возможность названа без оговорки, что кода нет",
            )

    # --- 6. Pro не отдельный пакет ---------------------------------------
    # Оборот «сборка Pro» сам по себе запрещён НЕ всегда: честная фраза
    # «Сборка Pro и сборка Enterprise — один и тот же набор файлов» — это ответ
    # на недоумение покупателя, а не заявление о двух пакетах. Поэтому такой
    # оборот проваливает проверку только тогда, когда рядом (в радиусе ±160
    # символов) нет утверждения, что файлы те же. Измерено 26.10.2026: без этого
    # правила собственная честная формулировка EULA давала ложный провал.
    same_files = r"один и тот же набор файлов|файлы те же|файлы не различаются|теми же файлами|те же файлы"
    forbidden_eula = (
        (r"сборк\w*\s+соответствующего\s+издания", None),
        (r"пакет\w*\s+Pro", None),
        (r"сборк\w*\s+Pro\b", same_files),
        (r"два\s+издания", None),
        (r"каждое\s+издание\s+поставляется", None),
    )
    for pattern, allow in forbidden_eula:
        hits = []
        for match in re.finditer(pattern, eula, re.I):
            if allow is not None:
                window = eula[max(0, match.start() - 160) : match.end() + 160]
                if re.search(allow, window, re.I):
                    continue
            hits.append(match.group(0))
        observe(
            not hits,
            f"EULA не называет Pro отдельным пакетом ({pattern})",
            "найден оборот без оговорки, что файлы те же: " + ", ".join(hits[:3]),
        )

    # --- 7. EULA действительно едет в поставку ---------------------------
    manifest = read(root, "tools/release/manifest.toml")
    if manifest is not None:
        observe(
            re.search(r'from\s*=\s*"community/EULA\.md"', manifest) is not None
            and re.search(r'to\s*=\s*"EULA\.md"', manifest) is not None,
            "манифест кладёт EULA в поставку как EULA.md",
            "в манифесте нет переноса community/EULA.md → EULA.md",
        )

    # --- 8. переводы README ----------------------------------------------
    translations = ("community/README.de.md", "community/README.es.md", "community/README.hi.md", "community/README.zh.md")
    for relative in translations:
        text = read(root, relative)
        if text is None:
            continue
        found = []
        for pattern in (r"Pro-Paket", r"paquete\s+Pro", r"Pro\s+पैकेज", r"Pro\s*包", r"two\s+packages", r"zwei\s+Pakete"):
            if re.search(pattern, text, re.I):
                found.append(pattern)
        observe(found == [], f"{relative}: Pro не назван отдельным пакетом", "найдены обороты: " + ", ".join(found))

    # --- 9. карта изданий ------------------------------------------------
    editions = read(root, "editions/editions.toml")
    if editions is not None:
        observe(
            re.search(r"ЕДИНСТВЕННАЯ\s+закрытая\s+поставка", editions, re.I) is not None,
            "editions.toml: закрытая поставка названа единственной",
        )
        observe(
            re.search(r"[Уу]ровне\w*\s+лицензии\s+два|Уровней\s+лицензии\s+два", editions) is not None,
            "editions.toml: уровней лицензии два",
        )

    # --- 10. объявленная граница про сайт --------------------------------
    context = read(root, "docs/AGENTS-context.md")
    if context is not None:
        marker = "ragraf-site"
        gate = "check-editions.php"
        observe(marker in context, "AGENTS-context объявляет репозиторий сайта", "нет упоминания ragraf-site")
        observe(gate in context, "AGENTS-context называет гейт страниц сайта", f"нет упоминания {gate}")

    print_result()
    if EXAMINED == 0:
        print("FAIL: ни одного наблюдения — это ложный PASS, а не успех")
        return 2
    return 0 if not FAILURES else 1


def print_result() -> None:
    print(f"осмотрено: {EXAMINED}")
    for failure in FAILURES:
        print("FAIL: " + failure)
    print(f"провалов: {len(FAILURES)}")


if __name__ == "__main__":
    sys.exit(main())
