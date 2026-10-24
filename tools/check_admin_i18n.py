"""Проверка переводов админ-панели RAGRAF и починки её состояния.

Зачем эта проверка
    Две правки панели ломаются МОЛЧА, и обе видно только исполнением кода.

    Первая: `GET /api/state` синхронно сверял контрольные суммы всех разработок.
    Измерено: около 136 секунд, из них 81 — один AutoClaw (52 931 файл). Браузер
    отменял запрос, и страница вечно показывала «Собираю сведения о разработках…».
    Теперь тяжёлые поля считает фоновый поток, а ответ берётся из снимка. Ошибка
    здесь не даёт исключения в файле: обработчик либо снова подвешивает страницу,
    либо падает уже в потоке сервера, где никто не смотрит. Поэтому проверка
    вызывает `build_state()` в ОТДЕЛЬНОМ процессе с жёстким сроком и отдельно
    запускает панель целиком.

    Вторая: интерфейс переведён на три языка (en, ru, zh), ключ — английский
    текст, словарь — `src/ragraf/i18n.py`. Пропущенный перевод не виден при
    запуске: в русском интерфейсе просто окажется английская строка. Поэтому
    ключи, которые реально зовёт клиент, сверяются со словарём ПО КАЖДОМУ языку.

    Отдельно проверяется запуск на cp1252-консоли: без `PYTHONIOENCODING=utf-8`
    печать русского баннера падала с `UnicodeEncodeError`, причём уже ПОСЛЕ
    `bind` — порт на миг открывался и закрывался.

Что проверяется
    A. словарь `src/ragraf/i18n.py`: языки, пустые и непереведённые записи,
       расхождение подстановок `{name}`, `normalize`/`pick_language`/`translate`,
       повторы ключей в литералах и таблица контракта (сырой русский текст →
       английский ключ). План называл её `CONTRACT`; в модуле эту роль выполняет
       `SERVER_TEXT`, поэтому проверяется назначение таблицы, а не её имя;
    B. покрытие ключей клиента (`admin/static/app.js` и `data-i18n` разметки)
       словарём по каждому языку; мёртвые ключи клиентских таблиц;
    C. контракт страницы и маршрутов без запуска сервера: язык страницы,
       подключаемые ресурсы, `/api/i18n`, `/api/state/progress`, признак
       `refreshing`, вызов `console_utf8()` из `main` и главное — срок ответа
       `build_state()` и сборка состояния ПОСЛЕ заполнения фонового снимка
       (это и есть защита от возврата 136 секунд и от панели, которая
       перестаёт отвечать через две минуты после запуска);
    D. живой сервер (`--base=http://127.0.0.1:8777`): `/api/state` за 5 секунд,
       прогресс сбора, словарь переводов, статика, переключатель языка.
       Сервер не слушает — раздел пропускается и провалом не считается;
    E. запуск `python tools/ragraf-admin.py --no-browser` дочерним процессом
       БЕЗ `PYTHONIOENCODING`: панель обязана подняться, напечатать баннер и
       ответить на `/api/health`.

Запуск
    python tools/check_admin_i18n.py
    python tools/check_admin_i18n.py --base=http://127.0.0.1:8777
    python tools/check_admin_i18n.py --skip-live --skip-startup
    python tools/check_admin_i18n.py --python=C:\\AutoClaw\\RAGRAF\\.venv\\Scripts\\python.exe

    Код возврата 0 — все непропущенные проверки прошли.

Границы доверия
    Стандартная библиотека, без зависимостей проекта: сам чекер обязан работать
    и под «голым» `python`, где нет PyYAML. Читаются только файлы; сервер
    поднимается на свободном порту и гасится здесь же. Штатный побочный эффект
    запуска панели — подключение журнала доступа к control-хранилищу корпуса;
    отдельной записи проверка не делает.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO / "src"
STATIC = REPO / "admin" / "static"
APP_JS = STATIC / "app.js"
CLIENT_I18N_JS = STATIC / "i18n.js"
SERVER_PY = SRC / "ragraf" / "admin_server.py"
I18N_PY = SRC / "ragraf" / "i18n.py"

PASS = 0
FAIL = 0
SKIP = 0
#: Вердикт раздела C о сроке build_state(): раздел D по нему понимает, отвечает
#: ли на порту текущая сборка панели или старая, пережившая правку.
BUILD_STATE_VERDICT = "unknown"

#: Раздел D адресуется так же, как в tools/check_admin.py.
BASE = "http://127.0.0.1:8777"
SKIP_LIVE = False
SKIP_STARTUP = False
CHILD_PYTHON = sys.executable

for _arg in sys.argv[1:]:
    if _arg.startswith("--base="):
        BASE = _arg.split("=", 1)[1].rstrip("/")
    elif _arg == "--skip-live":
        SKIP_LIVE = True
    elif _arg == "--skip-startup":
        SKIP_STARTUP = True
    elif _arg.startswith("--python="):
        CHILD_PYTHON = _arg.split("=", 1)[1]

PLACEHOLDER_RX = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
CYRILLIC_RX = re.compile(r"[А-Яа-яЁё]")
#: Имена продуктов из докстроки i18n.py: они не переводятся намеренно, и
#: совпадение перевода с ключом у них — не потерянный перевод, а решение.
NON_TRANSLATABLE = {"RAGRAF", "AutoClaw", "DeepSeek Harness", "SagaAI", "Joern"}
#: Подсказки к сбоям чтения состояния. Их КЛЮЧОМ служит сам русский текст
#: (сервер шлёт его в JSON, клиент переводит через `_server`), поэтому запись
#: «значение равно ключу» для русского языка здесь — правильный ответ, а не
#: недоперевод. Ключи берутся из модуля, чтобы список не разошёлся с кодом.
def _hint_keys() -> set[str]:
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
        from ragraf import i18n  # noqa: PLC0415
        return set(getattr(i18n, "HINT_TEXTS_EN", None) or {})
    except Exception:  # noqa: BLE001
        return set()

NON_TRANSLATABLE |= _hint_keys()
#: Обозначения и аббревиатуры (B, KB, MB, GB, MCP, URL): они одинаковы во всех
#: трёх языках, поэтому совпадение с ключом здесь тоже не потеря перевода.
#: Проверка не ослаблена: строка со строчными буквами, скопированная в перевод,
#: по-прежнему находка — исключение действует только на этот узкий вид записи.
ABBREV_RX = re.compile(r"^[A-Z0-9][A-Z0-9._/-]{0,5}$")


def non_translatable(key: str) -> bool:
    return key in NON_TRANSLATABLE or bool(ABBREV_RX.match(key))

COLLECTING_KEYS = ("running", "started_at", "finished_at", "error", "skipped",
                   "entries_done")
USAGE = (
    "Запуск: python tools/check_admin_i18n.py [--base=http://127.0.0.1:8777]\n"
    "        [--skip-live] [--skip-startup] [--python=<интерпретатор>]"
)


def _console_utf8() -> None:
    """Своя консоль тоже бывает cp1252: без этого печать русских подписей падает."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass


