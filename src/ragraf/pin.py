"""Пиннинг подписи операций: хеш описания, наблюдение, события расхождения.

Зачем. Поверхность продукта (какие операции есть, какие у них параметры с
умолчаниями, какие ключи ответа) до сих пор описывалась данными, но никто не
следил за тем, что КЛИЕНТ вызывает именно то описание, на которое рассчитывал.
Переименовали параметр, поменяли умолчание, убрали ключ ответа — снаружи это
выглядит как «индекс врёт», а не как «изменился контракт».

Что здесь есть и чего нет:

  * описание операции берётся из `surface.ops_of` — ВТОРОЙ реализации описаний
    не заводится: два описания расходятся так же, как любые две копии;
  * хеш считается по канонической форме (сортировка, без пробелов), поэтому
    одинаковый код даёт одинаковый хеш, а изменение умолчания — другой;
  * пин ставится человеком на релиз (`approve`), автоматического подтверждения
    нет: молчаливая самоподпись скрывала бы ровно то, ради чего пин нужен;
  * события живут в `control.sqlite`: `pin_mismatch` (описание разошлось с пином)
    и `pin_break` (после расхождения пришёл вызов, которого уже нет в описании);

Границы, названные прямо:

  * хеш описывает ОБЪЯВЛЕНИЕ в коде, а не поведение: операция может отвечать
    иначе при том же объявлении — это не ловится и не обещается;
  * ключи ответа видны только там, где ответ собран литералом; у операции с
    `returns_from = "variable"` в хеш входит факт «собирается из переменной»,
    а не список ключей;
  * `pin_break` пишется только ПОСЛЕ расхождения: до него неизвестное имя —
    обычная ошибка вызова, а не поломка контракта.

Автор: Mikhail Deynekin
Сайт: https://Deynekin.com
Почта: Mikhail@Deynekin.com
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

SCHEMA = 1

#: Закрытый список видов события. Вид вне списка — ошибка вызывающего кода.
EVENT_KINDS: tuple[str, ...] = ("pin_mismatch", "pin_break")

#: Закрытый список причин `pin_break`.
BREAK_REASONS: tuple[str, ...] = ("unknown_name", "bad_arguments")

#: Виды контракта. `op` — операция фасада (то, что зовёт агент через `query`),
#: `tool` — инструмент MCP (то, что зовёт плагин). Один механизм, два набора:
#: инструмент можно переименовать или сузить его схему, не тронув ни одной
#: операции, и без второго набора это оставалось незамеченным.
KINDS: tuple[str, ...] = ("op", "tool")

#: Виды записей истории. `sample` — смена ПОВЕДЕНИЯ (X15): у неё нет описания
#: и хеша формы, поэтому в `KINDS` ей нельзя, а в истории — нужно.
HISTORY_KINDS: tuple[str, ...] = KINDS + ("sample",)

#: Уровень события: у расхождения он выше, потому что это сигнал о контракте.
EVENT_LEVEL = {"pin_mismatch": "high", "pin_break": "medium"}

#: Срок хранения истории смен. Два правила сразу: последние
#: `HISTORY_KEEP_PER_OBJECT` смен на объект хранятся всегда (иначе свежая
#: правка вытеснит историю другого объекта), и всё, что было за
#: `HISTORY_KEEP_DAYS` дней, тоже хранится. Всё остальное — прошлое, которое
#: никому уже не нужно.
HISTORY_KEEP_PER_OBJECT = 20
HISTORY_KEEP_DAYS = 180

#: Ключи ответа, которые меняются при КАЖДОМ вызове и к поведению не относятся.
#: Без этого списка проба сравнивала бы ревизию, свежесть, пути журнала и номер
#: сеанса: два вызова подряд на неизменном коде всегда давали бы «расхождение»,
#: и сигнал утонул бы в шуме (замерено набором при первом заходе).
SAMPLE_VOLATILE = (
    "index_rev", "freshness", "drift", "capability", "edition", "features",
    "edition_notes", "provenance", "limits", "stale_warnings", "next_action",
    "ts", "at", "taken_at", "checked_at", "started_at", "finished_at",
    "duration_ms", "elapsed_ms", "wall_ms", "age_seconds", "run_id", "session",
    "tmp", "lock", "pid",
)

#: Что вырезается из строк ответа перед хешированием. Ключи — выше, значения —
#: здесь: строка может быть путём, отметкой или номером прогона, и такой хеш
#: поедет между машинами без всякой правки кода (замер X17).
_PATH_SPLIT = re.compile(r"[\\/]+")
_REV_TOKEN = re.compile(r"\brev[-_][0-9A-Za-z]+")
_HEX_TOKEN = re.compile(r"\b[0-9a-f]{12,}\b")
_ABS_TOKEN = re.compile(r"(?:[A-Za-z]:[\\/]|/)(?:[^\s\"',;:]+)")


def clean_text(value: str) -> str:
    """Строка без машинно-зависимых частей: путь → имя, ревизия → `rev-*`.

    Зачем. Хеш пробы сравнивают между двумя корпусами и двумя машинами. Абсолютный
    путь (`C:\\AutoClaw\\...`, `/home/u/...`), номер ревизии и длинный хеш в тексте
    отказа делают хеш разным при ОДНОМ И ТОМ ЖЕ коде — то есть сообщают об
    изменении поведения там, где его нет.

    Что остаётся: имена файлов без каталогов, текст ошибки, коды. Имя файла
    осмысленно (`ragraf.yaml` не равен `corpus.yaml`), а каталог — нет.
    """
    text = str(value)
    if not text:
        return text
    # Абсолютный путь целиком заменяется последним сегментом — один раз на
    # вхождение, чтобы не терять остальной текст ошибки.
    def _strip_path(match):
        raw = match.group(0)
        parts = [part for part in _PATH_SPLIT.split(raw) if part and part not in (".", "..")]
        return parts[-1] if parts else "<путь>"

    text = _ABS_TOKEN.sub(_strip_path, text)
    text = _REV_TOKEN.sub("rev-*", text)
    text = _HEX_TOKEN.sub("<хеш>", text)
    return text


#: Что пробуется по умолчанию: ФАКТИЧЕСКОЕ имя метода фасада, аргументы и
#: условие применимости.
#:
#: Почему `method`, а не `op_<имя>`: замер 10.11.2026 — `usage` в командной
#: строке вызывает `usage_summary`, то есть собирательное имя из `op_<имя>` для
#: него не существует; проба честно давала `not_sampled` на одном из четырёх
#: пунктов, и это никто не видел. Теперь список объявляет имена явно.
#:
#: `needs` — что должно быть готово, иначе проба не притворяется, а говорит
#: `not_sampled`: `config` — только выбранный корпус, `index` — собранный
#: индекс, `journal` — журнал обращений.
PROBES: dict[str, dict[str, Any]] = {
    "catalog": {
        "method": "op_catalog", "args": {}, "needs": "config",
        "why": "список корпусов и выбор: отвечает без индекса",
    },
    "freshness": {
        "method": "op_freshness", "args": {"force": False}, "needs": "index",
        "why": "свежесть — первое, что врёт после правки кода",
    },
    "index_status": {
        "method": "op_index_status", "args": {}, "needs": "index",
        "why": "ревизия и покрытие: форма ответа меняется при правках индексатора",
    },
    "route": {
        "method": "op_route", "args": {"query": "ragraf", "limit": 3},
        "needs": "config",
        "why": ("единственная проба С АРГУМЕНТАМИ: проверяет, что они вообще "
                "доходят и попадают в хеш"),
    },
    "usage": {
        "method": "usage_summary", "args": {"since_session": False},
        "needs": "journal",
        "why": "счётчики журнала: сводка меняется при правках учёта",
    },
}

PIN_DDL = """
CREATE TABLE IF NOT EXISTS op_pin (
  op          TEXT PRIMARY KEY,
  hash        TEXT NOT NULL,
  approved_at TEXT NOT NULL,
  approved_by TEXT NOT NULL,
  note        TEXT,
  kind        TEXT NOT NULL DEFAULT 'op'
);
CREATE TABLE IF NOT EXISTS pin_event (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  ts              TEXT NOT NULL,
  kind            TEXT NOT NULL,
  level           TEXT NOT NULL,
  op              TEXT NOT NULL,
  observed        TEXT,
  pinned          TEXT,
  pin_age_seconds INTEGER,
  detail          TEXT,
  related_id      INTEGER,
  contract        TEXT NOT NULL DEFAULT 'op'
);
CREATE INDEX IF NOT EXISTS idx_pin_event_op ON pin_event(op, id);
CREATE TABLE IF NOT EXISTS pin_history (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  op            TEXT NOT NULL,
  kind          TEXT NOT NULL DEFAULT 'op',
  hash          TEXT NOT NULL,
  approved_at   TEXT NOT NULL,
  approved_by   TEXT NOT NULL,
  note          TEXT,
  previous_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_pin_history_op ON pin_history(op, id);
CREATE TABLE IF NOT EXISTS pin_sample (
  op          TEXT NOT NULL,
  args_hash   TEXT NOT NULL,
  value_hash  TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'sampled',
  detail      TEXT,
  taken_at    TEXT NOT NULL,
  verdict     TEXT,
  checked_at  TEXT,
  value_json  TEXT,
  paths_json  TEXT,
  PRIMARY KEY (op, args_hash)
);
"""

#: Кэш описаний: (файл фасада, время и размер) → {операция: хеш}.
#: Размер в ключе не для красоты: две записи подряд в одну секунду дают
#: одинаковое время, и правка того же размера вернула бы старое описание
#: (замерено набором: правка текста описания не меняла хеш).
_CACHE: dict[tuple[str, float, int], dict[str, str]] = {}

#: Тот же кэш для инструментов MCP: другой файл, другой набор.
_TOOL_CACHE: dict[tuple[str, float, int], dict[str, str]] = {}


class PinRefusal(ValueError):
    """Действие с пином невозможно по названной причине."""


def canonical(payload: Any) -> str:
    """Каноническая запись для хеша: сортировка ключей, без пробелов."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _stamp(path: Path) -> tuple[float, int]:
    """Отпечаток файла для кэша описаний: время и размер.

    Одного времени мало: две записи в одну секунду дают одинаковое значение, и
    кэш вернул бы старое описание. Размер меняется почти при любой правке, а
    правка без изменения размера безвредна (тот же текст).
    """
    try:
        info = path.stat()
    except OSError:
        return (0.0, 0)
    return (info.st_mtime, int(info.st_size))


def _facade_path(root: str | Path) -> Path:
    """Файл с ОБЪЯВЛЕНИЕМ операций, которые обслуживают вызов.

    Сначала дерево, в котором работает сервер (`<root>/src/ragraf/facade.py`):
    так его видят прогоны из исходников и сам корпус RAGRAF. Если файла нет —
    а у любого другого корпуса его и не бывает, корпус это чужой проект —
    описание берётся у поставки: `facade.py` рядом с этим модулем.

    Почему не «просто корпус»: замерено 10.11.2026 — на корпусах `RevoAp` и
    `AMD` функция `hashes` отказывала («facade.py не прочитан»), наблюдение
    возвращало пустую строку, и расхождение не возникало НИКОГДА. Пин описывает
    контракт вызванного кода, а не проекта, который индексируется.
    """
    tree = Path(root) / "src" / "ragraf" / "facade.py"
    if tree.is_file():
        return tree
    return Path(__file__).resolve().parent / "facade.py"


def description(op: dict[str, Any]) -> dict[str, Any]:
    """Каноническое описание одной операции: имя, параметры, ключи ответа.

    Сортировка ключей ответа — часть формы, а не украшение: порядок ключей в
    литерале не должен менять хеш, иначе правка форматирования выглядела бы
    сменой контракта.
    """
    params = []
    for item in op.get("params") or []:
        entry: dict[str, Any] = {"name": str(item.get("name") or ""),
                                 "type": str(item.get("type") or "")}
        if "default" in item:
            entry["default"] = str(item.get("default"))
        if item.get("keyword"):
            entry["keyword"] = True
        params.append(entry)
    return {
        "name": str(op.get("name") or ""),
        "params": params,
        "returns": sorted(str(key) for key in (op.get("returns") or [])),
        "returns_from": str(op.get("returns_from") or ""),
    }


def hashes(root: str | Path) -> dict[str, str]:
    """Хеши описаний всех операций РАБОТАЮЩЕГО кода (`surface.ops_from_file`).

    `root` — корень корпуса: из него берётся дерево исходников, если оно там
    есть (см. `_facade_path`). Второго разбора фасада нет — только `surface`.
    """
    from . import surface

    facade = _facade_path(root)
    stamp = _stamp(facade)
    key = (str(facade), *stamp)
    cached = _CACHE.get(key)
    if cached is not None:
        return dict(cached)
    try:
        ops = surface.ops_from_file(facade)
    except Exception as exc:  # noqa: BLE001 — причина называется выше по стеку
        raise PinRefusal(f"описания операций не прочитаны: {type(exc).__name__}: {exc}") from exc
    out = {str(op["name"]): hashlib.sha256(
        canonical(description(op)).encode("utf-8")).hexdigest() for op in ops}
    # Кэш держит только последнее состояние: корпусов в процессе немного, а
    # мусор в памяти не нужен.
    _CACHE.clear()
    _CACHE[key] = out
    return dict(out)


def hash_of(root: str | Path, op: str) -> str:
    """Хеш описания одной операции. Пусто — операции нет в описании."""
    return hashes(root).get(str(op), "")


def _server_path(root: str | Path) -> Path:
    """Файл с объявлениями инструментов MCP (то же правило, что у `_facade_path`)."""
    tree = Path(root) / "src" / "ragraf" / "mcp_server.py"
    if tree.is_file():
        return tree
    return Path(__file__).resolve().parent / "mcp_server.py"


def _schema_form(node: Any) -> Any:
    """Схема входа без словесных описаний, с сортированными ключами.

    Поле `description` в контракт НЕ входит: это формулировка, а не форма.
    Правка опечатки в тексте не должна останавливать выпуск — так же, как
    тексты ошибок не входят в описание операции. Это названная граница, а не
    забытость (см. документ, § «Чего пин не видит»).
    """
    if isinstance(node, dict):
        return {str(key): _schema_form(node[key]) for key in sorted(node)
                if key != "description"}
    if isinstance(node, list):
        return [_schema_form(item) for item in node]
    return node


def tool_description(tool: dict[str, Any]) -> dict[str, Any]:
    """Каноническое описание инструмента: имя, схема входа, пометки безопасности."""
    annotations = {str(key): bool(value) for key, value in
                   sorted((tool.get("annotations") or {}).items())
                   if key.endswith("Hint")}
    return {"name": str(tool.get("name") or ""),
            "schema": _schema_form(tool.get("schema") or {}),
            "annotations": annotations}


def tool_hashes(root: str | Path) -> dict[str, str]:
    """Хеши описаний всех инструментов MCP работающего сервера."""
    from . import surface

    path = _server_path(root)
    stamp = _stamp(path)
    key = (str(path), *stamp)
    cached = _TOOL_CACHE.get(key)
    if cached is not None:
        return dict(cached)
    try:
        tools = surface.tools_from_file(path)
    except Exception as exc:  # noqa: BLE001 — причина называется выше по стеку
        raise PinRefusal(
            f"описания инструментов не прочитаны: {type(exc).__name__}: {exc}") from exc
    out = {str(tool["name"]): hashlib.sha256(
        canonical(tool_description(tool)).encode("utf-8")).hexdigest()
        for tool in tools}
    _TOOL_CACHE.clear()
    _TOOL_CACHE[key] = out
    return dict(out)


def tool_hash_of(root: str | Path, name: str) -> str:
    """Хеш описания одного инструмента. Пусто — такого инструмента нет."""
    return tool_hashes(root).get(str(name), "")


def descriptions(root: str | Path) -> dict[str, dict[str, Any]]:
    """Канонические описания операций — ровно те, из которых считается хеш.

    Нужны поставке (`ops_pins.json`): без них сводка изменений между выпусками
    видела бы только «хеш другой» и не могла бы отличить исчезнувший параметр
    от добавленного необязательного.
    """
    from . import surface

    return {str(op["name"]): description(op)
            for op in surface.ops_from_file(_facade_path(root))}


def tool_descriptions(root: str | Path) -> dict[str, dict[str, Any]]:
    """Канонические описания инструментов — та же форма, что в хеше."""
    from . import surface

    return {str(tool["name"]): tool_description(tool)
            for tool in surface.tools_from_file(_server_path(root))}


def hashes_of(root: str | Path, kind: str = "op") -> dict[str, str]:
    """Хеши описаний одного вида контракта: `op` — операции, `tool` — инструменты."""
    if kind not in KINDS:
        raise PinRefusal(f"вид контракта {kind!r} вне списка: {', '.join(KINDS)}")
    return hashes(root) if kind == "op" else tool_hashes(root)


def hash_of_kind(root: str | Path, name: str, kind: str = "op") -> str:
    """Хеш описания объекта контракта по виду."""
    return hashes_of(root, kind).get(str(name), "")


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Создать таблицы пинов и событий в существующем журнале.

    Колонка `kind` добавляется ДОГОНЯЮЩЕЙ миграцией: старые журналы не требуют
    удаления, а их записи читаются как `op` — до X10 других видов и не было.
    """
    conn.executescript(PIN_DDL)
    # У `op_pin` вид контракта — `kind`; у `pin_event` `kind` уже занято видом
    # события (pin_mismatch/pin_break), поэтому там колонка называется `contract`.
    for table, column in (("op_pin", "kind"), ("pin_event", "contract")):
        try:
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.Error:
            continue
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT NOT NULL DEFAULT 'op'")
    # Очищенный ответ пробы (X15): нужен, чтобы сказать ГДЕ разошлось, а не только
    # что разошлось. Старые журналы получают колонку той же догоняющей миграцией.
    try:
        sample_columns = {row[1] for row in conn.execute("PRAGMA table_info(pin_sample)")}
        if sample_columns and "value_json" not in sample_columns:
            conn.execute("ALTER TABLE pin_sample ADD COLUMN value_json TEXT")
        if sample_columns and "paths_json" not in sample_columns:
            conn.execute("ALTER TABLE pin_sample ADD COLUMN paths_json TEXT")
    except sqlite3.Error:
        pass
    conn.commit()


def _now() -> str:
    from . import util

    return util.now_iso()

def _age_seconds(stamp: str) -> int | None:
    """Возраст пина в секундах. Не разобралось — None, а не ноль."""
    from . import util

    moment = getattr(util, "parse_iso", None)
    if moment is None:
        return None
    try:
        parsed = moment(stamp)
    except Exception:  # noqa: BLE001 — формат мог быть чужим
        return None
    if parsed is None:
        return None
    return max(0, int(time.time() - parsed.timestamp()))


def age_seconds(stamp: str) -> int | None:
    """Возраст пина по отметке времени — публичное имя той же логики.

    Нужно сборщику поставки и воротам: возраст считается в ОДНОМ месте, иначе
    две редакции одного числа разойдутся.
    """
    return _age_seconds(stamp)


def pins(conn: sqlite3.Connection, kind: str = "op") -> dict[str, dict[str, Any]]:
    """Пины корпуса одного вида: имя → {hash, approved_at, approved_by, note}.

    Старый журнал без колонки `kind` читается как «все записи — операции»:
    до X10 других видов контракта и не было.
    """
    try:
        rows = conn.execute(
            "SELECT op, hash, approved_at, approved_by, note FROM op_pin WHERE kind=?",
            (str(kind),)).fetchall()
    except sqlite3.Error:
        try:
            rows = conn.execute(
                "SELECT op, hash, approved_at, approved_by, note FROM op_pin").fetchall()
        except sqlite3.Error:
            return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        out[str(row[0])] = {"hash": str(row[1]),
                            "approved_at": str(row[2]),
                            "approved_by": str(row[3]),
                            "note": str(row[4] or "")}
    return out


def approve(conn: sqlite3.Connection, root: str | Path, *, op: str = "",
            everything: bool = False, by: str = "", note: str = "",
            kind: str = "op") -> dict[str, Any]:
    """Подтвердить текущее описание человеком. Автоподтверждения нет.

    Один пин — `op`; все сразу — `everything`. Оба сразу или ни одного — отказ:
    «подтвердить всё и ещё вот это» звучало бы как одно действие, а делает разное.

    `kind="all"` подтверждает оба набора сразу (операции и инструменты): это и
    означает «контракт принят целиком», и других значений у `all` нет.
    """
    if bool(op) == bool(everything):
        raise PinRefusal("нужно выбрать одно: имя операции или подтверждение всех")
    if not str(by).strip():
        raise PinRefusal("не назван подтверждающий: пин без автора не отличается от самоподписи")
    kinds = KINDS if kind == "all" else (kind,)
    for item in kinds:
        if item not in KINDS:
            raise PinRefusal(f"вид контракта {item!r} вне списка: {', '.join(KINDS)}")
    ensure_schema(conn)
    now = _now()
    changed = []
    total = 0
    for item in kinds:
        current = hashes_of(root, item)
        targets = sorted(current) if everything else [op]
        if not everything and op not in current:
            if kind == "all":
                continue
            _missing(item, op)
        before = pins(conn, item)
        for name in targets:
            digest = current[name]
            old = (before.get(name) or {}).get("hash") or ""
            # История — только при СМЕНЕ хеша: повторное подтверждение того же
            # описания ничего не меняет и строку не добавляет, иначе журнал
            # смен заполнился бы одинаковыми записями.
            if old != digest:
                conn.execute(
                    "INSERT INTO pin_history(op, kind, hash, approved_at, approved_by,"
                    " note, previous_hash) VALUES(?,?,?,?,?,?,?)",
                    (name, item, digest, now, str(by).strip(), str(note or ""), old))
            conn.execute(
                "INSERT INTO op_pin(op, hash, approved_at, approved_by, note, kind)"
                " VALUES(?,?,?,?,?,?)"
                " ON CONFLICT(op) DO UPDATE SET hash=excluded.hash,"
                " approved_at=excluded.approved_at, approved_by=excluded.approved_by,"
                " note=excluded.note, kind=excluded.kind",
                (name, digest, now, str(by).strip(), str(note or ""), item))
            changed.append({"op": name, "kind": item, "hash": digest, "previous": old})
            total += 1
    if not total and not everything:
        raise PinRefusal(f"имени {op!r} нет ни в одном описании контракта: подтверждать нечего")
    conn.commit()
    # Чистка истории — часть записи, а не отдельный таймер: так её результат
    # виден там же, где появились строки, и не может пройти незамеченной.
    trimmed = prune(conn)
    return {"approved": changed, "total": total, "at": now, "history": trimmed}


def _missing(kind: str, op: str):
    """Отказ «такого имени нет в описании» — с названием вида контракта."""
    what = "операции" if kind == "op" else "инструмента"
    raise PinRefusal(f"{what} {op!r} нет в описании поверхности: подтверждать нечего")


def observe(conn: sqlite3.Connection, op: str, observed: str, *,
            root: str | Path, kind: str = "op") -> dict[str, Any] | None:
    """Сравнить наблюдённый хеш с пином. Вернуть событие, если оно новое.

    Событие пишется только при расхождении и только один раз на пару
    «объект + наблюдённый хеш»: иначе один сломанный клиент забил бы журнал
    тысячей одинаковых записей, и расхождение перестало бы быть заметным.

    Вид контракта — часть ключа: у операции и инструмента имена не совпадают,
    но журнал один, и события разных видов не должны глушить друг друга.
    """
    if not observed:
        return None
    ensure_schema(conn)
    item = pins(conn, kind).get(str(op)) or {}
    pinned = str(item.get("hash") or "")
    if not pinned or pinned == observed:
        return None
    # Возраст считается ЗДЕСЬ: он относится к пину, а не к вызову, и передавать
    # его снаружи значило бы разрешить вызывающему назвать любое число.
    pin_age = _age_seconds(str(item.get("approved_at") or ""))
    try:
        already = conn.execute(
            "SELECT id FROM pin_event WHERE kind='pin_mismatch' AND op=? AND observed=?"
            " AND contract=? ORDER BY id DESC LIMIT 1",
            (str(op), str(observed), str(kind))).fetchone()
    except sqlite3.Error:
        already = None
    if already is not None:
        return None
    what = "операции" if kind == "op" else "инструмента"
    conn.execute(
        "INSERT INTO pin_event(ts, kind, level, op, observed, pinned, pin_age_seconds,"
        " detail, related_id, contract) VALUES(?,?,?,?,?,?,?,?,NULL,?)",
        (_now(), "pin_mismatch", EVENT_LEVEL["pin_mismatch"], str(op), str(observed),
         pinned, pin_age, f"описание {what} разошлось с подтверждённым пином",
         str(kind)))
    conn.commit()
    row = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
    return {"id": int(row[0]), "op": str(op), "kind": str(kind),
            "observed": str(observed), "pinned": pinned,
            "pin_age_seconds": pin_age}


def _latest_mismatch(conn: sqlite3.Connection, name: str,
                     kind: str = "op") -> tuple[int, str, bool] | None:
    """Последнее расхождение: (id, имя, это расхождение ТОГО ЖЕ имени).

    Сначала ищется расхождение самого объекта: связать ошибку аргументов
    `search` с расхождением `repo_map` значило бы назвать чужую причину. Если
    своего нет — а у имени, которого уже нет в описании, пина быть не может —
    берётся последнее по журналу того же вида контракта, и связь помечается
    в `detail`.
    """
    try:
        row = conn.execute(
            "SELECT id, op FROM pin_event WHERE kind='pin_mismatch' AND op=?"
            " AND contract=? ORDER BY id DESC LIMIT 1",
            (str(name), str(kind))).fetchone()
        if row is not None:
            return int(row[0]), str(row[1]), True
        row = conn.execute(
            "SELECT id, op FROM pin_event WHERE kind='pin_mismatch' AND contract=?"
            " ORDER BY id DESC LIMIT 1", (str(kind),)).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    return int(row[0]), str(row[1]), False


def note_break(conn: sqlite3.Connection, name: str, *, reason: str,
               detail: str = "", kind: str = "op") -> dict[str, Any] | None:
    """Записать `pin_break`: ПЕРВЫЙ неуспешный вызов после расхождения.

    До расхождения неизвестное имя — обычная ошибка вызова, и шуметь о ней в
    журнале контракта нечего: пин ещё ничего не обещал.

    Именно ПЕРВЫЙ, а не каждый: клиент, живущий по старому контракту, ошибается
    на каждом обращении, и тысяча одинаковых записей превратила бы событие в
    шум. Ключ повтора — пара «расхождение + операция + причина»: новое
    расхождение снова даёт одну запись, другая операция или другая причина —
    тоже. Так в журнале остаётся ровно то, ради чего он нужен: после какого
    расхождения и какой вызов сломался первым.
    """
    if reason not in BREAK_REASONS:
        raise PinRefusal(f"причина {reason!r} вне списка: {', '.join(BREAK_REASONS)}")
    ensure_schema(conn)
    related = _latest_mismatch(conn, str(name), kind)
    if related is None:
        return None
    related_id, related_op, same_op = related
    try:
        already = conn.execute(
            "SELECT id FROM pin_event WHERE kind='pin_break' AND related_id=?"
            " AND op=? AND detail LIKE ? LIMIT 1",
            (related_id, str(name), f"{reason}%")).fetchone()
    except sqlite3.Error:
        already = None
    if already is not None:
        return None
    text = f"{reason}: {detail}".strip(": ")
    if not same_op:
        text += f" (расхождение по {'операции' if kind == 'op' else 'инструменту'} {related_op})"
    conn.execute(
        "INSERT INTO pin_event(ts, kind, level, op, observed, pinned, pin_age_seconds,"
        " detail, related_id, contract) VALUES(?,?,?,?,NULL,NULL,NULL,?,?,?)",
        (_now(), "pin_break", EVENT_LEVEL["pin_break"], str(name), text,
         related_id, str(kind)))
    conn.commit()
    row = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
    return {"id": int(row[0]), "op": str(name), "kind": str(kind),
            "reason": reason, "related_id": related_id,
            "related_op": related_op, "same_op": same_op}


def events(conn: sqlite3.Connection, *, limit: int = 50,
           kind: str = "", contract: str = "") -> list[dict[str, Any]]:
    """Последние события пина: от новых к старым.

    `kind` — вид события (`pin_mismatch`/`pin_break`), `contract` — вид контракта
    (`op`/`tool`). Пустая строка — без фильтра по этому полю.
    """
    where: list[str] = []
    args: list[Any] = []
    if kind:
        where.append("kind=?")
        args.append(kind)
    if contract:
        where.append("contract=?")
        args.append(contract)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    try:
        rows = conn.execute(
            "SELECT id, ts, kind, level, op, observed, pinned, pin_age_seconds,"
            " detail, related_id, contract FROM pin_event" + clause +
            " ORDER BY id DESC LIMIT ?", (*args, int(limit))).fetchall()
    except sqlite3.Error:
        return []
    return [{
        "id": int(row[0]), "ts": str(row[1]), "kind": str(row[2]),
        "level": str(row[3]), "op": str(row[4]),
        "observed": str(row[5] or ""), "pinned": str(row[6] or ""),
        "pin_age_seconds": None if row[7] is None else int(row[7]),
        "detail": str(row[8] or ""),
        "related_id": None if row[9] is None else int(row[9]),
        "contract": str(row[10] or "op"),
    } for row in rows]


#: Состояние пина корпуса. Закрытый список: наружу уходит только он.
#:
#: none     — пинов нет: контракт не принят, и ловить пока нечего;
#: agreed   — пины есть, все совпадают, неподтверждённых и устаревших нет;
#: partial  — пины есть, но часть операций не подтверждена (или пин остался
#:            на операции, которой уже нет в описании);
#: diverged — описание разошлось с пином: это и есть событие, ради которого
#:            механизм заведён.
PIN_STATES: tuple[str, ...] = ("none", "agreed", "diverged", "partial")


def state_payload(current: dict[str, str],
                  known: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Состояние набора пинов по данным описания и журнала. Ничего не пишет.

    Одно определение на двух потребителей — операцию `contract_pin_status` и
    ворота выпуска (`tools/release/gate_release.py`): две редакции одного
    вопроса разошлись бы, и «выпуск разрешён» перестало бы значить то же, что
    «состояние agreed».

    Порядок проверок — часть смысла: расхождение важнее неполноты, поэтому
    `diverged` перекрывает `partial`. Обратный порядок показывал бы «набор не
    полон» там, где контракт уже сломан.
    """
    if not known:
        return {"state": "none", "total_ops": len(current), "pinned": 0,
                "unpinned": len(current), "stale": 0, "diverged": [],
                "oldest_op": "", "approved_at": "", "pin_age_seconds": None}
    diverged = [name for name in sorted(current)
                if name in known and known[name]["hash"] != current[name]]
    unpinned = sorted(set(current) - set(known))
    stale = sorted(set(known) - set(current))
    if diverged:
        state = "diverged"
    elif unpinned or stale:
        state = "partial"
    else:
        state = "agreed"
    # «Когда набор приняли» — это САМЫЙ СТАРЫЙ пин: он и отделяет старое
    # одобрение от свежей правки, ради чего возраст и считается.
    oldest_op = min(known, key=lambda item: str(known[item]["approved_at"]))
    approved_at = str(known[oldest_op]["approved_at"])
    return {
        "state": state,
        "total_ops": len(current),
        "pinned": len(known),
        "unpinned": len(unpinned),
        "stale": len(stale),
        "diverged": diverged,
        "oldest_op": oldest_op,
        "approved_at": approved_at,
        "pin_age_seconds": _age_seconds(approved_at),
    }


def history(conn: sqlite3.Connection, *, limit: int = 50,
            kind: str = "", op: str = "") -> list[dict[str, Any]]:
    """История смен набора: от новых к старым.

    Зачем. Пин хранит ОДНУ строку на объект: `approved_at`/`approved_by`
    перезаписываются, и вопрос «кто принял прошлый набор и когда он сменился»
    ответа не имел. Здесь каждая смена — отдельная строка.

    Фильтры необязательны и складываются: `kind` — вид контракта, `op` — имя
    объекта. Пустая сторока фильтра означает «любой», а не «поиск пустого
    имени».
    """
    clauses = []
    args: list[Any] = []
    if str(kind).strip():
        clauses.append("kind=?")
        args.append(str(kind).strip())
    if str(op).strip():
        clauses.append("op=?")
        args.append(str(op).strip())
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    try:
        rows = conn.execute(
            "SELECT id, op, kind, hash, approved_at, approved_by, note, previous_hash"
            " FROM pin_history" + where + " ORDER BY id DESC LIMIT ?",
            (*args, int(limit))).fetchall()
    except sqlite3.Error:
        return []
    return [{
        "id": int(row[0]), "op": str(row[1]), "kind": str(row[2] or "op"),
        "hash": str(row[3]), "approved_at": str(row[4]),
        "approved_by": str(row[5]), "note": str(row[6] or ""),
        "previous_hash": str(row[7] or ""),
        "age_seconds": _age_seconds(str(row[4])),
    } for row in rows]


def _protected_ids(conn: sqlite3.Connection) -> set[int]:
    """Строки истории, на которые ссылаются события журнала.

    Событие говорит «наблюдали X, а пин стоял на Y»; строка истории с этим Y —
    доказательство того, что пин в тот момент был именно такой. Удалить её
    значит потерять связь события с прошлым — чистка этого не делает.
    """
    try:
        rows = conn.execute(
            "SELECT DISTINCT h.id FROM pin_history h"
            " JOIN pin_event e ON e.op = h.op AND e.pinned = h.hash"
            " AND e.contract = h.kind").fetchall()
    except sqlite3.Error:
        return set()
    return {int(row[0]) for row in rows}


def prune(conn: sqlite3.Connection, *, keep_per_object: int = HISTORY_KEEP_PER_OBJECT,
          keep_days: int = HISTORY_KEEP_DAYS) -> dict[str, Any]:
    """Срок хранения истории: что ушло, что осталось и почему.

    Правило: на каждый объект хранятся последние `keep_per_object` смен И все
    смены за `keep_days` дней. Строка, на которую ссылается событие журнала,
    не удаляется никогда — связь события с прошлым дороже чистки.

    Вызывается при подтверждении, а не таймером: чистка — часть записи, и
    отдельного процесса, который может упасть без следа, не заводится.
    """
    ensure_schema(conn)
    try:
        rows = conn.execute(
            "SELECT id, op, kind, approved_at FROM pin_history ORDER BY id DESC").fetchall()
    except sqlite3.Error:
        return {"removed": 0, "kept": 0, "protected": 0, "limit": ""}
    protected = _protected_ids(conn)
    seen: dict[tuple[str, str], int] = {}
    drop: list[int] = []
    for row in rows:
        key = (str(row[1]), str(row[2] or "op"))
        seen[key] = seen.get(key, 0) + 1
        age = _age_seconds(str(row[3]))
        # Неразобранная отметка считается свежей: удалять то, чей возраст
        # неизвестен, — значит терять данные по догадке.
        recent = age is None or age <= int(keep_days) * 86400
        in_window = seen[key] <= int(keep_per_object)
        if in_window or recent:
            continue
        if int(row[0]) in protected:
            continue
        drop.append(int(row[0]))
    if drop:
        conn.executemany("DELETE FROM pin_history WHERE id=?", [(item,) for item in drop])
        conn.commit()
    kept = int(conn.execute("SELECT COUNT(*) FROM pin_history").fetchone()[0]) \
        if _has_table(conn) else 0
    return {
        "removed": len(drop), "kept": kept, "protected": len(protected),
        "keep_per_object": int(keep_per_object), "keep_days": int(keep_days),
        "limit": (f"хранится {int(keep_per_object)} последних смен на объект и все "
                  f"за {int(keep_days)} дней; строки со ссылкой из журнала не "
                  f"удаляются"),
    }


def _has_table(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("SELECT 1 FROM pin_history LIMIT 1").fetchall()
        return True
    except sqlite3.Error:
        return False


def authors(conn: sqlite3.Connection, *, limit: int = 10) -> dict[str, Any]:
    """Кто и что менял: счётчик смен по авторам и по объектам.

    Это счётчик, а не оценка: одна смена может быть крупной правкой, а другая —
    подъёмом версии. Названо здесь, чтобы цифру не приняли за качество работы.
    """
    rows = history(conn, limit=100000)
    by_author: dict[str, int] = {}
    by_object: dict[str, int] = {}
    for item in rows:
        by_author[item["approved_by"]] = by_author.get(item["approved_by"], 0) + 1
        by_object[item["op"]] = by_object.get(item["op"], 0) + 1
    top_author = sorted(by_author.items(), key=lambda pair: (-pair[1], pair[0]))
    top_object = sorted(by_object.items(), key=lambda pair: (-pair[1], pair[0]))
    return {
        "total": len(rows),
        "by_author": [{"by": name, "changes": number} for name, number in top_author[:limit]],
        "by_object": [{"op": name, "changes": number} for name, number in top_object[:limit]],
        "top_author": top_author[0][0] if top_author else "",
        "top_object": top_object[0][0] if top_object else "",
        "top_object_changes": top_object[0][1] if top_object else 0,
        "note": ("счётчик смен, а не оценка качества: одна смена может быть "
                 "крупной правкой, другая — подъёмом версии"),
    }


def normalise(value: Any, drop: tuple[str, ...] | None = None) -> Any:
    """Ответ без изменчивых полей — то, что вообще можно сравнивать.

    Отбрасываются ключи из `SAMPLE_VOLATILE` (ревизия, свежесть, длительности,
    номера сеансов). Список читается ПРИ ВЫЗОВЕ, а не при определении: иначе его
    нельзя ни расширить на месте, ни проверить подсадкой — и свойство «один код,
    один хеш» держалось бы на том, что список никто не трогал.

    Чего НЕ делает: пути и имена файлов НЕ вырезаются. Они — часть ответа (какой
    файл, какая таблица), и внутри одного корпуса они стабильны.
    """
    if drop is None:
        drop = SAMPLE_VOLATILE
    if isinstance(value, dict):
        return {str(key): normalise(item, drop=drop) for key, item in value.items()
                if str(key) not in drop}
    if isinstance(value, (list, tuple)):
        return [normalise(item, drop=drop) for item in value]
    if isinstance(value, str):
        return clean_text(value)
    return value


def sample(conn: sqlite3.Connection, root: str | Path, *, op: str,
           args: dict[str, Any] | None = None,
           call: Any = None) -> dict[str, Any]:
    """Проба ПОВЕДЕНИЯ: что операция возвращает на заданном вызове.

    Зачем. Пин видит форму: имя, параметры с умолчаниями, ключи ответа. Правка,
    которая форму не меняет, но меняет результат (другой порядок, сдвинутая
    граница, убранная ветка), для пина невидима — это названо в §6 документа.
    Проба закрывает именно этот слепой участок: ответ канонизируется без
    изменчивых полей и хешируется.

    `call` — функция `(op, args) -> dict`: живой фасад в работе, подставной в
    проверках. Без неё проба не выдумывает результат, а говорит `not_sampled`.
    """
    payload = dict(args or {})
    args_hash = _short(canonical(payload))
    if call is None:
        return {"op": op, "args_hash": args_hash, "value_hash": "",
                "status": "not_sampled", "detail": "нет вызывающего: фасад не передан"}
    try:
        raw = call(op, payload)
    except Exception as exc:  # noqa: BLE001 — причина называется, а не глотается
        return {"op": op, "args_hash": args_hash, "value_hash": "",
                "status": "not_sampled",
                "detail": f"{type(exc).__name__}: {str(exc)[:160]}"}
    if not isinstance(raw, dict):
        return {"op": op, "args_hash": args_hash, "value_hash": "",
                "status": "not_sampled",
                "detail": f"ответ не словарь: {type(raw).__name__}"}
    cleaned = normalise(raw)
    return {"op": op, "args_hash": args_hash, "value_hash": _short(canonical(cleaned)),
            "status": "sampled", "detail": "", "keys": len(cleaned),
            "value": cleaned}


def probe_args(op: str) -> dict[str, Any]:
    """Аргументы пробы — из объявления, а не из головы вызывающего."""
    return dict((PROBES.get(op) or {}).get("args") or {})


def probe_method(op: str) -> str:
    """Фактическое имя метода фасада для пробы."""
    return str((PROBES.get(op) or {}).get("method") or ("op_" + op))


def probe_needs(op: str) -> str:
    """Какое условие должно быть готово, чтобы проба имела смысл."""
    return str((PROBES.get(op) or {}).get("needs") or "")


def take(conn: sqlite3.Connection, root: str | Path, *, call: Any = None,
         ready: Any = None, ops: list[str] | None = None) -> dict[str, Any]:
    """Записать пробы. Каждая строка — последняя проба вызова, истории нет.

    Почему без истории: сравнивать надо с ТЕМ, что было в прошлый раз, а не с
    каждым замером когда-либо; журнал проб в́рос бы в размере без пользы.

    `ready` — проверка условия из `needs`. Не готово — `not_sampled` с причиной:
    проба не должна выглядеть совпадением там, где она не выполнялась.
    """
    ensure_schema(conn)
    wanted = [item for item in (ops or list(PROBES)) if item in PROBES]
    unknown = sorted(set(ops or []) - set(PROBES))
    taken = []
    now = _now()
    for op in wanted:
        need = probe_needs(op)
        ready_ok = ready is None or not need or ready(need)
        if ready_ok:
            item = sample(conn, root, op=op, args=probe_args(op), call=call)
        else:
            # Условие не выполнено — но вызов всё равно делается: ответ-отказ
            # (или отказ модели вызова) — тоже поведение, и его надо помнить.
            # Так «нет индекса» перестаёт быть белым пятном: видно, ЧТО именно
            # операция отвечает, когда индекса нет.
            item = sample(conn, root, op=op, args=probe_args(op), call=call)
            if item["status"] == "sampled":
                item["status"] = "sampled_degraded"
                item["detail"] = f"условие не выполнено: needs={need}; ответ записан"
        conn.execute(
            "INSERT INTO pin_sample(op, args_hash, value_hash, status, detail, taken_at,"
            " verdict, checked_at, value_json, paths_json)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(op, args_hash) DO UPDATE SET value_hash=excluded.value_hash,"
            " status=excluded.status, detail=excluded.detail, taken_at=excluded.taken_at,"
            " value_json=excluded.value_json, paths_json=NULL",
            (item["op"], item["args_hash"], item["value_hash"], item["status"],
             item["detail"], now, None, None,
             canonical(item["value"]) if "value" in item else None, None))
        taken.append(item)
    conn.commit()
    return {"taken": taken, "unknown": unknown, "at": now}


def accept(conn: sqlite3.Connection, root: str | Path, *, by: str, note: str = "",
           call: Any = None, ready: Any = None,
           ops: list[str] | None = None) -> dict[str, Any]:
    """Принять текущее поведение как новое: проба и строка истории.

    Зачем отдельный режим. Сверка может сказать «изменилось», и это сообщение
    легко погасить молча — достаточно взять новую пробу. Здесь погашение
    требует ИМЕНИ человека и оставляет строку в `pin_history` (вид `sample`):
    видно, кто и когда сменил поведение, и что было до него.

    Автопринятия нет: без имени — отказ.
    """
    if not str(by).strip():
        raise PinRefusal("не назван принимающий: смена поведения без автора "
                         "не отличается от самопринятия")
    ensure_schema(conn)
    wanted = [item for item in (ops or list(PROBES)) if item in PROBES]
    now = _now()
    accepted: list[dict[str, Any]] = []
    for op in wanted:
        need = probe_needs(op)
        if ready is not None and need and not ready(need):
            fresh = {"op": op, "args_hash": _short(canonical(probe_args(op))),
                     "value_hash": "", "status": "not_sampled", "detail":
                     f"условие не выполнено: needs={need}"}
        else:
            fresh = sample(conn, root, op=op, args=probe_args(op), call=call)
        if fresh["status"] != "sampled":
            accepted.append({"op": op, "status": "not_sampled",
                             "detail": fresh["detail"]})
            continue
        stored = conn.execute(
            "SELECT value_hash FROM pin_sample WHERE op=? AND args_hash=?",
            (op, fresh["args_hash"])).fetchone()
        previous = str(stored[0]) if stored and stored[0] else ""
        conn.execute(
            "INSERT INTO pin_sample(op, args_hash, value_hash, status, detail, taken_at,"
            " verdict, checked_at, value_json) VALUES(?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(op, args_hash) DO UPDATE SET value_hash=excluded.value_hash,"
            " status=excluded.status, detail=excluded.detail, taken_at=excluded.taken_at,"
            " verdict='same', checked_at=excluded.checked_at,"
            " value_json=excluded.value_json",
            (op, fresh["args_hash"], fresh["value_hash"], "sampled", "", now, "same",
             now, canonical(fresh.get("value"))))
        if previous != fresh["value_hash"]:
            conn.execute(
                "INSERT INTO pin_history(op, kind, hash, approved_at, approved_by,"
                " note, previous_hash) VALUES(?,?,?,?,?,?,?)",
                (op, "sample", fresh["value_hash"], now, str(by).strip(),
                 str(note or ""), previous))
        accepted.append({"op": op, "status": "accepted", "hash": fresh["value_hash"],
                         "previous": previous})
    conn.commit()
    return {"accepted": accepted, "at": now,
            "total": sum(1 for item in accepted if item["status"] == "accepted")}


def compare(conn: sqlite3.Connection, root: str | Path, *, call: Any = None,
            ready: Any = None, ops: list[str] | None = None) -> dict[str, Any]:
    """Сверить поведение с последней пробой: совпало / разошлось / не пробовалось."""
    ensure_schema(conn)
    wanted = [item for item in (ops or list(PROBES)) if item in PROBES]
    now = _now()
    rows: list[dict[str, Any]] = []
    totals = {"sampled": 0, "same": 0, "changed": 0, "no_baseline": 0, "not_sampled": 0}
    for op in wanted:
        need = probe_needs(op)
        ready_ok = ready is None or not need or ready(need)
        fresh = sample(conn, root, op=op, args=probe_args(op), call=call)
        if fresh["status"] == "sampled" and not ready_ok:
            fresh["status"] = "sampled_degraded"
            fresh["detail"] = f"условие не выполнено: needs={need}; ответ записан"
        stored = conn.execute(
            "SELECT value_hash, status, taken_at, value_json FROM pin_sample"
            " WHERE op=? AND args_hash=?", (op, fresh["args_hash"])).fetchone()
        paths: list[str] = []
        if fresh["status"] not in ("sampled", "sampled_degraded"):
            verdict = "not_sampled"
        elif stored is None or not stored[0]:
            verdict = "no_baseline"
        elif str(stored[0]) == fresh["value_hash"]:
            verdict = "same"
        else:
            verdict = "changed"
            paths = diff_paths(_load_json(stored[3]), fresh.get("value"))
        totals["sampled"] += 1
        totals[verdict] = totals.get(verdict, 0) + 1
        if verdict in ("same", "changed", "no_baseline"):
            conn.execute(
                "UPDATE pin_sample SET verdict=?, checked_at=?, paths_json=?"
                " WHERE op=? AND args_hash=?",
                (verdict, now, canonical(paths) if paths else None, op, fresh["args_hash"]))
        rows.append({"op": op, "verdict": verdict, "value_hash": fresh["value_hash"],
                     "status": fresh["status"],
                     "stored_hash": str(stored[0]) if stored else "",
                     "taken_at": str(stored[2]) if stored else "",
                     "detail": fresh["detail"], "paths": paths[:12]})
    conn.commit()
    changed = [item["op"] for item in rows if item["verdict"] == "changed"]
    return {"checks": rows, "totals": totals, "changed": changed,
            "verdict": "изменилось" if changed else "не изменилось", "at": now}


def _load_json(text: Any) -> Any:
    try:
        return json.loads(str(text)) if text else None
    except (TypeError, ValueError):
        return None


def changed_ops(conn: sqlite3.Connection) -> list[str]:
    """Какие пробы разошлись при последней сверке — для отбора `--only-changed`.

    Нужно приёму: принимать весь набор ради одной изменившейся пробы — значит
    подписывать и то, что не менялось.
    """
    try:
        rows = conn.execute(
            "SELECT DISTINCT op FROM pin_sample WHERE verdict='changed' ORDER BY op").fetchall()
    except sqlite3.Error:
        return []
    return [str(row[0]) for row in rows]


def accept_stored(conn: sqlite3.Connection, *, by: str, note: str = "",
                  ops: list[str] | None = None) -> dict[str, Any]:
    """Принять УЖЕ СНЯТЫЕ пробы как новое поведение выпуска.

    Нужно сборке: у неё нет живого фасада с индексом, поэтому она не может
    вызвать пробы сама. Она берёт то, что положила `contract_pin_sample
    --sample-mode take`, и записывает смену истории: кто принял и что было до.

    Без имени — отказ: смена поведения без автора неотличима от самопринятия.
    """
    if not str(by).strip():
        raise PinRefusal("не назван принимающий: приём проб без автора "
                         "не отличается от самопринятия")
    ensure_schema(conn)
    rows = conn.execute("SELECT op, args_hash, value_hash, status FROM pin_sample"
                        " ORDER BY op").fetchall()
    wanted = {item for item in (ops or []) if item}
    now = _now()
    accepted: list[dict[str, Any]] = []
    for row in rows:
        op = str(row[0])
        if wanted and op not in wanted:
            continue
        value = str(row[2] or "")
        if not value:
            accepted.append({"op": op, "status": "not_sampled"})
            continue
        previous = ""
        last = conn.execute(
            "SELECT hash FROM pin_history WHERE op=? AND kind='sample'"
            " ORDER BY id DESC LIMIT 1", (op,)).fetchone()
        if last:
            previous = str(last[0] or "")
        if previous != value:
            conn.execute(
                "INSERT INTO pin_history(op, kind, hash, approved_at, approved_by,"
                " note, previous_hash) VALUES(?,?,?,?,?,?,?)",
                (op, "sample", value, now, str(by).strip(), str(note or ""), previous))
        conn.execute("UPDATE pin_sample SET verdict='same', checked_at=?, paths_json=NULL"
                     " WHERE op=? AND args_hash=?", (now, op, str(row[1])))
        accepted.append({"op": op, "status": "accepted", "hash": value,
                         "previous": previous})
    conn.commit()
    return {"accepted": accepted, "at": now,
            "total": sum(1 for item in accepted if item["status"] == "accepted")}


def diff_paths(old: Any, new: Any, *, prefix: str = "", depth: int = 0,
               limit: int = 40) -> list[str]:
    """Где именно ответы разошлись: пути, а не «изменилось».

    Нужно затем, чтобы отчёт называл место (`corpora[1]`, `totals.changed`),
    а не заставлял сравнивать два дампа глазами. Пути собираются по
    ОЧИЩЕННЫМ структурам, поэтому ревизия и время сюда не попадают.
    """
    if len(prefix) and depth > 6:
        return [prefix]
    found: list[str] = []
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in old:
                found.append(path + " (появилось)")
            elif key not in new:
                found.append(path + " (исчезло)")
            else:
                found.extend(diff_paths(old[key], new[key], prefix=path, depth=depth + 1))
    elif isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            found.append(f"{prefix or '[]'} (длина {len(old)} → {len(new)})")
        for index in range(min(len(old), len(new))):
            found.extend(diff_paths(old[index], new[index],
                                    prefix=f"{prefix}[{index}]", depth=depth + 1))
    elif old != new:
        found.append(prefix or "(корень)")
    return found[:limit]


def samples_document(conn: sqlite3.Connection) -> dict[str, Any]:
    """Пробы для `ops_pins.json`: хеши и итог, без значений и без времени.

    Почему только хеши: файл поставки должен быть побайтово одинаковым на двух
    прогонах (детерминизм проверяется делом), а значения проб — это ответы
    фасада на живом корпусе; им в манифесте не место.
    """
    try:
        rows = conn.execute(
            "SELECT op, args_hash, value_hash, status, verdict, paths_json"
            " FROM pin_sample ORDER BY op").fetchall()
    except sqlite3.Error:
        return {"probes": {}, "state": {"total": 0, "changed": 0, "state": "none"}}
    probes: dict[str, Any] = {}
    for row in rows:
        op = str(row[0])
        item = probes.setdefault(op, {"entries": [], "verdict": str(row[4] or ""),
                                      "paths": _load_json(row[5]) or []})
        item["entries"].append({"args": str(row[1]), "value": str(row[2]),
                                "status": str(row[3])})
        if row[5] and not item["paths"]:
            item["paths"] = sorted(str(path) for path in (_load_json(row[5]) or []))
    return {"probes": {name: {"entries": sorted(item["entries"],
                                                key=lambda entry: entry["args"]),
                             "verdict": item["verdict"],
                             "paths": sorted(item["paths"])}
                      for name, item in probes.items()},
            "state": sample_state(conn)}


def sample_state(conn: sqlite3.Connection) -> dict[str, Any]:
    """Состояние проб для ворот: сколько и что было при последней сверке."""
    try:
        rows = conn.execute(
            "SELECT verdict, COUNT(*) FROM pin_sample GROUP BY verdict").fetchall()
        total = int(conn.execute("SELECT COUNT(*) FROM pin_sample").fetchone()[0])
    except sqlite3.Error:
        return {"total": 0, "changed": 0, "checked": 0, "state": "none"}
    counted = {str(row[0] or "не сверялось"): int(row[1]) for row in rows}
    changed = counted.get("changed", 0)
    return {
        "total": total, "changed": changed,
        "checked": sum(number for name, number in counted.items() if name != "не сверялось"),
        "by_verdict": counted,
        "state": ("changed" if changed else ("agreed" if total else "none")),
    }


def _short(text: str) -> str:
    """Короткий хеш для журнала: полный не нужен, 16 знаков не сталкиваются."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def last_change(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Последняя смена набора — с возрастом, чтобы отчёт мог её назвать."""
    rows = history(conn, limit=1)
    if not rows:
        return None
    item = dict(rows[0])
    item["age_seconds"] = _age_seconds(item["approved_at"])
    return item


def state(conn: sqlite3.Connection, root: str | Path, *,
          kind: str = "op") -> dict[str, Any]:
    """Состояние пина корпуса одного вида — без событий и списков."""
    return state_payload(hashes_of(root, kind), pins(conn, kind))


def merge_states(first: str, second: str) -> str:
    """Сводное состояние двух наборов. Порядок — тот же, что внутри набора.

    «Принято» требует ОБОИХ наборов: пока инструменты не подтверждены,
    контракт принят не целиком, и выпуска это касается так же, как расхождение.
    """
    if "diverged" in (first, second):
        return "diverged"
    if "partial" in (first, second):
        return "partial"
    if first == "none" and second == "none":
        return "none"
    if first == "none" or second == "none":
        return "partial"
    return "agreed"


def state_all(conn: sqlite3.Connection, root: str | Path) -> dict[str, Any]:
    """Состояние обоих контрактов и сводное: одно число для ворот выпуска."""
    ops = state(conn, root, kind="op")
    tools = state(conn, root, kind="tool")
    return {"state": merge_states(ops["state"], tools["state"]),
            "ops": ops, "tools": tools}


def _report(conn: sqlite3.Connection, root: str | Path, kind: str) -> dict[str, Any]:
    """Разбор одного набора: пины, расхождения, неподтверждённое, устаревшее."""
    current = hashes_of(root, kind)
    known = pins(conn, kind)
    pinned: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    for name in sorted(current):
        item = known.get(name)
        if item is None:
            continue
        age = _age_seconds(item["approved_at"])
        row = {"op": name, "kind": kind, "hash": item["hash"],
               "observed": current[name], "approved_at": item["approved_at"],
               "approved_by": item["approved_by"], "note": item["note"],
               "pin_age_seconds": age, "match": item["hash"] == current[name]}
        pinned.append(row)
        if not row["match"]:
            mismatches.append({"op": name, "kind": kind, "pinned": item["hash"],
                               "observed": current[name],
                               "pin_age_seconds": age})
    return {
        "pins": pinned,
        "mismatches": mismatches,
        "unpinned": sorted(set(current) - set(known)),
        "stale_pins": sorted(set(known) - set(current)),
        "pin_state": state_payload(current, known),
    }


def status(conn: sqlite3.Connection, root: str | Path, *,
           limit: int = 50) -> dict[str, Any]:
    """Что пиннуто, что разошлось, что ещё не пиннуто — по обоим контрактам.

    Верхние ключи (`pins`, `mismatches`, `unpinned`, `stale_pins`, `totals`,
    `pin_state`) — про ОПЕРАЦИИ, как до X10: их читают отчёты и ворота.
    Инструменты — в `tools`, общий вывод — в `pin_state_all`.
    """
    ops = _report(conn, root, "op")
    tools = _report(conn, root, "tool")
    found = events(conn, limit=limit)
    changes = history(conn, limit=limit)
    last = changes[0] if changes else None
    totals = {"pinned": len(ops["pins"]), "mismatches": len(ops["mismatches"]),
              "unpinned": len(ops["unpinned"]), "stale_pins": len(ops["stale_pins"]),
              "events": len(found),
              "breaks": sum(1 for item in found if item["kind"] == "pin_break")}
    totals_tools = {"pinned": len(tools["pins"]),
                    "mismatches": len(tools["mismatches"]),
                    "unpinned": len(tools["unpinned"]),
                    "stale_pins": len(tools["stale_pins"])}
    return {
        "total_ops": ops["pin_state"]["total_ops"],
        "total_tools": tools["pin_state"]["total_ops"],
        "pin_state": ops["pin_state"],
        "pin_state_tools": tools["pin_state"],
        "pin_state_all": {"state": merge_states(ops["pin_state"]["state"],
                                          tools["pin_state"]["state"]),
                            "ops": ops["pin_state"]["state"],
                            "tools": tools["pin_state"]["state"]},
        "totals": totals,
        "totals_tools": totals_tools,
        "pins": ops["pins"],
        "mismatches": ops["mismatches"],
        "unpinned": ops["unpinned"],
        "stale_pins": ops["stale_pins"],
        "tools": {key: tools[key] for key in
                  ("pins", "mismatches", "unpinned", "stale_pins")},
        "events": found,
        "history": changes,
        "changed_at": str(last["approved_at"]) if last else "",
        "changed_age_seconds": (_age_seconds(str(last["approved_at"])) if last else None),
        "limits": [
            "пин описывает ОБЪЯВЛЕНИЕ операции и схему входа инструмента, а не "
            "их поведение: изменённый ответ при том же объявлении не ловится",
            "ключи ответа видны только у операций, собранных литералом; у "
            "остальных в хеш входит сам факт сборки из переменной",
            "словесные описания (description) в контракт не входят: это "
            "формулировка, а не форма",
        ],
    }
