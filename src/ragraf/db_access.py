"""Доступ к базам данных для индексации их структуры.

Где хранится значение доступа
    В `catalog/db_access.json` — тот же каталог, что у файлов доступа к панели, и
    он **исключён из корпуса**. Значит значение не попадает ни в индекс, ни в
    поисковую выдачу агента.

Приоритет источников
    **Переменные окружения важнее файла.** Владелец может не хранить значение на
    диске вовсе: тогда оно приходит из окружения и в файле его нет. Это тот же
    порядок, что уже принят для доступа к панели, и он проверяется тестом.

    Имена переменных (значения — секрет, имена — данные):
        RAGRAF_DB_HOST, RAGRAF_DB_PORT, RAGRAF_DB_USER, RAGRAF_DB_PASSWORD
        RAGRAF_DB_DIALECT (mysql | postgresql), RAGRAF_DB_NAME (конкретная база)

Что НЕ делает модуль
    Не выполняет ни одного запроса к данным при чтении настроек и не возвращает
    значение доступа наружу: наружу отдаётся только признак «задано».

Только чтение
    Проект сознательно не поддерживает запись и изменение структуры: индексация
    не должна уметь менять чужую базу. Единственный режим — `readonly`, и в этом
    модуле нет ни одной операции, кроме выборки.

Автор: Михаил Дейнекин
Сайт: https://Deynekin.com
Почта: Mikhail@Deynekin.com
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import util

REPO = Path(__file__).resolve().parents[2]
CATALOG_DIR = REPO / "catalog"

DIALECTS = ("mysql", "postgresql")

# Служебные базы: их структура не описывает проект, а шум в индексе заметный.
DEFAULT_EXCLUDED = (
    "information_schema", "performance_schema", "mysql", "sys",      # MySQL
    "template0", "template1", "pg_catalog", "pg_toast",              # PostgreSQL
)

# Пределы. Значения подобраны так, чтобы индексация 98 схем не занимала часы и не
# вычитывала таблицы целиком: примеры берутся выборкой с явным LIMIT.
DEFAULT_MAX_TABLES = 5000
DEFAULT_MAX_COLUMNS = 200
DEFAULT_SAMPLES_ROWS = 50
DEFAULT_SAMPLES_BYTES = 2_000_000
DEFAULT_TIMEOUT = 30


@dataclass
class DbAccess:
    """Настройки доступа к одной СУБД."""

    enabled: bool = False
    dialect: str = "mysql"
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "root"
    # Значение доступа. Наружу НЕ отдаётся: см. `public()`.
    password: str = ""
    # Какие базы брать. Пустой `include` означает «все, кроме служебных».
    databases_include: list[str] = field(default_factory=list)
    databases_exclude: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDED))
    # Сбор примеров строк — отдельное решение: это единственная часть, которая
    # касается содержимого, поэтому по умолчанию выключена.
    samples_enabled: bool = False
    samples_max_rows: int = DEFAULT_SAMPLES_ROWS
    samples_max_bytes: int = DEFAULT_SAMPLES_BYTES
    max_tables: int = DEFAULT_MAX_TABLES
    max_columns: int = DEFAULT_MAX_COLUMNS
    timeout_seconds: int = DEFAULT_TIMEOUT
    # Только чтение. Оставлено полем, чтобы интерфейс мог показать состояние;
    # значение всегда True — записи в базе RAGRAF не делает.
    readonly: bool = True
    updated_at: str = ""

    def public(self) -> dict[str, Any]:
        """Вид для интерфейса: БЕЗ значения доступа.

        Наружу отдаётся `password_set` — признак, что доступ задан, и источник,
        откуда он взялся (файл или окружение). Это позволяет понять состояние,
        не раскрывая самого значения.
        """
        data = asdict(self)
        data.pop("password", None)
        data["password_set"] = bool(self.password)
        data["source"] = self.source()
        data["dialects"] = list(DIALECTS)
        return data

    def source(self) -> str:
        """Откуда взято значение доступа: окружение важнее файла."""
        if os.environ.get("RAGRAF_DB_PASSWORD"):
            return "environment"
        if self.password:
            return "file"
        return "none"

    def exclusions(self) -> list[str]:
        return [name.lower() for name in self.databases_exclude]

    def accepts_database(self, name: str) -> bool:
        """Брать ли эту базу.

        Правило намеренно простое и предсказуемое: явный `include` выигрывает,
        иначе база берётся, если её имени нет в исключениях.
        """
        low = (name or "").lower()
        if not low:
            return False
        if self.databases_include:
            return any(low == inc.lower() for inc in self.databases_include)
        return low not in self.exclusions()


class DbAccessStore:
    """Хранилище настроек доступа к базам: один файл рядом с реестром."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (CATALOG_DIR / "db_access.json")

    def load(self) -> DbAccess:
        """Прочитать настройки. Неизвестные ключи игнорируются."""
        raw = util.read_json(self.path, default=None)
        if not isinstance(raw, dict):
            raw = {}
        nested = raw.get("access")
        source: dict[str, Any] = nested if isinstance(nested, dict) else raw

        access = DbAccess(
            enabled=bool(source.get("enabled", False)),
            dialect=str(source.get("dialect") or "mysql").strip().lower(),
            host=str(source.get("host") or "127.0.0.1").strip(),
            port=int(source.get("port") or 3306),
            user=str(source.get("user") or "root").strip(),
            password=str(source.get("password") or ""),
            databases_include=_as_list(source.get("databases_include")),
            databases_exclude=_as_list(source.get("databases_exclude"))
            or list(DEFAULT_EXCLUDED),
            samples_enabled=bool(source.get("samples_enabled", False)),
            samples_max_rows=int(source.get("samples_max_rows") or DEFAULT_SAMPLES_ROWS),
            samples_max_bytes=int(source.get("samples_max_bytes") or DEFAULT_SAMPLES_BYTES),
            max_tables=int(source.get("max_tables") or DEFAULT_MAX_TABLES),
            max_columns=int(source.get("max_columns") or DEFAULT_MAX_COLUMNS),
            timeout_seconds=int(source.get("timeout_seconds") or DEFAULT_TIMEOUT),
            readonly=True,
            updated_at=str(source.get("updated_at") or ""),
        )
        if access.dialect not in DIALECTS:
            access.dialect = "mysql"
        _apply_environment(access)
        return access

    def save(self, values: dict[str, Any]) -> DbAccess:
        """Сохранить настройки, проверив значения.

        Пустое значение доступа НЕ стирает уже сохранённое: иначе смена порта
        случайно сбрасывала бы доступ, и индексация переставала работать без
        понятной причины. Явный сброс делается ключом `password_clear`.
        """
        current = self.load()
        problems = validate(values)
        if problems:
            raise ValueError("; ".join(problems))

        clear = bool(values.get("password_clear"))
        merged: dict[str, Any] = {
            "enabled": bool(values.get("enabled", current.enabled)),
            "dialect": str(values.get("dialect") or current.dialect).lower(),
            "host": str(values.get("host") or current.host).strip(),
            "port": int(values.get("port") or current.port),
            "user": str(values.get("user") or current.user).strip(),
            "databases_include": _as_list(values.get("databases_include"))
            if "databases_include" in values else current.databases_include,
            "databases_exclude": _as_list(values.get("databases_exclude"))
            if "databases_exclude" in values else current.databases_exclude,
            "samples_enabled": bool(values.get("samples_enabled",
                                                current.samples_enabled)),
            "samples_max_rows": int(values.get("samples_max_rows")
                                    or current.samples_max_rows),
            "samples_max_bytes": int(values.get("samples_max_bytes")
                                     or current.samples_max_bytes),
            "max_tables": int(values.get("max_tables") or current.max_tables),
            "max_columns": int(values.get("max_columns") or current.max_columns),
            "timeout_seconds": int(values.get("timeout_seconds")
                                   or current.timeout_seconds),
        }
        if clear:
            merged["password"] = ""
        else:
            given = str(values.get("password") or "")
            merged["password"] = given or current.password
        merged["updated_at"] = util.now_iso()

        util.write_json(self.path, {"version": 1, "access": merged}, indent=2)
        _restrict(self.path)
        return self.load()