_console_utf8()


def check(cond: bool, label: str, detail: str = "") -> bool:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}".rstrip())
    return bool(cond)


def note(label: str, detail: str = "") -> None:
    """Замечание: находка без вердикта (мёртвый ключ, счётчик вызовов)."""
    print(f"  [WARN] {label} {detail}".rstrip())


def info(label: str, detail: str = "") -> None:
    print(f"  [INFO] {label} {detail}".rstrip())


def skip(label: str, detail: str = "") -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label} {detail}".rstrip())


def brief(items, limit: int = 5) -> str:
    items = list(items)
    tail = f" (+{len(items) - limit})" if len(items) > limit else ""
    return ", ".join(str(i) for i in items[:limit]) + tail


def brief_keys(items, limit: int = 10, width: int = 70) -> str:
    """Список ключей: длинный текст ключа обрезается, чтобы находка читалась."""
    items = list(items)
    shown = [f"{i[:width]}…" if len(i) > width else i for i in items[:limit]]
    tail = f" (+{len(items) - limit})" if len(items) > limit else ""
    return " | ".join(shown) + tail


def read_text(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def import_module(name: str):
    """Импорт настоящего модуля проекта: факты берутся из кода, а не из документа."""
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    try:
        return importlib.import_module(f"ragraf.{name}")
    except Exception as exc:
        check(False, f"ragraf.{name} импортируется", f"{type(exc).__name__}: {exc}")
        return None


# ------------------------------------------------------------------ A

def section_a(i18n) -> None:
    print("\n=== A. Словарь переводов src/ragraf/i18n.py ===")
    if i18n is None:
        note("раздел пропущен: ragraf.i18n не импортируется")
        return

    langs = tuple(getattr(i18n, "LANGUAGES", ()) or ())
    check(len(langs) == 3 and set(langs) == {"en", "ru", "zh"},
          "LANGUAGES — ровно en, ru, zh", f"получено {langs!r}")
    default = getattr(i18n, "DEFAULT_LANGUAGE", None)
    check(default == "en", "DEFAULT_LANGUAGE = en", f"получено {default!r}")
    for name in ("catalogue", "all_catalogues", "normalize", "pick_language", "translate"):
        check(callable(getattr(i18n, name, None)), f"{name}() объявлена")

    tables = getattr(i18n, "TRANSLATIONS", None)
    if not check(isinstance(tables, dict) and bool(tables), "TRANSLATIONS — непустой словарь"):
        return
    outside = sorted(set(tables) - set(langs))
    check(not outside, "в TRANSLATIONS нет языков сверх LANGUAGES", f"лишние: {brief(outside)}")

    for lang in [code for code in langs if code != "en"]:
        table = tables.get(lang)
        if not check(isinstance(table, dict) and bool(table), f"{lang}: таблица переводов непуста"):
            continue
        not_str = sorted(k for k, v in table.items()
                         if not isinstance(k, str) or not isinstance(v, str))
        check(not not_str, f"{lang}: ключи и значения — строки", f"не строки: {brief(not_str)}")
        empty = sorted(k for k, v in table.items() if not str(v).strip())
        check(not empty, f"{lang}: нет пустых переводов", f"пусто: {len(empty)}: {brief(empty)}")
        same = sorted(k for k, v in table.items()
                      if v == k and not non_translatable(k))
        exempt = sorted(k for k, v in table.items()
                        if v == k and non_translatable(k))
        check(not same, f"{lang}: нет перевода, равного ключу",
              f"равны ключу: {len(same)}: {brief(same)}")
        if exempt:
            info(f"{lang}: совпали с ключом непереводимые обозначения", brief(exempt, 8))
        if lang == "zh":
            cyr = sorted(k for k, v in table.items() if CYRILLIC_RX.search(v))
            check(not cyr, "zh: в переводе нет кириллицы", f"кириллица: {len(cyr)}: {brief(cyr)}")
        mismatch = []
        for key, value in table.items():
            want = set(PLACEHOLDER_RX.findall(key))
            got = set(PLACEHOLDER_RX.findall(value))
            if want != got:
                mismatch.append(f"{key!r}: {sorted(want)} -> {sorted(got)}")
        check(not mismatch, f"{lang}: набор подстановок {{name}} совпадает с ключом",
              f"расхождений: {len(mismatch)}: " + " | ".join(mismatch[:4]))

    ru_keys = set(tables.get("ru") or {})
    zh_keys = set(tables.get("zh") or {})
    diff = sorted(ru_keys ^ zh_keys)
    check(not diff, "ru и zh описывают один и тот же набор ключей",
          f"расхождений: {len(diff)}: {brief(diff)}")

    dupes = _duplicate_dict_keys(I18N_PY, "TRANSLATIONS")
    check(not dupes, "в литерале TRANSLATIONS нет повторённых ключей",
          f"повторы (Python оставляет последний молча): {brief(dupes)}")

    _check_normalize(i18n)
    _check_pick_language(i18n)
    _check_translate(i18n, tables)
    _check_catalogue(i18n, langs, tables)
    _check_server_maps(i18n)


def _duplicate_dict_keys(path: pathlib.Path, varname: str) -> list[str]:
    """Повторы ключей в литерале словаря: Python оставляет последний молча."""
    source = read_text(path)
    if not source:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            names = [target.id] if isinstance(target, ast.Name) else []
            value = node.value
        else:
            continue
        if varname in names and isinstance(value, ast.Dict):
            _collect_dups(value, varname, found)
    return found


def _collect_dups(node: ast.Dict, prefix: str, out: list[str]) -> None:
    seen: set[str] = set()
    for key_node, value_node in zip(node.keys, node.values):
        label = "?"
        if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
            label = key_node.value
            if label in seen:
                out.append(f"{prefix}[{label!r}]")
            seen.add(label)
        if isinstance(value_node, ast.Dict):
            _collect_dups(value_node, f"{prefix}.{label}", out)


def _check_normalize(i18n) -> None:
    cases = {"ru": "ru", "RU": "ru", "ru-RU": "ru", "zh_CN": "zh", "zh": "zh",
             "en-US": "en", "de": "en", "": "en", "xx-YY": "en", "  ": "en"}
    bad = []
    for raw, want in cases.items():
        try:
            got = i18n.normalize(raw)
        except Exception as exc:
            got = f"{type(exc).__name__}: {exc}"
        if got != want:
            bad.append(f"{raw!r} -> {got!r} (ждали {want!r})")
    try:
        got_none = i18n.normalize(None)
    except Exception as exc:
        got_none = f"{type(exc).__name__}: {exc}"
    if got_none != "en":
        bad.append(f"None -> {got_none!r} (ждали 'en')")
    check(not bad, "normalize(): ru/RU/ru-RU/zh_CN/неизвестный -> верный язык",
          "; ".join(bad[:5]))


def _check_pick_language(i18n) -> None:
    cases = [
        ("ru-RU,ru;q=0.9,en;q=0.8", "ru"),
        ("zh-CN,zh;q=0.9,en;q=0.8", "zh"),
        ("en-US,en;q=0.9", "en"),
        ("ru", "ru"),
        ("ru;q=0,en;q=0.5", "en"),
        ("en;q=0.4,ru;q=0.9", "ru"),
        ("de-DE,de;q=0.9", "en"),
        ("ru;q=abc,en", "en"),
        ("*", "en"),
        ("", "en"),
    ]
    bad = []
    for header, want in cases:
        try:
            got = i18n.pick_language(header)
        except Exception as exc:
            got = f"{type(exc).__name__}: {exc}"
        if got != want:
            bad.append(f"{header!r} -> {got!r} (ждали {want!r})")
    try:
        got_none = i18n.pick_language(None)
    except Exception as exc:
        got_none = f"{type(exc).__name__}: {exc}"
    if got_none != "en":
        bad.append(f"None -> {got_none!r} (ждали 'en')")
    check(not bad, "pick_language(): вес q и неподдержанные языки учтены",
          "; ".join(bad[:5]))


def _check_translate(i18n, tables) -> None:
    ru = tables.get("ru") or {}
    key = next((k for k, v in ru.items() if v != k and not PLACEHOLDER_RX.search(k)), None)
    if key is not None:
        check(i18n.translate(key, "ru") == ru[key], "translate() отдаёт перевод для ru")
        check(i18n.translate(key, "RU") == ru[key], "translate() нормализует язык ('RU' -> ru)")
        check(i18n.translate(key, "en") == key, "translate(key, 'en') отдаёт английский ключ")
        try:
            got = i18n.translate(key, None)
        except Exception as exc:
            got = f"{type(exc).__name__}: {exc}"
        check(got == key, "translate(key, None) не падает и отдаёт английский ключ",
              f"получено {got!r}")
    absent = "строка, которой нет в словаре перевода 12345"
    bad = []
    for lang in ("en", "ru", "zh", "de", "", "xx-YY"):
        try:
            got = i18n.translate(absent, lang)
        except Exception as exc:
            got = f"{type(exc).__name__}: {exc}"
        if got != absent:
            bad.append(f"{lang!r} -> {got!r}")
    check(not bad, "translate(): без перевода возвращается исходный текст", "; ".join(bad[:5]))


def _check_catalogue(i18n, langs, tables) -> None:
    try:
        all_catalogues = i18n.all_catalogues()
        cat_ru = i18n.catalogue("ru")
    except Exception as exc:
        check(False, "catalogue()/all_catalogues() выполняются",
              f"{type(exc).__name__}: {exc}")
        return
    check(isinstance(all_catalogues, dict) and set(all_catalogues) == set(langs),
          "all_catalogues() отдаёт все языки панели",
          f"получено {sorted(all_catalogues) if isinstance(all_catalogues, dict) else all_catalogues!r}")
    check(isinstance(cat_ru, dict) and len(cat_ru) >= 1, "catalogue('ru') непуст",
          f"получено {type(cat_ru).__name__} длиной {len(cat_ru) if isinstance(cat_ru, dict) else '?'}")
    before = {lang: dict(table) for lang, table in tables.items()}
    if isinstance(cat_ru, dict):
        cat_ru["__probe__"] = "x"
        nested = cat_ru.get("_server")
        if isinstance(nested, dict):
            nested["__probe__"] = "x"
    check(tables == before, "catalogue() отдаёт копию, а не сам словарь модуля",
          "правка возвращённого словаря изменила TRANSLATIONS")


def _check_server_maps(i18n) -> None:
    """Таблицы контракта: сырой русский текст -> английский ключ.

    План называл отдельную таблицу `CONTRACT`; в готовом модуле её роль
    выполняет `SERVER_TEXT` (так записано и в докстроке модуля). Проверяется
    НАЗНАЧЕНИЕ таблицы, а не её имя: каждая существующая таблица должна быть
    непуста, а её значения — переведены на оба языка. Отсутствие отдельного
    имени отмечается замечанием, а провалом становится только отсутствие
    отображения контракта как такового.
    """
    tables = {name: getattr(i18n, name, None) for name in ("SERVER_TEXT", "CONTRACT")}
    present = {name: table for name, table in tables.items()
               if isinstance(table, dict) and bool(table)}
    check(bool(present), "таблица контракта (SERVER_TEXT/CONTRACT) есть и непуста",
          "модуль не предоставляет ни CONTRACT, ни SERVER_TEXT")
    if "CONTRACT" not in present and "SERVER_TEXT" in present:
        note("отдельной таблицы CONTRACT в модуле нет",
             f"её роль выполняет SERVER_TEXT ({len(present['SERVER_TEXT'])} записей): "
             "проверено по фактической таблице, а не по имени из плана")
    for name, table in present.items():
        for lang in ("ru", "zh"):
            broken = []
            for raw, key in table.items():
                try:
                    value = i18n.translate(key, lang)
                except Exception as exc:
                    value = f"{type(exc).__name__}: {exc}"
                # Совпадение с ключом — находка, КРОМЕ записей, у которых
                # значение и есть правильный ответ: подсказки к сбоям читаются
                # русским текстом-ключом на всех языках (см. NON_TRANSLATABLE).
                same_as_key = value == key and not non_translatable(key)
                if not isinstance(value, str) or not value.strip() or same_as_key:
                    broken.append(f"{raw!r} -> {key!r}")
            check(not broken, f"{name}: значения переведены на {lang}",
                  f"не переведено: {len(broken)}: {brief(broken)}")
    raw_map = present.get("SERVER_TEXT") or present.get("CONTRACT") or {}
    translated = getattr(i18n, "TRANSLATIONS", {}) or {}
    ru = set(translated.get("ru") or {})
    zh = set(translated.get("zh") or {})
    missing = sorted(k for k in raw_map.values() if k not in ru or k not in zh)
    if raw_map:
        check(not missing, "таблица контракта ссылается на ключи, описанные в ru и zh",
              f"нет в словаре: {brief(missing)}")


# ------------------------------------------------------------------ B

def unescape_js(raw: str) -> str:
    out = []
    index = 0
    simple = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "'": "'", '"': '"'}
    while index < len(raw):
        ch = raw[index]
        if ch == "\\" and index + 1 < len(raw):
            out.append(simple.get(raw[index + 1], raw[index + 1]))
            index += 2
            continue
        out.append(ch)
        index += 1
    return "".join(out)


