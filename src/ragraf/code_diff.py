"""Что изменилось между ревизиями индекса (P2-1).

Зачем отдельный модуль. Дрейф был только у слоя баз (`db_ops.drift`) и у журнала
расхождений; между ревизиями КОДА сравнения не было вовсе. Вопрос, ради которого
это сделано, звучит так: «после серии правок — не сломалось ли что-то». Три
ответа, которых не давал никто:

  * **что изменилось** — файлы, символы, рёбра (числа и списки);
  * **кто потерял вызывающих** — символ был вызываем, стал нет. Это регрессия
    «эта функция больше нигде не вызывается»;
  * **какая таблица перестала заполняться** — у неё были записи (`insert`/
    `update`/`delete`), а теперь не стало.

Границы доверия. Сравниваются ФАКТЫ ИНДЕКСА двух ревизий, а не код на диске:
если ревизия собрана с `capability=degraded`, часть рёбер отсутствовала и в
сравнении появится ложное «исчезло». Поэтому режим каждой ревизии возвращается
рядом с числами, и вызывающий обязан на него смотреть.
"""

from __future__ import annotations

import json
from typing import Any

#: Виды рёбер, которые считаются вызовами. Разделение важно: `calls` —
#: подтверждённый вызов, `calls_heuristic` — вывод (достоверность ниже), и
#: складывать их в одно число значило бы выдавать вывод за факт.
CALL_KINDS = ("calls", "calls_heuristic")


