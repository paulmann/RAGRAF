#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Карта изданий: что публикуется, что удержано и что не пропало.

Задача, из которой это выросло (24.10.2026, указание владельца): идёт разработка
нескольких продуктов, и урезание одного из них не имеет права уничтожать
разработанное. Формулировка владельца: «Если ты урезаешь что-то для Community, оно
должно остаться в остальных папках, более полных пакетов».

Проверка отвечает на один вопрос свойством, а не обещанием: **у каждого файла
живого дерева есть место**. Файл либо попадает в сборку Community, либо объявлен
удержанным с причиной — правилом манифеста или папкой издания. Файл, о котором
нельзя сказать ни того, ни другого, пропадёт молча при следующей перестройке
дерева. Именно так уже терялся монолит Print-Spooler.

Границы названы прямо:

* проверяется ЖИВОЕ дерево, а не собранное: собранное уже прошло свой проверяльщик;
* «машино-локальное» (`.venv`, `.tools`, `.ragraf`, кеши) продуктом не является и
  перечислено в `MACHINE_LOCAL` с причиной — это не «забыли», это другой класс;
* «мусор оболочки» (файл нулевой длины, в имени нет букв и цифр) называется
  своим именем: у него причина и правка другие, чем у «файла без места»;
* файл считается удержанным по правилу манифеста, только если правило с ним
  совпало; «правило есть, но оно про другое» здесь не проходит;
* проверка не решает, ПОЛЕЗЕН ли удержанный файл: она требует, чтобы удержание
  было названо.

Запуск:
    python tools/release/editions_report.py              # вердикт и сводка
    python tools/release/editions_report.py --write      # плюс переписать таблицу
    python tools/release/editions_report.py --limit 40   # сколько строк печатать

Коды возврата: 0 — у каждого файла есть место, 1 — есть файлы без места,
2 — ошибка аргументов или разбора декларации.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import tomllib
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import relcommon as rel  # noqa: E402 - путь к модулю добавлен выше

REPO = pathlib.Path(__file__).resolve().parents[2]
DECLARATION = REPO / "editions/editions.toml"
REPORT_DOC = REPO / "editions/EXCLUDED-FROM-COMMUNITY.md"
MANIFEST = REPO / "tools/release/manifest.toml"
#: Манифесты закрытых изданий берутся из объявления (`editions/editions.toml`),
#: а не из списка в этом файле: с 26.10.2026 закрытая поставка одна (Enterprise),
#: и карта не должна требовать правки при изменении числа изданий.
#: Файл, лежащий в папке объявленного издания, публикуется, и называть его
#: удержанным значило бы врать о его судьбе: удержание — это «ни в одну поставку
#: не входит», а он входит.

#: Каталоги и файлы, которые продуктом не являются ВООБЩЕ: они живут на машине и
#: пересоздаются. Причина у каждого — чтобы список нельзя было расширить молча.
MACHINE_LOCAL: tuple[tuple[str, str], ...] = (
    (".git", "история git: не файл продукта, в поставке её заменяет сборка"),
    (".ragraf", "индекс корпуса этой машины: пересобирается, в чужом клоне неверен"),
    (".venv", "виртуальное окружение: чужие пакеты"),
    (".tools", "распакованные JDK и Joern: сторонние бинарники, 5,2 ГБ"),
    (".ort_gpu_probe", "скопированные библиотеки ONNX/CUDA этой машины"),
    (".pytest_cache", "кеш прогонов"),
    (".staging", "рабочие фикстуры и сборочные копии: публикации не подлежат"),
    (".zwork", "служебный каталог агента: вложения сессии"),
    (".agents", "служебное агента"),
    (".dev_agent", "служебное агента"),
    ("node_modules", "зависимости npm: пересоздаются установкой"),
    ("workspace", "рабочие данные Joern: двоичные графы свойств кода"),
)