def quoted_at(text: str, index: int) -> tuple[str, int] | None:
    """Строковый литерал JS из позиции index. Возвращает (значение, конец)."""
    quote = text[index]
    if quote not in "\"'":
        return None
    out = []
    cursor = index + 1
    while cursor < len(text):
        ch = text[cursor]
        if ch == "\\":
            if cursor + 1 >= len(text):
                return None
            out.append(text[cursor:cursor + 2])
            cursor += 2
            continue
        if ch == quote:
            return unescape_js("".join(out)), cursor + 1
        if ch == "\n":
            return None
        out.append(ch)
        cursor += 1
    return None


def strip_js_comments(text: str) -> str:
    """Убрать комментарии, не тронув литералы: ключ, упомянутый в пояснении,
    не является вызовом перевода."""
    out = []
    cursor = 0
    length = len(text)
    while cursor < length:
        ch = text[cursor]
        if ch in "\"'":
            got = quoted_at(text, cursor)
            if got is None:
                out.append(ch)
                cursor += 1
                continue
            _, end = got
            out.append(text[cursor:end])
            cursor = end
            continue
        if ch == "/" and cursor + 1 < length and text[cursor + 1] == "/":
            newline = text.find("\n", cursor)
            cursor = length if newline < 0 else newline
            continue
        if ch == "/" and cursor + 1 < length and text[cursor + 1] == "*":
            close = text.find("*/", cursor + 2)
            cursor = length if close < 0 else close + 2
            continue
        out.append(ch)
        cursor += 1
    return "".join(out)


