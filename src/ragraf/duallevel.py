"""Dual-level поиск: два уровня одного запроса — «тема» и «конкретика».

Зачем два уровня
    Один запрос почти всегда состоит из двух разных вещей. «Как устроена выдача
    лицензий» — это ТЕМА (понятие, класс задачи), а «licence_file» — конкретный
    термин. Лексика ловит второе и не видит первого: слово «лицензия» может не
    встречаться в чанке ни разу, хотя чанк про лицензии. Обратное тоже верно:
    поиск по теме без терминов выдаёт верхний уровень и теряет точное место.

    Поэтому запрос один раз раскладывается на пару ключей (`high_level` —
    тема, `low_level` — конкретика), и каждый уровень ищет своим способом:
    конкретика — лексикой и вектором по чанкам, тема — сущностями слоя
    документного графа, у которых есть адреса упоминаний.

Почему ключи берутся у модели, а не регуляркой
    Разложить «как устроена выдача лицензий» на тему и термины — задача понимания,
    а не разбора строки. Замер на этом корпусе: регулярка по словам длиннее трёх
    символов давала в `low_level` служебные слова («устроена», «выдача»), и
    совпадений по ним было больше, чем по делу. Поэтому ключи извлекает модель по
    промпту со строгим JSON — и, если модели нет, dual-level НЕ включается:
    подмена уровня догадкой выдала бы догадку за факт.

Почему кэш обязателен
    Запрос повторяется: агент спрашивает одно и то же в разных формулировках и
    оболочках. Извлечение ключей — один платный вызов на запрос; без кэша каждый
    `search` с `mode=docs` стоил бы денег. Кэш адресуется тем же способом, что и
    кэш извлечения фактов (нормализованный промпт + модель + версия промпта),
    поэтому версия промпта инвалидирует его целиком, а не по кускам.

Границы
    Модуль не решает, что показать: он отдаёт ключи и найденные сущности с
    адресами. Сборку ответа (порядок, слияние с лексикой, пометки деградации)
    делает фасад — там же, где живёт конверт ответа.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from . import docgraph as dg
from . import docgraph_ops as dgo
from . import llm as llm_mod
from . import store

#: Промпт разложения запроса. Версия входит в ключ кэша: правка текста — новый вход.
INSTRUCTION = """Ты раскладываешь поисковый запрос на два уровня ключей.

Отвечай ТОЛЬКО JSON-объектом такой формы:
{"high_level":["..."],"low_level":["..."]}

Правила:
1. high_level — 1–3 понятия: тема запроса, класс задачи, предметная область.
   Пример: для «как устроена выдача лицензий» — «лицензирование».
2. low_level — 1–5 конкретных терминов: имена, идентификаторы, имена функций,
   имена таблиц, ключи настроек, названия файлов. Пиши их так, как они выглядят
   в коде, без перевода.
3. Не придумывай терминов, которых нет в запросе и его смысле. Служебные слова
   («как», «где», «почему», «устроена») ключами не являются.
4. Язык ключей — язык запроса; термины кода оставляй в исходном виде.
5. Если запрос бессмысленный или пустой — верни {"high_level":[],"low_level":[]}.
6. Никаких пояснений и markdown вокруг JSON."""

#: Коды деградации. Машинные: по ним считается, почему dual-level не включился.
DEGRADE_NO_LLM = "llm_unavailable"
DEGRADE_EDITION = "edition"
DEGRADE_LAYER = "layer_empty"
DEGRADE_NO_KEYS = "no_keys"

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
#: Служебные слова, которые не могут быть ключом. Список короткий намеренно: он
#: подстраховка к промпту, а не второй механизм извлечения.
STOPWORDS = frozenset({
    "как", "где", "что", "кто", "когда", "почему", "зачем", "какой", "какая",
    "какие", "чем", "это", "для", "про", "все", "весь", "есть", "было", "были",
    "устроена", "устроен", "работает", "сделать", "нужно", "можно",
})


class KeyError_(Exception):
    """Отказ разбора ключей: код машинный, сообщение для человека."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = str(code or "error")
        self.message = str(message or "")


