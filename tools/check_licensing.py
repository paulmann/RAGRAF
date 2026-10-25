#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка согласия юридических метаданных RAGRAF.

Проверка формулируется через СВОЙСТВО, а не через написание строк. Причина
измерена: ``LICENSE`` содержал PolyForm Noncommercial, ``pyproject.toml``
объявлял MIT, а README утверждал, что в метаданных стоит PolyForm. Три
источника давали три разных ответа, и ни один не сверялся с другим, поэтому
дефект жил месяцами.

Что проверяется (каждый пункт — отдельное наблюдение):

1. выражение лицензии из ``pyproject.toml`` нормализуется тем же кодом, что и
   при сборке пакета (``packaging.licenses``); если модуль недоступен, проверка
   ПАДАЕТ, а не пропускает пункт молча;
2. лицензия, опознанная по тексту ``LICENSE``, совпадает с выражением из
   метаданных после нормализации;
3. в ``LICENSE`` есть строка правообладателя с именем из authors метаданных
   (этого пункта не было, и файл жил без правообладателя);
4. параметры BSL заполнены, а Additional Use Grant не равен ``None``: пустой
   грант сделал бы лицензию непригодной для производственного использования;
5. нижняя граница setuptools покрывает PEP 639, если используются
   ``license-files`` или выражение лицензии — иначе метаданные зависят от того,
   что установлено на машине;
6. документы текущего состояния не пересказывают метаданные неверно: любое
   процитированное ``license = "…"`` совпадает с фактическим выражением;
7. Community нигде не названа «open source» без отрицания — BSL не одобрен OSI;
8. README ссылается на файлы лицензии, а не пересказывает их, и эти файлы есть;
9. вклад (CLA) разрешает перелицензирование — без этого двойное лицензирование
   невозможно, и DCO для этой цели недостаточно;
10. документ о лицензировании фиксирует, что выданные ранее права не отзываются.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FAILURES: list[str] = []
EXAMINED = 0


def expect(condition: bool, message: str) -> None:
    """Учесть одно наблюдение и запомнить нарушение."""
    global EXAMINED
    EXAMINED += 1
    if not condition:
        FAILURES.append(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# Нормализация выражения лицензии — тот же код, которым пользуется сборка.
try:
    from packaging.licenses import canonicalize_license_expression
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.version import InvalidVersion, Version
except Exception as error:  # noqa: BLE001 - сообщение важнее типа исключения
    print(f"FAIL: модуль packaging недоступен ({error})")
    print("FAIL: без нормализации выражение лицензии сверить нечем; молчаливое")
    print("      пропускание этого пункта — ровно тот класс дефекта, из-за")
    print("      которого проверки объявляли исправленный код дефектным")
    print("осмотрено: 0")
    raise SystemExit(3)


def canonical(expression: str, what: str) -> str | None:
    """Нормализовать выражение; отказ — это провал, а не повод продолжить."""
    try:
        return canonicalize_license_expression(expression)
    except Exception as error:  # noqa: BLE001 - отказ обязан попасть в отчёт
        expect(False, f"{what}: выражение лицензии {expression!r} не принимается сборкой ({error})")
        return None


# Опознание лицензии по ЗАГОЛОВКУ, а не по телу текста. Это не придирка: в
# файле BSL присутствует строка «Change License: Apache License, Version 2.0»,
# и поиск по телу опознал бы файл одновременно как BSL и как Apache — то есть
# проверка «ровно одна лицензия» ложно падала бы на исправном файле.
TITLE_MARKERS: list[tuple[str, tuple[str, ...]]] = [
    ("BUSL-1.1", ("business source license 1.1",)),
    ("PolyForm-Noncommercial-1.0.0", ("polyform noncommercial license 1.0.0",)),
    ("MIT", ("mit license",)),
    ("Apache-2.0", ("apache license", "version 2.0")),
    ("AGPL-3.0-only", ("gnu affero general public license", "version 3")),
]

BODY_MARKERS: list[tuple[str, tuple[str, ...]]] = [
    ("BUSL-1.1", ("covenants of licensor", "additional use grant")),
    ("PolyForm-Noncommercial-1.0.0", ("polyform project", "noncommercial purposes")),
    ("MIT", ("permission is hereby granted, free of charge",)),
    ("Apache-2.0", ("terms and conditions for use, reproduction, and distribution",)),
    ("AGPL-3.0-only", ("gnu affero general public license",)),
]

BSL_FIELDS = ("Licensor", "Licensed Work", "Additional Use Grant", "Change Date", "Change License")

# Документы текущего состояния: их утверждения обязаны совпадать с фактом.
# Исторические документы сюда не входят намеренно — они сохраняют числа и
# формулировки своих релизов, и переписывать историю нельзя.
CURRENT_DOCS = (
    "README.md",
    "NOTICE",
    "CLA.md",
    "SUPPORT.md",
    "docs/03_implementation/LICENSING.md",
)

REFERENCE_FILES = (
    "LICENSE",
    "NOTICE",
    "CLA.md",
    "SUPPORT.md",
    "docs/03_implementation/LICENSING.md",
)

# Строки параметров BSL не участвуют в опознании заголовка: «Change License:
# Apache License, Version 2.0» иначе делала бы файл похожим на две лицензии.
PARAMETER_LINE = re.compile(
    r"(?i)^(licensor|licensed work|additional use grant|change date|change license)\s*:"
)


def detect_license(text: str) -> tuple[list[str], str]:
    """Опознать лицензии по заголовку, при неудаче — по телу текста."""
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not PARAMETER_LINE.match(line.strip())
    ]
    head = " ".join(lines[:6]).lower()
    by_title = [name for name, markers in TITLE_MARKERS if all(m in head for m in markers)]
    if by_title:
        return by_title, "по заголовку"
    lowered = text.lower()
    by_body = [name for name, markers in BODY_MARKERS if all(m in lowered for m in markers)]
    return by_body, "по телу текста"


