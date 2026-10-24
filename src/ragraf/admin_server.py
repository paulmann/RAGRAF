"""Интерфейс администратора RAGRAF: локальный веб-сервер без зависимостей.

Стек и почему такой
    Проект ставится на машину разработчика и не должен тянуть библиотеки.
    Поэтому сервер построен на стандартном `http.server` из Python — ноль
    зависимостей, ноль сборки, запуск одной командой. То же решение принято
    для самого RAGRAF (SQLite вместо сервера, tree-sitter вместо компилятора):
    инструмент обязан работать там, где есть только Python и код.

Безопасность
    * Слушается **только 127.0.0.1** — наружу интерфейс не доступен по замыслу.
      Это интерфейс администратора локальной машины, а не сетевая панель.
    * Токен доступа обязателен для изменяющих запросов: он создаётся при старте
      и печатается в вывод. Так случайная вкладка браузера не может изменить
      настройки, даже если сервер запущен.
    * Только POST для изменений: ни одна настройка не меняется от перехода
      по ссылке (защита от случайного клика и предзагрузки браузером).
    * Секретов в интерфейсе нет: RAGRAF не хранит паролей, реквизиты баз
      берутся из переменных окружения.

Что показывает
    * разработки: ревизии, контрольные суммы, состояние (актуально или нет);
    * размеры баз и их состав по таблицам;
    * статистику использования: обращения, факты со ссылками, оценка экономии;
    * качество индекса (блок quality): нормы векторов, размеры чанков, сигнатуры;
    * настройки: пороги авто-реиндексации и параметры индексации.

Запуск
    python tools/ragraf-admin.py --port 8777
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# Корень репозитория: файл лежит в src/ragraf/, значит корень — на два уровня выше.
# Раньше стояло parents[1], и каталог статики искался в src/admin/static —
# поэтому стили и скрипт отдавали 404 (найдено проверкой tools/check_admin.py).
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from ragraf import access as access_mod  # noqa: E402
from ragraf import client_setup  # noqa: E402
from ragraf import catalog as catalog_mod  # noqa: E402
from ragraf import settings as settings_mod  # noqa: E402
from ragraf import store, util  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

# ---------------------------------------------------------------- доступ
# RAGRAF-ACCESS-PATCH
#
# Три пути входа, намеренно:
#   * ПАРОЛЬ администратора (опциональный) — стабильный вход человека;
#   * ИМЕНОВАННЫЙ bearer-токен — вход агента, и в статистике видно, кому он выдан;
#   * токен СЕССИИ, печатаемый при запуске, — прежний путь. Он оставлен рабочим
#     намеренно: иначе уже запущенная панель перестала бы пускать владельца
#     до того, как он задаст пароль, и защита превратилась бы в потерю доступа.
#
# Файлы доступа лежат в `catalog/`: этот каталог исключён из корпуса
# (`config/ragraf.yaml`), поэтому секретоносные файлы не попадают ни в индекс,
# ни в поисковую выдачу агента.
PASSWORDS = access_mod.PasswordStore()
TOKENS = access_mod.TokenStore()
SESSION_COOKIE = "ragraf_admin_session"
SESSION_TTL_SECONDS = 8 * 3600
_SESSIONS: dict[str, dict[str, Any]] = {}

# Журнал доступа к панели и к MCP по HTTP: кто, чем, что и с каким результатом.
ACCESS_LOG: access_mod.AccessLog = access_mod.AccessLog(None)


def auth_state() -> dict[str, Any]:
    """Состояние доступа для интерфейса. БЕЗ секретов и без хэшей.

    Раньше эта точка отдавала токен администратора любому локальному клиенту.
    Наличие токена в ответе делало защиту формальностью: изменить настройки мог
    любой процесс на машине. Теперь отдаётся только состояние — задан ли пароль,
    сколько токенов заведено, какие права доступны.
    """
    state = PASSWORDS.state()
    tokens = TOKENS.state()
    state.update({
        "tokens_count": tokens["count"],
        "tokens_active": tokens["active"],
        "scopes": tokens["scopes"],
        "default_scopes": tokens["default_scopes"],
    })
    return state


def access_report(limit: int = 100) -> dict[str, Any]:
    """Журнал доступа и разрез по токенам: кто сколько и с каким результатом."""
    tokens = TOKENS.state()
    tokens["tokens"] = [t for t in tokens["tokens"]]
    return {
        "tokens": tokens,
        "log": ACCESS_LOG.summary(limit=limit),
        "session_hint": "токен сессии печатается при запуске панели",
    }


def sessions_open(remote: str) -> str:
    sid = secrets.token_urlsafe(24)
    _SESSIONS[sid] = {"created_at": time.time(), "remote": remote}
    sessions_prune()
    return sid


def sessions_prune() -> None:
    now = time.time()
    for sid in [s for s, d in _SESSIONS.items()
                if now - float(d.get("created_at") or 0) > SESSION_TTL_SECONDS]:
        _SESSIONS.pop(sid, None)


def sessions_ok(sid: str) -> bool:
    entry = _SESSIONS.get(sid or "")
    if not entry:
        return False
    if time.time() - float(entry.get("created_at") or 0) > SESSION_TTL_SECONDS:
        _SESSIONS.pop(sid, None)
        return False
    return True

# Токен сессии. Создаётся один раз и ЛЕЖИТ НА ДИСКЕ: он переживает перезапуск
# панели. Прежде он создавался при каждом запуске, и сохранённый в браузере вход
# переставал работать после любого перезапуска — владелец каждый раз вводил новый
# (жалоба 15.10.2026: «токен перестал приниматься»). Файл лежит рядом с индексом
# (каталог и так исключён из индексации), панель слушает только 127.0.0.1.
KEYFILE = REPO / ".ragraf" / "admin" / "session_key"


def panel_key(fresh: bool = False) -> str:
    """Значение доступа к панели: из файла, а при fresh=True — новое.

    Если файл недоступен для записи, панель всё равно обязана работать: тогда
    значение живёт до перезапуска, как прежде.
    """
    try:
        if not fresh and KEYFILE.exists():
            saved = KEYFILE.read_text(encoding="utf-8").strip()
            if saved:
                return saved
        value = secrets.token_urlsafe(24)
        KEYFILE.parent.mkdir(parents=True, exist_ok=True)
        KEYFILE.write_text(value, encoding="utf-8", newline="\n")
        return value
    except Exception:
        return secrets.token_urlsafe(24)


def rotate_panel_key() -> None:
    """Выпустить новое значение доступа (флаг --new-token)."""
    global ACCESS_TOKEN
    ACCESS_TOKEN = panel_key(fresh=True)


ACCESS_TOKEN = panel_key()

# Кэш тяжёлых сведений: сбор занимает время, а страница обновляется часто.
_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 5.0

# ---------------------------------------------------------------- «медленный слой»
# Замерено на этой машине: полная сверка контрольных сумм всех разработок
# занимает около 100 секунд, из них 81 секунда — одна разработка AutoClaw
# (52 931 файл). Пока это считалось внутри обработчика `/api/state`, ответ
# приходил через 136 секунд, браузер отменял запрос, и страница вечно
# показывала «Собираю сведения о разработках…» — панель выглядела сломанной.
#
# Поэтому дорогие поля собираются ОТДЕЛЬНО от ответа:
#   * ответ всегда берётся из снимка и отдаётся сразу (в том числе устаревший);
#   * снимок обновляет фоновый поток, и только он делает дорогую работу;
#   * клиент видит `refreshing: True` и сам перечитывает состояние, когда
#     сбор закончился. Пустая панель на это время не показывается.
#
# Тяжёлые поля — это `hash_matches`, `config_matches`, `files_now` и `size`
# (размеры баз и состав таблиц). Остальное в ответе — из реестра, оно дешёвое.
#: Срок жизни снимка тяжёлых сведений.
#: Замерено 15.10.2026: полный обход разработок занимает около 100 секунд, и почти
#: всё это время — одна разработка AutoClaw (54 тыс. файлов). При прежнем значении
#: 120 с панель собирала сведения почти непрерывно: страница видела «идёт сбор»,
#: опрашивала состояние каждые 3 секунды, и это выглядело как зависший интерфейс.
#: Теперь снимок живёт полчаса, а обновить его досрочно можно кнопкой обновления
#: (`/api/state?force=1`).
_SLOW_TTL = 1800.0
_SLOW_CACHE: dict[str, Any] = {}
_SLOW_LOCK = threading.Lock()
_SLOW_STATE: dict[str, Any] = {
    "running": False,
    "started_at": 0.0,
    "finished_at": 0.0,
    "error": "",
    "skipped": False,
    "entries_done": 0,
    "by_key": {},
}

# Снимок переживает перезапуск панели. Причина: первый сбор занимает около
# 100 секунд, и без файла каждая новая панель показывала пустые суммы и нулевые
# размеры баз до конца сбора. Файл лежит в `catalog/` — этот каталог исключён
# из корпуса (`config/ragraf.yaml`), поэтому служебные данные не попадают
# ни в индекс, ни в поиск агента.
_SLOW_SNAPSHOT = REPO / "catalog" / "admin_snapshot.json"

#: Запись снимков разрешена ТОЛЬКО процессу панели.
#: Служебные проверки (например `tools/check_admin_i18n.py`, раздел «контракт
#: страницы») запускают сбор в своём процессе с урезанным списком разработок, и их
#: снимок затирал полный: панель после этого показывала размеры баз одной
#: разработки из 27 (замерено 15.10.2026: в файле оказалась одна запись, и проверка
#: `tools/check_admin.py` падала на пустых размерах сразу после перезапуска).
SNAPSHOT_WRITE_ALLOWED = False


def _snapshot_load() -> None:
    """Прочитать последний снимок с диска. Сбой чтения не должен мешать старту."""
    try:
        raw = json.loads(_SLOW_SNAPSHOT.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(raw, dict):
        return
    values = raw.get("entries")
    stamp = raw.get("collected_at")
    if not isinstance(values, dict) or not isinstance(stamp, (int, float)):
        return
    with _SLOW_LOCK:
        for key, item in values.items():
            if isinstance(item, dict):
                _SLOW_CACHE[str(key)] = item
        _SLOW_CACHE["_at"] = float(stamp)


def _snapshot_save(entries: dict[str, Any], stamp: float) -> None:
    """Записать снимок. Запись атомарна: чтение не увидит половину файла."""
    payload = {"collected_at": stamp, "entries": entries}
    tmp = _SLOW_SNAPSHOT.with_suffix(".json.tmp")
    try:
        _SLOW_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(_SLOW_SNAPSHOT)
    except Exception:
        # Снимок — ускорение, а не источник правды: не записался — посчитается снова.
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


_snapshot_load()


def _cached(key: str, factory) -> Any:
    """Вернуть значение из кэша или собрать заново. Ошибки не кэшируются."""
    now = time.time()
    hit = _CACHE.get(key)
    if hit and (now - hit[0]) < _CACHE_TTL:
        return hit[1]
    value = factory()
    _CACHE[key] = (now, value)
    return value


def slow_cache() -> dict[str, Any]:
    """Снимок тяжёлых сведений по разработкам. Не блокирует ответ.

    Возвращается словарь `категория → собранные поля`, то есть ровно то, что
    подставляется в строку реестра. Время сборки лежит отдельным ключом
    `_at`: раньше значения хранились парой `(время, поля)`, и попытка слить
    их со строкой реестра падала с `TypeError: object is not iterable` —
    ровно тогда, когда фон заканчивал первый сбор, то есть панель переставала
    открываться через две минуты после запуска.
    """
    with _SLOW_LOCK:
        return {key: value for key, value in _SLOW_CACHE.items() if key != "_at"}


def slow_status() -> dict[str, Any]:
    """Состояние сбора тяжёлых сведений: страница показывает его словами."""
    with _SLOW_LOCK:
        running = bool(_SLOW_STATE["running"])
        return {
            "running": running,
            "started_at": _SLOW_STATE["started_at"],
            "finished_at": _SLOW_STATE["finished_at"],
            "error": _SLOW_STATE["error"],
            "skipped": _SLOW_STATE["skipped"],
            "entries_done": _SLOW_STATE["entries_done"],
        }


def slow_collect_async(entries: list[dict[str, Any]], *, force: bool = False) -> bool:
    """Запустить обновление снимка в фоне. Повторный вызов ничего не делает.

    Возвращает True, если сбор уже идёт или запущен сейчас. Сбор идёт в
    отдельном потоке намеренно: обработчик обязан ответить сразу.
    """
    with _SLOW_LOCK:
        if _SLOW_STATE["running"]:
            return True
        if _SLOW_CACHE and not force:
            age = time.time() - float(_SLOW_CACHE.get("_at") or 0.0)
            if age < _SLOW_TTL:
                return False
        _SLOW_STATE["running"] = True
        _SLOW_STATE["started_at"] = time.time()
        _SLOW_STATE["error"] = ""
        _SLOW_STATE["skipped"] = False

    def worker() -> None:
        by_key: dict[str, Any] = {}
        skipped = False
        if not entries:
            # Собирать нечего: без разработок дорогие поля не появятся, и
            # сообщать «идёт сбор» было бы неправдой.
            skipped = True
        for index, row in enumerate(entries):
            try:
                key = str(row.get("category") or "")
                by_key[key] = slow_collect_entry(row)
            except Exception as exc:
                # Одна сломанная разработка не отменяет остальные.
                by_key[str(row.get("category") or "")] = {
                    "hash_matches": None, "config_matches": None, "files_now": None,
                    "size": {}, "check_error": f"{type(exc).__name__}",
                }
            with _SLOW_LOCK:
                _SLOW_STATE["by_key"] = dict(by_key)
                _SLOW_STATE["entries_done"] = index + 1
        now = time.time()
        complete = len(by_key) == len(entries)
        with _SLOW_LOCK:
            # Снимок заменяется ЦЕЛИКОМ и только по завершённому сбору.
            # Замерено: сохранение после каждой разработки позволяло прерванному
            # сбору (перезапуск панели во время обхода) записать неполный снимок
            # поверх полного, и панель после перезапуска показывала одну строку
            # данных вместо двадцати четырёх.
            if complete:
                _SLOW_CACHE.clear()
                _SLOW_CACHE.update(by_key)
                _SLOW_CACHE["_at"] = now
            _SLOW_STATE["running"] = False
            _SLOW_STATE["finished_at"] = now
            _SLOW_STATE["error"] = ""
            _SLOW_STATE["skipped"] = skipped
            _SLOW_STATE["entries_done"] = len(by_key)
        if complete and not skipped and SNAPSHOT_WRITE_ALLOWED:
            _snapshot_save(by_key, now)

    threading.Thread(target=worker, name="ragraf-admin-slow", daemon=True).start()
    return True


#: Снимок тяжёлых разделов: категория → {usage, quality}. Заполняется фоном.
#: Срок жизни обязателен по той же причине, что и у обхода корпусов: без него
#: чтение баз всех разработок запускалось на КАЖДОМ обращении к состоянию (страница
#: обновляется каждые несколько секунд), и панель собирала сведения непрерывно.
#: Замерено 15.10.2026: 27 разработок, оба раздела пусты до первого сбора.
_DETAIL_TTL = 600.0
_DETAIL_CACHE: dict[str, Any] = {}
_DETAIL_LOCK = threading.Lock()
_DETAIL_STATE: dict[str, Any] = {"running": False}

#: Снимок разделов на диске: панель показывает их СРАЗУ после запуска, а не через
#: минуту. Прежде оба раздела были пусты, пока идёт сбор, и проверки проекта
#: падали на пустых разделах — трёхкратный ложный отказ (замерено 15.10.2026).
_DETAIL_SNAPSHOT = REPO / "catalog" / "admin_detail.json"


def _detail_snapshot_load() -> None:
    """Прочитать последний снимок разделов. Сбой чтения не мешает старту."""
    try:
        raw = json.loads(_DETAIL_SNAPSHOT.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(raw, dict):
        return
    values = raw.get("entries")
    stamp = raw.get("collected_at")
    if not isinstance(values, dict) or not isinstance(stamp, (int, float)):
        return
    with _DETAIL_LOCK:
        for key, item in values.items():
            if isinstance(item, dict):
                _DETAIL_CACHE[str(key)] = item
        _DETAIL_CACHE["_at"] = float(stamp)


def _detail_snapshot_save(entries: dict[str, Any], stamp: float) -> None:
    """Записать снимок разделов. Запись атомарна: чтение не увидит половину файла."""
    payload = {"collected_at": stamp, "entries": entries}
    tmp = _DETAIL_SNAPSHOT.with_suffix(".json.tmp")
    try:
        _DETAIL_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(_DETAIL_SNAPSHOT)
    except Exception:
        # Снимок — ускорение, а не источник правды: не записался — посчитается снова.
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


_detail_snapshot_load()


def detail_cache() -> dict[str, Any]:
    """Готовые разделы «Использование» и «Качество индекса». Не блокирует ответ."""
    with _DETAIL_LOCK:
        return {key: value for key, value in _DETAIL_CACHE.items() if key != "_at"}


def detail_status() -> dict[str, Any]:
    """Идёт ли сбор этих разделов: страница показывает это словами."""
    with _DETAIL_LOCK:
        return {"running": bool(_DETAIL_STATE["running"]),
                "done": int(_DETAIL_STATE.get("done") or 0)}


def _detail_collect(entries: list[dict[str, Any]], *, force: bool = False) -> bool:
    """Прочитать статистику и качество по разработкам в ОТДЕЛЬНОМ потоке.

    Почему отдельный поток, а не тот же, что обходит корпуса. Замерено
    15.10.2026: проверка контрольных сумм по 25 разработкам идёт около двух
    минут, и если читать базы после неё, разделы «Использование» и «Качество»
    остаются пустыми всё это время. Два сбора читают разное (файлы против баз),
    поэтому идут параллельно, а страница показывает ход каждого.

    Возвращает True, если сбор запущен или уже идёт.
    """
    with _DETAIL_LOCK:
        if _DETAIL_STATE["running"]:
            return True
        if _DETAIL_CACHE and not force:
            age = time.time() - float(_DETAIL_CACHE.get("_at") or 0.0)
            if age < _DETAIL_TTL:
                return False
        _DETAIL_STATE["running"] = True
        _DETAIL_STATE["done"] = 0

    def worker() -> None:
        collected: dict[str, Any] = {}
        for index, row in enumerate(entries):
            name = str(row.get("category") or "")
            cfg_path = Path(str(row.get("config") or ""))
            if not cfg_path.exists():
                continue
            try:
                cfg = load_config(cfg_path)
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
                collected[name] = {
                    "usage": {"error": reason, "error_hint": _failure_hint(exc)},
                    "quality": {"error": reason, "error_hint": _failure_hint(exc)},
                }
                continue
            # Различие «индекс не собран» и «нет права чтения» делает сам сбор
            # (`missing_index_reason` внутри collect_usage/collect_quality):
            # одна точка вместо повторов здесь и в операциях.
            collected[name] = {
                "usage": collect_usage(cfg),
                "quality": collect_quality(cfg),
            }
            with _DETAIL_LOCK:
                _DETAIL_STATE["done"] = index + 1
        finished = time.time()
        with _DETAIL_LOCK:
            if collected:
                _DETAIL_CACHE.clear()
                _DETAIL_CACHE.update(collected)
                _DETAIL_CACHE["_at"] = finished
            _DETAIL_STATE["running"] = False
        if collected and SNAPSHOT_WRITE_ALLOWED:
            # Снимок на диск: следующий запуск панели покажет разделы сразу.
            _detail_snapshot_save(collected, finished)

    threading.Thread(target=worker, name="ragraf-admin-detail", daemon=True).start()
    return True


def slow_collect_entry(row: dict[str, Any]) -> dict[str, Any]:
    """Дорогие сведения одной разработки: свежесть и размеры баз.

    Сбой одной разработки не отменяет остальные: причина записывается в
    поле `check_error`, а не выбрасывается наружу — панель должна показать
    по остальным разработкам то, что удалось собрать.
    """
    out: dict[str, Any] = {
        "hash_matches": None,
        "config_matches": None,
        "files_now": None,
        "size": {},
        "check_error": "",
    }
    entry = catalog_mod.Entry.from_dict(dict(row))
    if not entry.root or not entry.config:
        return out
    cfg_path = Path(entry.config)
    if not cfg_path.exists():
        out["check_error"] = "конфиг не найден"
        return out
    try:
        cfg = load_config(cfg_path)
        # Сверка контрольной суммы БЕЗ индексации: интерфейс только читает.
        from ragraf import watch as watch_mod

        now_hash, now_config, files_now, _ = watch_mod.current_hashes(cfg)
        out["hash_matches"] = bool(entry.corpus_hash and now_hash == entry.corpus_hash)
        out["files_now"] = files_now
        out["config_matches"] = bool(
            not entry.config_hash or now_config == entry.config_hash
        )
        out["size"] = storage_sizes(cfg)
    except Exception as exc:
        out["check_error"] = f"{type(exc).__name__}"
    return out


def collect_catalog(force: bool = False) -> dict[str, Any]:
    """Разработки с состоянием: ревизия, сумма, совпадение, размеры.

    Разделено на дешёвую и дорогую части. Дешёвая — то, что уже лежит в реестре
    (`catalog.summary()`); она отдаётся в ответе сразу. Дорогая — сверка
    контрольной суммы корпуса и обход баз, чтобы узнать размеры и состав таблиц;
    её считает фоновый поток (`slow_collect_async`), а ответ берёт последний
    готовый снимок. Причина разделения измерена: полный сбор занимал около
    100 секунд и подвешивал страницу.
    """
    catalog = catalog_mod.Catalog()
    summary = catalog.summary()
    entries = summary.get("entries") or []
    snapshot = slow_cache()
    for row in entries:
        row["hash_matches"] = None
        row["size"] = {}
        row.setdefault("check_error", "")
        key = str(row.get("category") or "")
        if key in snapshot:
            row.update(snapshot[key])
    # Сбор запускается на каждом обращении: если снимок устарел, он обновится,
    # а если ещё свеж — вызов ничего не делает. Ответ при этом не ждёт.
    # `force` — явное обновление с кнопки: иначе пришлось бы ждать срока снимка.
    slow_collect_async(entries, force=force)
    # Разделы «Использование» и «Качество индекса» собираются СРАЗУ, а не после
    # обхода корпусов: обход идёт около двух минут, и разделы не должны ждать
    # его окончания (замерено 15.10.2026).
    _detail_collect(entries, force=force)
    return summary


# ---------------------------------------------------------------------------
# Индексация из панели: переиндексация, запрет индексации, очистка индекса
# ---------------------------------------------------------------------------
#
# Панель до этого только читала. Кнопки «переиндексировать» и галочка «не
# индексировать» ставят её на путь записи, поэтому здесь явно названы границы:
#   * работа идёт в отдельном потоке: индексация длится минуты, а обработчик
#     обязан ответить сразу (тот же приём, что у фонового сбора);
#   * одновременно выполняется не больше одной индексации: две параллельные
#     записи в один индекс — это порча, а не ускорение;
#   * разрешение на индексацию спрашивается ПЕРЕД запуском, а не после:
#     иначе выключенная разработка всё равно пересобиралась бы;
#   * запрет индексации НЕ удаляет данные сам по себе — очистка отдельная
#     операция, и вызывающий говорит, чего хочет.
_JOB_LOCK = threading.Lock()
_JOB: dict[str, Any] = {
    "running": False,
    "kind": "",          # reindex | reindex_all | clear
    "category": "",
    "started_at": 0.0,
    "finished_at": 0.0,
    "total": 0,
    "done": 0,
    "current": "",
    "results": [],
    "error": "",
}


def job_status() -> dict[str, Any]:
    """Состояние фоновой работы панели. Страница показывает его словами."""
    with _JOB_LOCK:
        return {
            "running": bool(_JOB["running"]),
            "kind": _JOB["kind"],
            "category": _JOB["category"],
            "started_at": _JOB["started_at"],
            "finished_at": _JOB["finished_at"],
            "total": _JOB["total"],
            "done": _JOB["done"],
            "current": _JOB["current"],
            "error": _JOB["error"],
            "results": list(_JOB["results"]),
        }


def _job_begin(kind: str, category: str, total: int) -> bool:
    """Занять единственное место индексации. False — уже занято."""
    with _JOB_LOCK:
        if _JOB["running"]:
            return False
        _JOB.update({
            "running": True, "kind": kind, "category": category,
            "started_at": time.time(), "finished_at": 0.0,
            "total": total, "done": 0, "current": category,
            "results": [], "error": "",
        })
        return True


def _job_step(category: str, result: dict[str, Any]) -> None:
    with _JOB_LOCK:
        _JOB["done"] = int(_JOB["done"]) + 1
        _JOB["current"] = category
        _JOB["results"] = list(_JOB["results"]) + [result]


def _job_finish(error: str = "") -> None:
    with _JOB_LOCK:
        _JOB["running"] = False
        _JOB["finished_at"] = time.time()
        _JOB["current"] = ""
        _JOB["error"] = error


def config_for(category: str):
    """Конфиг разработки по имени из реестра.

    Возвращает (конфиг, запись, причина). Причина — текст, почему конфига нет:
    «config not found» без причины бесполезно, потому что за ним стоят разные
    положения дел (файла нет; каталог корпуса удалён; конфиг не разобран).
    Измерено на `repro5`: конфиг на месте, а каталог корпуса удалён — панель
    показывала «конфиг не найден», и владелец не знал, что чинить.
    """
    catalog = catalog_mod.Catalog()
    entry = catalog.get(category)
    if entry is None:
        return None, None, "development not found: {name}"
    if not entry.config:
        return None, entry, "the registry entry has no config path"
    path = Path(entry.config)
    if not path.exists():
        return None, entry, "config file is missing: {path}"
    try:
        return load_config(path), entry, ""
    except Exception as exc:
        return None, entry, _explain_failure(exc)


def reindex_project(category: str) -> dict[str, Any]:
    """Переиндексация одной разработки. Возвращает результат словами.

    Разрешение спрашивается ДО работы: выключенная галочкой разработка не
    должна пересобираться даже косвенно — иначе запрет ничего не значит.
    """
    catalog = catalog_mod.Catalog()
    cfg, entry, why = config_for(category)
    if cfg is None:
        return {"category": category, "ok": False, "error": why or "config not found (server)"}
    if entry is not None and not bool(entry.index_enabled):
        return {"category": category, "ok": False,
                "error": "indexing is disabled for this development"}
    try:
        from ragraf.indexer import Indexer

        idx = Indexer(cfg)
        try:
            report = idx.run(with_vectors=True)
        finally:
            idx.close()
        data = report.to_dict() if hasattr(report, "to_dict") else {}
        if getattr(report, "ok", False):
            # Реестр обязан получить результат прогона: путь, запускающий
            # индексатор и не записавший прогон, оставляет реестр с прошлым
            # состоянием (ловушка 1 файла AGENTS.md).
            try:
                from ragraf.watch import record_index_run

                record_index_run(cfg, report, trigger="admin:panel")
            except Exception as exc:
                data.setdefault("notes", []).append(
                    f"реестр не обновлён: {type(exc).__name__}: {exc}")
        return {
            "category": category,
            "ok": bool(getattr(report, "ok", False)),
            "rev_id": getattr(report, "rev_id", ""),
            "seconds": round(float(getattr(report, "seconds", 0.0) or 0.0), 1),
            "notes": list(data.get("notes") or [])[:6],
            "error": "" if getattr(report, "ok", False) else "indexing failed",
        }
    except Exception as exc:
        return {"category": category, "ok": False,
                "error": _explain_failure(exc)}


def clear_project_index(category: str) -> dict[str, Any]:
    """Удалить посчитанный индекс разработки. Реестр сохраняется.

    Успехом считается только фактическое исчезновение каталога: отказ удаления
    (например, запрет среды) возвращается как ошибка с текстом причины, иначе
    владелец видел бы «удалено» там, где индекс остался на месте.
    """
    try:
        report = catalog_mod.Catalog().clear_index(category)
    except KeyError:
        return {"category": category, "ok": False, "error": "not found"}
    except Exception as exc:
        return {"category": category, "ok": False, "error": _explain_failure(exc)}
    if not report.get("removed"):
        report["ok"] = False
        report.setdefault("error", "index directory was not removed")
    else:
        report["ok"] = True
    return report


def stale_categories(entries: list[dict[str, Any]] | None = None) -> list[str]:
    """Разработки, индекс которых отстал и которые разрешено индексировать.

    Свежесть берётся из того же снимка, что показывает панель: иначе кнопка
    «переиндексировать все» и таблица говорили бы о разных наборах.

    Готовый список строк можно передать снаружи. Это не оптимизация ради
    красоты: `collect_catalog()` сам запускает фоновый сбор и читает снимок,
    поэтому второй его вызов внутри одного ответа удваивал работу — замерено
    15.10.2026, `/api/state` отвечал 8 секунд вместо долей секунды и проверка
    справедливо назвала это возвратом прежнего дефекта.
    """
    rows = entries if entries is not None else (collect_catalog().get("entries") or [])
    out: list[str] = []
    catalog = catalog_mod.Catalog()
    for row in rows:
        category = str(row.get("category") or "")
        entry = catalog.get(category)
        if entry is not None and not bool(entry.index_enabled):
            continue
        if row.get("check_error"):
            continue
        if row.get("hash_matches") is False:
            out.append(category)
    return out


def start_reindex(categories: list[str]) -> dict[str, Any]:
    """Запустить переиндексацию в фоне. Ответ приходит сразу."""
    wanted = [c for c in categories if c]
    if not wanted:
        return {"ok": False, "error": "no developments need reindexing"}
    if not _job_begin("reindex_all" if len(wanted) > 1 else "reindex",
                      wanted[0], len(wanted)):
        return {"ok": False, "error": "indexing already running",
                "job": job_status()}

    def worker() -> None:
        try:
            for category in wanted:
                with _JOB_LOCK:
                    _JOB["current"] = category
                _job_step(category, reindex_project(category))
        except Exception as exc:  # защита потока: работа не должна пропасть молча
            _job_finish(_explain_failure(exc))
            return
        _job_finish()

    threading.Thread(target=worker, name="ragraf-admin-index", daemon=True).start()
    return {"ok": True, "job": job_status()}


def start_clear(category: str) -> dict[str, Any]:
    """Удалить индекс разработки в фоне: на большой базе это не мгновенно."""
    if not _job_begin("clear", category, 1):
        return {"ok": False, "error": "indexing already running",
                "job": job_status()}

    def worker() -> None:
        try:
            _job_step(category, clear_project_index(category))
        except Exception as exc:
            _job_finish(_explain_failure(exc))
            return
        _job_finish()

    threading.Thread(target=worker, name="ragraf-admin-clear", daemon=True).start()
    return {"ok": True, "job": job_status()}


def storage_sizes(cfg) -> dict[str, Any]:
    """Размеры баз разработки и состав по таблицам.

    Размер считается по файлам SQLite — это то, что занимает место на диске.
    Состав по таблицам нужен, чтобы видеть, что именно растёт: граф, лексика
    или журнал.
    """
    out: dict[str, Any] = {"files": [], "tables": [], "total_bytes": 0}
    targets = [
        ("граф кода", cfg.graph_db),
        ("лексика и векторы", cfg.lexvec_db),
        ("журнал и телеметрия", cfg.control_db),
    ]
    for title, path in targets:
        try:
            size = Path(path).stat().st_size
            # WAL-файл тоже занимает место и часто больше основного.
            wal = Path(str(path) + "-wal")
            wal_size = wal.stat().st_size if wal.exists() else 0
            out["files"].append({
                "title": title,
                "path": util.to_posix(path),
                "bytes": size + wal_size,
                "wal_bytes": wal_size,
            })
            out["total_bytes"] += size + wal_size
        except OSError:
            out["files"].append({"title": title, "path": util.to_posix(path),
                                 "bytes": 0, "missing": True})

    try:
        conn = store.connect(cfg.graph_db, readonly=True)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        for r in rows:
            name = str(r["name"])
            # FTS5 создаёт служебные таблицы: их считать не нужно, иначе
            # список превращается в шум.
            if name.endswith(("_data", "_idx", "_content", "_docsize", "_config")):
                continue
            try:
                count = conn.execute(f"SELECT COUNT(*) AS c FROM \"{name}\"").fetchone()["c"]
            except Exception:
                continue
            out["tables"].append({"name": name, "rows": int(count or 0)})
        conn.close()
    except Exception as exc:
        out["tables_error"] = type(exc).__name__
    return out


#: Подсказка для состояния «индекс не собран». Ключом служит сам текст: клиент
#: переводит его через карту серверного текста (i18n.SERVER_TEXT).
_INDEX_MISSING_HINT = ("Базы этой разработки ещё не собраны. Нажмите "
                       "«Переиндексировать», чтобы построить индекс: после "
                       "этого разделы заполнятся.")


def missing_index_reason(cfg) -> str:
    """Почему индекс этой разработки читать нечем. Пустая строка — читать можно.

    Отсутствие базы — это СОСТОЯНИЕ (индекс ещё не собран), а не сбой чтения.
    Обе причины дают один и тот же текст SQLite `unable to open database file`,
    поэтому различать их обязательно: иначе панель советует проверять права
    там, где нужно нажать «Переиндексировать» (замерено 15.10.2026 на
    разработке AutoClaw: есть только `control.sqlite`).
    """
    missing = [p for p in (cfg.graph_db, cfg.lexvec_db) if not Path(p).exists()]
    if not missing:
        return ""
    return ("индекс этой разработки не собран: нет файла "
            + ", ".join(str(p) for p in missing))


def collect_usage(cfg) -> dict[str, Any]:
    """Статистика использования: обращения, факты, оценка экономии.

    Возвращается в трёх разрезах, как просил владелец:
      * общая по всем агентам (поля верхнего уровня);
      * по каждому агенту (`by_agent`) и по семействам (`by_agent_kind`).

    Фильтры здесь не применяются: панель показывает общую картину,
    а выбор конкретного агента делает клиент по полю `by_agent`.

    Про причину сбоя. Раньше в ответ уходило только ИМЯ класса исключения, и
    администратор видел «Statistics unavailable: OperationalError» — строку,
    по которой нельзя ничего сделать. Замерено 14.10.2026: за этим стоял
    запрет записи в каталог `.ragraf` чужой разработки (SQLite открывает базу
    с потребностью в журнале, поэтому «только чтение» тоже требует права
    записи). Теперь сообщается текст ошибки и подсказка, что проверить.
    """
    absent = missing_index_reason(cfg)
    if absent:
        return {"error": absent, "error_kind": "IndexMissing",
                "error_hint": _INDEX_MISSING_HINT}
    try:
        from ragraf.facade import Facade

        # Панель представляется своим именем: иначе её собственные
        # обращения (чтение статистики и состояния) попадали бы в журнал
        # как `unknown` и искажали картину работы агентов.
        facade = Facade(cfg, tool="admin", telemetry=True, watch=False,
                        agent="RAGRAF-Admin", agent_kind="internal")
        try:
            return facade.usage_summary(since_session=False)
        finally:
            facade.close()
    except Exception as exc:
        return {"error": _explain_failure(exc),
                "error_kind": type(exc).__name__,
                "error_hint": _failure_hint(exc)}


def collect_sources() -> dict[str, Any]:
    """Состояние разрешённых корней и карты метаданных для страницы `/sources`.

    Читает только реестр и агрегаты карты. Содержимое файлов не трогает:
    страница обязана показывать ФАКТ доступа, а не данные.
    """
    from ragraf.sources import CATALOG_DIR, SourceRegistry

    registry = SourceRegistry()
    rows = [src.to_dict() for src in registry.sources()]
    payload: dict[str, Any] = {
        "count": len(rows),
        "sources": rows,
        "kinds": ["project", "folder", "share"],
        "storage": util.to_posix(CATALOG_DIR / "sources.json"),
        "map": None,
        "map_error": "",
        "limits": {
            "scan_seconds": 20,
            "scan_files": 50000,
            "note": "скан из панели ограничен по времени и числу файлов; полный — командой map scan",
        },
    }
    try:
        if (CATALOG_DIR / "global_map.sqlite").exists():
            from ragraf.global_map import GlobalMap

            gm = GlobalMap()
            try:
                gm.sync_sources(registry)
                payload["map"] = gm.status()
                # Счётчики живут в карте, а состав прав — в реестре. Таблица
                # обязана показывать и то и другое: иначе после удачного скана
                # колонки «файлов/секретов» остались бы пустыми и скан выглядел
                # бы несделанным.
                by_id = {int(row["id"]): row for row in payload["map"]["sources"]}
                for src in payload["sources"]:
                    row = by_id.get(int(src.get("id") or 0)) or {}
                    for key in ("files", "secrets", "last_scan_at", "scan_status",
                                "reason", "fingerprint"):
                        src[key] = row.get(key)
            finally:
                gm.close()
    except Exception as exc:  # noqa: BLE001 — страница не должна падать из-за карты
        payload["map_error"] = f"{type(exc).__name__}: {exc}"
    return payload


def collect_quality(cfg) -> dict[str, Any]:
    """Качество индекса: нормы векторов, чанки, сигнатуры.

    Причина сбоя сообщается текстом, а не именем класса: см. пояснение
    в `collect_usage` — по «OperationalError» администратор не может ничего
    сделать, а по тексту ошибки и подсказке может.
    """
    absent = missing_index_reason(cfg)
    if absent:
        return {"error": absent, "error_kind": "IndexMissing",
                "error_hint": _INDEX_MISSING_HINT}
    try:
        from ragraf.facade import Facade

        facade = Facade(cfg, tool="admin", telemetry=False, watch=False,
                        agent="RAGRAF-Admin", agent_kind="internal")
        try:
            status = facade.op_index_status()
            result = status.get("result") or {}
            # Состояние вычислителя показывается рядом с качеством: если векторы
            # считаются не там, где ожидал администратор, это видно сразу.
            compute: dict[str, Any] = {}
            try:
                compute = facade.embedder.compute_status().to_dict()
            except Exception as exc:
                compute = {"error": f"{type(exc).__name__}: {exc}"}
            return {
                "quality": result.get("quality") or {},
                "counts": status.get("counts") or {},
                "rev": (result.get("rev") or {}).get("rev_id"),
                "capability": status.get("capability"),
                "stages": result.get("stages") or [],
                "compute": compute,
            }
        finally:
            facade.close()
    except Exception as exc:
        return {"error": _explain_failure(exc),
                "error_kind": type(exc).__name__,
                "error_hint": _failure_hint(exc)}


def _explain_failure(exc: BaseException) -> str:
    """Короткое, но содержательное объяснение сбоя для интерфейса."""
    text = str(exc).strip()
    name = type(exc).__name__
    return f"{name}: {text}" if text else name


def _failure_hint(exc: BaseException) -> str:
    """Что проверить по этому сбою. Пусто, если подсказать нечего.

    Подсказки привязаны к измеренным причинам, а не к догадкам: «unable to
    open database file» на этом проекте означал запрет записи в каталог
    `.ragraf` чужой разработки, а `database is locked` — работу другого
    процесса с тем же индексом.

    Возвращается ПОЛНЫЙ текст, и он же служит ключом перевода: клиент зовёт
    `t()` с этим значением. Сокращённый ключ вида `hint_database_not_writable`
    выглядел аккуратнее, но ломал английский язык: для английского перевода
    нет, и на странице оставался сам ключ (замечено владельцем 15.10.2026).
    """
    text = str(exc).lower()
    if "unable to open database file" in text:
        return ("Каталог .ragraf этой разработки недоступен для записи. SQLite "
                "открывает базу с журналом, поэтому «только чтение» тоже требует "
                "права записи: проверьте права на каталог или запустите панель "
                "от имени, у которого они есть.")
    if "database is locked" in text:
        return ("С базой этой разработки работает другой процесс. Дождитесь "
                "окончания индексации или закройте лишние окна панели.")
    if isinstance(exc, PermissionError) or "permission denied" in text:
        return "Файл или каталог защищён от записи: проверьте права доступа."
    if "no such table" in text:
        return ("В базе нет ожидаемой таблицы: индекс собран другой версией "
                "RAGRAF. Переиндексируйте разработку.")
    return ""


def build_state(force: bool = False) -> dict[str, Any]:
    """Полное состояние для интерфейса. Одна функция — одна точка сбора.

    `force=True` — явное обновление с кнопки: тяжёлые сведения собираются заново,
    и кэш самого состояния не используется, иначе ответ показал бы прежние числа.
    """

    def factory() -> dict[str, Any]:
        catalog = collect_catalog(force=force)
        entries = catalog.get("entries") or []
        # Разделы «Использование» и «Качество индекса» берутся из ФОНОВОГО
        # снимка, а не читаются здесь. Причина измерена: чтение баз по 24
        # разработкам держало ответ 8 секунд, и страница снова ждала вместо
        # того, чтобы показать ход сбора (проверено 15.10.2026).
        # Пока снимка нет, раздел пуст, а `state.collecting` говорит, что сбор
        # идёт: пустой раздел не должен выглядеть как поломка.
        detail = detail_cache()
        # Состояние слоя баз собирается в общую точку: раздел «Базы данных» берёт
        # его оттуда же, откуда остальные разделы берут свои данные. Сбой сбора
        # не должен ломать страницу, поэтому ошибка превращается в пометку.
        try:
            db_info = db_state()
        except Exception as exc:
            db_info = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {
            "catalog": catalog,
            "detail": detail,
            "settings": settings_mod.state(
                {str(e.get("category")): e for e in entries}
            ).to_dict(),
            "db": db_info,
            "ragraf_root": util.to_posix(REPO),
            # Состояние фонового сбора. Страница обязана отличать «данных нет»
            # от «данные ещё собираются»: иначе пустая таблица выглядит как
            # поломка и администратор ждёт того, что уже не произойдёт.
            "collecting": slow_status(),
            # Сбор разделов «Использование» и «Качество индекса» идёт отдельно
            # от обхода корпусов: страница обязана показать оба хода.
            "detail_collecting": detail_status(),
        }

    if force:
        return factory()
    return _cached("state", factory)


class Handler(BaseHTTPRequestHandler):
    """Обработчик запросов интерфейса.

    Разделение простое: GET отдаёт данные, POST меняет настройки. Любое
    изменение требует токен — он приходит в заголовке или в теле.
    """

    server_version = "RAGRAF-admin/1.0"

    # ---------------- служебное ----------------

    def log_message(self, fmt: str, *args: Any) -> None:
        """Логи пишутся только в stderr: в консоли полезно видеть запросы."""
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send_json(self, payload: dict[str, Any], code: int = 200,
                   cookie: str | None = None) -> None:
        # RAGRAF-COOKIE-PATCH
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        if cookie:
            # Cookie сессии ставится ТОЛЬКО после успешного входа по паролю.
            # HttpOnly — чтобы её не читал скрипт страницы; SameSite=Strict —
            # чтобы запрос с чужого сайта не приехал с этой cookie.
            self.send_header("Set-Cookie", cookie)
        elif cookie == "":
            # Пустая строка означает явное снятие cookie (выход из панели).
            self.send_header(
                "Set-Cookie",
                f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict",
            )
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, code: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, content_type: str, code: int = 200) -> None:
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(min(length, 1_000_000))
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _token_ok(self, payload: dict[str, Any]) -> bool:
        """Проверка токена изменяющих запросов."""
        given = str(self.headers.get("X-RAGRAF-Token") or payload.get("token") or "")
        # Сравнение постоянного времени: обычное == раскрывает префикс по времени.
        return bool(given) and secrets.compare_digest(given, ACCESS_TOKEN)

    # ---------------- кто пришёл ----------------

    def _bearer(self) -> str:
        """Секрет из заголовка Authorization (схема Bearer)."""
        raw = str(self.headers.get("Authorization") or "").strip()
        if raw.lower().startswith("bearer "):
            return raw[7:].strip()
        return ""

    def _cookie(self) -> str:
        raw = str(self.headers.get("Cookie") or "")
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name == SESSION_COOKIE:
                return value.strip()
        return ""

    def principal(self, payload: dict[str, Any]) -> tuple[access_mod.Principal | None, str]:
        """Определить, кто пришёл: токен, парольная сессия или токен сессии.

        Порядок важен: сначала именованный токен (у него есть права и учёт),
        затем сессия браузера, затем токен сессии. Отказ во всех случаях
        выглядит ОДИНАКОВО для клиента: иначе перебор становится осмысленным.
        Причина отказа уходит в журнал, а не в ответ.
        """
        bearer_value = self._bearer()
        if bearer_value:
            record, allowed, why = TOKENS.authorize(bearer_value)
            if not allowed:
                # Запись может быть известна (отозванный или просроченный токен):
                # её метка уходит в журнал, чтобы владелец видел, ЧЕЙ токен
                # получил отказ, а не безымянную попытку.
                if record is not None:
                    self._denied_principal = access_mod.Principal(
                        kind="token", label=record.label,
                        scopes=list(record.scopes), token_id=record.token_id)
                return None, why
            return access_mod.Principal(kind="token", label=record.label,
                                        scopes=list(record.scopes),
                                        token_id=record.token_id), ""

        sid = self._cookie()
        if sid and sessions_ok(sid):
            return access_mod.Principal(kind="password", label="администратор",
                                        scopes=list(access_mod.SCOPES)), ""

        given = str(self.headers.get("X-RAGRAF-Token") or payload.get("token") or "")
        if given and secrets.compare_digest(given, ACCESS_TOKEN):
            return access_mod.Principal(kind="session", label="токен сессии",
                                        scopes=list(access_mod.SCOPES)), ""
        return None, "ни токена, ни парольной сессии"

    def audit(self, *, surface: str, operation: str, result: str,
              principal: access_mod.Principal | None, reason: str = "",
              started: float | None = None, detail: str = "") -> None:
        """Записать попытку в журнал доступа.

        Пишется и удача, и отказ, и ошибка: вопрос владельца звучит как
        «кто и как использовал», а на него нельзя ответить одним списком удач.
        """
        duration = ((time.time() - started) * 1000.0) if started else 0.0
        ACCESS_LOG.record(surface=surface, operation=operation, result=result,
                          principal=principal, remote=self.address_string(),
                          reason=reason, duration_ms=duration, detail=detail)

    # ---------------- GET ----------------

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/":
            self._send_html(PAGE_HTML)
            return
        if path == "/api/i18n":
            # Словарь переводов отдаётся целиком и один раз: клиент выбирает
            # язык у себя, поэтому смена языка не требует нового запроса.
            # Источник правды — `ragraf.i18n`, второго списка строк нет.
            from ragraf import i18n as i18n_mod

            self._send_json({
                "ok": True,
                "languages": list(i18n_mod.LANGUAGES),
                "default": i18n_mod.DEFAULT_LANGUAGE,
                "titles": dict(i18n_mod.LANGUAGE_TITLES),
                "catalogue": i18n_mod.all_catalogues(),
            })
            return
        if path == "/api/state":
            # `?force=1` — кнопка обновления: тяжёлые сведения собираются заново,
            # не дожидаясь срока снимка (полный обход идёт около 100 секунд).
            force = "force=1" in str(parsed.query or "")
            state = build_state(force=force)
            self._send_json({"ok": True, "state": state,
                             "refreshing": bool(state.get("collecting", {}).get("running"))})
            return
        if path == "/api/state/progress":
            # Отдельная дешёвая точка: страница спрашивает её, пока идёт сбор.
            # Ответ не содержит состояния целиком, поэтому опрос ничего не стоит.
            self._send_json({"ok": True, "progress": slow_status()})
            return
        if path == "/api/token":
            # Секрет здесь НЕ отдаётся: точка лишь сообщает состояние доступа.
            # Прежняя редакция возвращала токен администратора, и защита была
            # формальностью — его получал любой локальный клиент.
            self._send_json({"ok": True, "auth": auth_state(),
                             "port": self.server.server_port})
            return
        if path == "/api/access":
            self._send_json({"ok": True, "access": access_report()})
            return
        if path == "/api/settings":
            catalog = catalog_mod.Catalog()
            entries = {str(e.get("category")): e for e in catalog.summary()["entries"]}
            self._send_json({"ok": True, "settings": settings_mod.state(entries).to_dict()})
            return
        if path == "/api/mode":
            self._send_json(mode_payload())
            return
        if path == "/api/probe":
            self._send_json({"ok": True, "probe": panel_probe(panel_mode())})
            return
        if path == "/api/db":
            self._send_json(db_state())
            return
        if path == "/api/sources":
            # Разрешённые корни и карта метаданных. Чтение без токена: оно
            # ничего не меняет и не отдаёт содержимое файлов.
            self._send_json({"ok": True, **collect_sources()})
            return
        if path == "/sources":
            page = REPO / "admin" / "sources.html"
            if page.exists():
                self._send_html(page.read_text(encoding="utf-8"))
            else:
                self._send_json(
                    {"ok": False, "error": "страница sources.html не найдена"}, 404
                )
            return
        if path == "/value":
            page = REPO / "admin" / "value.html"
            if page.exists():
                self._send_html(page.read_text(encoding="utf-8"))
            else:
                self._send_json(
                    {"ok": False, "error": "страница value.html не найдена"}, 404
                )
            return
        if path == "/knowledge":
            page = REPO / "admin" / "knowledge.html"
            if page.exists():
                self._send_html(page.read_text(encoding="utf-8"))
            else:
                self._send_json(
                    {"ok": False, "error": "страница knowledge.html не найдена"}, 404
                )
            return
        if path.startswith("/knowledge/"):
            # Страница читает исходные документы как Markdown: по ним работают
            # кнопки «Скопировать» и «Скачать», и они же остаются единственным
            # источником текста. Список закрыт: произвольный путь не отдаётся.
            name = path[len("/knowledge/"):]
            documents = {
                "prompt.md": "text/markdown; charset=utf-8",
                "research.md": "text/markdown; charset=utf-8",
                "short-prompt.md": "text/markdown; charset=utf-8",
                "readme.md": "text/markdown; charset=utf-8",
            }
            if name in documents:
                document = REPO / "admin" / "knowledge" / name
                if document.exists():
                    self._send_text(
                        document.read_text(encoding="utf-8"),
                        documents[name],
                    )
                    return
            self._send_json({"ok": False, "error": "документ не найден"}, 404)
            return
        if path == "/api/clients":
            payload = client_setup.state()
            # Следы обращений: по ним панель отличает «настроено» от «дошло».
            # Файл пишут другие процессы (HTTP-сервис и stdio-сервер клиента),
            # поэтому сбой чтения превращается в пометку, а не в отказ страницы.
            try:
                from ragraf import contact as contact_mod

                payload["contact"] = contact_mod.state()
            except Exception as exc:
                payload["contact"] = {"clients": [], "error": type(exc).__name__}
            payload["service_running"] = port_is_live(client_setup.HTTP_PORT)
            self._send_json({"ok": True, "clients": payload})
            return
        if path == "/api/indexing":
            # Состояние фоновой индексации. Точка дешёвая и не требует входа:
            # она ничего не меняет, а страница обязана показывать ход работы.
            # Свежесть берётся из УЖЕ СОБРАННОГО снимка: запускать сбор внутри
            # этого ответа нельзя, иначе дешёвая точка становится тяжёлой.
            self._send_json({"ok": True, "job": job_status(),
                             "stale": stale_categories(
                                 (slow_cache() or {}).get("entries"))})
            return
        if path == "/api/health":
            self._send_json({"ok": True, "version": "1.0", "root": util.to_posix(REPO)})
            return
        self._send_json({"ok": False, "error": "не найдено"}, 404)

    # ---------------- POST ----------------

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        payload = self._read_body()

        if path == "/api/auth/login":
            password = str(payload.get("password") or "")
            if not PASSWORDS.is_set():
                self.audit(surface="panel", operation=path, result="denied",
                           principal=None, reason="пароль не задан")
                self._send_json({"ok": False, "error": "пароль не задан: "
                                                       "он не требуется для входа"}, 409)
                return
            if not PASSWORDS.verify(password):
                self.audit(surface="panel", operation=path, result="denied",
                           principal=None, reason="неверный пароль")
                self._send_json({"ok": False, "error": "неверный пароль"}, 403)
                return
            sid = sessions_open(self.address_string())
            me = access_mod.Principal(kind="password", label="администратор",
                                      scopes=list(access_mod.SCOPES))
            self.audit(surface="panel", operation=path, result="ok", principal=me)
            self._send_json({"ok": True, "logged_in": True,
                             "expires_hours": SESSION_TTL_SECONDS // 3600},
                            cookie=f"{SESSION_COOKIE}={sid}; Path=/; HttpOnly; "
                                   f"SameSite=Strict; Max-Age={SESSION_TTL_SECONDS}")
            return

        if path == "/api/auth/logout":
            sid = self._cookie()
            _SESSIONS.pop(sid, None)
            self.audit(surface="panel", operation=path, result="ok",
                       principal=None)
            self._send_json({"ok": True, "logged_in": False,
                             "clear_cookie": True}, cookie="")
            return

        if path == "/api/auth/token":
            # ПРОВЕРКА ПРЕДЪЯВЛЕННЫХ ПРАВ. Точка существует потому, что без неё
            # «вход» был фиктивным: страница читается и БЕЗ токена, поэтому
            # успешным выглядел любой ввод, а первая же запись получала 403 и
            # панель снова показывала экран входа.
            # Ответ не содержит секретов: только факт, вид входа и метка.
            principal, why = self.principal(payload)
            if principal is None:
                self.audit(surface="panel", operation=path, result="denied",
                           principal=getattr(self, "_denied_principal", None),
                           reason=why)
                self._send_json({"ok": False, "authenticated": False,
                                 "error": "token rejected"}, 403)
                return
            self.audit(surface="panel", operation=path, result="ok",
                       principal=principal)
            self._send_json({"ok": True, "authenticated": True,
                             "kind": principal.kind, "label": principal.label})
            return

        # ТОЧКИ ВХОДА ИДУТ ВЫШЕ ЭТОЙ ПРОВЕРКИ намеренно: иначе вход требовал бы
        # входа. Проверено живым прогоном: `/api/auth/login` отвечал 403 «нужен
        # вход», и заданный пароль нельзя было применить.
        principal, deny_reason = self.principal(payload)
        if principal is None:
            # Ответ ОДИНАКОВ для всех причин отказа: причина уходит в журнал.
            # Иначе перебор токена становится осмысленным.
            # Если предъявлен ИЗВЕСТНЫЙ токен (отозван или просрочен), в журнал
            # идёт его метка: владелец должен видеть, чей доступ перестал
            # работать, а не безымянную попытку.
            self.audit(surface="panel", operation=path or "unknown",
                       result="denied",
                       principal=getattr(self, "_denied_principal", None),
                       reason=deny_reason)
            self._send_json(
                {"ok": False, "error": "нужен вход: предъявите bearer-токен, "
                                       "войдите по паролю или используйте "
                                       "токен сессии с запуска панели"},
                403,
            )
            return
        started = time.time()

        if path == "/api/sources":
            from ragraf.sources import CATALOG_DIR, SourceRegistry, SourcesError

            action = str(payload.get("action") or "").strip()
            registry = SourceRegistry()
            result: dict[str, Any] = {}
            try:
                if action == "add":
                    src = registry.add(
                        str(payload.get("root") or ""),
                        kind=(str(payload.get("kind")) if payload.get("kind") else None),
                        priority=int(payload.get("priority") or 0),
                        note=str(payload.get("note") or ""),
                        network=bool(payload.get("network")),
                    )
                    result = src.to_dict()
                elif action == "remove":
                    src = registry.remove(payload.get("id") or payload.get("root"))
                    result = src.to_dict()
                elif action == "toggle":
                    src = registry.set_enabled(
                        payload.get("id") or payload.get("root"),
                        bool(payload.get("enabled")),
                    )
                    result = src.to_dict()
                elif action == "scan":
                    from ragraf.global_map import GlobalMap

                    gm = GlobalMap()
                    try:
                        gm.sync_sources(registry)
                        ident = payload.get("id")
                        if ident not in (None, "", 0, "0"):
                            target = registry.get(ident)
                            targets = [target] if target is not None else []
                        else:
                            targets = registry.sources(enabled_only=True)
                        scans = [
                            gm.scan(src, limits={"max_seconds": 20.0,
                                                 "max_files": 50000}).to_dict()
                            for src in targets
                        ]
                    finally:
                        gm.close()
                    result = {"scans": scans}
                else:
                    self._send_json(
                        {"ok": False, "error": f"неизвестное действие: {action}"}, 400
                    )
                    return
                # Реестр — источник истины по доступу: после любого изменения
                # карта синхронизируется, чтобы снятый корень не оставался в ней.
                try:
                    if (CATALOG_DIR / "global_map.sqlite").exists():
                        from ragraf.global_map import GlobalMap

                        gm = GlobalMap()
                        try:
                            gm.sync_sources(registry)
                        finally:
                            gm.close()
                except Exception:  # noqa: BLE001 — изменение доступа важнее чистки карты
                    pass
            except SourcesError as exc:
                self.audit(surface="panel", operation=path, result="error",
                           principal=principal, reason=str(exc))
                self._send_json({"ok": False, "error": str(exc)}, 400)
                return
            except Exception as exc:  # noqa: BLE001
                self.audit(surface="panel", operation=path, result="error",
                           principal=principal, reason=f"{type(exc).__name__}: {exc}")
                self._send_json(
                    {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500
                )
                return
            self.audit(surface="panel", operation=path, result="ok",
                       principal=principal, started=started,
                       detail=f"sources: {action}")
            self._send_json({"ok": True, "result": result, **collect_sources()})
            return

        if path == "/api/settings/global":
            values = payload.get("values") or {}
            try:
                saved = settings_mod.SettingsStore().save(values)
            except ValueError as exc:
                self.audit(surface="panel", operation=path, result="error",
                           principal=principal, reason=str(exc))
                self._send_json({"ok": False, "error": str(exc)}, 400)
                return
            _CACHE.clear()
            # Учёт обращения к данным — с меткой токена. Без этой записи журнал
            # видел бы только отказы и смену доступа, и на вопрос «какой агент
            # сколько работал» ответить было бы нельзя: успешные вызовы
            # оставались бы неучтёнными.
            self.audit(surface="panel", operation=path, result="ok",
                       principal=principal, started=started,
                       detail="глобальные настройки")
            self._send_json({"ok": True, "settings": saved,
                             "note": "настройки применятся при следующей проверке "
                                     "свежести; текущая ревизия не меняется"})
            return

        if path == "/api/settings/project":
            name = str(payload.get("category") or "")
            values = payload.get("values") or {}
            catalog = catalog_mod.Catalog()
            entry = catalog.get(name)
            if entry is None:
                self._send_json({"ok": False, "error": f"разработка не найдена: {name}"}, 404)
                return
            cfg_path = Path(entry.config)
            if not cfg_path.exists():
                self._send_json({"ok": False, "error": "конфиг разработки не найден"}, 404)
                return
            try:
                written = settings_mod.write_project_settings(cfg_path, values)
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
                return
            except OSError as exc:
                self.audit(surface="panel", operation=path, result="error",
                           principal=principal, reason=str(exc))
                self._send_json({"ok": False, "error": f"не удалось записать: {exc}"}, 500)
                return
            _CACHE.clear()
            self.audit(surface="panel", operation=path, result="ok",
                       principal=principal, started=started,
                       detail=f"разработка {name}")
            self._send_json({"ok": True, "written": written, "config": util.to_posix(cfg_path)})
            return

        if path == "/api/auth/password":
            password = str(payload.get("password") or "")
            confirm = str(payload.get("confirm") or password)
            current = str(payload.get("current") or "")
            if password != confirm:
                self._send_json({"ok": False, "error": "пароль и подтверждение "
                                                       "не совпадают"}, 400)
                return
            # Смена уже существующего пароля требует текущего: иначе угон
            # открытой сессии немедленно сменил бы владельца доступа.
            if PASSWORDS.is_set() and not PASSWORDS.verify(current):
                self.audit(surface="panel", operation=path, result="denied",
                           principal=principal, reason="текущий пароль неверен")
                self._send_json({"ok": False, "error": "текущий пароль неверен"}, 403)
                return
            try:
                PASSWORDS.set(password)
            except access_mod.AccessError as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
                return
            self.audit(surface="panel", operation=path, result="ok",
                       principal=principal, detail="пароль задан")
            self._send_json({"ok": True, "auth": auth_state()})
            return

        if path == "/api/auth/password/clear":
            current = str(payload.get("current") or "")
            try:
                PASSWORDS.clear(current)
            except access_mod.AccessError as exc:
                self.audit(surface="panel", operation=path, result="denied",
                           principal=principal, reason=str(exc))
                self._send_json({"ok": False, "error": str(exc)}, 403)
                return
            _SESSIONS.clear()
            self.audit(surface="panel", operation=path, result="ok",
                       principal=principal, detail="пароль снят")
            self._send_json({"ok": True, "auth": auth_state()})
            return

        if path == "/api/tokens/create":
            try:
                record, secret = TOKENS.create(
                    label=str(payload.get("label") or ""),
                    scopes=list(payload.get("scopes") or access_mod.DEFAULT_SCOPES),
                    owner=str(payload.get("owner") or ""),
                    note=str(payload.get("note") or ""),
                    expires_at=payload.get("expires_at") or None,
                )
            except access_mod.AccessError as exc:
                self.audit(surface="panel", operation=path, result="denied",
                           principal=principal, reason=str(exc))
                self._send_json({"ok": False, "error": str(exc)}, 400)
                return
            self.audit(surface="panel", operation=path, result="ok",
                       principal=principal,
                       detail=f"создан токен {record.token_id} для «{record.label}»")
            # Секрет показывается ОДИН раз: в файле лежит только его SHA-256.
            self._send_json({
                "ok": True,
                "token": record.public(),
                "issued_once": {"value": secret, "shown_once": True},
                "warning": "скопируйте значение сейчас: оно не сохраняется и "
                           "повторно показан не будет",
            })
            return

        if path in ("/api/tokens/revoke", "/api/tokens/restore", "/api/tokens/delete"):
            token_id = str(payload.get("token_id") or "")
            try:
                if path.endswith("revoke"):
                    record = TOKENS.revoke(token_id, reason=str(payload.get("reason") or ""))
                    detail = f"отозван {record.token_id}"
                elif path.endswith("restore"):
                    record = TOKENS.restore(token_id)
                    detail = f"восстановлен {record.token_id}"
                else:
                    TOKENS.delete(token_id)
                    detail = f"удалён {token_id}"
            except access_mod.AccessError as exc:
                self.audit(surface="panel", operation=path, result="denied",
                           principal=principal, reason=str(exc))
                self._send_json({"ok": False, "error": str(exc)}, 404)
                return
            self.audit(surface="panel", operation=path, result="ok",
                       principal=principal, detail=detail)
            self._send_json({"ok": True, "auth": auth_state()})
            return

        if path == "/api/index/reindex":
            # Переиндексация одной разработки (category) или всех отставших
            # (all=true). Работа уходит в фон: индексация длится минуты.
            if not principal:
                self._send_json({"ok": False, "error": "access required"}, 403)
                return
            if bool(payload.get("all")):
                wanted = stale_categories()
            else:
                wanted = [str(payload.get("category") or "")]
            result = start_reindex(wanted)
            self.audit(surface="panel", operation=path,
                       result="ok" if result.get("ok") else "denied",
                       principal=principal,
                       detail=("все отставшие: " + ", ".join(wanted[:6]))
                       if len(wanted) > 1 else (wanted[0] if wanted else "—"))
            self._send_json(result, 200 if result.get("ok") else 409)
            return
        if path == "/api/index/enabled":
            # Разрешить или запретить индексацию разработки. Запрет данных НЕ
            # удаляет: очистка — отдельное действие, которое вызывающий просит
            # явно. Иначе выключение галочки молча стирало бы индекс.
            if not principal:
                self._send_json({"ok": False, "error": "access required"}, 403)
                return
            category = str(payload.get("category") or "")
            enabled = bool(payload.get("enabled"))
            try:
                entry = catalog_mod.Catalog().set_index_enabled(category, enabled)
            except KeyError:
                self.audit(surface="panel", operation=path, result="denied",
                           principal=principal, detail=f"нет разработки {category}")
                # Ключ и подстановки отдаются раздельно: язык выбирает клиент,
                # а сервер не должен подставлять русский текст в ответ.
                self._send_json({"ok": False,
                                 "error": "development not found: {name}",
                                 "error_key": "development not found: {name}",
                                 "error_args": {"name": category}}, 404)
                return
            result: dict[str, Any] = {
                "ok": True, "category": category,
                "index_enabled": bool(entry.index_enabled),
            }
            if not enabled and bool(payload.get("clear")):
                # Очистка идёт в фоне: у крупной разработки это гигабайты.
                result["clear"] = start_clear(category)
            # Снимок сбрасывается: состояние разработки изменилось, и старое
            # «требуется реиндексация» показывалось бы как действующее.
            _CACHE.clear()
            self.audit(surface="panel", operation=path, result="ok",
                       principal=principal,
                       detail=f"{category}: индексация "
                              f"{'разрешена' if enabled else 'запрещена'}")
            self._send_json(result)
            return
        if path == "/api/index/clear":
            if not principal:
                self._send_json({"ok": False, "error": "access required"}, 403)
                return
            category = str(payload.get("category") or "")
            result = start_clear(category)
            _CACHE.clear()
            self.audit(surface="panel", operation=path,
                       result="ok" if result.get("ok") else "denied",
                       principal=principal, detail=f"очистка индекса {category}")
            self._send_json(result, 200 if result.get("ok") else 409)
            return

        if path == "/api/db/probe":
            # Проба НИЧЕГО НЕ МЕНЯЕТ, но вход всё равно требуется: общий шлюз
            # панели стоит выше и не делает исключений — иначе появилась бы точка,
            # открытая любому локальному клиенту. Значение из формы используется
            # только для этой пробы и не сохраняется.
            result = db_probe(payload)
            self.audit(surface="panel", operation=path,
                       result="ok" if result.get("ok") else "failed",
                       principal=principal,
                       detail=f"диалект {payload.get('dialect') or 'из настроек'}")
            self._send_json(result)
            return
        if path == "/api/db/save":
            # Проверка повторна намеренно: общий шлюз стоит выше, но если ветку
            # перенесут, изменяющее действие не должно остаться без защиты.
            if not principal:
                self._send_json({"ok": False, "error": "требуется доступ"}, 403)
                return
            result = db_save(payload)
            self.audit(surface="panel", operation=path,
                       result="ok" if result.get("ok") else "denied",
                       principal=principal,
                       detail="настройки слоя баз")
            self._send_json(result, 200 if result.get("ok") else 400)
            return
        if path == "/api/db/collect":
            if not principal:
                self._send_json({"ok": False, "error": "требуется доступ"}, 403)
                return
            # Сбор читает чужую базу и пишет в индекс: это изменяющее действие и
            # оно обязано быть в журнале доступа.
            result = db_collect_now(payload)
            self.audit(surface="panel", operation=path,
                       result="ok" if result.get("ok") else "failed",
                       principal=principal,
                       detail=("примеры собирались" if payload.get("samples")
                               else "только структура"))
            self._send_json(result, 200 if result.get("ok") else 400)
            return
        if path == "/api/mode":
            wanted = str(payload.get("mode") or "")
            if wanted not in panel_modes():
                self._send_json({"ok": False, "error":
                                 f"недопустимый режим: {wanted}; допустимы "
                                 f"{', '.join(panel_modes())}"}, 400)
                return
            try:
                settings_mod.SettingsStore().save({"ragraf_mode": wanted})
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
                return
            _CACHE.clear()
            self.audit(surface="panel", operation=path, result="ok",
                       principal=principal, detail=f"режим: {wanted}")
            # Вердикт выдаётся сразу: иначе режим остаётся надписью, не связанной
            # с фактическим состоянием каналов.
            self._send_json(mode_payload(panel_probe(panel_mode())))
            return

        if path == "/api/clients/harness":
            transport = str(payload.get("transport") or "stdio")
            result = client_setup.harness_apply(transport)
            self.audit(surface="panel", operation=path,
                       result="ok" if result.get("ok") else "error",
                       principal=principal,
                       detail=f"транспорт {transport}; "
                              f"{'применено' if result.get('ok') else result.get('error', '')}")
            self._send_json({"ok": bool(result.get("ok")), "result": result,
                             "clients": client_setup.state()},
                            200 if result.get("ok") else 400)
            return

        if path == "/api/clients/access":
            label = str(payload.get("label") or "").strip()
            try:
                record, value = TOKENS.create(
                    label=label,
                    scopes=list(payload.get("scopes") or access_mod.DEFAULT_SCOPES),
                    note=str(payload.get("note") or f"выдан для {label}"),
                )
            except access_mod.AccessError as exc:
                self.audit(surface="panel", operation=path, result="denied",
                           principal=principal, reason=str(exc))
                self._send_json({"ok": False, "error": str(exc)}, 400)
                return
            self.audit(surface="panel", operation=path, result="ok",
                       principal=principal,
                       detail=f"выдан доступ {record.token_id} для «{record.label}»")
            self._send_json({"ok": True, "token": record.public(),
                             "value_once": value, "shown_once": True,
                             "warning": "скопируйте значение сейчас: оно не "
                                        "сохраняется и повторно показано не будет"})
            return

        if path == "/api/cache/clear":
            _CACHE.clear()
            self._send_json({"ok": True, "note": "кэш интерфейса очищен"})
            return

        self._send_json({"ok": False, "error": "не найдено"}, 404)


def panel_modes() -> list[str]:
    """Список допустимых режимов работы RAGRAF."""
    return list(settings_mod.ENUM_KEYS.get("ragraf_mode", ("cli",)))


def _db_admin_config():
    """Конфигурация корпуса, которым управляет панель.

    Берётся тот же путь, что у проб каналов (`probes.ADMIN_CONFIG`), чтобы панель
    не имела собственного представления о том, «какой корпус главный»: два
    источника этого знания разошлись бы.
    """
    from . import probes as probes_mod
    from .config import load as load_config

    return load_config(probes_mod.ADMIN_CONFIG)


def _db_active_rev() -> str:
    """Активная ревизия корпуса: по НОМЕРУ, а не по времени.

    Порядок задаёт нумерация (`ORDER BY rev_id DESC`): сортировка по времени
    возвращала не ту ревизию — этот дефект в проекте уже был.
    """
    try:
        cfg = _db_admin_config()
        from . import store as store_mod

        conn = store_mod.connect(cfg.graph_db, readonly=True)
        try:
            row = conn.execute(
                "SELECT rev_id FROM rev ORDER BY rev_id DESC LIMIT 1").fetchone()
        finally:
            conn.close()
        return str(row["rev_id"]) if row else ""
    except Exception:
        return ""


def _db_index_summary(db_ops) -> dict:
    """Сводка слоя баз по активной ревизии: что уже собрано в индексе."""
    cfg = _db_admin_config()
    from . import store as store_mod

    conn = store_mod.connect(cfg.graph_db, readonly=True)
    try:
        rev_id = _db_active_rev()
        return db_ops.summary(conn, rev_id) if rev_id else {"collected": False}
    finally:
        conn.close()



def db_state() -> dict:
    """Состояние слоя баз для интерфейса: без значения доступа."""
    from . import db_access as access_mod
    from . import db_ops

    try:
        state = access_mod.state()
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # Сводка по индексу: сколько собрано, если собрано.
    try:
        summary = _db_index_summary(db_ops)
    except Exception as exc:
        summary = {"collected": False, "note": f"{type(exc).__name__}: {exc}"}
    state["summary"] = summary
    state["ok"] = True
    return state


def db_probe(payload: dict | None = None) -> dict:
    """Реальная проба подключения: открывает соединение и получает ответ.

    Изменения настроек здесь НЕ происходит: проба проверяет то, что можно
    проверить прямо сейчас. Сохранение — отдельное действие, и смешивать их
    нельзя: иначе «проверить» незаметно меняло бы рабочую настройку.
    """
    from . import db_access as access_mod
    from . import db_collect as collect_mod

    payload = payload or {}
    access = access_mod.DbAccessStore().load()
    for key in ("dialect", "host", "port", "user"):
        value = payload.get(key)
        if value not in (None, ""):
            if key == "port":
                try:
                    access.port = int(value)
                except (TypeError, ValueError):
                    return {"ok": False, "reason": f"порт не число: {value!r}"}
            else:
                setattr(access, key, str(value))
    # Значение из формы используется ТОЛЬКО для этой пробы и не сохраняется.
    if payload.get("password"):
        access.password = str(payload["password"])
    try:
        return collect_mod.probe(access)
    except Exception as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}


def db_collect_now(payload: dict | None = None) -> dict:
    """Сбор структуры (и примеров, если попросили) и запись в индекс.

    Пределы заданы жёстко на стороне сервера, а не приходят из формы: число
    строк примера, число таблиц и время ожидания — это не то, что должен
    настраивать каждый вызов.
    """
    from . import db_access as access_mod
    from . import db_collect as collect_mod
    from . import db_index as index_mod
    from . import db_writer as writer_mod
    from .embeddings import Embedder
    from .lexvec import LexVecStore
    from .revisions import GraphWriter

    payload = payload or {}
    access = access_mod.DbAccessStore().load()
    if not access.enabled:
        return {"ok": False, "reason": "сбор баз выключен в настройках",
                "action": "включите сбор и сохраните настройки"}
    want_samples = bool(payload.get("samples", access.samples_enabled))
    collected = collect_mod.collect(access, with_samples=want_samples)
    if collected.get("result") is None:
        return {"ok": False, "reason": collected.get("reason")}

    cfg = _db_admin_config()
    rev_id = str(payload.get("rev_id") or "") or _db_active_rev()
    if not rev_id:
        return {"ok": False, "reason": "в корпусе нет ревизии: сначала выполните index"}
    graph = GraphWriter(cfg)
    lex = LexVecStore(cfg)
    embedder = Embedder(cfg, allow_download=False)
    try:
        stats = writer_mod.write_introspection(graph, rev_id, collected["result"],
                                              collected.get("samples") or {})
        lex_stats = writer_mod.write_lexvec(lex, embedder, rev_id,
                                           collected["result"],
                                           collected.get("samples") or {})
        index_mod.record_fingerprint(graph.conn, rev_id, collected["fingerprint"],
                                     access.dialect)
    finally:
        try:
            lex.close()
        except Exception:
            pass
    return {
        "ok": True,
        "rev_id": rev_id,
        "fingerprint": collected["fingerprint"],
        "graph": stats,
        "lexvec": lex_stats,
        "samples": len(collected.get("samples") or {}),
        "notes": collected.get("notes") or [],
        "errors": collected.get("errors") or [],
        "seconds": collected.get("seconds"),
    }


def db_save(payload: dict) -> dict:
    """Сохранить настройки доступа. Значение доступа наружу не возвращается."""
    from . import db_access as access_mod

    try:
        access = access_mod.DbAccessStore().save(payload or {})
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "access": access.public()}


def port_is_live(port: int, timeout: float = 0.25) -> bool:
    """Отвечает ли кто-нибудь на порту этой машины.

    Нужно, чтобы панель отличала «HTTP-сервис поднят, но клиент ещё не обращался»
    (жёлтое состояние) от «обращений не видно вовсе» (красное). Проба — соединение,
    а не привязка: на Windows привязка ошибается из-за SO_REUSEADDR.
    """
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(timeout)
            return probe.connect_ex(("127.0.0.1", int(port))) == 0
    except Exception:
        return False


def panel_mode() -> str:
    """Текущий режим. Берётся из настроек, значение по умолчанию — `cli`."""
    try:
        value = settings_mod.SettingsStore().load().get("ragraf_mode")
    except Exception:
        value = None
    modes = panel_modes()
    return value if value in modes else (modes[0] if modes else "cli")


def mode_payload(probe: dict[str, Any] | None = None) -> dict[str, Any]:
    """Единый ответ о режиме — для чтения И для применения.

    Прежде чтение отдавало `mode` (строка) вместе со `modes` (список), а
    применение — только строку. Страница присваивала себе ответ целиком, теряла
    список режимов и показывала один `cli` без выбранного значения: смена режима
    выглядела как поломка (жалоба владельца 15.10.2026). Ответ один — и форма
    одна.
    """
    return {"ok": True, "mode": panel_mode(), "modes": panel_modes(),
            "probe": probe}


def panel_probe(mode: str) -> dict:
    """Пробы каналов и клиентов. Сбой пробы не должен ломать страницу."""
    try:
        from ragraf import probes as probes_mod

        return probes_mod.probe_all(mode)
    except Exception as exc:
        return {"mode": mode, "ok": False, "mode_ok": False,
                "channels": [], "clients": [],
                "error": f"{type(exc).__name__}: {exc}"}


#
# Язык страницы. По умолчанию английский — так просил владелец; набор языков
# и сам текст живут в `ragraf.i18n`, чтобы разметка не была вторым источником
# строк. Текст, помеченный `data-i18n`, подставляется клиентом при загрузке;
# до этого на странице стоит английский, а не пустое место.
PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>RAGRAF — control panel</title>
<link rel="stylesheet" href="/style.css">
<script>
/* Тема и плотность до первой отрисовки: иначе на тёмной системе мелькает
   светлый фон, а плотная таблица успевает «дёрнуться». Выбор пользователя
   (localStorage) важнее системной настройки; значения те же, что и в app.js:
   тема — 'system' | 'light' | 'dark', плотность — 'compact' | 'comfortable'. */
(function () {
  try {
    var mode = window.localStorage.getItem('ragraf-admin-theme') || 'system';
    var dark = mode === 'dark' || (mode !== 'light' && window.matchMedia
      && window.matchMedia('(prefers-color-scheme: dark)').matches);
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
    var density = window.localStorage.getItem('ragraf-admin-density');
    document.documentElement.setAttribute('data-density',
      density === 'compact' ? 'compact' : 'comfortable');
  } catch (e) { /* приватный режим: остаётся светлая схема и обычная плотность */ }
})();
</script>
</head>
<body>
<div class="shell">
<a class="skip-link" href="#app" data-i18n="Skip to content">Skip to content</a>

<!-- Постоянный сайдбар: разделы по группам и состояние корпусов. -->
<aside class="sidebar">
  <div class="sidebar__brand">
    <span class="sidebar__mark" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"
           stroke-linecap="round" stroke-linejoin="round" width="18" height="18">
        <path d="m12 2.8 9 4.6-9 4.6-9-4.6z"/><path d="m3 12.4 9 4.6 9-4.6"/>
        <path d="m3 17 9 4.6 9-4.6"/>
      </svg>
    </span>
    <div class="min-w-0">
      <p class="sidebar__title">RAGRAF</p>
      <p class="sidebar__sub" data-i18n="control panel">control panel</p>
    </div>
  </div>
  <nav class="sidebar__nav" id="nav" aria-label="Sections"></nav>
  <div class="sidebar__foot" id="sidebar-stats"></div>
</aside>

<!-- Та же навигация выдвигается на узком экране. -->
<!-- Та же навигация выдвигается на узком экране: подложка закрывает её щелчком. -->
<div class="scrim" id="drawer-scrim" data-action="sidebar-close" hidden></div>
<div class="drawer" id="drawer" hidden role="dialog" aria-label="Sections">
  <div class="sidebar__brand">
    <span class="sidebar__mark" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"
           stroke-linecap="round" stroke-linejoin="round" width="18" height="18">
        <path d="m12 2.8 9 4.6-9 4.6-9-4.6z"/><path d="m3 12.4 9 4.6 9-4.6"/>
        <path d="m3 17 9 4.6 9-4.6"/>
      </svg>
    </span>
    <div class="min-w-0">
      <p class="sidebar__title">RAGRAF</p>
      <p class="sidebar__sub" data-i18n="control panel">control panel</p>
    </div>
  </div>
  <nav class="sidebar__nav" id="nav-drawer" aria-label="Sections"></nav>
  <div class="drawer__close">
    <button class="btn btn--ghost btn--icon-sm" id="drawer-close" type="button"
            data-action="sidebar-close" data-i18n-aria="Close" aria-label="Close">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
           stroke-linecap="round" width="16" height="16"><path d="M18 6 6 18M6 6l12 12"/></svg>
    </button>
  </div>
</div>

<div class="main">
  <header class="topbar">
    <button class="btn btn--ghost btn--icon-sm show-under-lg" type="button"
            data-action="sidebar-open" aria-label="Sections">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
           stroke-linecap="round" width="18" height="18">
        <path d="M3.5 6.5h17M3.5 12h17M3.5 17.5h17"/></svg>
    </button>
    <h1 class="topbar__title" id="topbar-title">RAGRAF</h1>
    <span class="topbar__crumb" id="topbar-crumb">RAGRAF · 127.0.0.1</span>
    <button class="search-trigger" id="palette-open" type="button" data-action="palette-open"
            aria-haspopup="dialog" aria-label="Sections, developments and actions"
            style="margin-inline-start:1.5rem"></button>
    <div class="topbar__actions">
      <a class="btn btn--ghost btn--sm" href="/sources" title="Разрешённые корни карты">источники</a>
      <a class="btn btn--ghost btn--sm" href="/value" title="Польза за 30 дней">польза</a>
      <a class="btn btn--ghost btn--sm" href="/knowledge" target="_blank" rel="noopener" title="Промт и research контура знаний">знания</a>
      <span class="status" id="status" data-i18n="loading…">loading…</span>
      <label class="lang" for="lang-select">
        <span data-i18n="language">language</span>
        <select id="lang-select" aria-label="language"></select>
      </label>
      <div class="relative">
        <button class="btn btn--ghost btn--icon-sm" id="theme-toggle" type="button"
                data-action="theme-menu" aria-haspopup="menu" aria-expanded="false"
                aria-label="Theme"></button>
        <div class="menu menu--right" id="theme-menu" role="menu" hidden></div>
      </div>
      <button class="btn btn--ghost btn--icon-sm" id="reload" type="button" data-action="refresh"
              data-i18n-aria="Refresh" aria-label="Refresh" title="Refresh">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
             stroke-linecap="round" stroke-linejoin="round" width="17" height="17">
          <path d="M21 12a9 9 0 1 1-2.64-6.36L21 8"/><path d="M21 3v5h-5"/></svg>
      </button>
      <div class="account" id="account"></div>
    </div>
  </header>

  <!-- Результат последнего действия. Живёт ВНЕ разделов: перерисовка состояния
       не должна стирать запись о том, что уже произошло. -->
  <div class="notice-slot"><div class="notice" id="message" role="status" hidden></div></div>

  <main id="app" class="page" aria-live="polite">
    <p class="muted" data-i18n="Collecting information about developments…">Collecting information about developments…</p>
  </main>

  <footer class="footer">
    <div class="footer__row">
      <span class="mono">RAGRAF · control panel</span>
      <span class="mono" id="footer-rev"></span>
      <span class="toolbar__spacer"></span>
      <span class="inline"><kbd class="kbd">Ctrl K</kbd>
        <span data-i18n="Sections">Sections</span></span>
    </div>
  </footer>
</div>
</div>

<div id="dialog"></div>
<div id="palette"></div>
<div class="toasts" id="toasts" role="region" aria-label="Notifications"></div>
<script src="/app.js"></script>
</body>
</html>
"""