def parse_keys(text: str) -> dict[str, list[str]]:
    """Разобрать ответ модели в пару ключей. Строгий JSON, терпимость к ```."""
    raw = str(text or "").strip()
    if not raw:
        raise KeyError_("bad_json", "модель вернула пустой ответ")
    body = _FENCE.sub("", raw).strip()
    try:
        payload = json.loads(body)
    except ValueError:
        start, end = body.find("{"), body.rfind("}")
        if start < 0 or end <= start:
            raise KeyError_("bad_json", "в ответе нет JSON-объекта") from None
        try:
            payload = json.loads(body[start:end + 1])
        except ValueError as exc:
            raise KeyError_("bad_json", "ответ не разбирается как JSON") from exc
    if not isinstance(payload, dict):
        raise KeyError_("bad_json", "ответ не объект JSON")
    return {
        "high_level": clean_keys(payload.get("high_level")),
        "low_level": clean_keys(payload.get("low_level")),
    }


def clean_keys(values: Any, limit: int = 5) -> list[str]:
    """Оставить осмысленные ключи: без служебных слов, повторов и мусора.

    Служебное слово ключом быть не может — иначе `low_level` заполняется тем, что
    встречается в каждом чанке, и уровень «конкретика» показывает всё подряд.
    """
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(str(value or "").split())
        if not text or len(text) > 80:
            continue
        key = dg.norm_name(text)
        if not key or key in seen or key in STOPWORDS:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def extract_keys(client: llm_mod.LLMClient | None, query: str) -> dict[str, Any]:
    """Ключи запроса одним вызовом (или из кэша).

    Возвращает структуру с полями `high_level`, `low_level`, `cached`, `calls`,
    `code` (пусто при успехе). Отказ провайдера — не исключение наружу: вызывающий
    обязан уметь ответить лексикой и назвать причину, поэтому причина приходит
    полем, а не падением.
    """
    result: dict[str, Any] = {"high_level": [], "low_level": [], "cached": False,
                              "calls": 0, "code": "", "reason": ""}
    if client is None:
        result.update(code=DEGRADE_NO_LLM, reason="клиент LLM не передан")
        return result
    text = str(query or "").strip()
    if not text:
        result.update(code=DEGRADE_NO_KEYS, reason="пустой запрос: раскладывать нечего")
        return result
    try:
        answer = client.complete(INSTRUCTION, input_text=text, purpose="search:keys")
    except llm_mod.LLMError as exc:
        result.update(code=exc.code or DEGRADE_NO_LLM, reason=exc.message or exc.code)
        return result
    result["cached"] = bool(answer.get("cached"))
    result["calls"] = int(answer.get("calls") or 0)
    try:
        keys = parse_keys(str(answer.get("text") or ""))
    except KeyError_ as exc:
        result.update(code=exc.code, reason=exc.message)
        return result
    result.update(keys)
    if not keys["high_level"] and not keys["low_level"]:
        result.update(code=DEGRADE_NO_KEYS,
                      reason="модель не нашла в запросе ни темы, ни терминов")
    return result


# ---------------------------------------------------------------------------
# Уровень «тема»: сущности слоя с адресами упоминаний
# ---------------------------------------------------------------------------


def match_entities(conn: sqlite3.Connection | None, rev_id: str,
                   keys: list[str], *, limit: int = 20) -> list[dict[str, Any]]:
    """Сущности, чьё имя совпадает с ключом, вместе с адресами упоминаний.

    Сопоставление идёт по НОРМАЛИЗОВАННОМУ имени, но не только по точному
    равенству: ключ «лицензирование» должен находить «Лицензирование», а ключ
    «licence_file» — сущность «licence_file». Частичное совпадение (ключ внутри
    имени или имя внутри ключа) допускается с меньшим весом и помечается полем
    `matched_on`, чтобы читатель видел, точное это совпадение или частное.
    """
    if conn is None or not keys:
        return []
    wanted = [(key, dg.norm_name(key)) for key in keys if str(key).strip()]
    if not wanted:
        return []
    rows = conn.execute(
        "SELECT entity_id, name, norm_name, kind, mentions FROM entity "
        "WHERE rev_id = ? ORDER BY mentions DESC, entity_id", (rev_id,)
    ).fetchall()
    found: list[dict[str, Any]] = []
    for row in rows:
        name_norm = str(row["norm_name"] or "")
        for key, key_norm in wanted:
            exact = name_norm == key_norm
            partial = (not exact) and bool(key_norm) and (
                key_norm in name_norm or name_norm in key_norm)
            if not exact and not partial:
                continue
            if partial and len(key_norm) < 4:
                # Короткий ключ внутри длинного имени даёт случайные совпадения
                # («api» в «capital»). Такое совпадение не считается уровнем темы.
                continue
            found.append({
                "entity_id": str(row["entity_id"]),
                "name": str(row["name"]),
                "kind": str(row["kind"]),
                "mentions": int(row["mentions"] or 0),
                "matched_on": "exact" if exact else "partial",
                "matched_key": key,
            })
            break
        if len(found) >= limit:
            break
    return found