def parse_first_argument(text: str, index: int) -> tuple[str | None, str | None, bool]:
    """Разобрать первый аргумент вызова, начинающийся в index.

    Возвращает (ключ, имя массива, нельзя разобрать). Склейка литералов через
    `+` обязательна: длинный ключ в клиенте разбит на строки, и без склейки
    проверка объявила бы существующий перевод пропущенным.
    """
    literals: list[str] = []
    indexed: str | None = None
    other = False
    depth = 0
    cursor = index
    while cursor < len(text):
        ch = text[cursor]
        if ch in "\"'":
            got = quoted_at(text, cursor)
            if got is None:
                return None, indexed, True
            value, cursor = got
            literals.append(value)
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif ch == "," and depth == 0:
            break
        elif depth == 0 and not ch.isspace() and ch != "+":
            other = True
            if indexed is None:
                found = re.match(r"([A-Za-z_$][\w$]*)\s*\[", text[cursor:])
                if found:
                    indexed = found.group(1)
        cursor += 1
    if other or not literals:
        return None, indexed, True
    return "".join(literals), indexed, False


def array_literals(text: str, name: str) -> list[str]:
    """Строковые литералы массива `const name = [...]` — источник ключей,
    которые клиент передаёт в перевод через индекс (`t(units[index])`)."""
    found: list[str] = []
    pattern = re.compile(r"(?:const|let|var)\s+" + re.escape(name) + r"\s*=\s*\[")
    for match in pattern.finditer(text):
        cursor = match.end()
        depth = 1
        while cursor < len(text) and depth > 0:
            ch = text[cursor]
            if ch in "\"'":
                got = quoted_at(text, cursor)
                if got is None:
                    break
                value, cursor = got
                found.append(value)
                continue
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
            cursor += 1
    return found


def keys_used_by(text: str, name: str) -> tuple[set[str], int, set[str]]:
    """Ключи, которые клиент реально передаёт в перевод.

    Литералы первого аргумента (в том числе склеенные) плюс значения массивов,
    к которым клиент обращается по индексу. Третий элемент — вызовы без
    разбираемого ключа: их статически проверить нельзя, о них сообщается.
    """
    keys: set[str] = set()
    dynamic = 0
    indexed: set[str] = set()
    pattern = re.compile(r"(?<![\w$.])" + re.escape(name) + r"\s*\(")
    for match in pattern.finditer(text):
        head = text[max(0, match.start() - 12):match.start()]
        if re.search(r"function\s+$", head):
            continue  # это объявление функции, а не вызов
        cursor = match.end()
        while cursor < len(text) and text[cursor] in " \t\r\n":
            cursor += 1
        key, array_name, unparsed = parse_first_argument(text, cursor)
        if key is not None:
            keys.add(key)
            continue
        if array_name:
            indexed.add(array_name)
        elif unparsed:
            dynamic += 1
    for array_name in indexed:
        keys.update(array_literals(text, array_name))
    return keys, dynamic, indexed


