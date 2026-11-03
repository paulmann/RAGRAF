"""Список исключений, управляемый настройками (уровень pro).

Зачем отдельный модуль. Владелец 28.10.2026 попросил управлять исключениями из
настроек и по умолчанию не считать `temp/`, `cache/` и подобные каталоги. Сделать
это правкой базового списка (`config.DEFAULT_EXCLUDES`) нельзя, и причина
измерена: базовый список действует на ВСЕ корпуса сразу, а имена неоднозначны.
Замер `tools/measure_noisy_dirs.py` по 40 корпусам (28.10.2026):

  * `cache` — 15 файлов, из них файлов кода 0 (только JSON кеша ответов модели);
  * `logs` — 1 файл, кода 0;
  * `tmp` — 1663 файла, из них файлов кода 1215 (C# 723, PHP 228, JS 227);
  * `bin` — 56 файлов, и ВСЕ 56 — код (PHP 47, bash 6);
  * `publish` — 70 файлов, из них 68 документации и 2 скрипта;
  * `storage` — 76 файлов, из них 19 кода.

Поэтому исключение вынесено в НАСТРОЙКУ: она видна, её значение лежит в конфиге
корпуса, и её включение можно измерить до применения. Базовый список не меняется
— инвариант «`.ragraf` исключён всегда» и «расширение базового списка требует
замера» остаются в силе.

Границы, которые модуль соблюдает сам:
  * имена проверяются как имена КАТАЛОГОВ: шаблоны, слэши и `..` отклоняются —
    иначе «настройка» стала бы способом исключить что угодно, включая корпус;
  * `.ragraf` добавить нельзя: самоисключение обязательно, иначе индексация
    читает собственные базы;
  * регистр имеет значение (сопоставление в проекте регистрозависимо):
    `cache` не задевает `src/Cache`, где у revoap.com лежит рабочий код.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Iterable

#: Что предлагается включить по умолчанию. Это ПРЕДЛОЖЕНИЕ, а не принуждение:
#: включение проверяется замером (`impact`), и при потере файлов кода операция
#: отказывает, называя файлы.
NOISY_PRESET: tuple[str, ...] = ("temp", "tmp", "cache", "logs", "trash", "junk")

#: Что проверено как безопасное на всех 40 корпусах: файлов кода ноль.
SAFE_PRESET: tuple[str, ...] = ("cache", "logs")

#: Языки, потеря которых означает потерю работы. `markdown`, `text`, `sql`
#: сюда не входят: их отсутствие ломает документацию, но не навигацию по коду.
#: Между тем и они попадают в отчёт — терять документацию молча нельзя.
CODE_LANGS: frozenset[str] = frozenset({
    "php", "python", "javascript", "typescript", "csharp", "perl", "bash",
    "powershell", "ruby", "lua", "go", "java", "c", "cpp",
})

#: Имена, которые нельзя исключать настройкой ни при каких условиях.
FORBIDDEN: frozenset[str] = frozenset({".ragraf", "ragraf"})

NAME_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$")


class ExcludeError(ValueError):
    """Имя каталога не годится для списка исключений."""


def normalize(raw: str | Iterable[str] | None) -> list[str]:
    """Привести список имён к виду, пригодному для шаблонов исключения.

    Принимает строку через запятую (как её хранит настройка) или готовый
    список. Отклоняет всё, что не является простым именем каталога: это не
    придирка, а граница — «исключить» через настройку не должно означать
    «исключить что угодно».
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(",")]
    else:
        parts = [str(p).strip() for p in raw]

    out: list[str] = []
    for name in parts:
        if not name:
            continue
        if name in FORBIDDEN:
            raise ExcludeError(
                f"каталог {name!r} исключать нельзя: самоисключение индекса "
                "обязательно, иначе индексация читает собственные базы"
            )
        if name in (".", "..") or not NAME_RE.fullmatch(name):
            raise ExcludeError(
                f"{name!r} — не имя каталога: допустимы буквы, цифры, точка, "
                "дефис и подчёркивание; шаблоны, слэши и переходы вверх запрещены"
            )
        if name not in out:
            out.append(name)
    return out


def patterns(names: Iterable[str]) -> list[str]:
    """Шаблоны исключения для имён каталогов: `temp` → `**/temp/**`."""
    return [f"**/{name}/**" for name in names]


def impact(cfg: Any, names: Iterable[str]) -> dict[str, Any]:
    """Что выпадет из корпуса, если исключить эти каталоги.

    Сканирование идёт тем же сканером, что и индексация, и с ВЫКЛЮЧЕННЫМ
    профилем: считается именно эффект настройки, а не текущее состояние.
    Поэтому отчёт пригоден для решения до включения.
    """
    from . import corpus

    wanted = normalize(list(names))
    plain_cfg = cfg
    try:
        plain_cfg = replace(cfg, index=replace(cfg.index, exclude_noisy_enabled=False))
    except Exception:  # noqa: BLE001 — конфиг может быть заглушкой в проверках
        pass

    scan = corpus.scan(plain_cfg, digest=False)
    by_name: dict[str, dict[str, Any]] = {
        name: {"files": 0, "code": 0, "langs": {}, "examples": []} for name in wanted
    }
    total = 0
    for entry in scan.files:
        total += 1
        hit = None
        for seg in entry.path.split("/"):
            if seg in by_name:
                hit = seg
                break
        if hit is None:
            continue
        bucket = by_name[hit]
        bucket["files"] += 1
        lang = (entry.lang or "?").lower()
        bucket["langs"][lang] = bucket["langs"].get(lang, 0) + 1
        if lang in CODE_LANGS:
            bucket["code"] += 1
            if len(bucket["examples"]) < 5:
                bucket["examples"].append(entry.path)

    files = sum(b["files"] for b in by_name.values())
    code = sum(b["code"] for b in by_name.values())
    return {
        "names": wanted,
        "files_total": total,
        "files_dropped": files,
        "code_dropped": code,
        "by_name": by_name,
        "safe": code == 0,
        "verdict": (
            "файлов кода не теряется — включать безопасно"
            if code == 0
            else "в этих каталогах есть файлы КОДА: включение выбросит их из "
                 "индекса, нужно решение по каждому файлу"
        ),
    }


def describe(cfg: Any) -> dict[str, Any]:
    """Действующая настройка корпуса и её фактический эффект."""
    index_cfg = getattr(cfg, "index", None)
    enabled = bool(getattr(index_cfg, "exclude_noisy_enabled", False))
    names = normalize(str(getattr(index_cfg, "exclude_noisy_dirs", "") or ""))
    return {
        "enabled": enabled,
        "dirs": names,
        "patterns": patterns(names) if enabled else [],
        "preset_default": list(NOISY_PRESET),
        "preset_safe": list(SAFE_PRESET),
    }
