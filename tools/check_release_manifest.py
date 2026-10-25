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
8. гейт ловит все пять порч: проверка, которая никогда не падала, не стоит
   ничего;
9. артефакты сборщика (`checksums.sha256`) не совпадают с путями манифеста;
10. версия объявлена в одном месте: `pyproject.toml` не содержит литерала, а читает
    `ragraf.__version__`; в собранном дереве `CITATION.cff` и верхний раздел
    `CHANGELOG.md` называют ту же версию. CFF требует поле, поэтому «одно место» —
    это одно ЗНАЧЕНИЕ; согласие мест проверяется здесь;
11. у каждого файла живого дерева есть место: он либо публикуется, либо объявлен
    удержанным с причиной (`editions/editions.toml`, `tools/release/editions_report.py`).
    Иначе урезание Community молча уничтожает разработанное;
12. каждый поставляемый манифест (Pro, Enterprise) существует, наследует
    манифест ПРЕДЫДУЩЕГО издания и добавляет файлы ТОЛЬКО внутри своей
    объявленной папки. Свойство одно на все звенья: «манифест издания N+1 =
    манифест издания N + объявленная папка». Копия правил разошлась бы на первой
    правке, и расхождение не видно глазами: сборка перестала бы совпадать с
    предыдущим изданием по составу. Три копии проверки разошлись бы так же —
    поэтому функция одна, а звенья берутся из `editions/editions.toml`;
13. наборы правил согласованы СВОЙСТВОМ, а не на глаз: каждое включение и
    каждое исключение предыдущего издания присутствует в следующем с теми же
    источником, путём поставки и состоянием, а собственных исключений у издания
    быть не может;
14. поставка каждого издания проходит собственную проверку
    `tools/release/check_pro_delivery.py --manifest <манифест>`: два прогона
    сборщика дают одинаковый набор и одинаковые контрольные суммы, девять правил
    проверяльщика применены, секретоносных путей нет, инвентари прав сходятся с
    базовой линией, а лицензия привязана к сборке (без файла — Community и отказ
    с причиной, подписанная — открывает, подделанная — снова закрывает).

Молчаливый пропуск запрещён: нет python-модуля, нет сборщика, нет проверяльщика,
нет гейта — это провал, а не «нечего проверять».