def find_translation_helper(js: str) -> tuple[str | None, list[str]]:
    """Найти функцию перевода по СОДЕРЖИМОМУ, а не по имени.

    Имя выбирает автор; признак функции перевода — обращение к словарю
    (`catalogue`, `i18n`, `translations`) и к текущему языку. Из найденных
    кандидатов берётся тот, который зовут с ключами-литералами чаще всех.
    """
    positions: dict[str, list[int]] = {}
    for match in re.finditer(r"function\s+([A-Za-z_$][\w$]*)\s*\(", js):
        positions.setdefault(match.group(1), []).append(match.end())
    for match in re.finditer(
            r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function\s*)?\(", js):
        positions.setdefault(match.group(1), []).append(match.end())
    best: str | None = None
    best_keys: list[str] = []
    for name, starts in positions.items():
        window = " ".join(js[start:start + 700] for start in starts).lower()
        if not any(marker in window for marker in ("catalog", "translat", "i18n", "dictionar")):
            continue
        if not any(marker in window for marker in ("lang", "current", "locale")):
            continue
        keys, _, _ = keys_used_by(js, name)
        if len(keys) > len(best_keys):
            best, best_keys = name, sorted(keys)
    return best, best_keys


def js_object_keys(text: str) -> set[str]:
    """Ключи объектных литералов JS: `"key": значение`."""
    keys: set[str] = set()
    for match in re.finditer(r"""(?:"((?:[^"\\\n]|\\.)*)"|'((?:[^'\\\n]|\\.)*)')\s*:""", text):
        raw = match.group(1) if match.group(1) is not None else match.group(2)
        keys.add(unescape_js(raw))
    return keys


#: Служебные имена объектов клиентских таблиц: это не тексты интерфейса.
STRUCTURAL_JS_KEYS = {"ru", "zh", "en", "catalogue", "translations", "languages",
                      "default", "titles", "params", "i18n", "_server", "server"}


def section_b(i18n, admin) -> None:
    print("\n=== B. Покрытие ключей клиента словарём ===")
    if i18n is None:
        note("раздел пропущен: ragraf.i18n не импортируется")
        return
    js = read_text(APP_JS)
    if not check(bool(js), "клиентский скрипт admin/static/app.js читается",
                 f"файл не читается: {APP_JS}"):
        return
    # Комментарии убираются: упоминание ключа в пояснении — не вызов перевода.
    code = strip_js_comments(js)

    helper, keys = find_translation_helper(code)
    check(helper is not None, "в app.js найдена функция перевода",
          "функция с обращением к словарю переводов не найдена: перевод не подключён")
    if helper is None:
        note("покрытие ключей не проверялось: неизвестно, как клиент берёт перевод")
        return
    used, dynamic, indexed = keys_used_by(code, helper)
    keys = sorted(used)
    info(f"функция перевода: {helper}(), ключей-литералов: {len(keys)}")
    if indexed:
        info(f"ключи из массивов: {', '.join(sorted(indexed))}",
             f"всего ключей с ними: {len(keys)}")
    check(len(keys) >= 1, f"{helper}() вызывается с ключами-литералами",
          f"литералов не найдено, вызовов без литерала: {dynamic}")
    if dynamic:
        note(f"{helper}() вызывается {dynamic} раз без разбираемого ключа",
             "такие ключи статически не проверяются")

    html = getattr(admin, "PAGE_HTML", "") if admin is not None else ""
    # Разметка называет строки двумя атрибутами: `data-i18n` — видимый текст,
    # `data-i18n-aria` — доступное имя иконочной кнопки (без него кнопка читается
    # экранной читалкой как «кнопка» без названия). Оба обязаны быть в словаре.
    html_keys = sorted(set(re.findall(r"""data-i18n(?:-aria)?\s*=\s*["']([^"']+)["']""", html)))
    info(f"ключей в разметке (data-i18n / data-i18n-aria): {len(html_keys)}")

    tables = getattr(i18n, "TRANSLATIONS", {}) or {}
    for lang in ("ru", "zh"):
        table = tables.get(lang) or {}
        missing_js = sorted(k for k in keys if k not in table)
        check(not missing_js, f"{lang}: все ключи app.js есть в словаре",
              f"нет перевода: {len(missing_js)}: {brief_keys(missing_js)}")
        if html_keys:
            missing_html = sorted(k for k in html_keys if k not in table)
            check(not missing_html, f"{lang}: все ключи разметки (data-i18n) есть в словаре",
                  f"нет перевода: {len(missing_html)}: {brief_keys(missing_html)}")

    _check_client_bundle(js, html, keys, tables, i18n)


def _check_client_bundle(js: str, html: str, keys: list[str], tables, i18n) -> None:
    """Клиентские таблицы `admin/static/i18n.js`: если их нет, клиент обязан
    брать словарь у сервера — тогда проверяется, что ссылки на файл нет."""
    bundle_used = bool(re.search(r"i18n\.js", html)) or bool(re.search(r"i18n\.js", js))
    if not CLIENT_I18N_JS.exists():
        check(not bundle_used, "admin/static/i18n.js не нужен и на него нет ссылок",
              "страница или скрипт ссылается на i18n.js, которого нет")
        if not bundle_used:
            info("каталог переводов клиент берёт у сервера (/api/i18n)",
                 "отдельного файла клиентских таблиц в проекте нет")
        return
    js_keys = js_object_keys(read_text(CLIENT_I18N_JS)) - STRUCTURAL_JS_KEYS
    check(bool(js_keys), "admin/static/i18n.js описывает ключи переводов",
          f"объектных ключей не найдено: {CLIENT_I18N_JS}")
    if bundle_used and keys:
        missing = sorted(k for k in keys if k not in js_keys)
        check(not missing, "ключи app.js есть в admin/static/i18n.js",
              f"нет: {len(missing)}: {brief_keys(missing)}")
    python_keys = set()
    for table in tables.values():
        python_keys |= set(table)
    server_raw = getattr(i18n, "SERVER_TEXT", {}) or {}
    known = python_keys | set(server_raw) | set(server_raw.values()) | set(keys)
    dead = sorted(k for k in js_keys if k not in known)
    if dead:
        note(f"мёртвые ключи в admin/static/i18n.js: {len(dead)}", brief(dead, 8))
    else:
        info("мёртвых ключей в admin/static/i18n.js нет")


# ------------------------------------------------------------------ C

def section_c(i18n, admin) -> None:
    print("\n=== C. Контракт страницы и маршрутов (сервер не запускается) ===")
    if admin is None:
        note("раздел пропущен: ragraf.admin_server не импортируется")
        return
    default_lang = getattr(i18n, "DEFAULT_LANGUAGE", "en") if i18n is not None else "en"
    html = getattr(admin, "PAGE_HTML", "")
    if not check(isinstance(html, str) and len(html) > 200, "PAGE_HTML — непустая разметка",
                 f"получено {type(html).__name__} длиной {len(html) if isinstance(html, str) else '?'}"):
        return

    declared = re.search(r"<html[^>]*\blang\s*=\s*[\"']([A-Za-z-]+)[\"']", html)
    check(declared is not None, "в PAGE_HTML объявлен язык страницы (html lang)")
    if declared is not None:
        check(declared.group(1).lower().split("-")[0] == default_lang,
              f"язык страницы — язык по умолчанию ({default_lang})",
              f"объявлено {declared.group(1)!r}")

    assets = set(re.findall(r"""(?:src|href)\s*=\s*["'](/[\w./-]+\.(?:js|css))["']""", html))
    check("/app.js" in assets and "/style.css" in assets,
          "страница подключает /app.js и /style.css", f"подключено: {sorted(assets)}")
    source = read_text(SERVER_PY)
    unserved = sorted(a for a in assets if f'"{a}"' not in source and f"'{a}'" not in source)
    check(not unserved, "каждый ресурс страницы отдаётся статикой",
          f"маршрута нет, браузер получит 404: {unserved}")
    dangling = sorted(a for a in assets
                      if a.split("/")[-1] not in {p.name for p in STATIC.glob("*")})
    check(not dangling, "файлы подключённых ресурсов лежат в admin/static",
          f"файла нет: {dangling}")

    try:
        get_source = inspect.getsource(admin.Handler.do_GET)
    except Exception as exc:
        check(False, "исходник Handler.do_GET читается", f"{type(exc).__name__}: {exc}")
    else:
        for endpoint in ("/api/state", "/api/state/progress", "/api/i18n"):
            check(f'"{endpoint}"' in get_source or f"'{endpoint}'" in get_source,
                  f"GET {endpoint} объявлен в маршрутизации")
        check("refreshing" in get_source, "/api/state отдаёт признак refreshing")
        check("catalogue" in get_source or "all_catalogues" in get_source,
              "/api/i18n отдаёт словарь переводов")

    check(callable(getattr(admin, "console_utf8", None)), "console_utf8() объявлена")
    try:
        main_source = inspect.getsource(admin.main)
    except Exception as exc:
        main_source = ""
        check(False, "исходник main() читается", f"{type(exc).__name__}: {exc}")
    if main_source:
        check(bool(re.search(r"console_utf8\s*\(\s*\)", main_source)),
              "main() вызывает console_utf8() (защита запуска от cp1252)",
              "вызова нет: запуск без PYTHONIOENCODING снова упадёт")

    _check_collecting()
    _check_snapshot_merge()

    if i18n is not None:
        _check_server_map_in_catalogue(i18n, read_text(APP_JS))


def _check_collecting() -> None:
    """Главная регрессия: ответ состояния обязан быть быстрым.

    `build_state()` вызывается в ОТДЕЛЬНОМ процессе с жёстким сроком: если
    тяжёлая сверка вернётся в обработчик, проверка это увидит, а не повиснет.
    """
    global BUILD_STATE_VERDICT
    BUILD_STATE_VERDICT = "fail"
    probe = (
        "import json, os, sys, time\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from ragraf import admin_server as m\n"
        "t = time.perf_counter()\n"
        "state = m.build_state()\n"
        "dt = time.perf_counter() - t\n"
        "print(json.dumps({'seconds': dt,\n"
        "                  'collecting': state.get('collecting'),\n"
        "                  'entries': len((state.get('catalog') or {}).get('entries') or [])}))\n"
        "sys.stdout.flush()\n"
        "os._exit(0)\n"
    )
    try:
        result = subprocess.run([sys.executable, "-c", probe, str(SRC)], cwd=str(REPO),
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=25)
    except subprocess.TimeoutExpired:
        check(False, "build_state() отвечает быстрее 25 секунд",
              "вызов не завершился: тяжёлая сверка снова выполняется внутри ответа")
        return
    except Exception as exc:
        check(False, "build_state() вызывается в отдельном процессе",
              f"{type(exc).__name__}: {exc}")
        return
    if result.returncode != 0:
        check(False, "build_state() выполняется без исключения",
              f"код={result.returncode}; stderr: {(result.stderr or '').strip()[-300:]}")
        return
    try:
        data = json.loads((result.stdout or "").strip().splitlines()[-1])
    except Exception:
        check(False, "build_state() возвращает разбираемый ответ",
              f"stdout: {(result.stdout or '')[:200]!r}")
        return
    seconds = float(data.get("seconds") or 0.0)
    fast_ok = check(seconds < 5.0, "build_state() отвечает быстрее 5 секунд (было 136 с)",
                    f"замер {seconds:.2f} с")
    collecting = data.get("collecting")
    collecting_ok = check(isinstance(collecting, dict),
                          "state.collecting — объект состояния сбора",
                          f"получено {collecting!r}")
    fields_ok = False
    if collecting_ok:
        missing = [key for key in COLLECTING_KEYS if key not in collecting]
        fields_ok = check(not missing, "в state.collecting есть все поля состояния",
                          f"нет полей: {missing}")
    BUILD_STATE_VERDICT = "ok" if (fast_ok and collecting_ok and fields_ok) else "fail"
    info(f"разработок в реестре на момент проверки: {data.get('entries')}")


SNAPSHOT_MERGE_PROBE = (
    "import json, os, sys, time\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from ragraf import admin_server as m\n"
    "from ragraf import catalog as cm\n"
    "entries = cm.Catalog().summary().get('entries') or []\n"
    "if not entries:\n"
    "    print('NO-ENTRIES', flush=True)\n"
    "    os._exit(0)\n"
    "row = min(entries, key=lambda r: 0 if str(r.get('category')) == 'ragraf' else 1)\n"
    "key = str(row.get('category') or '')\n"
    "m.slow_collect_async([row], force=True)\n"
    "deadline = time.time() + 150\n"
    "while m.slow_status()['running'] and time.time() < deadline:\n"
    "    time.sleep(0.1)\n"
    "status = m.slow_status()\n"
    "print('SEEDED ' + json.dumps({'category': key, 'done': status['entries_done'],"
    " 'running': status['running']}), flush=True)\n"
    "state = m.build_state()\n"
    "body = json.dumps({'ok': True, 'state': state,"
    " 'refreshing': bool(state.get('collecting', {}).get('running'))}, ensure_ascii=False)\n"
    "print('MERGE-OK ' + str(len(body)), flush=True)\n"
    "os._exit(0)\n"
)


def _check_snapshot_merge() -> None:
    """Ответ состояния обязан собираться и ПОСЛЕ заполнения фонового снимка.

    Это обычное состояние работающей панели: сбор заканчивается примерно через
    100 секунд, и с этого момента каждый `/api/state` берёт тяжёлые поля из
    снимка. Без этой проверки путь слияния со снимком не исполняется ни разу —
    вызов в свежем процессе видит пустой снимок и проходит, а панель после
    окончания сбора перестаёт отвечать. Именно так и было: снимок хранит пару
    (время, значения), а слияние передаёт эту пару в `dict.update`.
    """
    try:
        result = subprocess.run([sys.executable, "-c", SNAPSHOT_MERGE_PROBE, str(SRC)],
                                cwd=str(REPO), capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=220)
    except subprocess.TimeoutExpired:
        check(False, "state собирается после заполнения фонового снимка",
              "вызов build_state() со снимком не завершился за 220 секунд")
        return
    except Exception as exc:
        check(False, "state собирается после заполнения фонового снимка",
              f"{type(exc).__name__}: {exc}")
        return
    out = result.stdout or ""
    err = result.stderr or ""
    if "NO-ENTRIES" in out:
        skip("сборка state после заполнения снимка", "реестр разработок пуст")
        return
    check(result.returncode == 0 and "MERGE-OK" in out,
          "state собирается и после заполнения фонового снимка (обычное состояние панели)",
          f"код={result.returncode}; {(err.strip() or out.strip())[-400:]}")
    seeded = next((line for line in out.splitlines() if line.startswith("SEEDED")), "")
    if seeded:
        info("снимок заполнялся по разработке", seeded[7:].strip())


def _check_server_map_in_catalogue(i18n, js: str) -> None:
    """Клиент переводит русский текст сервера через `catalogue[lang]._server`."""
    if "_server" not in js:
        note("app.js не использует catalogue[lang]._server — проверка карты серверного текста не нужна")
        return
    server_raw = getattr(i18n, "SERVER_TEXT", {}) or {}
    if not server_raw:
        check(False, "SERVER_TEXT непуст", "карта серверного текста пуста")
        return
    bad = []
    for lang in ("en", "ru", "zh"):
        try:
            catalogue = i18n.catalogue(lang)
        except Exception as exc:
            bad.append(f"{lang}: {type(exc).__name__}: {exc}")
            continue
        server_map = catalogue.get("_server") if isinstance(catalogue, dict) else None
        if not isinstance(server_map, dict):
            bad.append(f"{lang}: нет _server")
            continue
        for raw in server_raw:
            value = server_map.get(raw)
            if not isinstance(value, str) or not value.strip():
                bad.append(f"{lang}: {raw!r}")
    check(not bad, "catalogue[lang]._server покрывает SERVER_TEXT по каждому языку",
          f"проблем: {len(bad)}: {brief(bad)}")


# ------------------------------------------------------------------ D

def reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def http_get(path: str, timeout: float = 10.0) -> tuple[int, str, float]:
    url = BASE + path
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            return response.status, body, time.perf_counter() - started
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return exc.code, body, time.perf_counter() - started
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}", time.perf_counter() - started


