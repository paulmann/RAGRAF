"""Воспроизводимая трасса подтверждённого сбоя (X7).

Зачем трассы, если кейсы уже проверяются. Проверка отвечает «падает сейчас или
нет», но не сохраняет, ЧЕМ именно кейс падал: журнал шагов живёт своей жизнью,
объявление кейса правится, и через неделю нельзя ни повторить сбой, ни доказать,
что он был. Здесь лежит всё нужное для повтора: объявление кейса ЦЕЛИКОМ (а не
ссылка на файл — файл правится), шаги сеанса, решения гейта, ревизия индекса и
отпечаток содержимого.

Почему в журнале управления (`control.sqlite`). Трасса — событие процесса, а не
свойство корпуса: у неё нет `rev_id`, и ротация ревизий её не касается. В том же
файле уже лежат прогоны качества и события контракта, поэтому третьего хранилища
не заводим.

Границы, названные прямо.

  * Записывается только сбой. Пройденный кейс — не трасса: запись о нём
    превратила бы журнал сбоев в журнал прогонов, и «сколько раз падало»
    перестало бы читаться.
  * Одинаковый сбой не плодит записей: ключ — отпечаток содержимого (sha256 от
    канонического JSON). Две записи одного сбоя выглядят как две болезни.
  * «Починилось» заявляется только на ТОЙ ЖЕ ревизии. На другой ревизии падение
    могло уйти по другой причине, поэтому вердикт называется `other_rev` с
    оговоркой, а не `fixed`.

Автор: Mikhail Deynekin
Сайт: https://Deynekin.com
Почта: Mikhail@Deynekin.com
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Callable, Iterable

#: Версия схемы трассы. Другая версия читается как «трасса другого формата».
SCHEMA = 1

#: Виды вызова операции. Закрытый список: вид вне его — ошибка вызывающего кода.
ACTIONS: tuple[str, ...] = ("save", "list", "replay")

#: Вердикты повтора. `other_rev` — не «починилось»: ревизия другая.
VERDICTS: tuple[str, ...] = ("fixed", "still_failing", "other_rev",
                             "not_reproducible")

#: Пределы записи: трасса — доказательство, а не копия журнала целиком.
MAX_STEPS = 500
MAX_GATE_ROWS = 200

TRACE_DDL = """
CREATE TABLE IF NOT EXISTS quality_trace (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  ts       TEXT NOT NULL,
  case_id  TEXT NOT NULL,
  kind     TEXT NOT NULL,
  rev      TEXT NOT NULL,
  session  TEXT NOT NULL,
  status   TEXT NOT NULL,
  digest   TEXT NOT NULL UNIQUE,
  label    TEXT,
  payload  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quality_trace_case ON quality_trace(case_id, id);
"""


class TraceRefusal(ValueError):
    """Отказ записи трассы: причина называется, а не превращается в пустую запись."""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Создать таблицу трасс в существующем журнале (чужую схему не меняя)."""
    conn.executescript(TRACE_DDL)
    conn.commit()


def canonical(payload: Any) -> str:
    """Канонический JSON: порядок ключей и пробелы на отпечаток не влияют."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def digest(payload: Any) -> str:
    """Отпечаток содержимого трассы: им же проверяется повтор записи."""
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


def _now() -> str:
    from . import util

    return util.now_iso()


def _trim(items: Iterable[Any], limit: int) -> list[Any]:
    out: list[Any] = []
    for item in items or []:
        if len(out) >= limit:
            break
        out.append(item)
    return out


def save(conn: sqlite3.Connection, *, case: dict[str, Any],
         result: dict[str, Any], steps: Iterable[Any], gate_rows: Iterable[Any],
         rev: str, session: str, label: str = "",
         case_source: str = "") -> dict[str, Any]:
    """Записать подтверждённый сбой. Пропущенный кейс — не сбой.

    Возвращает `saved` и `id`: повтор того же сбоя отвечает прежним id, а не
    второй записью. Это ответ, а не ошибка: клиент, повторивший сохранение,
    не должен получить отказ.
    """
    if not str(rev or "").strip():
        raise TraceRefusal("ревизия не названа: трасса без ревизии не "
                           "воспроизводима — неизвестно, на каком индексе она снята")
    status = str(result.get("status") or "")
    if status != "fail":
        raise TraceRefusal(
            "записывается только подтверждённый сбой: у кейса состояние "
            f"{status or 'неизвестно'}. Пройденный кейс — не трасса: иначе журнал "
            "сбоев стал бы журналом прогонов")
    payload = {
        "schema": SCHEMA,
        "rev": str(rev or ""),
        "session": str(session or ""),
        "case_source": str(case_source or ""),
        "case": case,
        "result": {
            "status": status,
            "reason": str(result.get("reason") or ""),
            "address": str(result.get("address") or ""),
            "missing_steps": list(result.get("missing_steps") or []),
            "violations": list(result.get("violations") or []),
            "bypass": list(result.get("bypass") or []),
            "missing_paths": list(result.get("missing_paths") or []),
        },
        "steps": _trim(steps, MAX_STEPS),
        "gate": _trim(gate_rows, MAX_GATE_ROWS),
    }
    mark = digest(payload)
    ensure_schema(conn)
    existing = conn.execute(
        "SELECT id FROM quality_trace WHERE digest = ?", (mark,)).fetchone()
    if existing is not None:
        return {"saved": False, "id": int(existing[0]), "digest": mark,
                "case_id": str(case.get("id") or ""), "rev": str(rev or ""),
                "reason": "такой сбой уже записан: повтор не плодит записей"}
    case_id = str(case.get("id") or "")
    kind = str(case.get("kind") or "")
    conn.execute(
        "INSERT INTO quality_trace(ts, case_id, kind, rev, session, status,"
        " digest, label, payload) VALUES(?,?,?,?,?,?,?,?,?)",
        (_now(), case_id, kind, str(rev or ""), str(session or ""), status, mark,
         str(label or ""), canonical(payload)))
    conn.commit()
    row = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
    return {"saved": True, "id": int(row[0]), "digest": mark, "case_id": case_id,
            "rev": str(rev or ""), "reason": ""}


def traces(conn: sqlite3.Connection, *, case_id: str = "",
           limit: int = 20) -> dict[str, Any]:
    """Список трасс без содержимого: содержимое — только у `load`."""
    ensure_schema(conn)
    sql = ("SELECT id, ts, case_id, kind, rev, session, status, digest, label"
           " FROM quality_trace")
    params: tuple[Any, ...] = ()
    if case_id:
        sql += " WHERE case_id = ?"
        params = (case_id,)
    sql += " ORDER BY id DESC LIMIT ?"
    params += (max(1, min(int(limit or 20), 200)),)
    rows = conn.execute(sql, params).fetchall()
    out = [{"id": int(row[0]), "ts": str(row[1]), "case_id": str(row[2]),
            "kind": str(row[3]), "rev": str(row[4]), "session": str(row[5]),
            "status": str(row[6]), "digest": str(row[7]),
            "label": str(row[8] or "")} for row in rows]
    total = conn.execute("SELECT COUNT(*) FROM quality_trace").fetchone()[0]
    return {"traces": out, "shown": len(out), "total": int(total),
            "case_id": case_id}


def load(conn: sqlite3.Connection, trace_id: int) -> dict[str, Any] | None:
    """Полная трасса: объявление кейса, шаги, решения гейта и ревизия."""
    ensure_schema(conn)
    row = conn.execute(
        "SELECT id, ts, case_id, kind, rev, session, status, digest, label, payload"
        " FROM quality_trace WHERE id = ?", (int(trace_id or 0),)).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(str(row[9]))
    except json.JSONDecodeError:
        return None
    return {"id": int(row[0]), "ts": str(row[1]), "case_id": str(row[2]),
            "kind": str(row[3]), "rev": str(row[4]), "session": str(row[5]),
            "status": str(row[6]), "digest": str(row[7]),
            "label": str(row[8] or ""), "payload": payload}


def replay(conn: sqlite3.Connection, trace_id: int, *, rev: str,
           evaluator: Callable[[dict[str, Any], str], dict[str, Any]]
           ) -> dict[str, Any]:
    """Повторить сохранённый сбой на текущем индексе и назвать вердикт.

    Оценка передаётся вызывающим: модуль не знает, где живут граф и журнал, и не
    должен знать — иначе трасса потянет за собой весь фасад и перестанет быть
    проверяемой отдельно.
    """
    stored = load(conn, int(trace_id or 0))
    if stored is None:
        return {"found": False, "verdict": "not_reproducible", "trace_id":
                int(trace_id or 0), "reason": "трассы с таким id нет",
                "case_id": "", "rev_before": "", "rev_now": str(rev or "")}
    payload = stored.get("payload") or {}
    case = payload.get("case") or {}
    before = str(payload.get("rev") or "")
    session = str(payload.get("session") or "")
    try:
        now = evaluator(case, session)
    except Exception as exc:  # noqa: BLE001 — причина называется, а не глотается
        return {"found": True, "verdict": "not_reproducible",
                "trace_id": stored["id"], "case_id": stored["case_id"],
                "rev_before": before, "rev_now": str(rev or ""),
                "reason": f"повтор не выполнен: {type(exc).__name__}: {exc}"}
    status = str((now or {}).get("status") or "")
    if status == "fail":
        verdict = "still_failing"
    elif status == "ok":
        # На другой ревизии «прошло» не значит «починено»: падение могло уйти
        # по другой причине, и называть это исправлением значило бы врать.
        verdict = "fixed" if before == str(rev or "") else "other_rev"
    else:
        verdict = "not_reproducible"
    return {
        "found": True, "verdict": verdict, "trace_id": stored["id"],
        "case_id": stored["case_id"], "rev_before": before,
        "rev_now": str(rev or ""), "same_rev": before == str(rev or ""),
        "status_before": str((payload.get("result") or {}).get("status") or ""),
        "status_now": status, "steps_before": len(payload.get("steps") or []),
        "reason": str((now or {}).get("reason") or ""),
    }