def wire_access_log() -> None:
    """Подключить журнал доступа к control-хранилищу.

    Хранилище берётся у того корпуса, который панель уже показывает: отдельной
    базы журнал не заводит — его задача быть рядом с телеметрией, чтобы сводка
    связывала обращение и результат. Если корпусов нет, журнал молча
    выключается: администрирование не должно падать из-за пустого реестра.

    ВАЖНО про потоки. Панель обслуживает запросы в отдельных потоках
    (`ThreadingHTTPServer`), а соединение SQLite по умолчанию привязано
    к создавшему его потоку. Измерено: обращение к `/api/access` падало с
    `ProgrammingError: SQLite objects created in a thread can only be used in
    that same thread`, тогда как проверка из того же потока проходила. Поэтому
    соединение берётся с `check_same_thread=False`: запись идёт короткой
    транзакцией, а журнал append-only, поэтому конкуренции за одни и те же
    строки нет.
    """
    try:
        catalog = catalog_mod.Catalog()
        entries = catalog.summary().get("entries") or []
    except Exception:
        return
    ordered = sorted(entries,
                     key=lambda e: 0 if str(e.get("category")) == "ragraf" else 1)
    for row in ordered:
        cfg_path = Path(str(row.get("config") or ""))
        if not cfg_path.exists():
            continue
        try:
            cfg = load_config(cfg_path)
            # Соединение открывается ЗДЕСЬ, а не через store.connect: тому нужен
            # `check_same_thread=False`, чтобы им могли пользоваться потоки
            # сервера. Измерено: без этого обращение к /api/access падало с
            # `ProgrammingError: SQLite objects created in a thread can only be
            # used in that same thread`.
            Path(cfg.control_db).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(cfg.control_db), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=10000")
            control = _ThreadSafeControl(conn)
            access_mod.AccessLog(control).ensure_schema()
            ACCESS_LOG.control = control
            print(f"журнал доступа: {util.to_posix(cfg.control_db)}")
            return
        except Exception:
            continue


