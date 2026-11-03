"""Темы документного графа: детерминированные кластеры и сводки по цитатам.

Зачем темы
    Слой фактов отвечает на вопрос «кто с кем рядом» (`entity_neighbors`) и «какие
    документы связаны» (`doc_relations`). Он не отвечает на вопрос «о чём этот
    корпус»: у сущностей нет ни групп, ни веса, ни заголовка. Тема — это группа
    сущностей, которые встречаются вместе чаще случайного, плюс адреса, по которым
    её можно проверить.

Почему кластеризация детерминированная
    Leiden и любые вероятностные схемы дают разный результат на одном входе. Для
    нас это неприемлемо: `theme_id` должен быть одинаковым на любой машине и в
    любой ревизии при том же слое, иначе тема «переезжает» между прогонами и
    сравнивать нечего. Поэтому: граф со-встречаемости (ребро — совместное
    упоминание в одном чанке, вес — число чанков), отсечение слабых рёбер,
    компоненты связности, разрез крупной компоненты по детерминированному порядку.
    `theme_id` = sha1(seed_entity_id + '|' + отсортированные участники) — та же
    формула, что в `docgraph.theme_id`, поэтому запись и чтение не расходятся.

Почему сводка отдельно от темы
    Тема, участники и адреса считаются без модели и бесплатно. Сводка — платный
    вызов и единственная часть, которая может быть недоступна. Поэтому сводка
    лежит в отдельной таблице, её отсутствие названо полем (`summary=null`), а не
    пустой строкой, и без цитат она не выдаётся вовсе.

Границы
    Модуль не решает, что показать: он строит, читает и пишет. Порядок ответа и
    отказ по изданию — в фасаде.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import docgraph as dg
from . import docgraph_ops as dgo
from . import store

#: Пределы объявлены в ответе и проверяются до работы.
MIN_WEIGHT_DEFAULT = 2
MAX_SIZE = 40
MAX_THEMES = 50
MAX_CITATIONS = 3
MAX_QUOTES_PER_THEME = 8
MAX_SUMMARY_CHARS = 600

#: Версия промпта сводки. Входит в ключ кэша и в запись сводки: правка текста —
#: новый вход, старые сводки перестают считаться свежими по отпечатку.
SUMMARY_PROMPT_VERSION = "themes-v1"

#: Коды неполноты. Машинные: по ним считается, почему тем или сводок нет.
DEGRADE_EDITION = "edition"
DEGRADE_LAYER = "layer_empty"
DEGRADE_NO_COOCCURRENCE = "no_cooccurrence"
DEGRADE_NO_THEMES = "themes_not_built"
DEGRADE_NO_LLM = "llm_unavailable"
DEGRADE_BAD_SUMMARY = "summary_rejected"

SUMMARY_INSTRUCTION = """Ты пишешь короткую сводку темы по цитатам из документов.

Отвечай ТОЛЬКО JSON-объектом такой формы:
{"summary": "…", "citations": ["path:строка-строка", …]}

Правила:
1. summary — 1–3 предложения о том, что общего у приведённых цитат. Не пересказывай
   каждую цитату по отдельности.
2. citations — адреса ТОЛЬКО из списка, который дан во входе. Придуманный адрес —
   ошибка, а не догадка.
3. Если цитаты не связаны общей мыслью, верни {"summary": "", "citations": []}.
4. Никаких пояснений и markdown вокруг JSON."""


def _rows_ready(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Именованный доступ к колонкам независимо от того, кто открыл соединение.

    Модуль вызывают и из индексатора (`store.connect`, где `row_factory` уже
    Row), и из проверок, и из CLI. `row["entity_id"]` на обычном соединении
    падает с `TypeError: tuple indices must be integers` — эта ошибка стоила
    одного прогона набора, поэтому доступ к колонкам приводится здесь, а не
    надеждой на дисциплину вызывающего.
    """
    if conn.row_factory is None:
        conn.row_factory = sqlite3.Row
    return conn