def main() -> int:
    pyproject_path = REPO / "pyproject.toml"
    license_path = REPO / "LICENSE"

    if not pyproject_path.is_file():
        print("FAIL: нет pyproject.toml — метаданные лицензии проверить негде")
        print("осмотрено: 0")
        return 1
    if not license_path.is_file():
        print("FAIL: нет файла LICENSE")
        print("осмотрено: 0")
        return 1

    data = tomllib.loads(read(pyproject_path))
    project = data.get("project", {})
    build_system = data.get("build-system", {})

    # --- 1. Выражение лицензии из метаданных -------------------------------
    raw_license = project.get("license")
    expression: str | None = None
    if isinstance(raw_license, str):
        expression = raw_license
    elif isinstance(raw_license, dict) and "text" in raw_license:
        expression = str(raw_license["text"])
    elif isinstance(raw_license, dict) and "file" in raw_license:
        # Метаданные берут лицензию из файла — тогда сверяется сам файл.
        expect(
            (REPO / str(raw_license["file"])).resolve() == license_path.resolve(),
            f"project.license.file указывает на {raw_license['file']!r}, а не на LICENSE",
        )
    else:
        expect(False, f"в pyproject.toml нет выражения лицензии (project.license = {raw_license!r})")

    normalized: str | None = None
    if expression is not None:
        normalized = canonical(expression, "pyproject.toml")

    # --- 2. Опознание лицензии по тексту и сравнение -----------------------
    license_text = read(license_path)
    detected, how = detect_license(license_text)
    expect(
        len(detected) > 0,
        "лицензия в LICENSE не опознана: ни заголовок, ни тело не совпали с известными",
    )
    expect(
        len(detected) <= 1,
        "текст LICENSE одновременно похож на несколько лицензий: " + (", ".join(detected) or "—"),
    )

    if normalized is not None and len(detected) == 1:
        detected_norm = canonical(detected[0], f"LICENSE ({how})")
        if detected_norm is not None:
            expect(
                detected_norm == normalized,
                f"рассогласование источников: LICENSE содержит {detected[0]} ({how}), "
                f"а pyproject.toml объявляет {normalized}",
            )

    # --- 3. Строка правообладателя ----------------------------------------
    authors = [a.get("name", "") for a in project.get("authors", []) if isinstance(a, dict)]
    authors = [a for a in authors if a]
    expect(len(authors) > 0, "в pyproject.toml нет authors: сверять строку правообладателя не с чем")
    for author in authors:
        surname = author.split()[-1]
        pattern = re.compile(r"(?mi)^\s*copyright(\s*\(c\))?\s*\d{4}.*" + re.escape(surname))
        expect(
            bool(pattern.search(license_text)),
            f"в LICENSE нет строки правообладателя с именем {author!r} из pyproject.toml authors",
        )

    # --- 4. Параметры BSL --------------------------------------------------
    if "BUSL-1.1" in detected:
        for field in BSL_FIELDS:
            expect(
                re.search(rf"(?mi)^\s*{re.escape(field)}\s*:", license_text) is not None,
                f"в LICENSE нет параметра BSL {field!r}",
            )
        grant = re.search(
            r"(?mis)^\s*Additional Use Grant\s*:(?P<value>.*?)(?=\n\s*Change\s+Date\s*:)",
            license_text,
        )
        grant_text = grant.group("value").strip() if grant else ""
        expect(
            bool(grant_text),
            "параметр Additional Use Grant пуст: без гранта BSL запрещает производственное использование",
        )
        expect(
            grant_text.lower().strip(" .") != "none",
            "Additional Use Grant равен None: Community станет непригодной для работы, это не разрешение",
        )
        expect(
            len(grant_text) >= 20,
            "Additional Use Grant короче осмысленной формулировки: проверить, что он разрешает production",
        )

    # --- 5. Нижняя граница setuptools -------------------------------------
    uses_pep639 = ("license-files" in project) or (expression is not None)
    setuptools_floor: Version | None = None
    for item in build_system.get("requires", []):
        try:
            requirement = Requirement(str(item))
        except InvalidRequirement:
            continue
        if requirement.name.lower() != "setuptools":
            continue
        for specifier in requirement.specifier:
            if specifier.operator not in (">=", "==", "~="):
                continue
            try:
                version = Version(specifier.version)
            except InvalidVersion:
                continue
            if setuptools_floor is None or version > setuptools_floor:
                setuptools_floor = version
    if uses_pep639:
        expect(
            setuptools_floor is not None,
            "build-system.requires не задаёт нижнюю границу setuptools, "
            "а expression/license-files — это PEP 639",
        )
        if setuptools_floor is not None:
            expect(
                setuptools_floor >= Version("77"),
                f"setuptools>={setuptools_floor} ниже 77: license-files и выражение лицензии — "
                "PEP 639, сборка проходит случайно и зависит от установленной версии",
            )

    # --- 6. Документы не пересказывают метаданные неверно -----------------
    quoted = re.compile(r"license\s*=\s*\"(?P<value>[^\"]+)\"")
    for name in CURRENT_DOCS:
        path = REPO / name
        if not path.is_file():
            continue
        text = read(path)
        for match in quoted.finditer(text):
            stated = match.group("value")
            if normalized is None:
                continue
            expect(
                canonical(stated, name) == normalized,
                f'{name} утверждает license = "{stated}", а в pyproject.toml {normalized}',
            )

    # --- 7. «open source» только с отрицанием ------------------------------
    # Окно включает сам оборот: иначе «не является open source» выглядело бы как
    # утверждение, потому что отрицание стоит ПЕРЕД искомым текстом, а не после.
    # Разрешены кавычки и предлог между отрицанием и оборотом («не «open source»»,
    # «нельзя называть Community «open source»»). Граница правила названа честно:
    # это проверка ФОРМУЛИРОВКИ, а не смысла — она ловит прямое утверждение и
    # не разбирает иронию.
    negation = re.compile(
        r"(?i)(?:(?:не|нельзя|not)\s+[«\"'(]?(?:является\s+)?(?:\w+\s+){0,3}[«\"'(]?open[\s-]*source"
        r"|не\s+одобр\w+)"
    )
    for name in CURRENT_DOCS:
        path = REPO / name
        if not path.is_file():
            continue
        text = read(path)
        for match in re.finditer(r"(?i)open[\s-]*source", text):
            window = text[max(0, match.start() - 100): match.end() + 60]
            expect(
                bool(negation.search(window)),
                f"{name} называет Community «open source» без отрицания: BSL 1.1 не одобрен OSI",
            )

    # --- 8. README ссылается, а не пересказывает ---------------------------
    readme_path = REPO / "README.md"
    if readme_path.is_file():
        readme = read(readme_path)
        for name in REFERENCE_FILES:
            expect(
                (REPO / name).is_file(),
                f"нет файла {name}: README ссылается на него как на источник истины",
            )
            expect(
                f"({name})" in readme,
                f"README не ссылается на {name}: раздел лицензии обязан ссылаться, а не пересказывать",
            )
    else:
        expect(False, "нет README.md: раздел лицензии проверить нечем")

    # --- 9. Право перелицензирования в CLA ---------------------------------
    cla_path = REPO / "CLA.md"
    if cla_path.is_file():
        cla = read(cla_path).lower()
        expect(
            ("перелиценз" in cla) or ("relicens" in cla),
            "CLA.md не даёт права перелицензирования: двойное лицензирование невозможно",
        )
        expect(
            "dco" in cla,
            "CLA.md не объясняет, почему DCO для перелицензирования недостаточно",
        )

    # --- 10. Ранее выданные права описаны как неотзываемые -----------------
    licensing_path = REPO / "docs/03_implementation/LICENSING.md"
    if licensing_path.is_file():
        licensing = read(licensing_path).lower()
        expect(
            ("не отзыв" in licensing) or ("not revok" in licensing) or ("неотзыв" in licensing),
            "LICENSING.md не фиксирует, что выданные ранее права не отзываются",
        )
        expect(
            ("bsl" in licensing) or ("busl" in licensing),
            "LICENSING.md не описывает действующую лицензию",
        )

    print(f"осмотрено: {EXAMINED}")
    for failure in FAILURES:
        print(f"FAIL: {failure}")
    if FAILURES:
        print(f"провалов: {len(FAILURES)}")
        return 1
    print("свойства юридических метаданных выполняются")
    return 0


if __name__ == "__main__":
    sys.exit(main())
