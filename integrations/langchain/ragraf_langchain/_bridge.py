# -*- coding: utf-8 -*-
"""Мост к RAGRAF: где лежит корпус, откуда берётся пакет, как создаётся фасад.

Зачем отдельный модуль. У интеграции две точки отказа, и обе обязаны называть
причину, а не падать трассировкой:

1. Конфигурация корпуса не найдена — тогда непонятно, о каком проекте спрашивают,
   и ответ «пусто» был бы ложью (пустой результат поиска — это код 0 по контракту
   `src/ragraf/cli.py:87`, то есть «ничего не нашлось», а не «не туда смотрели»).
2. Пакет `ragraf` не установлен, но исходники лежат рядом. В этом дереве пакет
   намеренно НЕ устанавливается: `tools/ragraf.py:27-33` подставляет `src` в
   `sys.path`, потому что `PYTHONPATH` в окружении выполнения отфильтровывается.
   Здесь делается то же самое, но с явным поиском корня проекта.

Фасад создаётся ОДИН раз на конфигурацию И НА ПОТОК: конструктор открывает три
хранилища SQLite, а соединения `sqlite3` привязаны к создавшему их потоку. Замер
03.11.2026: вызов `RAGRAFRetriever.ainvoke` уводит работу в поток (`asyncio.to_thread`),
и общий фасад там падал с `SQLite objects created in a thread can only be used in
that same thread`. Поэтому кэш — по паре (конфигурация, поток), а не только по
пути: это цена асинхронного вызова, и она названа в README, а не спрятана.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Any

__all__ = [
    "RAGRAFConfigError",
    "find_project_root",
    "resolve_config_path",
    "get_facade",
    "facade_cache_size",
    "describe_environment",
]

#: Признак корня проекта RAGRAF: исходники пакета на месте.
PROJECT_MARKER = Path("src") / "ragraf" / "facade.py"
#: Переменные окружения, которыми корпус можно задать снаружи.
ENV_CONFIG = ("RAGRAF_CONFIG", "RAGRAF_PROJECT")

_LOCK = threading.Lock()
_FACADES: dict[tuple[str, str, str, bool, int], Any] = {}


class RAGRAFConfigError(RuntimeError):
    """Корпус не найден или конфигурация не разбирается — с названной причиной."""

    def __init__(self, message: str, tried: list[str] | None = None) -> None:
        self.tried = list(tried or [])
        if self.tried:
            message = f"{message}\nИскали здесь:\n" + "\n".join(f"  - {item}" for item in self.tried)
        super().__init__(message)


def find_project_root(start: str | Path) -> Path | None:
    """Ближайший вверх каталог, в котором лежат исходники RAGRAF."""
    base = Path(start).resolve()
    if base.is_file():
        base = base.parent
    for candidate in (base, *base.parents):
        if (candidate / PROJECT_MARKER).is_file():
            return candidate
    return None


def resolve_config_path(config_path: str | Path | None = None,
                        start: str | Path | None = None) -> Path:
    """Найти файл конфигурации корпуса, назвав все проверенные места.

    Порядок: явный аргумент → `RAGRAF_CONFIG` → `RAGRAF_PROJECT` (путь к корпусу
    либо к его конфигурации) → `<каталог>/config/ragraf.yaml` вверх по дереву.
    """
    tried: list[str] = []

    def accept(value: str | Path | None) -> Path | None:
        if not value:
            return None
        path = Path(value).expanduser()
        tried.append(str(path))
        if path.is_dir():
            path = path / "config" / "ragraf.yaml"
        return path if path.is_file() else None

    found = accept(config_path)
    if found is not None:
        return found
    for name in ENV_CONFIG:
        found = accept(os.environ.get(name))
        if found is not None:
            return found
    base = Path(start).resolve() if start else Path.cwd()
    for candidate in (base, *base.parents):
        tried.append(str(candidate / "config" / "ragraf.yaml"))
        if (candidate / "config" / "ragraf.yaml").is_file():
            return candidate / "config" / "ragraf.yaml"
    raise RAGRAFConfigError(
        "не найдена конфигурация корпуса RAGRAF; передайте config_path явно "
        "или задайте RAGRAF_CONFIG", tried)


def _import_ragraf(config_path: Path) -> Any:
    """Импортировать пакет `ragraf`, при необходимости подставив `src` в `sys.path`."""
    try:
        import ragraf  # noqa: PLC0415 — импорт по требованию: без конфигурации он не нужен

        return ragraf
    except ImportError:
        pass
    root = find_project_root(config_path)
    if root is None:
        raise RAGRAFConfigError(
            "пакет ragraf не установлен, а исходники не найдены рядом с "
            f"конфигурацией {config_path}; установите пакет (`pip install .`) "
            "или работайте в дереве RAGRAF")
    src = str(root / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    import ragraf  # noqa: PLC0415

    return ragraf


def get_facade(config_path: str | Path | None = None, *,
               watch: bool = False,
               telemetry: bool = True,
               agent: str = "langchain",
               agent_kind: str = "external",
               tool: str = "dev",
               task: str = "") -> Any:
    """Фасад операций чтения, один на конфигурацию.

    `watch=False` по умолчанию намеренно: при `watch=True` конструктор зовёт
    `_ensure_fresh` (`src/ragraf/facade.py:1142`, вызов — `:826`) и при расхождении
    контрольной суммы запускает переиндексацию, а библиотечный вызов из LangChain
    не должен неожиданно пересобирать корпус на сотни секунд.
    Свежесть при этом не замалчивается: `ragraf_freshness` и поле `stale` в
    метаданных каждого документа говорят правду о состоянии индекса.

    `tool="dev"` — канал из закрытого перечня (`mcp`, `cli`, `admin`, `dev`,
    `selftest`, `src/ragraf/telemetry.py:151`). Шестого значения интеграция не
    заводит: иначе статистика по каналам разошлась бы с документацией.

    Ключ кэша включает идентификатор потока: соединение SQLite нельзя передать
    другому потоку, и общий фасад на два потока — это не оптимизация, а падение.
    """
    path = resolve_config_path(config_path)
    key = (str(path), str(agent), str(tool), bool(watch), threading.get_ident())
    with _LOCK:
        cached = _FACADES.get(key)
        if cached is not None:
            return cached
        _import_ragraf(path)
        from ragraf.config import load as load_config  # noqa: PLC0415
        from ragraf.facade import Facade  # noqa: PLC0415

        cfg = load_config(path)
        facade = Facade(cfg, tool=tool, telemetry=telemetry, watch=watch,
                        agent=agent, agent_kind=agent_kind, task=task)
        _FACADES[key] = facade
        return facade


def facade_cache_size() -> int:
    """Сколько фасадов открыто в этом процессе (для проверок и диагностики)."""
    with _LOCK:
        return len(_FACADES)


def clear_facades() -> None:
    """Закрыть и забыть открытые фасады (освобождает соединения SQLite)."""
    with _LOCK:
        for facade in _FACADES.values():
            closer = getattr(facade, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:  # noqa: BLE001 — закрытие не имеет права падать
                    pass
        _FACADES.clear()


def describe_environment(config_path: str | Path | None = None) -> dict[str, Any]:
    """Что нашлось: путь конфигурации, корень корпуса, версия пакета, ревизия."""
    path = resolve_config_path(config_path)
    ragraf = _import_ragraf(path)
    facade = get_facade(path)
    return {
        "config": str(path),
        "root": str(getattr(facade.cfg, "root", "")),
        "package": getattr(ragraf, "__version__", "unknown"),
        "index_rev": facade.current_rev(),
        "python": sys.version.split()[0],
    }