def _as_list(value: Any) -> list[str]:
    """Список имён: принимается строка с разделителями или список."""
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.replace(";", ",").replace("\n", ",").split(",")
        return [p.strip() for p in parts if p.strip()]
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _restrict(path: Path) -> None:
    """Сузить права на файл, если система это позволяет (см. access.py)."""
    try:
        path.chmod(0o600)
    except Exception:
        pass


def _apply_environment(access: DbAccess) -> None:
    """Перекрыть настройки значениями окружения.

    Окружение важнее файла намеренно: так значение доступа можно вообще не
    хранить на диске. Пустая переменная ничего не перекрывает — иначе пустое
    значение в окружении случайно обнуляло бы рабочую настройку.
    """
    mapping = {
        "RAGRAF_DB_HOST": "host",
        "RAGRAF_DB_USER": "user",
        "RAGRAF_DB_PASSWORD": "password",
        "RAGRAF_DB_DIALECT": "dialect",
    }
    for env_name, attr in mapping.items():
        value = (os.environ.get(env_name) or "").strip()
        if value:
            setattr(access, attr, value)
    port = (os.environ.get("RAGRAF_DB_PORT") or "").strip()
    if port.isdigit():
        access.port = int(port)
    if access.dialect not in DIALECTS:
        access.dialect = "mysql"