def as_json(raw: str):
    try:
        return json.loads(raw)
    except Exception:
        return None


def section_d(i18n) -> None:
    print(f"\n=== D. Живой сервер: {BASE} ===")
    if SKIP_LIVE:
        skip("живые проверки", "раздел пропущен ключом --skip-live")
        return
    parsed = urllib.parse.urlparse(BASE)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not reachable(host, port):
        skip("живые проверки",
             f"на {host}:{port} никто не слушает — запустите python tools/ragraf-admin.py --no-browser")
        return
    info(f"сервер отвечает на {host}:{port}")

    code, raw, seconds = http_get("/api/state", timeout=8.0)
    check(code == 200, "GET /api/state отвечает 200 (было: запрос не дожидался)",
          f"код={code} за {seconds:.2f} с, ответ {raw[:160]!r}")
    check(seconds < 5.0, "GET /api/state укладывается в 5 секунд (было 136 с)",
          f"замер {seconds:.2f} с")
    if code == 200 and BUILD_STATE_VERDICT == "fail":
        note("на порту отвечает СТАРАЯ сборка панели",
             "текущий исходник на build_state() падает, а живой сервер отдаёт состояние: "
             "зелёные пункты раздела D к текущему коду не относятся — перезапустите панель")
    state = (as_json(raw) or {}).get("state") if code == 200 else None
    if check(isinstance(state, dict), "ответ /api/state содержит state"):
        entries = ((state.get("catalog") or {}).get("entries")) or []
        check(isinstance(entries, list) and len(entries) >= 1,
              "state.catalog.entries непуст", f"разработок: {len(entries)}")
        collecting = state.get("collecting")
        if check(isinstance(collecting, dict), "state.collecting — объект состояния сбора",
                 f"получено {collecting!r}"):
            missing = [key for key in COLLECTING_KEYS if key not in collecting]
            check(not missing, "в state.collecting есть все поля", f"нет полей: {missing}")
        body = as_json(raw) or {}
        check(isinstance(body.get("refreshing"), bool),
              "верхний уровень /api/state несёт refreshing",
              f"получено {body.get('refreshing')!r}")

    code, raw, seconds = http_get("/api/state/progress", timeout=5.0)
    progress = as_json(raw) if code == 200 else None
    check(code == 200 and isinstance(progress, dict) and progress.get("ok") is True,
          "GET /api/state/progress отвечает ok", f"код={code}, ответ {raw[:120]!r}")
    check(isinstance((progress or {}).get("progress"), dict),
          "/api/state/progress несёт объект progress", f"получено {(progress or {}).get('progress')!r}")
    check(seconds < 2.0, "/api/state/progress отвечает быстро", f"замер {seconds:.2f} с")

    code, raw, _ = http_get("/api/i18n", timeout=5.0)
    data = as_json(raw) if code == 200 else None
    if check(code == 200 and isinstance(data, dict) and data.get("ok") is True,
             "GET /api/i18n отвечает ok", f"код={code}, ответ {raw[:120]!r}"):
        languages = data.get("languages") or []
        check(sorted(languages) == ["en", "ru", "zh"],
              "/api/i18n перечисляет три языка", f"получено {languages!r}")
        check(data.get("default") == "en", "/api/i18n называет язык по умолчанию",
              f"получено {data.get('default')!r}")
        catalogue = data.get("catalogue") or {}
        check(set(catalogue) >= {"en", "ru", "zh"},
              "/api/i18n отдаёт словарь по всем трём языкам", f"получено {sorted(catalogue)}")
        for lang in ("ru", "zh"):
            table = catalogue.get(lang) or {}
            check(len(table) >= 1, f"/api/i18n: словарь {lang} непуст", f"записей: {len(table)}")
            server_map = table.get("_server")
            check(isinstance(server_map, dict) and bool(server_map),
                  f"/api/i18n: словарь {lang} несёт _server", f"получено {type(server_map).__name__}")
        # Для русского карты совпадать с исходным текстом ЗАКОННО: русский
        # ответ остаётся русским. А вот китайская карта не смеет отдавать
        # русский текст — это и есть проверка.
        cyrillic = []
        for source, value in (((catalogue.get("zh") or {}).get("_server")) or {}).items():
            if (not isinstance(value, str) or not value.strip() or value == source
                    or CYRILLIC_RX.search(value)):
                cyrillic.append(f"{source!r} -> {value!r}")
        check(not cyrillic, "/api/i18n: _server zh не отдаёт русский текст",
              f"не переведено: {len(cyrillic)}: {brief_keys(cyrillic)}")
        if i18n is not None:
            # Сверка с текущим исходником имеет смысл только тогда, когда на
            # порту отвечает та же ревизия: панель, запущенная до правки, даёт
            # расхождение, которое дефектом кода не является.
            current = getattr(i18n, "TRANSLATIONS", {}) or {}
            live_keys = set(catalogue.get("ru") or {}) - {"_server"}
            current_keys = set(current.get("ru") or {})
            if live_keys != current_keys:
                note("на порту отвечает другая ревизия панели",
                     f"каталог ru: {len(live_keys)} записей против {len(current_keys)} в исходнике — "
                     "перезапустите панель; сверка _server с текущим исходником не выполнялась")
            else:
                server_raw = getattr(i18n, "SERVER_TEXT", {}) or {}
                for lang in ("ru", "zh"):
                    server_map = (catalogue.get(lang) or {}).get("_server") or {}
                    missing = [r for r in server_raw if r not in server_map]
                    check(not missing, f"/api/i18n: _server покрывает SERVER_TEXT ({lang})",
                          f"нет: {brief_keys(missing)}")

    code, js, _ = http_get("/app.js", timeout=5.0)
    check(code == 200 and len(js) > 1000, "GET /app.js отдаётся", f"код={code}, длина {len(js)}")
    code, css, _ = http_get("/style.css", timeout=5.0)
    check(code == 200 and len(css) > 1000, "GET /style.css отдаётся", f"код={code}, длина {len(css)}")

    code, html, _ = http_get("/", timeout=5.0)
    check(code == 200 and "RAGRAF" in html, "страница отдаётся", f"код={code}")
    _check_switcher(html, js)


