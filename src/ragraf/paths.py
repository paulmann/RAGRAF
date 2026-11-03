"""Где RAGRAF хранит СОСТОЯНИЕ: каталог управления и файл глобальных настроек.

Назначение
    Один выбор пути на весь продукт. До этого модуля путь к `catalog/`
    вычислялся в семи местах независимо (`access.py`, `db_access.py`,
    `settings.py`, `sources.py`, `sso.py`, `vault.py`, `catalog.py`), и все семь
    указывали на каталог УСТАНОВКИ. Следствие измерено 30.10.2026: прогон
    проверок в песочнице писал в живой `catalog/settings.json` и в живой реестр
    разработок — то есть прогон менял состояние, на котором сам же и проверяет.

Два уровня переопределения
    1. `RAGRAF_CATALOG_DIR` — каталог состояния целиком (реестр, настройки,
       журнал состава, токены доступа). Им пользуется прогон проверок и любая
       песочница: одна переменная отводит все записи в отдельный каталог.
    2. `RAGRAF_SETTINGS_PATH` — только файл настроек. Нужен, когда песочнице
       требуется свой файл настроек, но реестр берётся настоящий.

    Переменные читаются при КАЖДОМ обращении, а не один раз при импорте:
    проверке это позволяет завести своё состояние уже после `import ragraf`,
    не перезапуская процесс.

Границы
    Модуль отвечает только за ПУТЬ. Он ничего не создаёт, не читает и не
    переносит: отсутствующий файл — это «настроек нет», а не повод завести
    каталог.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Корень установки RAGRAF: два уровня вверх от `src/ragraf/paths.py`.
REPO = Path(__file__).resolve().parents[2]

#: Имя переменной, отводящей весь каталог состояния в другое место.
CATALOG_ENV = "RAGRAF_CATALOG_DIR"

#: Имя переменной, отводящей только файл глобальных настроек.
SETTINGS_ENV = "RAGRAF_SETTINGS_PATH"

#: Имя переменной, отводящей каталог, куда `ensure` кладёт конфигурацию
#: нового корпуса. Нужна прогонам проверок: конфиг пробы писался в живой
#: `config/` проекта, попадал в корпус своим именем с pid и делал корпус
#: устаревшим после каждого прогона (замер 31.10.2026: две проверки подряд
#: обе запускали переиндексацию 43–63 с).
CONFIG_ENV = "RAGRAF_CONFIG_DIR"

#: Имя переменной, отводящей домашний каталог состояния целиком (`~/.ragraf`).
#: Нужна там, где одна установка обслуживает несколько независимых наборов
#: состояния: песочница, перенос на другую машину, второй профиль.
HOME_ENV = "RAGRAF_HOME"


def home_dir() -> Path:
    """Домашний каталог состояния: `~/.ragraf`.

    Зачем отдельный уровень. Замер 01.11.2026 на чистой установке: `REPO`
    (два уровня вверх от этого файла) в установленном колесе — это
    `<venv>/lib/python3.13`, поэтому `catalog/` и `config/` попадали ВНУТРЬ
    каталога интерпретатора. Следствия: `init` отказывал на втором запуске,
    а обновление venv уносило реестр, секреты и лицензию.

    `RAGRAF_HOME` отводит домашний каталог целиком (песочница, перенос состояния
    на другую машину). Значение по умолчанию — `~/.ragraf`, тот же корень, куда
    `scripts/install.ps1` ставит свой venv: у продукта один дом, а не два.
    """
    override = os.environ.get(HOME_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".ragraf"


def is_checkout() -> bool:
    """Рядом с кодом лежит рабочая копия проекта, а не установка.

    Признак взят по двум файлам сразу: `pyproject.toml` в корне и каталог
    `src/ragraf`. В установленном колесе нет ни одного: `parents[2]` там —
    каталог интерпретатора.
    """
    root = install_root()
    return (root / "pyproject.toml").is_file() and (root / "src" / "ragraf").is_dir()


def install_root() -> Path:
    """Откуда запущен код: рабочая копия либо каталог установки."""
    return REPO


def catalog_dir() -> Path:
    """Каталог состояния: реестр разработок, настройки, журнал состава.

    Порядок: переменная окружения → рабочая копия (`<корень>/catalog`) →
    домашний каталог (`~/.ragraf/catalog`). `RAGRAF_CATALOG_DIR` должен
    указывать на КАТАЛОГ; путь к файлу вместо каталога — ошибка вызывающей
    стороны, и здесь она не «исправляется» молча.
    """
    override = os.environ.get(CATALOG_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    if is_checkout():
        return REPO / "catalog"
    return home_dir() / "catalog"


def settings_path() -> Path:
    """Файл глобальных настроек.

    `RAGRAF_SETTINGS_PATH` сильнее каталога состояния: если заданы обе
    переменные, настройки берутся из файла, названного явно. Так песочница
    может держать настоящий реестр и свои настройки — или наоборот.
    """
    override = os.environ.get(SETTINGS_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return catalog_dir() / "settings.json"


def config_dir() -> Path:
    """Каталог, куда `ensure` кладёт конфигурацию нового корпуса.

    Без переменной окружения — `config/` рядом с установленным RAGRAF, как
    было всегда. `RAGRAF_CONFIG_DIR` должен указывать на КАТАЛОГ.
    """
    override = os.environ.get(CONFIG_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    if is_checkout():
        return REPO / "config"
    return home_dir() / "config"


def state_file(name: str) -> Path:
    """Файл состояния по имени: `state_file("catalog.json")`.

    Короткая форма нужна, чтобы потребители не собирали путь сами: сборка
    пути в другом месте — это и есть способ снова разойтись.
    """
    return catalog_dir() / name


def describe() -> dict[str, str]:
    """Откуда взят путь: для ответа операции и для разбора прогона.

    Возвращает источник (`env` / `install`) рядом с путём — по одному пути
    нельзя понять, почему он такой, и разбор начинается с догадок.
    """
    env_catalog = os.environ.get(CATALOG_ENV, "").strip()
    env_config = os.environ.get(CONFIG_ENV, "").strip()
    env_settings = os.environ.get(SETTINGS_ENV, "").strip()
    return {
        "catalog_dir": str(catalog_dir()),
        "catalog_source": source_of(env_catalog or env_settings),
        "settings_path": str(settings_path()),
        "settings_source": source_of(env_settings or env_catalog),
        "config_dir": str(config_dir()),
        "config_source": source_of(env_config),
        "install_root": str(install_root()),
        "home_dir": str(home_dir()),
        "layout": "checkout" if is_checkout() else "install",
    }


def source_of(env_used: str) -> str:
    """Откуда путь: переменная, рабочая копия или домашний каталог.

    Три значения, а не два: до этапа 15 ответ говорил «install» про путь,
    который на установке оказывался каталогом интерпретатора, — и разбор
    начинался с догадок.
    """
    if env_used:
        return "env"
    return "checkout" if is_checkout() else "home"