def check_weight(value: Any) -> int:
    """Порог веса ребра: целое ≥ 1, иначе ошибка вызова."""
    weight = MIN_WEIGHT_DEFAULT if value is None else int(value)
    if weight < 1:
        raise ValueError(f"min_weight вне диапазона ≥ 1: {weight}")
    return weight


def describe_degraded(code: str, reason: str) -> str:
    """Причина неполноты тем — словами для человека."""
    if code == DEGRADE_EDITION:
        return (f"темы закрыты изданием: {reason}. Это уровень документа, а не "
                "поиска: локальные факты и граф соседей работают отдельно")
    if code == DEGRADE_LAYER:
        return f"тем нет: {reason}. Слой фактов собирается проходом `docgraph build`"
    if code == DEGRADE_NO_THEMES:
        return (f"тем нет: {reason}. Слой собран, но темы в нём не построены — "
                "пересобрать: `ragraf docgraph themes` (бесплатно, без модели)")
    if code == DEGRADE_NO_COOCCURRENCE:
        return (f"тем нет: {reason}. Меньше порога совместных упоминаний: "
                "снижать порог — решение, а не настройка по умолчанию")
    if code == DEGRADE_NO_LLM:
        return (f"сводки недоступны: {reason}. Темы, участники и адреса на месте — "
                "сводка единственная платная часть")
    if code == DEGRADE_BAD_SUMMARY:
        return f"сводка отвергнута: {reason}. В слой записана не была"
    return f"темы ограничены: {code} ({reason})"


# ---------------------------------------------------------------------------
# Сборка: детерминированные кластеры
# ---------------------------------------------------------------------------


def _rows(conn: sqlite3.Connection, rev_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT m.entity_id, m.chunk_id, m.path, m.start_line, m.end_line, "
        "m.char_start, m.char_end, e.name, e.norm_name, e.mentions "
        "FROM entity_mention m JOIN entity e ON e.rev_id = m.rev_id "
        "AND e.entity_id = m.entity_id WHERE m.rev_id = ? "
        "ORDER BY m.chunk_id, m.entity_id", (rev_id,),
    ).fetchall()


