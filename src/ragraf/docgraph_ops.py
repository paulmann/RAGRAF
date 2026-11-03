"""Две операции чтения документного графа: соседи сущности и связи документов.

Зачем отдельный модуль
    `docgraph.py` держит слой (схема, идентификаторы, отпечаток, целостность),
    `docgraph_extract.py` — проход, который этот слой наполняет. Здесь — ЧТЕНИЕ:
    два запроса, у каждого факта в ответе обязателен адрес. Модуль не знает про
    фасад, изданий и журналов: чистые функции над соединением, поэтому его можно
    проверить без сети, без CLI и без лицензии.

Правило, которое держит всё остальное: ФАКТ БЕЗ АДРЕСА НЕ ВЫДАЁТСЯ
    У каждого соседа — адрес упоминания связи (`path:start_line-end_line`), у
    каждой пары документов — адреса упоминания связующей сущности с ОБЕИХ
    сторон. Запись, адрес которой не нашёлся, не «показывается без адреса» и не
    «показывается с адресом родителя»: она отбрасывается, а её число попадает в
    `rejected_no_citation`. Молчаливая потеря запрещена так же, как выдумка:
    читатель должен видеть, что часть фактов не прошла проверку.

Почему обход в ширину, а не рекурсивный SQL
    Глубина ограничена тремя шагами, зато нужна не только пара «кто рядом», но и
    путь: на каждом шаге известно, ЧЕРЕЗ кого пришли. Один рекурсивный запрос дал
    бы соседей без направления и без промежуточных звеньев, то есть ответ, по
    которому нельзя проверить, почему сущность считается соседом.

Границы
    Темы (`theme`, `theme_member`) в этом модуле только читаются для вида
    `kind="theme"`, и честно: пока слой тем не собран, ответ — «тем нет» с
    причиной, а не пустой список. Сводки тем не читаются вовсе (этап 5).
"""

from __future__ import annotations

import itertools
import sqlite3
from typing import Any

from . import docgraph as dg
from . import store

#: Пределы, которые объявлены в ответе и проверяются до запроса.
MAX_DEPTH = 3
DEFAULT_DEPTH = 1
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
#: Сколько адресов показывать на один факт. Один — минимум для проверяемости;
#: три — достаточно, чтобы увидеть, что связь не единичная случайность.
MAX_CITATIONS = 3


def clamp_limit(limit: int | None) -> int:
    """Проверить предел, а не обрезать молча.

    Обрезка `limit=1000 → 200` выглядела бы как выполненный запрос: читатель
    решил бы, что получил все 1000. Поэтому значение вне диапазона — ошибка
    вызова с названным пределом.
    """
    value = DEFAULT_LIMIT if limit is None else int(limit)
    if value < 1 or value > MAX_LIMIT:
        raise ValueError(
            f"limit вне диапазона 1..{MAX_LIMIT}: {value}. Предел назван, а не "
            f"обрезан: иначе ответ выглядел бы полным"
        )
    return value


def check_depth(depth: int | None) -> int:
    """Глубина обхода: только 1..3, иначе ошибка вызова."""
    value = DEFAULT_DEPTH if depth is None else int(depth)
    if value < 1 or value > MAX_DEPTH:
        raise ValueError(
            f"depth вне диапазона 1..{MAX_DEPTH}: {value}. Глубина ограничена: "
            f"на четвёртом шаге соседом становится почти вся сущностная сеть"
        )
    return value


def check_kind(kind: str | None) -> str:
    """Вид соседей: `entity` или `theme`."""
    value = str(kind or "entity").strip().casefold()
    if value not in ("entity", "theme"):
        raise ValueError(f"kind: ожидается entity или theme, получено {kind!r}")
    return value


# ---------------------------------------------------------------------------
# Разрешение имени сущности
# ---------------------------------------------------------------------------


