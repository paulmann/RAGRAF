#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка манифеста поставки — 25-е правило чекера конвенций проекта.

Проверка формулируется через СВОЙСТВО, а не через написание строк. Задача,
из которой она выросла (этап 3 модернизации, 24.10.2026): публикация собиралась
руками в `.staging/release-v0.2`, поэтому обвязка отставала от кода, а установщик
существовал в трёх версиях. Манифест и сборщик решают это только в одном случае:
если кто-то проверяет, что манифест описывает ФАКТИЧЕСКОЕ дерево, а собранное по
нему дерево — годно. Иначе манифест сам становится четвёртой версией правды.

Что проверяется (каждый пункт — отдельное наблюдение):

1. манифест существует, разбирается как TOML и называет продукт;
2. каждый источник из манифеста существует в живом дереве;
3. пути поставки заданы относительно, без `..` и без абсолютных путей;
4. один путь поставки не назначается дважды (иначе один файл молча затрёт другой);
5. у каждого исключения есть осмысленная причина (>= 20 знаков), состояние
   `active` или `guard`, и шаблоны не повторяются;
6. пометка исключения не врёт: `active` обязан что-то отсекать, `guard` — ничего;
7. сборка в песочницу даёт дерево, которое проверяльщик признаёт годным, и
   осматривает больше нуля объектов;
8. гейт ловит все четыре порчи: проверка, которая никогда не падала, не стоит
   ничего;
9. артефакты сборщика (`checksums.sha256`) не совпадают с путями манифеста;
10. версия объявлена в одном месте: `pyproject.toml` не содержит литерала, а читает
    `ragraf.__version__`; в собранном дереве `CITATION.cff` и верхний раздел
    `CHANGELOG.md` называют ту же версию. CFF требует поле, поэтому «одно место» —
    это одно ЗНАЧЕНИЕ; согласие мест проверяется здесь;
11. у каждого файла живого дерева есть место: он либо публикуется, либо объявлен
    удержанным с причиной (`editions/editions.toml`, `tools/release/editions_report.py`).
    Иначе урезание Community молча уничтожает разработанное.

Молчаливый пропуск запрещён: нет python-модуля, нет сборщика, нет проверяльщика,
нет гейта — это провал, а не «нечего проверять».

Запуск: `python tools/check_release_manifest.py`
Коды возврата: 0 — свойства выполняются, 1 — есть нарушения.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
RELEASE = REPO / "tools/release"
MANIFEST = RELEASE / "manifest.toml"
# Признак «манифест подменён параметром»: нужен только для честного имени в
# сообщении и для проверки, что путь вообще существует.
MANIFEST_OVERRIDE = False

sys.path.insert(0, str(RELEASE))
import relcommon as rel  # noqa: E402 - путь к общему модулю добавлен выше
import editions_report  # noqa: E402 - карта изданий, тот же каталог
import re  # noqa: E402 - разбор объявления версии
import tomllib  # noqa: E402 - pyproject читается как TOML

FAILURES: list[str] = []
EXAMINED = 0
#: Собранное дерево последнего прогона: нужно свойству версии (CITATION, CHANGELOG).
BUILT_TREE: pathlib.Path | None = None


def expect(condition: bool, message: str) -> None:
    global EXAMINED
    EXAMINED += 1
    if not condition:
        FAILURES.append(message)