def build(conn: sqlite3.Connection, rev_id: str, *,
          min_weight: int = MIN_WEIGHT_DEFAULT, max_size: int = MAX_SIZE,
          store_rows: bool = True) -> dict[str, Any]:
    """Построить темы из слоя. Один и тот же слой даёт один и тот же набор.

    Возвращает `{themes: [...], edges, components, truncated, stored, reason}`.
    Запись идёт через `docgraph.add_theme`: тема без участников отказывает там, а
    не здесь, — одно правило на оба пути.
    """
    min_weight = check_weight(min_weight)
    max_size = max(1, int(max_size))
    result: dict[str, Any] = {"themes": [], "mentions": 0, "edges": 0,
                              "kept_edges": 0, "components": 0, "truncated": False,
                              "stored": 0, "reason": "", "min_weight": min_weight,
                              "max_size": max_size}
    if conn is None:
        result["reason"] = "слоя нет: таблицы упоминаний не читаются"
        return result
    conn = _rows_ready(conn)
    try:
        rows = _rows(conn, rev_id)
    except sqlite3.Error as exc:
        result["reason"] = f"таблица упоминаний не читается: {exc}"
        return result
    result["mentions"] = len(rows)
    if not rows:
        result["reason"] = "в слое нет упоминаний: собирать темы не из чего"
        return result

    info: dict[str, dict[str, Any]] = {}
    by_chunk: dict[str, set[str]] = {}
    for row in rows:
        entity_id = str(row["entity_id"])
        slot = info.get(entity_id)
        if slot is None:
            info[entity_id] = {
                "entity_id": entity_id,
                "name": str(row["name"]),
                "norm_name": str(row["norm_name"]),
                "mentions": int(row["mentions"] or 0),
            }
        by_chunk.setdefault(str(row["chunk_id"]), set()).add(entity_id)

    weights: dict[tuple[str, str], int] = {}
    for chunk_entities in by_chunk.values():
        members = sorted(chunk_entities)
        for index, left in enumerate(members):
            for right in members[index + 1:]:
                key = (left, right)
                weights[key] = weights.get(key, 0) + 1
    result["edges"] = len(weights)
    kept = {key: value for key, value in weights.items() if value >= min_weight}
    result["kept_edges"] = len(kept)
    if not kept:
        result["reason"] = (f"нет ни одной пары сущностей с весом ≥ {min_weight}: "
                            f"пар всего {len(weights)}")
        return result

    adjacency: dict[str, set[str]] = {}
    strength: dict[str, int] = {}
    for (left, right), value in kept.items():
        adjacency.setdefault(left, set()).add(right)
        adjacency.setdefault(right, set()).add(left)
        strength[left] = strength.get(left, 0) + value
        strength[right] = strength.get(right, 0) + value

    seen: set[str] = set()
    groups: list[list[str]] = []
    for start in sorted(adjacency):
        if start in seen:
            continue
        stack, component = [start], []
        seen.add(start)
        while stack:
            current = stack.pop()
            component.append(current)
            for other in sorted(adjacency.get(current, ())):
                if other not in seen:
                    seen.add(other)
                    stack.append(other)
        groups.append(sorted(component))
    result["components"] = len(groups)

    themes: list[dict[str, Any]] = []
    for component in sorted(groups):
        # Разрез крупной компоненты. Порядок детерминирован: сначала сила связей,
        # потом нормализованное имя, потом идентификатор — при полном равенстве
        # выбор не зависит от порядка обхода.
        order = sorted(component, key=lambda eid: (
            -strength.get(eid, 0), info[eid]["norm_name"], eid))
        for index in range(0, len(order), max_size):
            part = order[index:index + max_size]
            if len(part) < 2:
                continue
            if index + max_size < len(order):
                result["truncated"] = True
            seed = sorted(part, key=lambda eid: (
                -info[eid]["mentions"], info[eid]["norm_name"], eid))[0]
            members = {eid: float(strength.get(eid, 1)) for eid in part}
            themes.append({
                "theme_id": dg.theme_id(seed, members),
                "title": info[seed]["norm_name"] or info[seed]["name"],
                "seed_entity_id": seed,
                "size": len(part),
                "members": [
                    {"entity_id": eid, "name": info[eid]["name"],
                     "mentions": info[eid]["mentions"],
                     "weight": members[eid]}
                    for eid in sorted(part, key=lambda e: (-members[e], info[e]["norm_name"]))
                ],
            })
    themes.sort(key=lambda item: (-item["size"], item["theme_id"]))
    result["themes"] = themes
    if not themes:
        result["reason"] = "компонент с двумя и более участниками нет"
        return result

    if store_rows:
        try:
            conn.execute("DELETE FROM theme WHERE rev_id = ?", (rev_id,))
            conn.execute("DELETE FROM theme_member WHERE rev_id = ?", (rev_id,))
            # Сводки опираются на участников: после пересборки они неактуальны,
            # и оставлять их значило бы показывать текст о другом составе темы.
            conn.execute("DELETE FROM theme_summary WHERE rev_id = ?", (rev_id,))
            for theme in themes:
                dg.add_theme(conn, rev_id, title=theme["title"],
                             seed_entity_id=theme["seed_entity_id"],
                             members={m["entity_id"]: m["weight"]
                                      for m in theme["members"]},
                             built_by="deterministic:cooccurrence",
                             commit=False, with_schema=True)
            conn.commit()
            result["stored"] = len(themes)
        except sqlite3.Error as exc:
            conn.rollback()
            result["reason"] = f"темы не записаны: {exc}"
    return result


# ---------------------------------------------------------------------------
# Чтение: темы с адресами
# ---------------------------------------------------------------------------