def _rows(conn, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _rev_mode(conn, rev: str) -> dict[str, Any]:
    try:
        row = conn.execute(
            "SELECT capability, state, file_count, node_count, edge_count, created_at"
            " FROM rev WHERE rev_id = ?", (rev,)).fetchone()
    except Exception:
        return {"rev_id": rev, "capability": "", "state": ""}
    if not row:
        return {"rev_id": rev, "capability": "", "state": ""}
    data = dict(row)
    data["rev_id"] = rev
    return data


def file_changes(conn, rev_from: str, rev_to: str) -> dict[str, Any]:
    """Файлы: добавленные, удалённые, изменённые (по контрольной сумме)."""
    def load(rev: str) -> dict[str, str]:
        return {
            str(r["path"]): str(r["sha256"] or "")
            for r in _rows(conn, "SELECT path, sha256 FROM file WHERE rev_id = ?", (rev,))
        }

    before, after = load(rev_from), load(rev_to)
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(p for p in set(before) & set(after) if before[p] != after[p])
    return {
        "added": added, "removed": removed, "changed": changed,
        "counts": {"added": len(added), "removed": len(removed),
                   "changed": len(changed),
                   "before": len(before), "after": len(after)},
    }


def symbol_changes(conn, rev_from: str, rev_to: str, *, limit: int = 200) -> dict[str, Any]:
    """Символы: добавленные и исчезнувшие (по идентификатору узла).

    Сравнение по `node_id`, а не по имени: идентификатор выводится из полного
    имени и пути, поэтому переименование видно как «исчез один, появился
    другой», а не как «ничего не менялось».
    """
    def load(rev: str) -> dict[str, dict]:
        return {
            str(r["node_id"]): r
            for r in _rows(
                conn,
                "SELECT node_id, kind, name, fqn, path, start_line FROM node"
                " WHERE rev_id = ? AND kind NOT IN ('file', 'external')", (rev,))
        }

    before, after = load(rev_from), load(rev_to)
    added_ids = sorted(set(after) - set(before))
    removed_ids = sorted(set(before) - set(after))
    return {
        "added": [after[i] for i in added_ids[:limit]],
        "removed": [before[i] for i in removed_ids[:limit]],
        "counts": {"added": len(added_ids), "removed": len(removed_ids),
                   "before": len(before), "after": len(after)},
    }


def edge_changes(conn, rev_from: str, rev_to: str) -> dict[str, Any]:
    """Рёбра: сколько было и стало, и по каким видам связь изменилась."""
    def load(rev: str) -> dict[str, int]:
        return {
            str(r["kind"]): int(r["n"])
            for r in _rows(conn,
                           "SELECT kind, COUNT(*) AS n FROM edge WHERE rev_id = ?"
                           " GROUP BY kind", (rev,))
        }

    before, after = load(rev_from), load(rev_to)
    kinds = sorted(set(before) | set(after))
    delta = {
        kind: {"before": before.get(kind, 0), "after": after.get(kind, 0),
               "delta": after.get(kind, 0) - before.get(kind, 0)}
        for kind in kinds
    }
    return {
        "by_kind": delta,
        "total": {"before": sum(before.values()), "after": sum(after.values())},
    }


def callers_lost(conn, rev_from: str, rev_to: str, *, limit: int = 50) -> dict[str, Any]:
    """Символы, потерявшие ВСЕХ вызывающих: была вызываема — стала нет.

    Это и есть регрессия, ради которой операция заведена: «эта функция больше
    нигде не вызывается». Считается входящая степень по видам `calls*`;
    символ, у которого вызывающих не было и раньше, в ответ НЕ попадает — иначе
    список заполнился бы мёртвым кодом, который мёртвым и был.
    """
    placeholders = ",".join("?" for _ in CALL_KINDS)

    def degrees(rev: str) -> dict[str, int]:
        # Заглушки (`@file:`, `@ext:`) исключены: у них нет узла в графе, и в
        # списке «потеряли вызывающих» они давали 29 записей с пустыми полями —
        # измерено 14.10.2026 на собственном корпусе.
        return {
            str(r["dst"]): int(r["n"])
            for r in _rows(
                conn,
                f"SELECT dst, COUNT(*) AS n FROM edge WHERE rev_id = ?"
                f" AND kind IN ({placeholders}) AND dst NOT LIKE '@%' GROUP BY dst",
                (rev, *CALL_KINDS))
        }

    before, after = degrees(rev_from), degrees(rev_to)
    lost = [d for d in before if before[d] > 0 and after.get(d, 0) == 0]
    if not lost:
        return {"items": [], "count": 0, "note": "вызывающих не потерял ни один символ"}
    # Узел берётся из НОВОЙ ревизии, а если его там уже нет — из старой:
    # «вызывающих не стало» и «символ удалён» — разные случаи, и оба важны.
    nodes: dict[str, dict] = {}
    for rev_name, removed_flag in ((rev_to, False), (rev_from, True)):
        chunk = lost[:400]
        if not chunk:
            continue
        marks = ",".join("?" for _ in chunk)
        for r in _rows(
            conn,
            f"SELECT node_id, kind, name, fqn, path, start_line FROM node"
            f" WHERE rev_id = ? AND node_id IN ({marks})", (rev_name, *chunk)):
            node = dict(r)
            node["removed"] = removed_flag
            nodes.setdefault(str(r["node_id"]), node)
    items = []
    for node_id in sorted(lost):
        node = nodes.get(node_id)
        record = {
            "node_id": node_id,
            "fqn": (node or {}).get("fqn", ""),
            "kind": (node or {}).get("kind", ""),
            "name": (node or {}).get("name", ""),
            "path": (node or {}).get("path"),
            "line": (node or {}).get("start_line"),
            "callers_before": before[node_id],
            "callers_after": 0,
            "removed": bool((node or {}).get("removed", False)),
        }
        items.append(record)
        if len(items) >= limit:
            break
    return {"items": items, "count": len(lost),
            "removed_among_lost": sum(1 for i in items if i["removed"]),
            "note": ("символы, у которых были вызывающие и не стало ни одного; "
                     "`removed: true` означает, что символ и сам исчез из кода")}


def writes_lost(conn, rev_from: str, rev_to: str, *, limit: int = 50) -> dict[str, Any]:
    """Таблицы, которые перестали заполняться: записи были — не стало.

    Считаются рёбра `@table:` с видом доступа на запись (`insert`, `update`,
    `delete`, `ddl`). Это ответ на вопрос «таблица перестала заполняться»,
    который иначе замечают уже по пустым отчётам в базе.
    """
    write_access = ("insert", "update", "delete", "ddl", "write", "replace")

    def writes(rev: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in _rows(conn,
                         "SELECT dst, props FROM edge WHERE rev_id = ?"
                         " AND dst >= '@table:' AND dst < '@tablf'", (rev,)):
            props = row.get("props")
            access = ""
            if props:
                try:
                    data = json.loads(props) if isinstance(props, str) else dict(props)
                    access = str(data.get("access") or data.get("write") or "").lower()
                except Exception:
                    access = ""
            if access in write_access:
                name = str(row["dst"])[len("@table:"):]
                counts[name] = counts.get(name, 0) + 1
        return counts

    before, after = writes(rev_from), writes(rev_to)
    lost = sorted(t for t in before if after.get(t, 0) == 0)
    return {
        "items": [{"table": t, "writes_before": before[t], "writes_after": 0}
                  for t in lost[:limit]],
        "count": len(lost),
        "note": "таблицы, у которых были обращения на запись и не стало ни одного",
    }


def code_diff(conn, rev_from: str, rev_to: str, *, limit: int = 100) -> dict[str, Any]:
    """Полная сводка изменений между двумя ревизиями кода."""
    if rev_from == rev_to:
        return {
            "from": rev_from, "to": rev_to, "same": True,
            "note": ("сравнивать не с чем: названа та же ревизия — предыдущей"
                     " нет либо она не найдена"),
        }
    return {
        "from": rev_from,
        "to": rev_to,
        "same": False,
        "modes": {"from": _rev_mode(conn, rev_from), "to": _rev_mode(conn, rev_to)},
        "files": file_changes(conn, rev_from, rev_to),
        "symbols": symbol_changes(conn, rev_from, rev_to, limit=limit),
        "edges": edge_changes(conn, rev_from, rev_to),
        "callers_lost": callers_lost(conn, rev_from, rev_to, limit=limit),
        "writes_lost": writes_lost(conn, rev_from, rev_to, limit=limit),
        "note": ("сравниваются факты ИНДЕКСА двух ревизий; при capability=degraded"
                 " часть рёбер отсутствовала — смотрите modes"),
    }