def run(script: pathlib.Path, *args: str) -> tuple[int, str]:
    completed = subprocess.run(
        [sys.executable, str(script), *args], capture_output=True, text=True, check=False
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def main() -> int:
    """Проверить манифест. Параметр --manifest нужен для доказательства:
    испорченную копию манифеста можно прогнать на этом же дереве и увидеть, что
    проверка краснеет ИМЕННО на испорченном правиле. Без него «проверка ловит
    порчу» пришлось бы доказывать временным удалением живого файла.
    """
    global MANIFEST, MANIFEST_OVERRIDE
    if len(sys.argv) > 1:
        if len(sys.argv) != 3 or sys.argv[1] != "--manifest":
            print("FAIL: единственный допустимый параметр — --manifest <путь>")
            return 2
        MANIFEST = pathlib.Path(sys.argv[2]).resolve()
        MANIFEST_OVERRIDE = True

    # --- 1. Манифест существует и разбирается -----------------------------
    name = MANIFEST.name if MANIFEST_OVERRIDE else MANIFEST.relative_to(REPO).as_posix()
    expect(MANIFEST.is_file(), f"нет манифеста поставки: {name}")
    if not MANIFEST.is_file():
        return report()

    try:
        manifest = rel.load_manifest(MANIFEST, REPO)
        parsed = True
        expect(bool(manifest.product), "манифест не называет продукт")
    except Exception as error:  # noqa: BLE001 - манифест может быть калечным
        parsed = False
        expect(False, f"манифест не разбирается как TOML: {error}")
        manifest = None  # type: ignore[assignment]

    if manifest is not None:
        expect(len(manifest.includes) > 0, "в манифесте нет ни одного включения")

        # --- 2. Источники существуют ------------------------------------
        for include in manifest.includes:
            expect(
                (REPO / include.source).exists(),
                f"нет источника из манифеста: {include.source}",
            )

        # --- 3. Пути поставки относительные -----------------------------
        for include in manifest.includes:
            target = include.target
            expect(
                not target.startswith("/")
                and ".." not in target.split("/")
                and ":" not in target,
                f"{include.source}: путь поставки не является относительным ({target})",
            )

        # --- 4. Один путь поставки — один источник ----------------------
        seen: dict[str, str] = {}
        for include in manifest.includes:
            if include.target in seen:
                expect(
                    False,
                    f"путь поставки {include.target} назначен дважды: "
                    f"{seen[include.target]} и {include.source}",
                )
                continue
            seen[include.target] = include.source
            expect(True, f"{include.source} → {include.target}")

        # --- 5. Причины исключений и их состояние -----------------------
        patterns: dict[str, int] = {}
        for exclude in manifest.excludes:
            patterns[exclude.pattern] = patterns.get(exclude.pattern, 0) + 1
            expect(
                len(exclude.why) >= 20,
                f"{exclude.pattern}: причина исключения не названа ({exclude.why!r})",
            )
            expect(
                exclude.state in {"active", "guard"},
                f"{exclude.pattern}: состояние {exclude.state!r} не из двух допустимых",
            )
        for pattern, count in patterns.items():
            expect(count == 1, f"шаблон исключения повторяется {count} раза: {pattern}")

        # --- 6. Пометка исключения соответствует факту ------------------
        plan = rel.collect(REPO, manifest)
        for problem in plan.problems:
            expect(False, problem)
        for exclude in manifest.excludes:
            hits = len(plan.excluded.get(exclude.pattern, []))
            if exclude.state == "active":
                expect(hits > 0, f"{exclude.pattern}: помечено active, но не отсекает ни одного файла")
            else:
                expect(
                    hits == 0,
                    f"{exclude.pattern}: помечено guard, но отсекает {hits} файл(ов) — пометка устарела",
                )

        # --- 9. Артефакты сборщика не совпадают с путями манифеста ------
        for artifact in manifest.artifacts:
            expect(
                artifact not in seen,
                f"артефакт сборщика {artifact} совпадает с путём поставки — файл будет затёрт",
            )
        expect(len(plan.files) > 0, "манифест не дал ни одного файла поставки")

    # --- 7. Сборка в песочницу и проверяльщик ---------------------------
    builder = RELEASE / "build_release.py"
    verifier = RELEASE / "verify_release.py"
    gate = RELEASE / "gate_release.py"
    for script, label in ((builder, "сборщик"), (verifier, "проверяльщик"), (gate, "гейт")):
        expect(script.is_file(), f"NOT-IMPLEMENTED: нет {label}а — {script.relative_to(REPO).as_posix()}")

    if builder.is_file() and verifier.is_file():
        global BUILT_TREE
        sandbox = pathlib.Path(tempfile.mkdtemp(prefix="ragraf-manifest-check-"))
        tree = sandbox / "tree"
        BUILT_TREE = tree
        code, output = run(builder, "--out", str(tree))
        expect(code == 0, f"сборка манифеста в песочницу завершилась кодом {code}: {output.strip()[-300:]}")
        if code == 0:
            code, output = run(verifier, "--tree", str(tree))
            expect(code == 0, f"проверяльщик забраковал собственную сборку: {output.strip()[-300:]}")
            examined = 0
            for line in output.splitlines():
                if line.startswith("осмотрено:"):
                    examined = int(line.split(":", 1)[1].strip() or 0)
            expect(examined > 0, "проверяльщик осмотрел 0 объектов — это ложный PASS")
            expect(
                "правил применено: 8" in output,
                "проверяльщик применил не все восемь правил",
            )

    # --- 8. Гейт ловит порчи -------------------------------------------
    if gate.is_file():
        code, output = run(gate)
        caught = "порч поймано: 4 из 4"
        expect(code == 0, f"гейт поставки провален (код {code}): {output.strip()[-300:]}")
        expect(caught in output, f"гейт не подтвердил четыре пойманные порчи: {output.strip()[-300:]}")

    # --- 10. Версия объявлена в одном месте -----------------------------
    live_pyproject = REPO / "pyproject.toml"
    expect(live_pyproject.is_file(), "нет pyproject.toml в живом дереве")
    version = ""
    if live_pyproject.is_file():
        raw = live_pyproject.read_text(encoding="utf-8")
        data = tomllib.loads(raw)
        project = data.get("project", {})
        expect(
            "version" not in project,
            "pyproject.toml объявляет version литералом — версия снова в двух местах",
        )
        expect(
            "version" in (project.get("dynamic") or []),
            "pyproject.toml не перечисляет version в dynamic",
        )
        attr = (((data.get("tool") or {}).get("setuptools") or {}).get("dynamic") or {}).get("version") or {}
        expect(
            attr.get("attr") == "ragraf.__version__",
            f"версия не привязана к атрибуту пакета (attr={attr.get('attr')!r})",
        )
        init = REPO / "src/ragraf/__init__.py"
        expect(init.is_file(), "нет src/ragraf/__init__.py — объявление версии не найдено")
        if init.is_file():
            found = re.search(r'^__version__\s*=\s*"([^"]+)"', init.read_text(encoding="utf-8"), re.M)
            expect(found is not None, "в src/ragraf/__init__.py нет __version__")
            version = found.group(1) if found else ""
            expect(
                re.fullmatch(r"\d+\.\d+\.\d+", version) is not None,
                f"версия не в форме X.Y.Z: {version!r}",
            )

    if version and BUILT_TREE is not None and BUILT_TREE.is_dir():
        citation = BUILT_TREE / "CITATION.cff"
        expect(citation.is_file(), "в собранном дереве нет CITATION.cff")
        if citation.is_file():
            cff = citation.read_text(encoding="utf-8", errors="replace")
            cited = re.search(r'^version:\s*"([^"]+)"', cff, re.M)
            expect(
                cited is not None and cited.group(1) == version,
                f"CITATION.cff называет версию {cited.group(1) if cited else '—'}, а код — {version}",
            )
            expect(
                re.search(r'^date-released:\s*"\d{4}-\d{2}-\d{2}"', cff, re.M) is not None,
                "в CITATION.cff нет даты выпуска в виде ГГГГ-ММ-ДД",
            )
        changelog = BUILT_TREE / "CHANGELOG.md"
        expect(changelog.is_file(), "в собранном дереве нет CHANGELOG.md")
        if changelog.is_file():
            log = changelog.read_text(encoding="utf-8", errors="replace")
            expect(
                re.search(rf"^## \[{re.escape(version)}\]", log, re.M) is not None,
                f"в CHANGELOG.md нет раздела выпуска [{version}]",
            )
    else:
        expect(BUILT_TREE is not None, "собранное дерево недоступно — свойство версии не проверено")

    # --- 11. У каждого файла живого дерева есть место --------------------
    try:
        _rows, counts, problems = editions_report.collect()
    except SystemExit as error:
        expect(False, f"карта изданий не читается: {error}")
    else:
        expect(
            not problems,
            f"файлов без места: {len(problems)}; первый: {problems[0] if problems else ''}",
        )
        expect(counts.get("публикуется в Community", 0) > 0, "карта изданий не нашла публикуемых файлов")
        expect(
            counts.get("удержано правилом манифеста", 0) + counts.get("удержано папкой издания", 0) > 0,
            "карта изданий не нашла удержанных файлов — проверять нечего",
        )

    return report()


def report() -> int:
    for problem in FAILURES:
        print(f"FAIL: {problem}")
    print(f"осмотрено: {EXAMINED}")
    if FAILURES:
        print(f"нарушений: {len(FAILURES)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
