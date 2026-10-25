# -*- coding: utf-8 -*-
"""Копии навыка: совпадение с проектом либо объявленное различие — и проверка обоих.

Зачем отдельная проверка. Требование `AGENTS.md` §11 «копия в служебном каталоге
обязана совпадать с проектной побайтово» держалось на памяти: замер 25.10.2026
показал, что копии разошлись (`operations.md` 16 633 Б против 17 653 Б, в копии
«39 операций»), причём копия в дереве и копия у учётной записи совпадали МЕЖДУ
СОБОЙ — то есть сравнение копий друг с другом ничего не обнаруживает.

Проверяется свойство, а не написание. Свойство одно: у каждого файла навыка либо
копия равна источнику побайтово, либо различие ОБЪЯВЛЕНО в
`docs/skills/ragraf-core/VARIANTS.json` — и объявление сходится с фактом.

Почему объявления недостаточно и нужны правила. Пара sha256 говорит «этот файл
такой, каким его объявили», но не говорит, что в нём нет выдуманного имени
инструмента или устаревшего числа. Поэтому у объявленного файла есть правила:
`marker` (само различие названо в файле), `tool_names_exist_in_code` (полное имя
`mcp__ragraf__ragraf_*` обязано существовать в `mcp_server.py`) и
`numbers_match_code` (числа операций, инструментов и проверок берутся из кода).

Границы честные. Копия вне дерева может отсутствовать (другая машина, другая
учётная запись): это печатается как «не осмотрена», но не выдаётся ни за
совпадение, ни за находку. Копия В ДЕРЕВЕ обязана быть — её отсутствие находка.

Коды возврата: 0 — объявления сходятся, 1 — находка либо объявления нет,
2 — реестр вариантов не разбирается.
"""

from __future__ import annotations

import glob
import hashlib
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
DECLARATION = REPO / "docs" / "skills" / "ragraf-core" / "VARIANTS.json"
MCP_SERVER = REPO / "src" / "ragraf" / "mcp_server.py"
FACADE = REPO / "src" / "ragraf" / "facade.py"
DOCTRINE = REPO / "tools" / "Invoke-ProjectDoctrine.ps1"

# Числовые заявления, которые копия обязана разделять с кодом.
NUMBER_CLAIM = re.compile(r"(\d+)\s*(операци|инструмент|проверк)")
NUMBER_KIND = {"операци": "ops", "инструмент": "tools", "проверк": "checks"}

FAILS: list[str] = []
EXAMINED = 0