def resolve_entity(conn: sqlite3.Connection, rev_id: str, ref: str) -> dict[str, Any] | None:
    """Найти сущность по `entity_id` или имени.

    Неоднозначность не скрывается: если имя есть у нескольких сущностей, берётся
    та, у которой больше упоминаний (при равенстве — меньший `entity_id`, чтобы
    выбор был детерминирован), а число остальных попадает в `alternatives`.
    «Выбрал первую попавшуюся» без этого поля означало бы, что читатель не узнает
    о существовании второго значения.
    """
    text = str(ref or "").strip()
    if not text:
        return None
    row = conn.execute(
        "SELECT entity_id, name, norm_name, kind, mentions, first_path, first_line "
        "FROM entity WHERE rev_id = ? AND entity_id = ?", (rev_id, text)
    ).fetchone()
    if row is None:
        rows = conn.execute(
            "SELECT entity_id, name, norm_name, kind, mentions, first_path, first_line "
            "FROM entity WHERE rev_id = ? AND norm_name = ? "
            "ORDER BY mentions DESC, entity_id", (rev_id, dg.norm_name(text))
        ).fetchall()
        if not rows:
            return None
        row = rows[0]
        alternatives = len(rows) - 1
    else:
        alternatives = 0
    return {
        "entity_id": str(row["entity_id"]),
        "name": str(row["name"]),
        "kind": str(row["kind"]),
        "mentions": int(row["mentions"] or 0),
        "first_path": row["first_path"],
        "first_line": row["first_line"],
        "alternatives": alternatives,
    }


def _mention_rows(conn: sqlite3.Connection, rev_id: str, entity_id: str,
                  *, path: str | None = None, chunk_id: str | None = None,
                  limit: int = MAX_CITATIONS) -> list[dict[str, Any]]:
    """Упоминания сущности — это и есть адреса факта."""
    sql = ("SELECT chunk_id, path, start_line, end_line, char_start, char_end "
           "FROM entity_mention WHERE rev_id = ? AND entity_id = ?")
    params: list[Any] = [rev_id, entity_id]
    if path is not None:
        sql += " AND path = ?"
        params.append(path)
    if chunk_id is not None:
        sql += " AND chunk_id = ?"
        params.append(chunk_id)
    sql += " ORDER BY path, start_line, char_start LIMIT ?"
    params.append(int(limit))
    out = []
    for row in conn.execute(sql, params).fetchall():
        out.append({
            "path": str(row["path"]),
            "start_line": int(row["start_line"]),
            "end_line": int(row["end_line"]),
            "chunk_id": str(row["chunk_id"]),
            "char_start": int(row["char_start"]),
            "char_end": int(row["char_end"]),
            "locator": locator(row["path"], row["start_line"], row["end_line"]),
        })
    return out


def locator(path: Any, start: Any, end: Any) -> str:
    """Адрес одной строкой: ровно та форма, которую принимает `doc_get`."""
    try:
        start_i, end_i = int(start), int(end)
    except (TypeError, ValueError):
        return str(path or "")
    return f"{path}:{start_i}" if end_i <= start_i else f"{path}:{start_i}-{end_i}"


# ---------------------------------------------------------------------------
# Операция 1: соседи сущности
# ---------------------------------------------------------------------------