def load_declaration(path: pathlib.Path) -> dict:
    if not path.is_file():
        raise SystemExit(f"FAIL: нет декларации изданий: {path}")
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise SystemExit(f"FAIL: декларация изданий не разбирается: {error}") from error


#: Знаки, которыми оболочка создаёт ФАЙЛ вместо перенаправления. Замерено
#: 25.10.2026: в корне проекта оказался файл с именем «=» нулевой длины — след
#: команды, в которой перенаправление попало в аргумент. Такой файл не «продукт
#: без места»: у него другая причина и другая правка (его удаляют, а не
#: объявляют удержанным), поэтому и сообщение обязано быть другим.
SHELL_ARTIFACT_MARKS: tuple[str, ...] = ("=", ">", "<", "|", "&")

#: Формулировка причины. Ищется проверками как признак класса, поэтому она
#: вынесена в константу, а не собирается по месту.
SHELL_ARTIFACT_REASON = (
    "artifact of a shell redirect: файл нулевой длины, в имени нет ни одной буквы, зато "
    "есть знак перенаправления — так оболочка создаёт файл, когда перенаправление "
    "попало в аргумент"
)


def shell_artifact_of(name: str, size: int) -> str | None:
    """Причина, по которой файл — мусор команды оболочки; None — не мусор.

    Проверяется СВОЙСТВО, а не совпадение с известным именем. Мусором считается
    файл, у которого одновременно:

    1. нулевая длина — у файла с содержимым есть смысл, и удалять его нельзя;
    2. нет ни одной БУКВЫ — имя `=real.txt` остаётся рабочим файлом, даже если
       начинается со знака;
    3. есть хотя бы один знак оболочки (`=`, `>`, `<`, `|`, `&`) — иначе `12345`
       и `2026-10-25` объявлялись бы мусором, хотя это обычные имена.

    Почему не «ни букв, ни цифр»: измерено прогоном — `2>&1` содержит цифры, и
    такая формулировка его НЕ ловила, хотя имя создано той же ошибкой. Почему не
    «имя равно знаку»: тогда пропали бы `2>&1`, `>>`, `1>&2`.

    Ограничение платформы, названное прямо: Windows не позволяет создать файл с
    именем `>` или `2>&1` (`OSError: Invalid argument`), поэтому на диске
    встречается только форма вида `=`; остальные проверяются по имени, и это
    свойство, а не список известных случаев.
    """
    if size != 0:
        return None
    if any(ch.isalpha() for ch in name):
        return None
    if not any(mark in name for mark in SHELL_ARTIFACT_MARKS):
        return None
    return SHELL_ARTIFACT_REASON


def machine_local_of(relative: str) -> str | None:
    """Причина, по которой файл не продукт; None — файл продукта.

    Проверяются ВСЕ сегменты пути, а не только первый: служебный каталог агента
    встречается и вложенным (измерено: `assets/logo.svg/.dev_agent/updates/health.json`
    — каталог с именем файла плюс служебное агента внутри). Проверка по первому
    сегменту объявила бы такой файл «продуктом без места», то есть ложным провалом.
    """
    segments = relative.split("/")
    for segment in segments:
        for name, why in MACHINE_LOCAL:
            if segment == name:
                return why
    if any(segment == "__pycache__" for segment in segments):
        return "кеш байткода Python: пересоздаётся"
    if any(segment.endswith(".egg-info") for segment in segments):
        return "метаданные сборки пакета: пересоздаются установкой"
    return None


def retained_of(relative: str, declaration: dict) -> tuple[str, str] | None:
    """Объявленная папка издания (или декларации), внутри которой лежит файл."""
    for entry in declaration.get("retained", {}).get("folders", []):
        path = str(entry.get("path", "")).rstrip("/")
        if path and (relative == path or relative.startswith(path + "/")):
            return path, str(entry.get("why", "")).strip()
    return None