def entity_chunks(conn: sqlite3.Connection | None, rev_id: str,
                  entity_ids: list[str], *, per_entity: int = 3) -> list[dict[str, Any]]:
    """Чанки, где упомянуты сущности: адрес + ссылка на сущность.

    Адрес берётся из `entity_mention` — той же таблицы, по которой работают
    `entity_neighbors` и `doc_relations`. Второго способа адресовать упоминание в
    проекте нет и быть не должно: два способа разошлись бы на первой правке.
    """
    if conn is None or not entity_ids:
        return []
    out: list[dict[str, Any]] = []
    for entity_id in entity_ids:
        rows = conn.execute(
            "SELECT chunk_id, path, start_line, end_line, char_start, char_end "
            "FROM entity_mention WHERE rev_id = ? AND entity_id = ? "
            "ORDER BY path, start_line LIMIT ?", (rev_id, entity_id, int(per_entity))
        ).fetchall()
        for row in rows:
            start, end = int(row["start_line"]), int(row["end_line"])
            out.append({
                "entity_id": entity_id,
                "chunk_id": str(row["chunk_id"]),
                "path": str(row["path"]),
                "start_line": start,
                "end_line": end,
                "locator": f"{row['path']}:{start}" if end <= start
                           else f"{row['path']}:{start}-{end}",
                "char_start": int(row["char_start"]),
                "char_end": int(row["char_end"]),
            })
    return out


def layer_ready(conn: sqlite3.Connection | None, rev_id: str) -> tuple[bool, str]:
    """Готов ли уровень «тема»: слой собран и в нём есть сущности.

    Возвращает (готов, причина). Разделение «слоя нет» и «слой пуст» важно: первое
    требует прохода, второе — проверки настройки модели, и один текст на оба
    случая увёл бы не туда.
    """
    if conn is None:
        return False, "слой документного графа не построен: таблиц `entity` нет"
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM entity WHERE rev_id = ?", (rev_id,)
        ).fetchone()
    except sqlite3.Error as exc:
        return False, f"таблица сущностей не читается: {exc}"
    count = int(row["c"] if hasattr(row, "keys") else row[0])
    if count == 0:
        return False, ("слой создан, но сущностей в нём нет: проход извлечения не "
                       "выполнялся (нужен документный граф, издание pro)")
    return True, ""


def themes_of(conn: sqlite3.Connection | None, rev_id: str,
              limit: int = 20) -> list[dict[str, Any]]:
    """Темы слоя с участниками. Пусто, пока слой тем не собран (этап 5)."""
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT theme_id, title, size FROM theme WHERE rev_id = ? "
            "ORDER BY size DESC, theme_id LIMIT ?", (rev_id, int(limit))
        ).fetchall()
    except sqlite3.Error:
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        members = conn.execute(
            "SELECT m.entity_id, e.name FROM theme_member m "
            "JOIN entity e ON e.rev_id = m.rev_id AND e.entity_id = m.entity_id "
            "WHERE m.rev_id = ? AND m.theme_id = ? ORDER BY m.weight DESC LIMIT 5",
            (rev_id, row["theme_id"]),
        ).fetchall()
        out.append({
            "theme_id": str(row["theme_id"]),
            "title": str(row["title"]),
            "size": int(row["size"] or 0),
            "members": [{"entity_id": str(m["entity_id"]), "name": str(m["name"])}
                        for m in members],
        })
    return out


def describe_degraded(code: str, reason: str) -> str:
    """Текст причины для человека. Один источник формулировок на все режимы."""
    if code == DEGRADE_NO_LLM:
        return ("dual-level не включён: LLM не настроен, поэтому ключи запроса не "
                f"извлечены ({reason}). Выдача — обычная лексика и вектор; уровни "
                "«тема» и «конкретика» не разделены")
    if code == DEGRADE_EDITION:
        return ("dual-level не включён: уровень «тема» требует возможностей "
                f"документного графа (издание pro), а действует другое издание "
                f"({reason}). Выдача — обычная лексика и вектор")
    if code == DEGRADE_LAYER:
        return (f"уровень «тема» недоступен: {reason}. Выдача — обычная лексика и вектор")
    if code == DEGRADE_NO_KEYS:
        return (f"dual-level не включён: {reason}. Выдача — обычная лексика и вектор")
    return f"dual-level ограничен: {code} ({reason})"