def switcher_selectors(html: str, js: str) -> list[str]:
    """Признаки переключателя языка: элемент разметки или его поиск в клиенте."""
    found: list[str] = []
    if re.search(r"""(?is)<select[^>]*\bid\s*=\s*["'][^"']*lang""", html):
        found.append("select с id, содержащим lang")
    if re.search(r"""(?is)(?:class|name)\s*=\s*["'][^"']*\blang""", html):
        found.append("элемент с class/name, содержащим lang")
    for looked_up in re.findall(r"""getElementById\(\s*["']([^"']+)["']\s*\)""", js or ""):
        if "lang" in looked_up.lower() and re.search(
                rf"""\bid\s*=\s*["']{re.escape(looked_up)}["']""", html):
            found.append(f"клиент ищет #{looked_up}, элемент есть в разметке")
    if re.search(r"(?is)language[-_]?(?:switch|select|picker|menu)", html + (js or "")):
        found.append("имя language-switch в разметке или клиенте")
    return found


def _check_switcher(html: str, js: str) -> None:
    found = switcher_selectors(html, js)
    check(bool(found), "на странице есть переключатель языка",
          "ни элемента выбора языка, ни его поиска в клиенте не найдено")


# ------------------------------------------------------------------ E

def child_env() -> dict:
    """Окружение без PYTHONIOENCODING: именно так панель запускает администратор."""
    env = dict(os.environ)
    for name in ("PYTHONIOENCODING", "PYTHONUTF8", "PYTHONLEGACYWINDOWSSTDIO"):
        env.pop(name, None)
    # Баннер иначе остаётся в буфере трубы до конца процесса; кодировку это
    # не меняет, поэтому авария cp1252 воспроизводится по-прежнему.
    env["PYTHONUNBUFFERED"] = "1"
    return env


