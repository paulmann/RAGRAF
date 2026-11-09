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
import sqlite3
import time
from pathlib import Path
from typing import Any

SCHEMA = 1

#: Закрытый список видов события. Вид вне списка — ошибка вызывающего кода.
EVENT_KINDS: tuple[str, ...] = ("pin_mismatch", "pin_break")

#: Закрытый список причин `pin_break`.
BREAK_REASONS: tuple[str, ...] = ("unknown_name", "bad_arguments")

#: Уровень события: у расхождения он выше, потому что это сигнал о контракте.
EVENT_LEVEL = {"pin_mismatch": "high", "pin_break": "medium"}

PIN_DDL = """
CREATE TABLE IF NOT EXISTS op_pin (
  op          TEXT PRIMARY KEY,
  hash        TEXT NOT NULL,
  approved_at TEXT NOT NULL,
  approved_by TEXT NOT NULL,
  note        TEXT
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
  related_id      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_pin_event_op ON pin_event(op, id);
"""

#: Кэш описаний: (корень, время правки facade.py) → {операция: хеш}.
_CACHE: dict[tuple[str, float], dict[str, str]] = {}


class PinRefusal(ValueError):
    """Действие с пином невозможно по названной причине."""


def canonical(payload: Any) -> str:
    """Каноническая запись для хеша: сортировка ключей, без пробелов."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _facade_path(root: str | Path) -> Path:
    return Path(root) / "src" / "ragraf" / "facade.py"


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
    """Хеши описаний всех операций корпуса. Читается из `surface.ops_of`."""
    from . import surface

    facade = _facade_path(root)
    try:
        stamp = facade.stat().st_mtime
    except OSError:
        stamp = 0.0
    key = (str(root), stamp)
    cached = _CACHE.get(key)
    if cached is not None:
        return dict(cached)
    try:
        ops = surface.ops_of(Path(root))
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


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Создать таблицы пинов и событий в существующем журнале (схему не меняя)."""
    conn.executescript(PIN_DDL)
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