def open_layer(cfg) -> sqlite3.Connection | None:
    """Соединение слоя документного графа или None. Только чтение не требуется."""
    path = getattr(cfg, "lexvec_db", None)
    if not path:
        return None
    try:
        conn = store.connect(path, readonly=True)
    except (OSError, sqlite3.Error):
        return None
    if not store.table_exists(conn, "entity"):
        conn.close()
        return None
    return conn


# ---------------------------------------------------------------------------
# Два уровня одного ответа: локальные чанки и глобальные факты
# ---------------------------------------------------------------------------

#: Режимы выдачи. Перечень закрытый: опечатка в режиме должна быть отказом, а не
#: тихой подменой — иначе клиент получает ответ не на тот вопрос, который задал.
LEVEL_LOCAL = "local"
LEVEL_GLOBAL = "global"
LEVEL_BOTH = "both"
LEVEL_MODES: tuple[str, ...] = (LEVEL_LOCAL, LEVEL_GLOBAL, LEVEL_BOTH)
DEFAULT_LEVEL_MODE = LEVEL_BOTH

#: Сколько фактов глобального уровня отдавать за один ответ.
MAX_LEVEL_FACTS = 50

#: Код «глобальному уровню не на что опереться»: локальных фактов нет вовсе.
DEGRADE_NO_LOCAL = "no_local_facts"

_LEVEL_TEXT = {
    DEGRADE_EDITION: (
        "глобальный уровень закрыт изданием: {reason}. Локальный уровень работает "
        "без него: базовый поиск не платный, платный только граф"
    ),
    DEGRADE_LAYER: (
        "глобальный уровень неполон: {reason}. Локальные факты выданы, "
        "сущностей и связей в ответе нет — и это не «связей нет», а несобранный "
        "слой"
    ),
    DEGRADE_NO_LOCAL: (
        "глобальный уровень пуст: {reason}. Слой при этом может быть собран — "
        "проверять надо не его, а запрос"
    ),
}


def describe_global_level(code: str, reason: str) -> str:
    """Причина неполноты глобального уровня — словами для человека.

    Отдельно от `describe_degraded`: там речь про уровень «тема» у `search`, и
    подменять формулировки одной функцией значило бы называть чужой уровень.
    """
    template = _LEVEL_TEXT.get(str(code or ""))
    if template is None:
        return f"глобальный уровень ограничен: {code} ({reason})"
    return template.format(reason=reason or "причина не названа")