class _ThreadSafeControl:
    """Минимальная обёртка: одно соединение для потоков сервера.

    Публичный интерфейс — только `conn`, потому что журналу больше ничего
    не нужно. Отдельный класс вместо правки `ControlStore` потому, что тот
    используется индексатором и менять его поведение ради панели нельзя.
    """

    def __init__(self, conn) -> None:
        self.conn = conn


def console_utf8() -> None:
    """Привести вывод в консоль к UTF-8, чтобы запуск не падал на кодировке.

    Измерено: `python tools/ragraf-admin.py --no-browser` без
    `PYTHONIOENCODING=utf-8` падал на первом же print с
    `UnicodeEncodeError: 'charmap' codec can't encode characters`, потому что
    консоль Windows здесь в cp1252. Падал он ПОСЛЕ `bind`, поэтому порт на миг
    открывался и закрывался — панель выглядела сломанной. Отсутствие настройки
    окружения не должно быть причиной отказа: сервер обязан подняться сам.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass


def port_is_free(port: int) -> bool:
    """Свободен ли порт.

    Проверка идёт СОЕДИНЕНИЕМ, а привязка — без SO_REUSEADDR. Иначе на Windows
    проба ошибается: этот режим позволяет привязаться к уже занятому порту, и
    второй экземпляр панели «встаёт» на тот же порт, не слушая его (проверено
    15.10.2026: два экземпляра печатали один адрес, а слушал только один).
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.35)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def free_port(start: int, tries: int = 20) -> int:
    """Первый свободный порт начиная с указанного.

    Запрошенный порт может быть занят — вторым экземпляром панели или стендом на
    соседнем порту. Прежде это кончалось отказом при запуске, а до этого форма
    «--port 11701» молча игнорировалась, и панель вставала на порт по умолчанию:
    запускающий считал, что указал порт, а на деле нет (замерено 15.10.2026).
    """
    for candidate in range(start, start + max(1, tries)):
        if port_is_free(candidate):
            return candidate
    return start


