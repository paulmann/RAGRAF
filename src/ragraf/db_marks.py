"""Признаки колонок БД: генерируемая, автоинкремент, ключ (P1-4).

Зачем отдельный модуль. Признаки колонки приходят из РАЗНЫХ источников и в
разном виде:

  * живая база (MySQL): подсказка СУБД `EXTRA` строкой
    (`auto_increment`, `VIRTUAL GENERATED`, `STORED GENERATED`,
    `DEFAULT_GENERATED`), ключ — `COLUMN_KEY` (`PRI`, `UNI`, `MUL`) и
    отдельная таблица индексов (`unique_flag`);
  * миграции проекта: всё это надо вычитать из текста `CREATE TABLE`.

Пока признаки разбирались в разных местах по-разному, и это дало измеренный
дефект: колонка считалась генерируемой по подстроке `"GENERATED"`. На живой
базе таких совпадений было 171, из них **92 — `DEFAULT_GENERATED`**, то есть
обычная колонка со значением по умолчанию-выражением. `write_check` объявлял
их нерехзаписываемыми и запрещал корректную вставку.

Правило здесь одно и на все источники: генерируемая колонка — это ТОЛЬКО
`VIRTUAL GENERATED` или `STORED GENERATED`. `DEFAULT_GENERATED` — это признак
выражения по умолчанию, а не генерируемого столбца.

Модуль чистый: ни базы, ни файлов; поэтому проверяется без окружения
(`tests/test_db_marks.py`).
"""

from __future__ import annotations

import re

#: Единственные формы, которые делают колонку НЕЗАПИСЫВАЕМОЙ.
GENERATED_RE = re.compile(r"\b(VIRTUAL|STORED)\s+GENERATED\b", re.IGNORECASE)

#: Значение по умолчанию задано выражением (MySQL 8+). Запись в колонку
#: разрешена: это НЕ генерируемая колонка.
DEFAULT_EXPR_RE = re.compile(r"\bDEFAULT_GENERATED\b", re.IGNORECASE)

AUTO_INCREMENT_RE = re.compile(r"\bAUTO_INCREMENT\b", re.IGNORECASE)

#: Генерируемая колонка в ТЕКСТЕ DDL (миграции). Синтаксис MySQL другой:
#: `col type GENERATED ALWAYS AS (expr) STORED` — слово STORED стоит ПОСЛЕ
#: выражения, а `GENERATED` идёт с `ALWAYS AS`. Поэтому одного образца
#: `VIRTUAL GENERATED` (как в подсказке СУБД) для миграций НЕ ХВАТАЕТ:
#: разбор миграций не находил ни одной генерируемой колонки.
DDL_GENERATED_RE = re.compile(
    r"\b(?:GENERATED\s+ALWAYS\s+)?AS\s*\([^)]*\)\s*(?P<kind>VIRTUAL|STORED)\b",
    re.IGNORECASE | re.DOTALL,
)
DDL_GENERATED_ALWAYS_RE = re.compile(r"\bGENERATED\s+ALWAYS\b", re.IGNORECASE)


def generated_kind_ddl(text: str | None) -> str:
    """Генерируемая колонка по тексту DDL: `virtual` | `stored` | `""`.

    `GENERATED ALWAYS AS (...)` без слова VIRTUAL/STORED — тоже генерируемая
    колонка: по умолчанию MySQL делает её VIRTUAL, и запись в неё запрещена.
    Молчать об этом нельзя — именно на такой колонке вставка и падала.
    """
    raw = text or ""
    m = DDL_GENERATED_RE.search(raw)
    if m:
        return m.group("kind").lower()
    if DDL_GENERATED_ALWAYS_RE.search(raw):
        return "virtual"
    return ""


def generated_kind(extra: str | None) -> str:
    """`virtual` | `stored` | `""` — в точности по подсказке СУБД."""
    m = GENERATED_RE.search(extra or "")
    return m.group(1).lower() if m else ""


def is_generated(extra: str | None) -> bool:
    """Можно ли писать в колонку. Единственная точка решения (P0-3, P1-4)."""
    return bool(generated_kind(extra))


def is_auto_increment(extra: str | None) -> bool:
    return bool(AUTO_INCREMENT_RE.search(extra or ""))


def has_default_expression(extra: str | None) -> bool:
    """Значение по умолчанию — выражение, а не константа.

    Поле нужно, чтобы отличить «СУБД подставит сама» от «колонка генерируемая»:
    и то, и другое освобождает агента от значения, но по разным причинам.
    """
    return bool(DEFAULT_EXPR_RE.search(extra or ""))


def marks(extra: str | None = "", column_key: str | None = "",
          *, unique_index: bool = False) -> dict:
    """Разбор признаков колонки в один словарь.

    `unique_index` — сведения из таблицы индексов живой базы: уникальность
    бывает объявлена составным индексом, и по одному `column_key` её не видно.
    """
    kind = generated_kind(extra)
    key = (column_key or "").strip().upper()
    return {
        "pk": key == "PRI",
        "unique": key == "UNI" or bool(unique_index),
        "auto_increment": is_auto_increment(extra),
        "generated": bool(kind),
        "generated_kind": kind,
        "default_expression": has_default_expression(extra),
    }


def writable(col_marks: dict) -> bool:
    """Можно ли записывать значение в колонку с такими признаками."""
    return not bool(col_marks.get("generated"))


def describe(col_marks: dict) -> str:
    """Человеческая строка признаков — для ответа и для сообщения об отказе."""
    parts = []
    if col_marks.get("pk"):
        parts.append("первичный ключ")
    if col_marks.get("unique"):
        parts.append("уникальная")
    if col_marks.get("auto_increment"):
        parts.append("AUTO_INCREMENT")
    if col_marks.get("generated"):
        parts.append(f"генерируемая ({col_marks.get('generated_kind')})")
    if col_marks.get("default_expression"):
        parts.append("значение по умолчанию — выражение")
    return ", ".join(parts) if parts else "обычная"