#: Печать кириллицы при cp1252 — та самая авария, ради которой сделан console_utf8.
CP1252_PRINT = (
    "import sys\n"
    "sys.stdout.reconfigure(encoding='cp1252', errors='strict')\n"
    "print('\\u041f\\u0440\\u043e\\u0432\\u0435\\u0440\\u043a\\u0430: RAGRAF \\u2014 "
    "\\u0438\\u043d\\u0442\\u0435\\u0440\\u0444\\u0435\\u0439\\u0441')\n"
)
CP1252_PRINT_FIXED = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from ragraf import admin_server as m\n"
    "sys.stdout.reconfigure(encoding='cp1252', errors='strict')\n"
    "m.console_utf8()\n"
    "print('\\u041f\\u0440\\u043e\\u0432\\u0435\\u0440\\u043a\\u0430: RAGRAF \\u2014 "
    "\\u0438\\u043d\\u0442\\u0435\\u0440\\u0444\\u0435\\u0439\\u0441')\n"
)


def run_probe(args: list[str], timeout: float = 60.0):
    try:
        return subprocess.run(args, cwd=str(REPO), env=child_env(), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except Exception as exc:
        return exc


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def section_e() -> None:
    print("\n=== E. Запуск панели без PYTHONIOENCODING (консоль cp1252) ===")
    if SKIP_STARTUP:
        skip("запуск дочернего процесса", "раздел пропущен ключом --skip-startup")
        return
    if not pathlib.Path(CHILD_PYTHON).exists():
        skip("запуск дочернего процесса", f"интерпретатор не найден: {CHILD_PYTHON}")
        return

    control = run_probe([CHILD_PYTHON, "-c", CP1252_PRINT])
    if isinstance(control, Exception):
        skip("воспроизведение аварии кодировки", f"{type(control).__name__}: {control}")
    elif control.returncode != 0:
        info("авария воспроизводится: печать кириллицы в cp1252 падает",
             "UnicodeEncodeError — как в исходном дефекте")
    else:
        skip("воспроизведение аварии кодировки",
             "печать кириллицы в cp1252 здесь не падает — среда не та")

    fixed = run_probe([CHILD_PYTHON, "-c", CP1252_PRINT_FIXED, str(SRC)])
    if isinstance(fixed, Exception):
        check(False, "console_utf8() снимает аварию кодировки",
              f"{type(fixed).__name__}: {fixed}")
    else:
        check(fixed.returncode == 0, "console_utf8() снимает аварию кодировки",
              f"код={fixed.returncode}; stderr: {(fixed.stderr or '').strip()[-200:]}")

    port = free_port()
    command = [CHILD_PYTHON, str(REPO / "tools" / "ragraf-admin.py"),
               f"--port={port}", "--no-browser"]
    info("команда", " ".join(command))
    try:
        proc = subprocess.Popen(command, cwd=str(REPO), env=child_env(),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace", bufsize=1)
    except Exception as exc:
        check(False, "панель запускается дочерним процессом", f"{type(exc).__name__}: {exc}")
        return

    out: list[str] = []
    err: list[str] = []

    def drain(pipe, sink: list[str]) -> None:
        try:
            for line in pipe:
                sink.append(line)
        except Exception:
            pass
        finally:
            try:
                pipe.close()
            except Exception:
                pass

    threads = [threading.Thread(target=drain, args=(proc.stdout, out), daemon=True),
               threading.Thread(target=drain, args=(proc.stderr, err), daemon=True)]
    for thread in threads:
        thread.start()

    healthy = False
    deadline = time.time() + 25.0
    while time.time() < deadline and proc.poll() is None:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
                healthy = response.status == 200 and bool(payload.get("ok"))
                if healthy:
                    break
        except Exception:
            time.sleep(0.3)
    time.sleep(0.4)

    died = proc.poll() is not None
    check(not died, "процесс панели не умер при запуске",
          f"код выхода={proc.returncode}")
    check(healthy, f"GET /api/health отвечает 200 на порту {port}",
          "сервер не ответил за 25 секунд")
    stdout_text = "".join(out)
    stderr_text = "".join(err)
    banner = "RAGRAF" in stdout_text and (
        f"127.0.0.1:{port}" in stdout_text or "запущен" in stdout_text
        or "control panel" in stdout_text or "started" in stdout_text)
    check(banner, "при старте печатается баннер",
          f"stdout: {stdout_text.strip()[:200]!r}")
    if died or not healthy:
        print("  [FAIL] stderr процесса панели:")
        print("    " + (stderr_text.strip()[-1500:].replace("\n", "\n    ") or "(пусто)"))
        if "No module named" in stderr_text:
            note("интерпретатору не хватает зависимостей проекта",
                 f"перезапустите с --python={REPO / '.venv' / 'Scripts' / 'python.exe'}")

    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
    check(proc.returncode is not None, "процесс панели погашен проверкой",
          f"код выхода={proc.returncode}")
    for thread in threads:
        thread.join(timeout=3)
    lines = [line for line in stdout_text.splitlines() if line.strip()]
    title = next((line for line in lines if "RAGRAF" in line), lines[0] if lines else "")
    info("баннер панели", title.strip())


# ------------------------------------------------------------------ main

def main() -> int:
    if "--help" in sys.argv[1:] or "-h" in sys.argv[1:]:
        print(USAGE)
        return 0
    print("=== RAGRAF: переводы панели и починка /api/state ===")
    print(f"корень:        {REPO}")
    print(f"интерпретатор: {sys.executable}")

    i18n = import_module("i18n")
    admin = import_module("admin_server")

    section_a(i18n)
    section_b(i18n, admin)
    section_c(i18n, admin)
    section_d(i18n)
    section_e()

    print("\n=== ИТОГ ===")
    tail = f", пропущено: {SKIP}" if SKIP else ""
    print(f"пройдено: {PASS}, провалено: {FAIL}{tail}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