def sha256_of(path: pathlib.Path) -> str:
    """sha256 файла байтами: сравнение копий, а не текстов."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code_counts() -> dict[str, object]:
    """Числа, с которыми сравниваются заявления копии: из кода, не из документов."""
    mcp_text = MCP_SERVER.read_text(encoding="utf-8")
    tools = set(re.findall(r'"name":\s*"(ragraf_[a-z_]+)"', mcp_text))
    facade_text = FACADE.read_text(encoding="utf-8")
    ops = re.findall(r"(?m)^\s*def (op_[a-z_]+)\(", facade_text)
    doctrine_text = DOCTRINE.read_text(encoding="utf-8")
    return {"tools": tools, "ops": len(ops),
            "checks": registered_checks(doctrine_text)}


def registered_checks(doctrine_text: str) -> int:
    """Число проверок чекера: записи в его реестре, а не число функций Test-*.

    Считать функции нельзя: `Test-RegexLiteral` — помощник, а не проверка, и такой
    счёт даёт 30 при действующих 29. Реестр объявлен в `$script:CheckFunctions`,
    и именно его длина печатается в итоге прогона — значит сравнивать надо с ней.
    """
    block = re.search(r"\$script:CheckFunctions\s*=\s*@\((.*?)\n\)", doctrine_text,
                      re.S)
    if not block:
        return 0
    return len(re.findall(r"'(Test-[A-Za-z]+)'", block.group(1)))


def fail(text: str) -> None:
    FAILS.append(text)
    print("FAIL: " + text)


def note(text: str) -> None:
    print("  " + text)


def apply_rule(rule: dict, text: str, label: str, counts: dict[str, object]) -> None:
    """Одно правило объявленного файла. Правило без реализации — находка."""
    global EXAMINED
    kind = str(rule.get("kind") or "")
    if kind == "marker":
        EXAMINED += 1
        marker = str(rule.get("text") or "")
        if not marker:
            fail(label + ": правило marker без текста: проверять нечего")
        elif marker not in text:
            fail(label + ": различие не объявлено в самом файле — нет строки "
                      + repr(marker))
        else:
            note(label + ": различие объявлено в файле (" + marker + ")")
        return
    if kind == "tool_names_exist_in_code":
        EXAMINED += 1
        used = sorted(set(re.findall(r"mcp__ragraf__(ragraf_[a-z_]+)", text)))
        if not used:
            fail(label + ": копия не называет ни одного полного имени инструмента — "
                      "правило нечего проверять, значит шаблон разошёлся с файлом")
            return
        missing = [name for name in used if name not in counts["tools"]]
        if missing:
            fail(label + ": копия называет инструменты, которых нет в mcp_server.py: "
                      + ", ".join(missing))
        else:
            note(label + ": все " + str(len(used)) + " полных имён инструментов "
                      "существуют в коде")
        return
    if kind == "numbers_match_code":
        EXAMINED += 1
        claims = list(NUMBER_CLAIM.finditer(text))
        if not claims:
            note(label + ": числовых заявлений нет — правило проверять нечего "
                      "(это названо, а не выдано за успех)")
            return
        for match in claims:
            stated = int(match.group(1))
            kind_key = NUMBER_KIND[match.group(2)]
            actual = counts[kind_key]
            if kind_key == "tools":
                actual = len(counts["tools"])
            if stated != int(actual):
                fail(label + ": называет " + str(stated) + " (" + match.group(0)
                     + "), в коде " + str(actual))
            else:
                note(label + ": число " + str(stated) + " совпадает с кодом")
        return
    fail(label + ": объявлено правило " + repr(kind) + ", которого проверка не знает: "
              "объявление не проверено, а значит недействительно")


def copy_roots(entry: dict) -> list[pathlib.Path]:
    """Корни копий по объявлению: `~` разворачивается, маска раскрывается.

    Маска (`*` в пути) объявлена не для удобства: копия живёт в каталоге каждой
    учётной записи этой машины, а перечислять чужие идентификаторы в публикуемом
    файле нельзя. Раскрытие маски означает, что новая учётная запись попадает под
    проверку сама, без правки объявления.
    """
    raw = str(entry.get("root") or "")
    if raw.startswith("~"):
        raw = str(pathlib.Path.home()) + raw[1:]
    candidate = pathlib.Path(raw)
    if not candidate.is_absolute():
        candidate = REPO / raw
    pattern = str(candidate)
    if any(char in pathlib.Path(pattern).name for char in "*?[") or "*" in pattern:
        return [pathlib.Path(item) for item in sorted(glob.glob(pattern, recursive=True))]
    return [candidate]


def main() -> int:
    global EXAMINED
    if not DECLARATION.exists():
        print("FAIL: NOT-IMPLEMENTED: нет " + str(DECLARATION.relative_to(REPO))
              + " — объявлять различие нечем, а без объявления копия обязана "
                "совпадать побайтово")
        return 1
    try:
        declaration = json.loads(DECLARATION.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        print("FAIL: реестр вариантов не разбирается: " + type(exc).__name__)
        return 2

    source_dir = REPO / str(declaration.get("source") or "docs/skills/ragraf-core")
    declared_files = [str(name) for name in (declaration.get("files") or [])]
    if not declared_files:
        print("FAIL: реестр не перечисляет файлы навыка: проверять нечего")
        return 1
    for name in declared_files:
        EXAMINED += 1
        if not (source_dir / name).is_file():
            fail("источник навыка не содержит " + name + ": " + str(source_dir / name))

    counts = code_counts()
    if int(counts["checks"]) < 1:
        fail("реестр проверок чекера не прочитан: сравнивать числовые заявления "
             "копии с нулём значило бы объявить верным любое число")
    note("код: инструментов " + str(len(counts["tools"])) + ", операций "
         + str(counts["ops"]) + ", проверок " + str(counts["checks"]))

    for variant in declaration.get("variants") or []:
        name = str(variant.get("name") or "?")
        files = variant.get("files") or {}
        missing_modes = [f for f in declared_files if f not in files]
        if missing_modes:
            fail("вариант " + name + ": у файлов " + ", ".join(missing_modes)
                 + " не объявлен режим: молчание не равно совпадению")
        for entry in variant.get("copies") or []:
            roots = copy_roots(entry)
            if not roots:
                note(name + " (" + str(entry.get("root")) + "): копий по объявлению "
                     "нет — не осмотрены (это не совпадение и не находка)")
                continue
            for root in roots:
              label = name + " (" + str(root) + ")"
              if not root.is_dir():
                if entry.get("in_tree"):
                    fail("копия в дереве отсутствует: " + str(root))
                else:
                    note(label + ": копия вне дерева отсутствует — не осмотрена")
                continue
              for file_name in declared_files:
                mode = files.get(file_name) or {}
                target = root / file_name
                EXAMINED += 1
                if not target.is_file():
                    fail(label + ": в копии нет " + file_name + ": " + str(target))
                    continue
                source_hash = sha256_of(source_dir / file_name)
                copy_hash = sha256_of(target)
                kind = str(mode.get("mode") or "")
                if kind == "identical":
                    if source_hash != copy_hash:
                        fail(label + ": " + file_name + " не совпадает с проектом "
                             "побайтово (проект " + source_hash[:12] + " "
                             + str((source_dir / file_name).stat().st_size) + " Б, копия "
                             + copy_hash[:12] + " " + str(target.stat().st_size)
                             + " Б) — это забытая синхронизация")
                    else:
                        note(label + ": " + file_name + " совпадает с проектом побайтово")
                    continue
                if kind != "declared":
                    fail(label + ": у " + file_name + " неизвестный режим " + repr(kind))
                    continue
                declared = mode.get("declared") or {}
                stated_source = str(declared.get("sha256_source") or "")
                stated_copy = str(declared.get("sha256_copy") or "")
                if not stated_source or not stated_copy:
                    fail(label + ": " + file_name + " объявлен как declared без пары "
                         "sha256 — объявление непроверяемо")
                    continue
                if source_hash == copy_hash:
                    fail(label + ": " + file_name + " объявлен различающимся, но "
                         "совпадает с проектом побайтово — объявление описывает "
                         "различие, которого нет; его надо снять")
                    continue
                if stated_source != source_hash:
                    fail(label + ": источник " + file_name + " изменился после "
                         "объявления (объявлен " + stated_source[:12] + ", факт "
                         + source_hash[:12] + ") — копия не пересобрана, это забытая "
                         "синхронизация")
                    continue
                if stated_copy != copy_hash:
                    fail(label + ": копия " + file_name + " изменена после объявления "
                         "(объявлена " + stated_copy[:12] + ", факт " + copy_hash[:12]
                         + ") — объявление описывает другой файл")
                    continue
                note(label + ": " + file_name + " различается с проектом, различие "
                             "объявлено (проект " + source_hash[:12] + ", копия "
                             + copy_hash[:12] + ")")
                for rule in mode.get("rules") or []:
                    apply_rule(rule, target.read_text(encoding="utf-8"),
                               label + ": " + file_name, counts)

    print()
    print("осмотрено: " + str(EXAMINED) + ", находок: " + str(len(FAILS)))
    if EXAMINED == 0:
        print("FAIL: ни одного объекта не осмотрено — это ложный PASS")
        return 1
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