def _mentions_of(conn: sqlite3.Connection, rev_id: str, entity_id: str,
                 *, limit: int = MAX_CITATIONS) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT chunk_id, path, start_line, end_line FROM entity_mention "
        "WHERE rev_id = ? AND entity_id = ? ORDER BY path, start_line LIMIT ?",
        (rev_id, entity_id, int(limit)),
    ).fetchall()
    out = []
    for row in rows:
        out.append({
            "chunk_id": str(row["chunk_id"]),
            "path": str(row["path"]),
            "start_line": int(row["start_line"]),
            "end_line": int(row["end_line"]),
            "locator": dgo.locator(row["path"], row["start_line"], row["end_line"]),
        })
    return out


def _summary_of(conn: sqlite3.Connection, rev_id: str,
                theme_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT model, prompt_version, text, citations_json, created_at "
        "FROM theme_summary WHERE rev_id = ? AND theme_id = ?", (rev_id, theme_id),
    ).fetchone()
    if row is None or not str(row["text"] or "").strip():
        return None
    try:
        citations = json.loads(row["citations_json"] or "[]")
    except ValueError:
        citations = []
    if not isinstance(citations, list) or not citations:
        # Сводка без цитат не выдаётся: её нечем проверить.
        return None
    return {"text": str(row["text"]), "citations": [str(c) for c in citations],
            "model": str(row["model"]), "prompt_version": str(row["prompt_version"]),
            "created_at": str(row["created_at"])}


def read(conn: sqlite3.Connection | None, rev_id: str, *, scope: str | None = None,
         limit: int = MAX_THEMES) -> dict[str, Any]:
    """Темы слоя с участниками, адресами и сводкой (если она есть)."""
    limit = max(1, min(int(limit), MAX_THEMES))
    result: dict[str, Any] = {"themes": [], "count": 0, "scope": scope or "",
                              "limit": limit, "reason": "", "summaries": 0,
                              "themes_total": 0}
    if conn is None:
        result["reason"] = "слоя нет: таблицы тем не читаются"
        return result
    conn = _rows_ready(conn)
    try:
        total = conn.execute("SELECT COUNT(*) AS c FROM theme WHERE rev_id = ?",
                             (rev_id,)).fetchone()
        result["themes_total"] = int(total["c"] if hasattr(total, "keys") else total[0])
        rows = conn.execute(
            "SELECT theme_id, title, seed_entity_id, size FROM theme WHERE rev_id = ? "
            "ORDER BY size DESC, theme_id LIMIT ?", (rev_id, limit),
        ).fetchall()
    except sqlite3.Error as exc:
        result["reason"] = f"таблица тем не читается: {exc}"
        return result

    prefix = str(scope or "").strip().replace("\\", "/")
    for row in rows:
        theme_id = str(row["theme_id"])
        member_rows = conn.execute(
            "SELECT m.entity_id, e.name, e.mentions, m.weight FROM theme_member m "
            "JOIN entity e ON e.rev_id = m.rev_id AND e.entity_id = m.entity_id "
            "WHERE m.rev_id = ? AND m.theme_id = ? ORDER BY m.weight DESC, e.name",
            (rev_id, theme_id),
        ).fetchall()
        members = []
        citations: list[str] = []
        for member in member_rows:
            mentions = _mentions_of(conn, rev_id, str(member["entity_id"]))
            if prefix and not any(str(m["path"]).startswith(prefix) for m in mentions):
                continue
            members.append({
                "entity_id": str(member["entity_id"]),
                "name": str(member["name"]),
                "mentions": int(member["mentions"] or 0),
                "weight": float(member["weight"] or 1.0),
                "citations": [m["locator"] for m in mentions],
                "locator": mentions[0]["locator"] if mentions else "",
            })
            for mention in mentions:
                if mention["locator"] not in citations and len(citations) < MAX_CITATIONS:
                    citations.append(mention["locator"])
        if not members:
            # Область сузила тему до пустоты — это не тема этой области.
            continue
        summary = _summary_of(conn, rev_id, theme_id)
        if summary:
            result["summaries"] += 1
        result["themes"].append({
            "theme_id": theme_id,
            "title": str(row["title"]),
            "seed_entity_id": str(row["seed_entity_id"]),
            "size": int(row["size"] or 0),
            "size_in_scope": len(members),
            "members": members,
            "top_entities": members[:5],
            "citations": citations,
            "summary": summary,
        })
    result["count"] = len(result["themes"])
    if not result["themes"] and not result["reason"]:
        result["reason"] = (
            "тем в слое нет: либо сборка тем не выполнялась, либо пар с весом "
            "≥ порога не нашлось — проверить: `ragraf docgraph themes`"
            if result["themes_total"] == 0 else
            "в указанной области ни одна тема не упоминается")
    return result