def entity_neighbors(conn: sqlite3.Connection, rev_id: str, entity: str,
                     depth: int = DEFAULT_DEPTH, kind: str = "entity",
                     limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Соседи сущности с адресами. `depth` — шаги обхода, `kind` — вид соседей."""
    depth = check_depth(depth)
    limit = clamp_limit(limit)
    kind = check_kind(kind)
    start = resolve_entity(conn, rev_id, entity)
    if start is None:
        return {
            "query": {"entity": entity, "depth": depth, "kind": kind, "limit": limit},
            "entity": None,
            "neighbors": [],
            "rejected_no_citation": 0,
            "truncated": False,
            "reason": (f"сущность {entity!r} в слое ревизии {rev_id} не найдена: "
                       f"её нет среди извлечённых упоминаний"),
        }

    if kind == "theme":
        # Темы собираются этапом 5. Пока таблица пуста, ответ «тем нет» с
        # причиной — а не пустой список, который читался бы как «связей нет».
        themes = _themes_of(conn, rev_id, start["entity_id"], limit=limit)
        return {
            "query": {"entity": entity, "depth": depth, "kind": kind, "limit": limit},
            "entity": start,
            "neighbors": themes,
            "rejected_no_citation": 0,
            "truncated": False,
            "reason": ("" if themes else
                       "тем в слое ревизии нет: слой тем собирается этапом 5 "
                       "плана «Агентские интеграции и документный граф»"),
        }

    neighbors, rejected, truncated = _walk(conn, rev_id, start["entity_id"],
                                          depth=depth, limit=limit)
    return {
        "query": {"entity": entity, "depth": depth, "kind": kind, "limit": limit},
        "entity": start,
        "neighbors": neighbors,
        "rejected_no_citation": rejected,
        "truncated": truncated,
        "reason": ("" if neighbors else
                   "связей у сущности нет: в слое нет ни одного отношения, "
                   "где она участвует"),
    }


def _walk(conn: sqlite3.Connection, rev_id: str, start_id: str, *, depth: int,
          limit: int) -> tuple[list[dict[str, Any]], int, bool]:
    """Обход в ширину с адресом у каждого шага. Возвращает (соседи, отказы, обрезано)."""
    seen: set[str] = {start_id}
    frontier: list[str] = [start_id]
    out: list[dict[str, Any]] = []
    rejected = 0
    truncated = False

    for step in range(1, depth + 1):
        next_frontier: list[str] = []
        for current in frontier:
            rows = conn.execute(
                "SELECT rel_id, src_entity_id, dst_entity_id, kind, chunk_id, path, "
                "start_line, end_line, weight FROM entity_relation "
                "WHERE rev_id = ? AND (src_entity_id = ? OR dst_entity_id = ?) "
                "ORDER BY kind, path, start_line, rel_id",
                (rev_id, current, current),
            ).fetchall()
            for row in rows:
                outgoing = str(row["src_entity_id"]) == current
                other = str(row["dst_entity_id"]) if outgoing else str(row["src_entity_id"])
                if other in seen:
                    continue
                other_row = conn.execute(
                    "SELECT name, kind, mentions FROM entity "
                    "WHERE rev_id = ? AND entity_id = ?", (rev_id, other)
                ).fetchone()
                if other_row is None:
                    # Конец отношения без сущности — битая ссылка слоя, а не сосед.
                    rejected += 1
                    continue
                citation = {
                    "path": str(row["path"]),
                    "start_line": int(row["start_line"]),
                    "end_line": int(row["end_line"]),
                    "chunk_id": str(row["chunk_id"]),
                    "locator": locator(row["path"], row["start_line"], row["end_line"]),
                    "source": "relation",
                    "relation_id": str(row["rel_id"]),
                }
                # Адрес самой связи обязателен: без него факт не выдаётся.
                if not citation["path"] or not citation["start_line"]:
                    rejected += 1
                    continue
                seen.add(other)
                next_frontier.append(other)
                out.append({
                    "entity_id": other,
                    "name": str(other_row["name"]),
                    "kind": str(other_row["kind"]),
                    "mentions": int(other_row["mentions"] or 0),
                    "relation": str(row["kind"]),
                    "direction": "out" if outgoing else "in",
                    "weight": float(row["weight"] or 1.0),
                    "step": step,
                    "via": current,
                    "citations": [citation],
                    "entity_citations": _mention_rows(conn, rev_id, other,
                                                      limit=MAX_CITATIONS),
                })
                if len(out) >= limit:
                    truncated = True
                    return out, rejected, truncated
        frontier = next_frontier
        if not frontier:
            break
    return out, rejected, truncated


def _themes_of(conn: sqlite3.Connection, rev_id: str, entity_id: str,
               *, limit: int) -> list[dict[str, Any]]:
    """Темы, в которые входит сущность. Пусто, пока слой тем не собран."""
    try:
        rows = conn.execute(
            "SELECT t.theme_id, t.title, t.size, m.weight FROM theme_member m "
            "JOIN theme t ON t.rev_id = m.rev_id AND t.theme_id = m.theme_id "
            "WHERE m.rev_id = ? AND m.entity_id = ? "
            "ORDER BY m.weight DESC, t.theme_id LIMIT ?",
            (rev_id, entity_id, int(limit)),
        ).fetchall()
    except sqlite3.Error:
        return []
    return [{
        "theme_id": str(row["theme_id"]),
        "title": str(row["title"]),
        "size": int(row["size"] or 0),
        "weight": float(row["weight"] or 1.0),
        # Своих адресов у темы нет: участники и их цитаты — этап 5.
        "citations": [],
    } for row in rows]


# ---------------------------------------------------------------------------
# Операция 2: связи документов через общие сущности
# ---------------------------------------------------------------------------


def doc_relations(conn: sqlite3.Connection, rev_id: str, *, doc: str | None = None,
                  entity: str | None = None,
                  limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Пары документов, связанных общими сущностями, с адресами с обеих сторон.

    Вход — документ (`doc`) либо сущность (`entity`); задать нужно ровно одно.
    Связь «документ A ↔ документ B» выдаётся только тогда, когда у связующей
    сущности есть упоминание В ОБОИХ документах: иначе адрес есть с одной
    стороны, а вторая остаётся утверждением.

    Что значит каждый вход:
      * `doc=A` — пары «A и другой документ», где общая сущность упомянута в
        обоих;
      * `entity=E` — пары документов, которые ОБА упоминают E.
    Два входа не сливаются в один запрос: у них разный смысл ответа, и общий
    запрос пришлось бы объяснять читателю вместо того, чтобы ответить.
    """
    limit = clamp_limit(limit)
    if bool(doc) == bool(entity):
        raise ValueError(
            "doc_relations: нужен ровно один вход — doc (путь) или entity (имя/id); "
            f"получено doc={doc!r}, entity={entity!r}"
        )

    query = {"doc": doc, "entity": entity, "limit": limit}
    resolved: dict[str, Any] | None = None
    empty = {"query": query, "entity": None, "pairs": [],
             "rejected_no_citation": 0, "truncated": False}
    if entity:
        resolved = resolve_entity(conn, rev_id, str(entity))
        if resolved is None:
            return {**empty, "reason": f"сущность {entity!r} в слое не найдена"}
        paths = [str(row["path"]) for row in conn.execute(
            "SELECT DISTINCT path FROM entity_mention WHERE rev_id = ? AND entity_id = ? "
            "ORDER BY path", (rev_id, resolved["entity_id"]),
        ).fetchall()]
        if len(paths) < 2:
            return {**empty, "entity": resolved,
                    "reason": (f"сущность упомянута в документов: {len(paths)}. "
                               f"Связь между документами требует минимум двух")}
        pairs = []
        rejected = 0
        for first, second in itertools.combinations(paths, 2):
            citations, missing = _both_sides(conn, rev_id, resolved["entity_id"],
                                            first, second)
            if missing:
                rejected += 1
                continue
            pairs.append({
                "from": first, "to": second, "shared": 1,
                "via_entities": [{"entity_id": resolved["entity_id"],
                                  "name": resolved["name"]}],
                "citations": citations,
            })
        pairs.sort(key=lambda item: (item["from"], item["to"]))
        truncated = len(pairs) > limit
        return {"query": query, "entity": resolved, "pairs": pairs[:limit],
                "rejected_no_citation": rejected, "truncated": truncated,
                "reason": "" if pairs else "пар документов через эту сущность нет"}

    path = str(doc)
    entity_rows = conn.execute(
        "SELECT DISTINCT entity_id FROM entity_mention WHERE rev_id = ? AND path = ? "
        "ORDER BY entity_id", (rev_id, path),
    ).fetchall()
    if not entity_rows:
        return {**empty, "reason": (f"документ {path!r} в слое не упомянут: у него "
                                    f"нет ни одной извлечённой сущности")}
    entity_ids = [str(row["entity_id"]) for row in entity_rows]
    marks = ",".join("?" for _ in entity_ids)
    rows = conn.execute(
        f"SELECT entity_id, path FROM entity_mention WHERE rev_id = ? "
        f"AND entity_id IN ({marks}) AND path <> ? GROUP BY entity_id, path "
        f"ORDER BY path, entity_id", (rev_id, *entity_ids, path),
    ).fetchall()

    by_other: dict[str, list[str]] = {}
    for row in rows:
        by_other.setdefault(str(row["path"]), []).append(str(row["entity_id"]))

    names = _names_of(conn, rev_id, set(entity_ids))
    pairs = []
    rejected = 0
    for other, shared in sorted(by_other.items()):
        citations: list[dict[str, Any]] = []
        usable: list[str] = []
        for eid in shared:
            both, missing = _both_sides(conn, rev_id, eid, path, other)
            if missing:
                rejected += 1
                continue
            usable.append(eid)
            citations.extend(both)
        if not usable:
            continue
        pairs.append({
            "from": path, "to": other, "shared": len(usable),
            "via_entities": [{"entity_id": eid, "name": names.get(eid, "")}
                             for eid in usable],
            "citations": citations,
        })
    pairs.sort(key=lambda item: (-int(item["shared"]), item["to"]))
    truncated = len(pairs) > limit
    return {
        "query": query, "entity": None, "pairs": pairs[:limit],
        "rejected_no_citation": rejected, "truncated": truncated,
        "reason": "" if pairs else (
            "общих сущностей с другими документами нет: связей через слой не найдено"),
    }


def _both_sides(conn: sqlite3.Connection, rev_id: str, entity_id: str,
                first: str, second: str) -> tuple[list[dict[str, Any]], bool]:
    """Адреса упоминания сущности с ОБЕИХ сторон пары документов.

    Возвращает (адреса, есть_пропуск). Если хотя бы одной стороны нет, связь не
    выдаётся: адрес с одной стороны и утверждение вместо адреса с другой — это
    ровно то, ради чего слой заведён, только наоборот.
    """
    out: list[dict[str, Any]] = []
    missing = False
    for side, path in (("from", first), ("to", second)):
        rows = _mention_rows(conn, rev_id, entity_id, path=path, limit=1)
        if not rows:
            missing = True
            continue
        out.append({"side": side, "entity_id": entity_id, **rows[0]})
    return out, missing


def _names_of(conn: sqlite3.Connection, rev_id: str,
              ids: set[str]) -> dict[str, str]:
    """Имена сущностей одним запросом: по одному на каждую было бы N запросов."""
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT entity_id, name FROM entity WHERE rev_id = ? AND entity_id IN ({marks})",
        (rev_id, *sorted(ids)),
    ).fetchall()
    return {str(row["entity_id"]): str(row["name"]) for row in rows}


