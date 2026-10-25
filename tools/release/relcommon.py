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
  подражается): два файла, различающиеся регистром, — разные файлы поставки;
* манифест может НАСЛЕДОВАТЬ другой манифест (`[release] inherits = "…"`), и это
  не удобство, а защита от расхождения правил. Второе издание отличается от
  первого ДОБАВКОЙ, а не повтором шестидесяти включений: копия разошлась бы на
  первой правке, а правила, повторённые дважды, — измеренный класс дефекта этого
  проекта (установщик существовал в трёх версиях, каждая считала себя верной).
  Цепочка из двух звеньев разрешена, но ОБЪЯВЛЯЕТСЯ явно (`inherit_chain = 2`):
  Enterprise = Pro + своя папка, Pro = Community + своя папка. Без объявления
  «где кончаются правила» переставало бы читаться — поэтому длина цепочки
  проверяется, а не подразумевается.
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

#: Предел длины цепочки наследования, в звеньях. Community — основание (0),
#: Pro — звено 1, Enterprise — звено 2. Четвёртое издание потребует отдельного
#: решения, а не тихого удлинения цепочки: у каждого звена есть цена чтения.
MAX_INHERIT_DEPTH = 2


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
    #: Пути, которые исключение основания отсекает, а ЭТО издание публикует.
    #: Единственный способ выразить «модуль закрытой части лежит в src/»: иначе
    #: пришлось бы либо держать копию файла в папке издания (две копии расходятся
    #: на первой правке), либо разрешить исключению «не срабатывать» без
    #: объявления — то есть тихо.
    pro_only: tuple[str, ...] = ()


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


def _read_manifest(path: pathlib.Path) -> tuple[dict, dict]:
    """Прочитать манифест как TOML: (все данные, раздел [release])."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return data, data.get("release", {})


def chain_depth(path: pathlib.Path, source: pathlib.Path,
                limit: int = MAX_INHERIT_DEPTH) -> int:
    """Длина цепочки наследования в звеньях, начиная с этого манифеста.

    Одно звено — один `[release] inherits`. Community наследует ноль звеньев,
    Pro — одно (Community), Enterprise — два (Pro и через него Community).
    Больше предела не разрешается: чем длиннее цепочка, тем труднее ответить
    «откуда это правило», а именно этот вопрос наследование и должно сохранять
    читаемым. Предел измеряется здесь, а не подразумевается в комментарии.
    """
    depth = 0
    current = path
    while True:
        _, release = _read_manifest(current)
        inherited = str(release.get("inherits") or "").strip()
        if not inherited:
            return depth
        depth += 1
        if depth > limit:
            raise ValueError(
                f"цепочка наследования длиннее {limit} звеньев (предел "
                f"MAX_INHERIT_DEPTH): манифест {path.name} наследника не читается"
            )
        current = (source / normalise(inherited)).resolve()
        if not current.is_file():
            raise FileNotFoundError(f"манифест-основание не найден: {current}")


def _chain(path: pathlib.Path, source: pathlib.Path) -> list[pathlib.Path]:
    """Цепочка манифестов от основания к этому: правила читаются по порядку."""
    items = [path]
    current = path
    while True:
        _, release = _read_manifest(current)
        inherited = str(release.get("inherits") or "").strip()
        if not inherited:
            break
        current = (source / normalise(inherited)).resolve()
        if not current.is_file():
            raise FileNotFoundError(f"манифест-основание не найден: {current}")
        items.append(current)
    items.reverse()
    return items


def load_manifest(path: pathlib.Path, source: pathlib.Path) -> Manifest:
    _, release = _read_manifest(path)
    depth = chain_depth(path, source)

    # Длина цепочки объявляется явно, если звеньев больше одного. Одного звена
    # объявление не требует: Community → Pro читается в два шага и без него.
    declared_raw = release.get("inherit_chain")
    declared = None
    if declared_raw not in (None, ""):
        try:
            declared = int(declared_raw)
        except (TypeError, ValueError):
            raise ValueError(
                f"inherit_chain = {declared_raw!r} не число: ожидается длина "
                f"цепочки в звеньях ({depth})"
            ) from None
    if depth >= 2 and declared is None:
        raise ValueError(
            f"цепочка наследования из {depth} звеньев не объявлена: укажите "
            f"[release] inherit_chain = {depth} — иначе неизвестно, где "
            f"кончаются правила"
        )
    if declared is not None and declared != depth:
        raise ValueError(
            f"объявлено inherit_chain = {declared}, а цепочка фактически {depth} "
            f"звен(а/ьев): объявление расходится с файлами"
        )

    includes: list[Include] = []
    excludes: list[Exclude] = []
    merged: dict = {}
    for item in _chain(path, source):
        item_data, item_release = _read_manifest(item)
        # Порядок слияния: основание первым, конечный манифест последним.
        # Значит `product`, `artifacts` и прочие поля конечного перекрывают
        # унаследованные, а правила складываются в порядке чтения.
        merged.update(item_release)
        for raw in item_data.get("include", []):
            origin = normalise(raw["from"])
            target = normalise(raw["to"])
            includes.append(
                Include(
                    source=origin,
                    target=target,
                    kind=raw.get("kind", "file"),
                    is_dir=(source / origin).is_dir(),
                )
            )
        for raw in item_data.get("exclude", []):
            pattern = normalise(raw["pattern"])
            excludes.append(
                Exclude(
                    pattern=pattern,
                    state=raw.get("state", "active"),
                    why=raw.get("why", "").strip(),
                    regex=pattern_to_regex(pattern),
                )
            )

    pro_only_raw = merged.get("pro_only", [])
    if not isinstance(pro_only_raw, list):
        raise ValueError(
            "pro_only должен быть списком путей: "
            f"{type(pro_only_raw).__name__}"
        )
    pro_only: list[str] = []
    for item in pro_only_raw:
        value = normalise(str(item))
        if not value:
            continue
        if (source / value).is_dir():
            raise ValueError(f"pro_only: {value} — каталог, а не файл: объявляйте файлы поимённо")
        if not (source / value).is_file():
            raise ValueError(
                f"pro_only: файла {value} нет в живом дереве — объявление описывает "
                f"то, чего нет"
            )
        if value in pro_only:
            raise ValueError(f"pro_only: {value} объявлен дважды")
        pro_only.append(value)

    return Manifest(
        path=path,
        product=str(merged.get("product", "RAGRAF")),
        artifacts=tuple(normalise(item) for item in merged.get("artifacts", [])),
        includes=tuple(includes),
        excludes=tuple(excludes),
        pro_only=tuple(pro_only),
    )


# ---------------------------------------------------------------------------
# Сбор кандидатов
# ---------------------------------------------------------------------------


@dataclass
class Plan:
    """Что именно попадёт в поставку и что отсечено."""

    files: dict[str, pathlib.Path] = field(default_factory=dict)   # target → источник
    excluded: dict[str, list[str]] = field(default_factory=dict)   # шаблон → отсечённые
    #: Шаблон - пути, которые исключение поймало, но издание их публикует
    #: (объявлено в `pro_only`). Считаются отдельно от `excluded`: «правило
    #: сработало» и «файл всё равно в поставке» — разные утверждения, и
    #: смешивать их значило бы врать о составе.
    kept: dict[str, list[str]] = field(default_factory=dict)
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
            if matched is not None and source_rel in manifest.pro_only:
                # Закрытый модуль этого издания: исключение основания поймало
                # путь, но объявление издания его публикует. Запоминается, чтобы
                # отчёт не выдавал правило за действующее ограничение.
                plan.kept.setdefault(matched.pattern, []).append(source_rel)
                matched = None
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