Запуск: `python tools/check_release_manifest.py`
Коды возврата: 0 — свойства выполняются, 1 — есть нарушения.
"""

from __future__ import annotations

import json
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


def check_edition_step(base: dict, step: dict) -> int:
    """Свойство «манифест издания N+1 = манифест N + объявленная папка».

    Одна функция на все звенья цепочки. Копия проверки на каждое издание — тот
    же дефект, что копия правил манифеста: копии расходятся на первой правке, и
    расхождение не видно глазами (одно издание проверяется строже другого).

    `base` и `step` — записи объявления: name, title, manifest, folder.
    """
    label = step["title"]
    derived_path = REPO / step["manifest"]
    expect(derived_path.is_file(),
           f"нет манифеста поставки {label}: {step['manifest']} — издание объявлено "
           f"поставляемым, а состава у него нет")
    if not derived_path.is_file():
        return 0
    try:
        derived = rel.load_manifest(derived_path, REPO)
        expect(True, f"манифест {label} разобран")
    except Exception as error:  # noqa: BLE001 - манифест может быть калечным
        expect(False, f"манифест {label} не разбирается: {error}")
        return 0

    base_manifest = rel.load_manifest(REPO / base["manifest"], REPO)

    # Наследование — не удобство, а то, что делает свойство проверяемым:
    # «+ объявленная папка» имеет смысл только относительно предыдущего издания.
    data = tomllib.loads(derived_path.read_text(encoding="utf-8"))
    inherited = rel.normalise(str((data.get("release") or {}).get("inherits") or ""))
    expect(inherited == base["manifest"],
           f"{label}: манифест наследует {inherited or '—'}, а основанием объявлено "
           f"издание {base['title']} ({base['manifest']})")

    base_targets = {include.target: include.source for include in base_manifest.includes}
    derived_targets = {include.target: include.source for include in derived.includes}
    for target, origin in sorted(base_targets.items()):
        expect(derived_targets.get(target) == origin,
               f"правило {base['title']} {origin} → {target} отсутствует или изменено "
               f"в манифесте {label} ({derived_targets.get(target)!r})")
    base_rules = {rule.pattern: rule.state for rule in base_manifest.excludes}
    derived_rules = {rule.pattern: rule.state for rule in derived.excludes}
    for pattern, state in sorted(base_rules.items()):
        expect(derived_rules.get(pattern) == state,
               f"исключение {pattern} в манифесте {label} отсутствует или сменило "
               f"состояние ({derived_rules.get(pattern)!r} против {state!r})")
    for pattern in sorted(set(derived_rules) - set(base_rules)):
        expect(False, f"манифест {label} заводит собственное исключение {pattern}: "
                      f"правила должны быть общими, иначе издания расходятся")

    folder = step["folder"]
    added = sorted(set(derived_targets) - set(base_targets))
    expect(bool(added),
           f"манифест {label} не добавляет ни одного пути к поставке {base['title']} — "
           f"это не издание, а копия")
    for target in added:
        expect(target == folder or target.startswith(folder + "/"),
               f"манифест {label} добавляет путь вне объявленной папки {folder}/ "
               f"закрытого издания: {target}")

    tool = RELEASE / "check_pro_delivery.py"
    expect(tool.is_file(),
           "NOT-IMPLEMENTED: нет tools/release/check_pro_delivery.py — "
           f"поставка {label} не проверяется ничем")
    if tool.is_file():
        code, output = run(tool, "--manifest", str(derived_path), "--json")
        payload = None
        brace = output.find("{")
        if brace >= 0:
            try:
                payload = json.loads(output[brace:])
            except Exception:  # noqa: BLE001 - ответ инструмента может быть негодным
                payload = None
        expect(payload is not None,
               f"машинный ответ проверки поставки {label} не разобран (код {code})")
        if payload is not None:
            expect(payload.get("examined", 0) > 0,
                   f"проверка поставки {label} осмотрела 0 свойств — это ложный PASS")
            examples = payload.get("failure_examples") or []
            expect(code == 0 and payload.get("ok") is True,
                   f"поставка {label} не прошла собственную проверку (код {code}): "
                   + "; ".join(str(item) for item in examples[:3]))
            expect(int(payload.get("added_over_community") or 0) > 0,
                   f"проверка поставки {label} не подтвердила ни одного файла сверх Community")
    return 1


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
                "правил применено: 9" in output,
                "проверяльщик применил не все девять правил",
            )

    # --- 8. Гейт ловит порчи -------------------------------------------
    if gate.is_file():
        code, output = run(gate)
        caught = "порч поймано: 5 из 5"
        expect(code == 0, f"гейт поставки провален (код {code}): {output.strip()[-300:]}")
        expect(caught in output, f"гейт не подтвердил пять пойманных порч: {output.strip()[-300:]}")

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
        # Мусор команды оболочки — отдельный класс дефекта с отдельным
        # сообщением: файл «=» нулевой длины назывался «файлом без места»,
        # то есть причиной, по которой непонятно, что делать (измерено
        # 25.10.2026, ПРОМТ-И14). Сначала мусор, потом всё остальное.
        artifacts = [p for p in problems if "shell redirect" in p]
        homeless = [p for p in problems if "shell redirect" not in p]
        expect(
            not artifacts,
            "мусор команды оболочки (удалить, а не объявлять удержанным): "
            + "; ".join(artifacts[:3]),
        )
        expect(
            not homeless,
            f"файлов без места: {len(homeless)}; первый: {homeless[0] if homeless else ''}",
        )
        expect(counts.get("публикуется в Community", 0) > 0, "карта изданий не нашла публикуемых файлов")
        expect(
            counts.get("удержано правилом манифеста", 0) + counts.get("удержано папкой издания", 0) > 0,
            "карта изданий не нашла удержанных файлов — проверять нечего",
        )

    # --- 12-14. Поставки закрытых изданий -------------------------------
    # Свойства живут здесь, а не в отдельном инструменте, потому что вопрос
    # тот же: «описывает ли манифест фактическое дерево». Звенья берутся из
    # объявления изданий: новая редакция не появится без правки проверки, а
    # объявленная и не собранная — покраснеет.
    # Прогон с подменённым манифестом (`--manifest`) эти свойства пропускает: он
    # доказывает, что проверка краснеет на испорченном ПЕРВОМ манифесте.
    if not MANIFEST_OVERRIDE:
        declaration_file = REPO / "editions/editions.toml"
        pro_dir = REPO / "pro"
        if not declaration_file.is_file() and not pro_dir.is_dir():
            print("NOTE: поставок закрытых изданий в этом дереве нет (нет editions/ и "
                  "pro/) — это опубликованная копия; свойства 12-14 не применимы")
            expect(True, "")
            return report()
        expect(declaration_file.is_file(),
               "editions/editions.toml не найден, а папка pro/ есть — объявление "
               "изданий потеряно")
        if declaration_file.is_file():
            declaration = tomllib.loads(declaration_file.read_text(encoding="utf-8"))
            rows = declaration.get("edition") or {}
            labels = list(rows.keys())
            expect(len(labels) >= 2,
                   f"в объявлении изданий меньше двух записей: {labels} — цепочку не из чего строить")
            previous: dict | None = None
            for name in labels:
                item = rows.get(name) or {}
                entry = {
                    "name": name,
                    "title": str(item.get("title") or name),
                    "manifest": rel.normalise(str(item.get("manifest") or "")),
                    "folder": str(item.get("folder") or "").rstrip("/"),
                    "status": str(item.get("status") or ""),
                }
                expect(bool(entry["manifest"]),
                       f"{entry['title']}: в объявлении нет манифеста — состава у издания нет")
                if previous is None:
                    previous = entry
                    continue
                if entry["status"] != "shipped":
                    # Объявленное, но не поставляемое издание обязано это сказать:
                    # иначе «нет манифеста» неотличимо от потерянного манифеста.
                    print(f"NOTE: издание {entry['title']} объявлено, но не поставляется "
                          f"(status={entry['status']}) — манифест не проверяется")
                    previous = entry
                    continue
                expect(bool(entry["folder"]),
                       f"{entry['title']}: поставляемое издание без объявленной папки — "
                       f"добавлять нечего")
                expect((REPO / entry["folder"]).is_dir() if entry["folder"] else False,
                       f"{entry['title']}: объявлена папка {entry['folder']}/, а её нет — "
                       f"материалы издания потеряны")
                check_edition_step(previous, entry)
                previous = entry

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