def default_port(dialect: str) -> int:
    return 5432 if dialect == "postgresql" else 3306


def validate(values: dict[str, Any]) -> list[str]:
    """Проверить значения перед сохранением. Возвращает причины отказа."""
    problems: list[str] = []
    dialect = str(values.get("dialect") or "").lower()
    if dialect and dialect not in DIALECTS:
        problems.append(f"«dialect»: допустимо {', '.join(DIALECTS)}, "
                        f"получено {dialect!r}")
    port = values.get("port")
    if port is not None:
        try:
            number = int(port)
        except (TypeError, ValueError):
            problems.append(f"«port»: нужно число, получено {port!r}")
        else:
            if not (1 <= number <= 65535):
                problems.append(f"«port»: вне диапазона 1..65535 ({number})")
    host = str(values.get("host") or "")
    if host and any(ch.isspace() for ch in host):
        problems.append("«host»: пробелы в адресе недопустимы")
    for key, low, high in (("samples_max_rows", 1, 1000),
                           ("samples_max_bytes", 1000, 50_000_000),
                           ("max_tables", 1, 100_000),
                           ("max_columns", 10, 5000),
                           ("timeout_seconds", 1, 600)):
        if key in values and values[key] is not None:
            try:
                number = int(values[key])
            except (TypeError, ValueError):
                problems.append(f"«{key}»: нужно число, получено {values[key]!r}")
                continue
            if not (low <= number <= high):
                problems.append(f"«{key}»: вне диапазона {low}..{high} ({number})")
    return problems


def state() -> dict[str, Any]:
    """Состояние доступа для интерфейса: без значения, но с источником."""
    access = DbAccessStore().load()
    return {
        "access": access.public(),
        "default_port": default_port(access.dialect),
        "default_excluded": list(DEFAULT_EXCLUDED),
        "limits": {
            "samples_max_rows": [1, 1000],
            "samples_max_bytes": [1000, 50_000_000],
            "max_tables": [1, 100_000],
            "max_columns": [10, 5000],
            "timeout_seconds": [1, 600],
        },
    }
