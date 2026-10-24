#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Общая часть конвейера поставки: разбор манифеста и сбор кандидатов.

Почему отдельный модуль, а не копия в каждом скрипте. Три инструмента —
сборщик, проверяльщик и гейт — обязаны понимать манифест ОДИНАКОВО. Разошедшись,
они дали бы ту же болезнь, из-за которой появился весь этап: три версии
установщика, каждая из которых считала себя правильной. Поэтому разбор манифеста
и сопоставление шаблонов живут здесь в одном экземпляре.

Границы, названные честно:

* `**` в шаблоне означает «ноль или более каталогов», `*` и `?` внутри сегмента
  не переходят через `/`. Это не `pathlib.Path.match` (там `**` ведёт себя как
  `*`, то есть `**/*.py` не совпадает с `b.py`) и не полноценный gitignore:
  поддерживается ровно то, что есть в манифесте, и ничего больше;
* ведущий `./` и обратные слэши нормализуются: манифест пишется для человека,
  а не для конкретной платформы;
* регистр сравнивается как есть (Windows-регистронезависимость здесь не
  подражается): два файла, различающиеся регистром, — разные файлы поставки.
"""

from __future__ import annotations

import fnmatch
import hashlib
import pathlib
import re
import sys
import tomllib
from dataclasses import dataclass, field

MANIFEST_DEFAULT = "tools/release/manifest.toml"


# ---------------------------------------------------------------------------
# Разбор манифеста
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Include:
    source: str          # путь в живом дереве, posix-вид
    target: str          # путь в публикуемом дереве, posix-вид
    kind: str            # wrapper | tree | file
    is_dir: bool


@dataclass(frozen=True)
class Exclude:
    pattern: str
    state: str           # active | guard
    why: str
    regex: re.Pattern[str]


@dataclass
class Manifest:
    path: pathlib.Path
    product: str
    artifacts: tuple[str, ...]
    includes: tuple[Include, ...]
    excludes: tuple[Exclude, ...]


def normalise(relative: str) -> str:
    text = relative.replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text.strip("/")


def pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """Шаблон манифеста → регулярное выражение по posix-пути."""
    parts = normalise(pattern).split("/")
    chunks: list[str] = []
    for index, segment in enumerate(parts):
        last = index == len(parts) - 1
        if segment == "**":
            chunks.append("(?:.*)" if last else "(?:[^/]+/)*")
            continue
        piece = []
        for char in segment:
            if char == "*":
                piece.append("[^/]*")
            elif char == "?":
                piece.append("[^/]")
            else:
                piece.append(re.escape(char))
        chunks.append("".join(piece) + ("" if last else "/"))
    return re.compile("^" + "".join(chunks) + "$")


def load_manifest(path: pathlib.Path, source: pathlib.Path) -> Manifest:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    release = data.get("release", {})

    includes: list[Include] = []
    for raw in data.get("include", []):
        origin = normalise(raw["from"])
        target = normalise(raw["to"])
        origin_path = source / origin
        includes.append(
            Include(
                source=origin,
                target=target,
                kind=raw.get("kind", "file"),
                is_dir=origin_path.is_dir(),
            )
        )

    excludes: list[Exclude] = []
    for raw in data.get("exclude", []):
        pattern = normalise(raw["pattern"])
        excludes.append(
            Exclude(
                pattern=pattern,
                state=raw.get("state", "active"),
                why=raw.get("why", "").strip(),
                regex=pattern_to_regex(pattern),
            )
        )

    return Manifest(
        path=path,
        product=release.get("product", "RAGRAF"),
        artifacts=tuple(normalise(item) for item in release.get("artifacts", [])),
        includes=tuple(includes),
        excludes=tuple(excludes),
    )


# ---------------------------------------------------------------------------
# Сбор кандидатов
# ---------------------------------------------------------------------------


@dataclass
class Plan:
    """Что именно попадёт в поставку и что отсечено."""

    files: dict[str, pathlib.Path] = field(default_factory=dict)   # target → источник
    excluded: dict[str, list[str]] = field(default_factory=dict)   # шаблон → отсечённые
    problems: list[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(path.stat().st_size for path in self.files.values())


def collect(source: pathlib.Path, manifest: Manifest) -> Plan:
    plan = Plan()
    for exclude in manifest.excludes:
        plan.excluded.setdefault(exclude.pattern, [])

    seen_targets: dict[str, str] = {}

    for include in manifest.includes:
        origin = source / include.source
        if not origin.exists():
            plan.problems.append(f"нет источника из манифеста: {include.source}")
            continue

        pairs: list[tuple[str, pathlib.Path, str]] = []
        if include.is_dir:
            for path in sorted(origin.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(origin).as_posix()
                pairs.append(
                    (
                        f"{include.source}/{relative}",
                        path,
                        f"{include.target}/{relative}" if include.target else relative,
                    )
                )
        else:
            pairs.append((include.source, origin, include.target))

        for source_rel, path, target_rel in pairs:
            matched = next(
                (rule for rule in manifest.excludes if rule.regex.match(source_rel)),
                None,
            )
            if matched is not None:
                plan.excluded[matched.pattern].append(source_rel)
                continue
            if target_rel in seen_targets:
                plan.problems.append(
                    f"два источника претендуют на один путь поставки: "
                    f"{target_rel} ← {seen_targets[target_rel]} и {source_rel}"
                )
                continue
            seen_targets[target_rel] = source_rel
            plan.files[target_rel] = path

    for artifact in manifest.artifacts:
        if artifact in plan.files:
            plan.problems.append(
                f"артефакт сборщика {artifact} совпал с путём из манифеста — "
                f"сборщик перезапишет файл поставки"
            )

    return plan


# ---------------------------------------------------------------------------
# Мелкие помощники
# ---------------------------------------------------------------------------


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def is_inside(parent: pathlib.Path, child: pathlib.Path) -> bool:
    """Строгое вложение (равенство не считается вложением)."""
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return parent != child


def overlaps(first: pathlib.Path, second: pathlib.Path) -> bool:
    a = first.resolve()
    b = second.resolve()
    return a == b or is_inside(a, b) or is_inside(b, a)


def default_source() -> pathlib.Path:
    """Корень живого дерева: два каталога вверх от tools/release/."""
    return pathlib.Path(__file__).resolve().parents[2]


def fail(message: str, code: int = 2) -> int:
    print(f"FAIL: {message}")
    return code


def main() -> int:  # pragma: no cover - модуль не запускается сам
    print("relcommon — общая часть конвейера поставки; запускайте build/verify/gate")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