def facts_of_chunks(conn: sqlite3.Connection | None, rev_id: str,
                    chunk_ids: list[str], *,
                    limit: int = MAX_LEVEL_FACTS) -> dict[str, Any]:
    """Сущности и связи, в которые входят заданные чанки, — с адресами.

    Это и есть переход «локальный уровень → глобальный»: вход — chunk_id
    локальных фактов, выход — сущности и отношения с адресами цитат из
    `entity_mention` и `entity_relation`. Направление выбрано не случайно:
    локальный факт — то, что нашлось по запросу, глобальный — то, во что этот
    факт входит. Обратное направление (ключи запроса → сущности) живёт в
    `match_entities` и отвечает на другой вопрос — «что ещё похоже».

    Факт без адреса не выдаётся: он отбрасывается и считается в
    `rejected_no_citation`. Молчаливая потеря запрещена так же, как выдумка.
    """
    result: dict[str, Any] = {"entities": [], "relations": [], "chunks": 0,
                              "rejected_no_citation": 0, "truncated": False,
                              "reason": ""}
    wanted = [str(c) for c in chunk_ids if str(c or "").strip()]
    result["chunks"] = len(wanted)
    if conn is None or not wanted:
        return result
    link_chunks = set(wanted)
    marks = ",".join("?" for _ in wanted)
    try:
        rows = conn.execute(
            "SELECT m.chunk_id, m.entity_id, m.path, m.start_line, m.end_line, "
            "e.name, e.kind, e.mentions FROM entity_mention m "
            "JOIN entity e ON e.rev_id = m.rev_id AND e.entity_id = m.entity_id "
            f"WHERE m.rev_id = ? AND m.chunk_id IN ({marks}) "
            "ORDER BY e.name, m.path, m.start_line, m.char_start",
            (rev_id, *wanted),
        ).fetchall()
    except sqlite3.Error as exc:
        result["reason"] = f"таблица упоминаний не читается: {exc}"
        return result

    entities: dict[str, dict[str, Any]] = {}
    for row in rows:
        path = str(row["path"] or "")
        if not path.strip():
            # Адрес обязателен: упоминание без пути не факт, а запись.
            result["rejected_no_citation"] += 1
            continue
        address = dgo.locator(path, row["start_line"], row["end_line"])
        entity_key = str(row["entity_id"])
        item = entities.get(entity_key)
        if item is None:
            if len(entities) >= int(limit):
                result["truncated"] = True
                continue
            entities[entity_key] = {
                "entity_id": entity_key,
                "name": str(row["name"]),
                "kind": str(row["kind"]),
                "mentions": int(row["mentions"] or 0),
                # `via_chunk` — ЯВНАЯ связь уровней: вот чанк, через который
                # этот глобальный факт получен. Подразумевать её нельзя.
                "via_chunk": {"chunk_id": str(row["chunk_id"]), "locator": address},
                "locator": address,
                "citations": [address],
            }
            continue
        if address not in item["citations"] and len(item["citations"]) < 3:
            item["citations"].append(address)

    if not entities:
        return result

    ids = list(entities)
    id_marks = ",".join("?" for _ in ids)
    try:
        rel_rows = conn.execute(
            "SELECT rel_id, src_entity_id, dst_entity_id, kind, chunk_id, path, "
            "start_line, end_line, weight FROM entity_relation "
            f"WHERE rev_id = ? AND (chunk_id IN ({marks}) "
            f"OR src_entity_id IN ({id_marks}) OR dst_entity_id IN ({id_marks})) "
            "ORDER BY kind, path, start_line, rel_id",
            (rev_id, *wanted, *ids, *ids),
        ).fetchall()
    except sqlite3.Error as exc:
        result["reason"] = f"таблица отношений не читается: {exc}"
        rel_rows = []

    names: dict[str, str] = {key: str(item["name"]) for key, item in entities.items()}
    missing = {str(row["src_entity_id"]) for row in rel_rows} | {
        str(row["dst_entity_id"]) for row in rel_rows}
    missing -= set(names)
    if missing:
        names.update(_names_of(conn, rev_id, sorted(missing)))

    relations: list[dict[str, Any]] = []
    for row in rel_rows:
        src, dst = str(row["src_entity_id"]), str(row["dst_entity_id"])
        path = str(row["path"] or "")
        if not path.strip() or src not in names or dst not in names:
            # Конец отношения без сущности — битая ссылка слоя, а не связь.
            result["rejected_no_citation"] += 1
            continue
        if len(relations) >= int(limit):
            result["truncated"] = True
            break
        address = dgo.locator(path, row["start_line"], row["end_line"])
        chunk = str(row["chunk_id"])
        relations.append({
            "rel_id": str(row["rel_id"]),
            "kind": str(row["kind"]),
            "src": {"entity_id": src, "name": names[src]},
            "dst": {"entity_id": dst, "name": names[dst]},
            # `via_entities` — вторая явная связь уровней: через какие
            # сущности факт относится к выдаче.
            "via_entities": [src, dst],
            "via_chunk": ({"chunk_id": chunk, "locator": address}
                          if chunk in link_chunks else None),
            "citations": [address],
            "weight": float(row["weight"] or 1.0),
        })

    result["entities"] = sorted(
        entities.values(), key=lambda e: (-int(e["mentions"]), str(e["name"])))
    result["relations"] = relations
    return result


def _names_of(conn: sqlite3.Connection, rev_id: str,
              entity_ids: list[str]) -> dict[str, str]:
    """Имена сущностей по идентификаторам — один запрос, без догадок."""
    if not entity_ids:
        return {}
    marks = ",".join("?" for _ in entity_ids)
    rows = conn.execute(
        "SELECT entity_id, name FROM entity WHERE rev_id = ? "
        f"AND entity_id IN ({marks})", (rev_id, *entity_ids)
    ).fetchall()
    return {str(row["entity_id"]): str(row["name"]) for row in rows}