def overview(conn: sqlite3.Connection | None, rev_id: str, *,
             limit: int = MAX_THEMES) -> dict[str, Any]:
    """Карта документного графа: сколько чего и какие темы главные."""
    limit = max(1, min(int(limit), MAX_THEMES))
    counts = {"documents": 0, "entities": 0, "mentions": 0, "relations": 0,
              "themes": 0, "summaries": 0}
    result: dict[str, Any] = {"counts": counts, "top_themes": [], "reason": "",
                              "limit": limit, "degraded": ""}
    if conn is None:
        result["reason"] = "слоя нет: таблицы графа не читаются"
        return result
    conn = _rows_ready(conn)
    for key, table in (("documents", "document"), ("entities", "entity"),
                       ("mentions", "entity_mention"),
                       ("relations", "entity_relation"), ("themes", "theme"),
                       ("summaries", "theme_summary")):
        if not store.table_exists(conn, table):
            result["reason"] = f"таблицы {table} нет: слой не создан"
            return result
        try:
            row = conn.execute(f"SELECT COUNT(*) AS c FROM {table} WHERE rev_id = ?",
                               (rev_id,)).fetchone()
        except sqlite3.Error as exc:
            result["reason"] = f"таблица {table} не читается: {exc}"
            return result
        counts[key] = int(row["c"] if hasattr(row, "keys") else row[0])
    if counts["entities"] == 0:
        result["reason"] = "слой создан, но фактов в нём нет"
        result["degraded"] = DEGRADE_LAYER
        return result
    if counts["themes"] == 0:
        result["degraded"] = DEGRADE_NO_THEMES
        result["reason"] = ("слой фактов собран, тем нет: сборка тем — "
                           "`ragraf docgraph themes` (без модели)")
    themes = read(conn, rev_id, limit=limit)
    result["top_themes"] = [
        {"theme_id": item["theme_id"], "title": item["title"], "size": item["size"],
         "citations": item["citations"][:MAX_CITATIONS],
         "summary": (item["summary"] or {}).get("text", "")}
        for item in themes["themes"]
    ]
    if counts["themes"] and counts["summaries"] < counts["themes"]:
        result.setdefault("notes", []).append(
            f"сводок {counts['summaries']} из {counts['themes']} тем: остальные "
            "без модели (сводка — единственная платная часть)")
    return result


# ---------------------------------------------------------------------------
# Сводки: LLM по цитатам участников
# ---------------------------------------------------------------------------


def _quotes(conn: sqlite3.Connection, rev_id: str, theme: dict[str, Any]) -> list[dict]:
    """Цитаты участников: текст берётся из чанка по смещениям упоминания.

    Копия текста в слой не кладётся (иначе слой станет вторым корпусом и
    разойдётся с ним), поэтому цитата собирается здесь и живёт только в запросе.
    """
    out: list[dict] = []
    for member in theme["members"]:
        rows = conn.execute(
            "SELECT m.chunk_id, m.path, m.start_line, m.end_line, m.char_start, "
            "m.char_end, t.text FROM entity_mention m "
            "JOIN chunk_text t ON t.rev_id = m.rev_id AND t.chunk_id = m.chunk_id "
            "WHERE m.rev_id = ? AND m.entity_id = ? ORDER BY m.path, m.start_line "
            "LIMIT 2", (rev_id, member["entity_id"]),
        ).fetchall()
        for row in rows:
            text = str(row["text"] or "")
            start = int(row["char_start"] or 0)
            end = int(row["char_end"] or 0)
            quote = text[start:end].strip() if end > start else ""
            if not quote:
                continue
            out.append({
                "entity_id": member["entity_id"],
                "entity": member["name"],
                "locator": dgo.locator(row["path"], row["start_line"], row["end_line"]),
                "quote": quote,
            })
            if len(out) >= MAX_QUOTES_PER_THEME:
                return out
    return out