# ---------------------------------------------------------------------------
# Состояние слоя: собран ли он и есть ли в нём факты
# ---------------------------------------------------------------------------


def layer_state(conn: sqlite3.Connection | None, rev_id: str) -> dict[str, Any]:
    """Собран ли слой и сколько в нём фактов. Без догадок о причине.

    `conn=None` означает «таблиц слоя нет вовсе» — это отдельное состояние от
    «таблица есть, строк ноль»: первое требует прохода, второе — проверки
    настройки LLM или корпуса.
    """
    if conn is None:
        return {"built": False, "entities": 0, "mentions": 0, "relations": 0,
                "reason": ("слой документного графа в этой сборке не создан: нужен "
                           "проход `docgraph build` (или `index --doc-graph`)")}
    counts = {"entities": 0, "mentions": 0, "relations": 0}
    for key, table in (("entities", "entity"), ("mentions", "entity_mention"),
                       ("relations", "entity_relation")):
        if not store.table_exists(conn, table):
            return {"built": False, **counts,
                    "reason": f"таблицы {table} нет: слой не создан"}
        try:
            row = conn.execute(
                f"SELECT COUNT(*) AS c FROM {table} WHERE rev_id = ?", (rev_id,)
            ).fetchone()
        except sqlite3.Error as exc:
            return {"built": False, **counts,
                    "reason": f"таблица {table} не читается: {exc}"}
        counts[key] = int(row["c"] if hasattr(row, "keys") else row[0])
    built = counts["entities"] > 0
    return {
        "built": built,
        **counts,
        "reason": "" if built else (
            "слой создан, но фактов в нём нет: проход не нашёл сущностей, "
            "процитированных из чанков"),
    }