def pins(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Пины корпуса: операция → {hash, approved_at, approved_by, note}."""
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
            everything: bool = False, by: str = "", note: str = "") -> dict[str, Any]:
    """Подтвердить текущее описание человеком. Автоподтверждения нет.

    Один пин — `op`; все сразу — `everything`. Оба сразу или ни одного — отказ:
    «подтвердить всё и ещё вот это» звучало бы как одно действие, а делает разное.
    """
    if bool(op) == bool(everything):
        raise PinRefusal("нужно выбрать одно: имя операции или подтверждение всех")
    if not str(by).strip():
        raise PinRefusal("не назван подтверждающий: пин без автора не отличается от самоподписи")
    current = hashes(root)
    ensure_schema(conn)
    now = _now()
    targets = sorted(current) if everything else [op]
    if not everything and op not in current:
        raise PinRefusal(f"операции {op!r} нет в описании поверхности: подтверждать нечего")
    before = pins(conn)
    changed = []
    for name in targets:
        digest = current[name]
        old = (before.get(name) or {}).get("hash") or ""
        conn.execute(
            "INSERT INTO op_pin(op, hash, approved_at, approved_by, note)"
            " VALUES(?,?,?,?,?)"
            " ON CONFLICT(op) DO UPDATE SET hash=excluded.hash,"
            " approved_at=excluded.approved_at, approved_by=excluded.approved_by,"
            " note=excluded.note",
            (name, digest, now, str(by).strip(), str(note or "")))
        changed.append({"op": name, "hash": digest, "previous": old})
    conn.commit()
    return {"approved": changed, "total": len(changed), "at": now}


def observe(conn: sqlite3.Connection, op: str, observed: str, *,
            root: str | Path) -> dict[str, Any] | None:
    """Сравнить наблюдённый хеш с пином. Вернуть событие, если оно новое.

    Событие пишется только при расхождении и только один раз на пару
    «операция + наблюдённый хеш»: иначе один сломанный клиент забил бы журнал
    тысячей одинаковых записей, и расхождение перестало бы быть заметным.
    """
    if not observed:
        return None
    ensure_schema(conn)
    item = pins(conn).get(str(op)) or {}
    pinned = str(item.get("hash") or "")
    if not pinned or pinned == observed:
        return None
    # Возраст считается ЗДЕСЬ: он относится к пину, а не к вызову, и передавать
    # его снаружи значило бы разрешить вызывающему назвать любое число.
    pin_age = _age_seconds(str(item.get("approved_at") or ""))
    try:
        already = conn.execute(
            "SELECT id FROM pin_event WHERE kind='pin_mismatch' AND op=? AND observed=?"
            " ORDER BY id DESC LIMIT 1", (str(op), str(observed))).fetchone()
    except sqlite3.Error:
        already = None
    if already is not None:
        return None
    conn.execute(
        "INSERT INTO pin_event(ts, kind, level, op, observed, pinned, pin_age_seconds,"
        " detail, related_id) VALUES(?,?,?,?,?,?,?,?,NULL)",
        (_now(), "pin_mismatch", EVENT_LEVEL["pin_mismatch"], str(op), str(observed),
         pinned, pin_age, "описание операции разошлось с подтверждённым пином"))
    conn.commit()
    row = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
    return {"id": int(row[0]), "op": str(op), "observed": str(observed),
            "pinned": pinned, "pin_age_seconds": pin_age}


def note_break(conn: sqlite3.Connection, name: str, *, reason: str,
               detail: str = "") -> dict[str, Any] | None:
    """Записать `pin_break`: вызов, которого нет в описании, ПОСЛЕ расхождения.

    До расхождения неизвестное имя — обычная ошибка вызова, и шуметь о ней в
    журнале контракта нечего: пин ещё ничего не обещал.
    """
    if reason not in BREAK_REASONS:
        raise PinRefusal(f"причина {reason!r} вне списка: {', '.join(BREAK_REASONS)}")
    ensure_schema(conn)
    try:
        last = conn.execute(
            "SELECT id FROM pin_event WHERE kind='pin_mismatch' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except sqlite3.Error:
        return None
    if last is None:
        return None
    related = int(last[0])
    conn.execute(
        "INSERT INTO pin_event(ts, kind, level, op, observed, pinned, pin_age_seconds,"
        " detail, related_id) VALUES(?,?,?,?,NULL,NULL,NULL,?,?)",
        (_now(), "pin_break", EVENT_LEVEL["pin_break"], str(name),
         f"{reason}: {detail}".strip(": "), related))
    conn.commit()
    row = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
    return {"id": int(row[0]), "op": str(name), "reason": reason, "related_id": related}


def events(conn: sqlite3.Connection, *, limit: int = 50,
           kind: str = "") -> list[dict[str, Any]]:
    """Последние события пина: от новых к старым."""
    try:
        if kind:
            rows = conn.execute(
                "SELECT id, ts, kind, level, op, observed, pinned, pin_age_seconds,"
                " detail, related_id FROM pin_event WHERE kind=? ORDER BY id DESC LIMIT ?",
                (kind, int(limit))).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, ts, kind, level, op, observed, pinned, pin_age_seconds,"
                " detail, related_id FROM pin_event ORDER BY id DESC LIMIT ?",
                (int(limit),)).fetchall()
    except sqlite3.Error:
        return []
    return [{
        "id": int(row[0]), "ts": str(row[1]), "kind": str(row[2]),
        "level": str(row[3]), "op": str(row[4]),
        "observed": str(row[5] or ""), "pinned": str(row[6] or ""),
        "pin_age_seconds": None if row[7] is None else int(row[7]),
        "detail": str(row[8] or ""),
        "related_id": None if row[9] is None else int(row[9]),
    } for row in rows]


def status(conn: sqlite3.Connection, root: str | Path, *,
           limit: int = 50) -> dict[str, Any]:
    """Что пиннуто, что разошлось, что ещё не пиннуто. Ничего не меняет."""
    current = hashes(root)
    known = pins(conn)
    pinned: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    for name in sorted(current):
        item = known.get(name)
        if item is None:
            continue
        age = _age_seconds(item["approved_at"])
        row = {"op": name, "hash": item["hash"], "observed": current[name],
               "approved_at": item["approved_at"], "approved_by": item["approved_by"],
               "note": item["note"], "pin_age_seconds": age,
               "match": item["hash"] == current[name]}
        pinned.append(row)
        if not row["match"]:
            mismatches.append({"op": name, "pinned": item["hash"],
                               "observed": current[name],
                               "pin_age_seconds": age})
    unpinned = sorted(set(current) - set(known))
    stale = sorted(set(known) - set(current))
    found = events(conn, limit=limit)
    totals = {"pinned": len(pinned), "mismatches": len(mismatches),
              "unpinned": len(unpinned), "stale_pins": len(stale),
              "events": len(found),
              "breaks": sum(1 for item in found if item["kind"] == "pin_break")}
    return {
        "total_ops": len(current),
        "totals": totals,
        "pins": pinned,
        "mismatches": mismatches,
        "unpinned": unpinned,
        "stale_pins": stale,
        "events": found,
        "limits": [
            "пин описывает ОБЪЯВЛЕНИЕ операции, а не её поведение: изменённый "
            "ответ при том же объявлении не ловится",
            "ключи ответа видны только у операций, собранных литералом; у "
            "остальных в хеш входит сам факт сборки из переменной",
        ],
    }