def _parse_summary(text: str, allowed: set[str]) -> tuple[str, list[str]]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    payload: Any
    try:
        payload = json.loads(raw)
    except ValueError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("в ответе нет JSON-объекта") from None
        payload = json.loads(raw[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("ответ не объект JSON")
    summary = " ".join(str(payload.get("summary") or "").split())
    citations = payload.get("citations")
    if not isinstance(citations, list):
        citations = []
    clean = [str(c).strip() for c in citations if str(c).strip()]
    if not summary:
        raise ValueError("сводка пуста")
    if len(summary) > MAX_SUMMARY_CHARS:
        raise ValueError(f"сводка длиннее {MAX_SUMMARY_CHARS} символов")
    if not clean:
        raise ValueError("сводка без цитат")
    unknown = [c for c in clean if c not in allowed]
    if unknown:
        raise ValueError(f"цитаты вне списка: {unknown[:2]}")
    return summary, clean


def summaries(conn: sqlite3.Connection | None, rev_id: str, client,
              *, limit: int = MAX_THEMES, prompt_version: str = SUMMARY_PROMPT_VERSION,
              model: str = "") -> dict[str, Any]:
    """Сводки тем по цитатам. Без модели — названная причина, не пустой текст."""
    result: dict[str, Any] = {"written": 0, "skipped": 0, "rejected": 0,
                              "reason": "", "code": "", "notes": []}
    if conn is None:
        result.update(code=DEGRADE_LAYER, reason="слоя нет")
        return result
    if client is None:
        result.update(code=DEGRADE_NO_LLM, reason="клиент LLM не передан")
        return result
    themes = read(conn, rev_id, limit=limit)["themes"]
    if not themes:
        result.update(code=DEGRADE_NO_THEMES, reason="тем в слое нет")
        return result
    seen_codes: dict[str, int] = {}
    for theme in themes:
        quotes = _quotes(conn, rev_id, theme)
        if not quotes:
            result["skipped"] += 1
            continue
        allowed = {q["locator"] for q in quotes}
        payload = {"title": theme["title"], "quotes": quotes}
        try:
            answer = client.complete(SUMMARY_INSTRUCTION,
                                     input_text=json.dumps(payload, ensure_ascii=False),
                                     purpose=f"themes:{theme['theme_id']}")
        except Exception as exc:  # noqa: BLE001 — отказ провайдера называется
            code = str(getattr(exc, "code", "") or "llm_error")
            seen_codes[code] = seen_codes.get(code, 0) + 1
            result["skipped"] += 1
            continue
        try:
            text, citations = _parse_summary(str(answer.get("text") or ""), allowed)
        except ValueError as exc:
            result["rejected"] += 1
            result["notes"].append(f"тема {theme['theme_id'][:8]}: {exc}")
            continue
        conn.execute(
            "INSERT OR REPLACE INTO theme_summary(rev_id, theme_id, model, "
            "prompt_version, text, citations_json, created_at) VALUES(?,?,?,?,?,?,?)",
            (rev_id, theme["theme_id"], model or "llm", prompt_version, text,
             json.dumps(citations, ensure_ascii=False), dg.util.now_iso()),
        )
        result["written"] += 1
    conn.commit()
    if not result["written"] and not result["skipped"] and not result["rejected"]:
        result.update(code=DEGRADE_BAD_SUMMARY, reason="ни одной сводки не получилось")
    for code, count in sorted(seen_codes.items()):
        result["notes"].append(f"отказов провайдера {code}: {count}")
    if result["skipped"] and not result["written"]:
        result.setdefault("code", DEGRADE_NO_LLM)
        result["reason"] = result["reason"] or "провайдер не дал ни одного ответа"
    return result