def collect() -> tuple[list[tuple[str, str, str]], Counter, list[str]]:
    """Классифицировать каждый файл живого дерева."""
    declaration = load_declaration(DECLARATION)
    manifest = rel.load_manifest(MANIFEST, REPO)
    plan = rel.collect(REPO, manifest)
    # Опубликованным считается ИСТОЧНИК, а не путь поставки: обвязка меняет имя
    # (`community/CHANGELOG.md` → `CHANGELOG.md`), и сравнение с путями поставки
    # объявило бы всю обвязку «файлами без места» — ложный провал на 18 файлах.
    published = {path.relative_to(REPO).as_posix() for path in plan.files.values()}
    # Обвязка живёт в двух местах: `community/**` — источник поставки, и те же
    # байты лежат в живом дереве на своих штатных путях (этап 3 выровнял их).
    # Такой файл НЕ «без места»: он публикуется, только из другого источника.
    # Признак — побайтовое совпадение с опубликованным источником из community/.
    wrapper_hashes = {
        rel.sha256_file(REPO / source)
        for source in published
        if source.startswith("community/")
    }

    rows: list[tuple[str, str, str]] = []
    counts: Counter = Counter()
    problems: list[str] = []

    # Где файл публикуется у ПОЛНОГО издания. Ошибка любого манифеста не имеет
    # права уронить карту изданий — причина остаётся в problems, а класс
    # «публикуется в <издание>» просто не пополняется.
    published_by: list[tuple[str, str, str, set[str]]] = []
    #: Путь → (издание, манифест): файлы, которые издание публикует по
    #: объявлению `pro_only`. Они лежат в общем дереве (src/, tests/), поэтому
    #: проверка «внутри папки издания» их не находит, а исключение основания их
    #: ловит — и без этого класса карта назвала бы живой файл удержанным.
    pro_modules: dict[str, tuple[str, str]] = {}
    for edition_name, edition_item in (declaration.get("edition") or {}).items():
        item = edition_item or {}
        folder = str(item.get("folder") or "").rstrip("/")
        manifest_rel = rel.normalise(str(item.get("manifest") or ""))
        if edition_name == "community" or not folder:
            continue
        title = str(item.get("title") or edition_name)
        if not manifest_rel:
            problems.append(
                f"{edition_name}: издание объявлено без манифеста — состав не объявлен"
            )
            continue
        manifest_file = REPO / manifest_rel
        if not manifest_file.is_file():
            problems.append(
                f"нет манифеста {title}: {manifest_rel} — закрытое издание не имеет "
                f"объявленного состава"
            )
            continue
        try:
            plan = rel.collect(REPO, rel.load_manifest(manifest_file, REPO))
        except Exception as error:  # noqa: BLE001 - карта не падает из-за одного манифеста
            problems.append(f"манифест {title} не разбирается: {error}")
            continue
        published_by.append(
            (title, folder, manifest_rel,
             {path.relative_to(REPO).as_posix() for path in plan.files.values()})
        )
        try:
            declared = rel.load_manifest(manifest_file, REPO)
        except Exception as error:  # noqa: BLE001 - карта не падает из-за одного манифеста
            problems.append(f"манифест {title} не разбирается: {error}")
            continue
        for item in declared.pro_only:
            # Первое издание, которое публикует путь, и есть ответ: следующие
            # наследуют его объявление, и подписывать файл последним изданием
            # значит отвечать не на тот вопрос («в каком манифесте упомянут»).
            pro_modules.setdefault(item, (title, manifest_rel))

    for path in sorted(REPO.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(REPO).as_posix()

        if machine_local_of(relative) is not None:
            counts["машино-локальное"] += 1
            continue

        # Мусор команды оболочки называется своим именем и своей причиной.
        # Раньше он попадал в «файлы без места»: сообщение верное по форме и
        # бесполезное по существу — по нему нельзя понять, что удалять.
        artifact = shell_artifact_of(path.name, path.stat().st_size)
        if artifact is not None:
            counts["мусор оболочки"] += 1
            problems.append(f"{relative}: {artifact}")
            continue

        if relative in published:
            counts["публикуется в Community"] += 1
            continue

        module_owner = pro_modules.get(relative)
        if module_owner is not None:
            counts[f"публикуется в {module_owner[0]} (закрытый модуль)"] += 1
            rows.append((relative, f"закрытый модуль {module_owner[0]}",
                         f"объявлен в pro_only манифеста {module_owner[1]}: в Community "
                         f"его нет, в издании он публикуется"))
            continue

        matched = next((rule for rule in manifest.excludes if rule.regex.match(relative)), None)
        if matched is not None:
            counts["удержано правилом манифеста"] += 1
            rows.append((relative, "правило манифеста", f"{matched.pattern}: {matched.why}"))
            continue

        if rel.sha256_file(path) in wrapper_hashes:
            counts["дубль обвязки: публикуется из community/"] += 1
            rows.append((relative, "community/ (побайтовый дубль)",
                         "тот же файл публикуется из community/; в живом дереве оставлен как рабочий"))
            continue

        owner = next(
            ((title, folder, manifest_rel) for title, folder, manifest_rel, files in published_by
             if relative in files
             and (relative == folder or relative.startswith(folder + "/"))),
            None,
        )
        if owner is not None:
            counts[f"публикуется в {owner[0]}"] += 1
            rows.append((relative, f"манифест {owner[0]} ({owner[1]}/)",
                         f"входит в поставку издания по {owner[2]}"))
            continue

        hold = retained_of(relative, declaration)
        if hold is not None:
            counts["удержано папкой издания"] += 1
            rows.append((relative, f"папка {hold[0]}/", hold[1]))
            continue

        counts["НЕТ МЕСТА"] += 1
        problems.append(
            f"{relative}: файл не публикуется и нигде не объявлен удержанным — "
            f"он пропадёт молча при перестройке дерева"
        )

    return rows, counts, problems


def render(rows: list[tuple[str, str, str]], counts: Counter) -> str:
    lines = [
        "# Что не входит в Community и где это остаётся",
        "",
        "Файл создан `tools/release/editions_report.py`; править руками нельзя —",
        "следующий прогон перепишет таблицу. Правило одно, из указания владельца",
        "24.10.2026: **урезанное издание ничего не отменяет**. То, что не публикуется",
        "в Community, остаётся в живом дереве, а закрытые части — в папках изданий.",
        "",
        "Счёт по классам: " + ", ".join(f"{name} — {value}" for name, value in sorted(counts.items())) + ".",
        "",
        "| файл живого дерева | где остаётся | причина |",
        "|---|---|---|",
    ]
    for relative, where, why in rows:
        lines.append(f"| `{relative}` | {where} | {why} |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="editions_report.py", description="Карта изданий RAGRAF")
    parser.add_argument("--write", action="store_true", help=f"переписать {REPORT_DOC.relative_to(REPO).as_posix()}")
    parser.add_argument("--limit", type=int, default=15, help="сколько строк удержанного печатать")
    args = parser.parse_args(argv)

    rows, counts, problems = collect()
    declaration = load_declaration(DECLARATION)

    print("издания: " + ", ".join(
        f"{key} ({value.get('status', '?')})"
        for key, value in declaration.get("edition", {}).items()
    ))
    for name, value in sorted(counts.items()):
        print(f"  {name:30} {value}")

    for relative, where, _why in rows[: max(0, args.limit)]:
        print(f"  удержано: {relative} ← {where}")
    if len(rows) > args.limit:
        print(f"  … ещё {len(rows) - args.limit} строк в таблице")

    if args.write:
        REPORT_DOC.write_bytes(render(rows, counts).encode("utf-8"))
        print(f"записано: {REPORT_DOC.relative_to(REPO).as_posix()} ({len(rows)} строк)")

    for problem in problems:
        print(f"FAIL: {problem}")
    print(f"осмотрено: {sum(counts.values())}")
    if problems:
        print(f"нарушений: {len(problems)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