def main(argv: list[str]) -> int:
    console_utf8()
    global SNAPSHOT_WRITE_ALLOWED
    # Снимки пишет только панель: см. комментарий у SNAPSHOT_WRITE_ALLOWED.
    SNAPSHOT_WRITE_ALLOWED = True
    requested = 8777
    open_browser = True
    renew = False
    rest = list(argv)
    while rest:
        arg = rest.pop(0)
        if arg.startswith("--port="):
            requested = int(arg.split("=", 1)[1])
        elif arg == "--port" and rest:
            # Форма через пробел — тоже.
            requested = int(rest.pop(0))
        elif arg == "--no-browser":
            open_browser = False
        elif arg == "--new-token":
            # Выпустить новое значение доступа: прежнее перестанет приниматься.
            renew = True
    if renew:
        rotate_panel_key()
    port = free_port(requested)

    # Статика отдаётся из пакета: интерфейс должен работать без установки файлов.
    static = REPO / "admin" / "static"

    # Журнал доступа подключается ДО открытия порта: иначе первые обращения
    # остались бы незаписанными.
    wire_access_log()

    def build_handler() -> type[Handler]:
        class StaticHandler(Handler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path in ("/style.css", "/app.js"):
                    name = parsed.path.lstrip("/")
                    path = static / name
                    if path.exists():
                        body = path.read_bytes()
                        self.send_response(200)
                        self.send_header(
                            "Content-Type",
                            "text/css; charset=utf-8" if name.endswith(".css")
                            else "application/javascript; charset=utf-8",
                        )
                        self.send_header("Content-Length", str(len(body)))
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        self.wfile.write(body)
                        return
                super().do_GET()

        return StaticHandler

    class _PanelServer(ThreadingHTTPServer):
        # Отказ от SO_REUSEADDR намеренный: на Windows этот режим позволяет
        # второму экземпляру привязаться к занятому порту, и два процесса
        # считают себя рабочими, а отвечает случайный из них.
        allow_reuse_address = False

    server = _PanelServer(("127.0.0.1", port), build_handler())
    url = f"http://127.0.0.1:{port}/"
    print("=" * 68)
    print("RAGRAF — интерфейс управления запущен")
    print(f"адрес:        {url}")
    if port != requested:
        print(f"порт {requested} занят — взят следующий свободный: {port}")
    print(f"токен доступа: {ACCESS_TOKEN}")
    print(f"файл со значением: {util.to_posix(KEYFILE)}")
    print("  (значение переживает перезапуск; --new-token выпускает новое)")
    if PASSWORDS.is_set():
        print("пароль:       задан — вход возможен по нему; токен сессии тоже работает")
    else:
        print("пароль:       не задан — задать можно в разделе «Доступ» панели")
    _tokens = TOKENS.state()
    print(f"токенов выдано: {_tokens['count']} (действующих {_tokens['active']})")
    print("доступ только с этой машины (127.0.0.1): сетевого доступа нет по замыслу")
    print("остановка: Ctrl+C")
    # flush обязателен: при перенаправленном выводе баннер оставался в буфере, и
    # напечатанный токен нельзя было прочитать (замерено 15.10.2026, когда
    # проверочный экземпляр панели поднимали именно ради этого токена).
    print("=" * 68, flush=True)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nостановлен")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
