"""Документный граф: отпечаток слоя, схема фактов, целостность, план прогона.

Что это и чего здесь ЕЩЁ нет
    План «Агентские интеграции и документный граф» (§3.2) вводит слой фактов о
    документах: сущности, упоминания, отношения, темы и сводки. Этот модуль —
    его фундамент (этап 1): схема, правило адресуемости факта, отпечаток входа
    и две команды состояния. **Извлечения здесь нет** — LLM-проход появляется
    этапом 2, поэтому `docgraph build` без `--dry-run` честно отказывает, а не
    делает вид, что что-то собрал.

Правило, ради которого всё построено: нет упоминания — нет факта
    Каждый факт (сущность, отношение, участник темы) обязан иметь адрес чанка,
    смещения внутри текста и хэш цитаты. Цитата хранится ХЭШЕМ, а не копией:
    слой не должен становиться вторым корпусом, который разойдётся с первым.
    Записи идут только через `add_entity`/`add_relation`/`add_theme`, и эти
    функции отказывают без упоминания. Сырой `INSERT` в обход API ловит
    `check_integrity` — иначе правило держалось бы на честном слове того, кто
    пишет следующий модуль.

Идентификаторы детерминированы, а не случайны
    `entity_id = sha1(norm_name + '|' + kind)`, `theme_id = sha1(seed + '|' +
    отсортированные участники)`. Одинаковый вход даёт одинаковый идентификатор
    на любой машине и в любой ревизии: иначе сравнивать ревизии между собой и
    стабилизировать темы было бы нечем.

Отпечаток входа: пересборка только по делу
    Отпечаток считается от (провайдер, модель, версия промпта, температура,
    набор настроек извлечения) и пишется в ДВА места: в запись ревизии
    (`rev.json`, рядом с `corpus_hash` — как это уже сделано для живого слоя баз)
    и в `meta` журнала под ключом `docgraph_fingerprint:<rev>`. Смена отпечатка
    — это решение, а не мелочь: она попадает в журнал расхождений записью
    `docgraph_fingerprint`, чтобы вопрос «почему слой вдруг другой» имел ответ
    в журнале, а не в чьей-то памяти.

    Провайдер входит в отпечаток, хотя план его не перечисляет: смена
    провайдера меняет производителя фактов ровно так же, как смена модели, и
    оставить её вне отпечатка значило бы объявить «вход тот же» там, где он
    другой.

Температура и воспроизводимость
    По умолчанию 0 (`llm.DEFAULT_TEMPERATURE`). Значение входит в отпечаток,
    поэтому «попробуем 0.7 и посмотрим» не улучшает прежний слой молча.

Границы
    Модуль не вызывает LLM (это `llm.py`) и не читает корпус. Он считает
    отпечаток, проверяет целостность уже записанного слоя и отвечает на два
    вопроса: «что настроено и что собрано» и «сколько будет стоить прогон».
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import llm as llm_mod
from . import settings as settings_mod
from . import store, util

#: Схема слоя. Все таблицы версионируются `rev_id` — значит ротация ревизий
#: убирает их данные автоматически (`storage.tables_with_rev` считает список по
#: схеме, а не по рукописному перечню; рукописный уже один раз оставил
#: документный слой вне политики хранения).
DOCGRAPH_DDL = """
CREATE TABLE IF NOT EXISTS entity (
  rev_id TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  name TEXT NOT NULL,
  norm_name TEXT NOT NULL,
  kind TEXT NOT NULL,
  mentions INTEGER NOT NULL DEFAULT 0,
  first_path TEXT,
  first_line INTEGER,
  PRIMARY KEY (rev_id, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_entity_norm ON entity(rev_id, norm_name);
CREATE INDEX IF NOT EXISTS idx_entity_kind ON entity(rev_id, kind);

CREATE TABLE IF NOT EXISTS entity_mention (
  rev_id TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  chunk_id TEXT NOT NULL,
  path TEXT NOT NULL,
  start_line INTEGER NOT NULL,
  end_line INTEGER NOT NULL,
  char_start INTEGER NOT NULL,
  char_end INTEGER NOT NULL,
  quote_hash TEXT NOT NULL,
  PRIMARY KEY (rev_id, entity_id, chunk_id, char_start, char_end)
);
CREATE INDEX IF NOT EXISTS idx_mention_chunk ON entity_mention(rev_id, chunk_id);
CREATE INDEX IF NOT EXISTS idx_mention_path ON entity_mention(rev_id, path);

CREATE TABLE IF NOT EXISTS entity_relation (
  rev_id TEXT NOT NULL,
  rel_id TEXT NOT NULL,
  src_entity_id TEXT NOT NULL,
  dst_entity_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  quote_hash TEXT NOT NULL,
  chunk_id TEXT NOT NULL,
  path TEXT NOT NULL,
  start_line INTEGER NOT NULL,
  end_line INTEGER NOT NULL,
  weight REAL NOT NULL DEFAULT 1.0,
  PRIMARY KEY (rev_id, rel_id)
);
CREATE INDEX IF NOT EXISTS idx_relation_src ON entity_relation(rev_id, src_entity_id);
CREATE INDEX IF NOT EXISTS idx_relation_dst ON entity_relation(rev_id, dst_entity_id);

CREATE TABLE IF NOT EXISTS theme (
  rev_id TEXT NOT NULL,
  theme_id TEXT NOT NULL,
  title TEXT NOT NULL,
  seed_entity_id TEXT NOT NULL,
  size INTEGER NOT NULL DEFAULT 0,
  built_by TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (rev_id, theme_id)
);

CREATE TABLE IF NOT EXISTS theme_member (
  rev_id TEXT NOT NULL,
  theme_id TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  weight REAL NOT NULL DEFAULT 1.0,
  PRIMARY KEY (rev_id, theme_id, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_theme_member_entity ON theme_member(rev_id, entity_id);

CREATE TABLE IF NOT EXISTS theme_summary (
  rev_id TEXT NOT NULL,
  theme_id TEXT NOT NULL,
  model TEXT NOT NULL DEFAULT '',
  prompt_version TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL,
  citations_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  PRIMARY KEY (rev_id, theme_id)
);
"""

#: Настройки, которые входят в «набор извлечения». Список явный: он же и есть
#: определение того, что считается изменением входа.
EXTRACTION_KEYS: tuple[str, ...] = (
    "docgraph_max_chars",
    "docgraph_max_entities",
    "docgraph_min_quote_chars",
)


class DocGraphError(Exception):
    """Отказ слоя: код машинный, сообщение для человека."""

    def __init__(self, code: str, message: str = "", detail: dict | None = None):
        super().__init__(message or code)
        self.code = str(code or "error")
        self.message = str(message or "")
        self.detail = dict(detail or {})

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": dict(self.detail)}


# ---------------------------------------------------------------------------
# Идентификаторы и адреса
# ---------------------------------------------------------------------------


def norm_name(name: str) -> str:
    """Нормализованное имя сущности: регистр, пробелы по краям, двойные внутри.

    Нормализация детерминирована и не зависит от локали (`casefold`, а не
    `lower` + локальные правила): идентификатор обязан совпадать на разных
    машинах.
    """
    text = " ".join(str(name or "").split())
    return text.casefold()


def entity_id(name: str, kind: str) -> str:
    """`sha1(norm_name|kind)` — одинаковый вход, одинаковый идентификатор."""
    payload = f"{norm_name(name)}|{str(kind or '').strip().casefold()}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def theme_id(seed_entity: str, members: Iterable[str]) -> str:
    """`sha1(seed|отсортированные участники)` — порядок входа не важен."""
    ordered = "|".join(sorted({str(item) for item in members if str(item).strip()}))
    payload = f"{str(seed_entity or '').strip()}|{ordered}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def relation_id(src: str, dst: str, kind: str, chunk_id: str) -> str:
    """Идентификатор отношения: пара, вид и чанк-основание."""
    payload = "\x1f".join([str(src or ""), str(dst or ""), str(kind or ""),
                           str(chunk_id or "")])
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Mention:
    """Упоминание факта в тексте: адрес чанка, смещения и ЦИТАТА.

    Текст цитаты здесь только для того, чтобы посчитать хэш: в хранилище
    уходит `quote_hash`, а не сам текст. Так слой не дублирует корпус.
    """

    chunk_id: str
    path: str
    start_line: int
    end_line: int
    char_start: int
    char_end: int
    quote: str

    @property
    def quote_hash(self) -> str:
        return util.sha256_text(str(self.quote or ""))

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "path": self.path,
            "start_line": int(self.start_line),
            "end_line": int(self.end_line),
            "char_start": int(self.char_start),
            "char_end": int(self.char_end),
            "quote_hash": self.quote_hash,
        }


def _checked_mentions(mentions: Iterable[Mention], *, what: str) -> list[Mention]:
    """Проверить упоминания. Пустой список — отказ, а не пустая запись."""
    items = [m for m in (mentions or []) if isinstance(m, Mention)]
    if not items:
        raise DocGraphError(
            "mention_required",
            f"факт «{what}» не создаётся без entity_mention: нужен адрес чанка, "
            "смещения и хэш цитаты — иначе это догадка, а не факт",
            {"what": what},
        )
    for m in items:
        if not str(m.chunk_id or "").strip() or not str(m.path or "").strip():
            raise DocGraphError(
                "mention_address_incomplete",
                f"у упоминания факта «{what}» нет адреса чанка (chunk_id/path)",
                {"what": what},
            )
        if int(m.end_line) < int(m.start_line) or int(m.char_end) < int(m.char_start):
            raise DocGraphError(
                "mention_address_incomplete",
                f"у упоминания факта «{what}» границы перевёрнуты",
                {"what": what, "start_line": m.start_line, "end_line": m.end_line},
            )
        if not str(m.quote or "").strip():
            raise DocGraphError(
                "quote_required",
                f"у упоминания факта «{what}» нет цитаты: хэш считать не из чего",
                {"what": what},
            )
    return items


# ---------------------------------------------------------------------------
# Схема и запись фактов
# ---------------------------------------------------------------------------


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Создать таблицы слоя. Идемпотентно: повторный запуск ничего не меняет."""
    conn.executescript(DOCGRAPH_DDL)
    conn.commit()


def add_entity(conn: sqlite3.Connection, rev_id: str, *, name: str, kind: str,
               mentions: Iterable[Mention], commit: bool = True,
               with_schema: bool = True) -> str:
    """Записать сущность вместе с упоминаниями. Возвращает `entity_id`.

    `commit=False` нужен проходу извлечения (`docgraph_extract`): он пишет весь
    слой ОДНОЙ транзакцией, чтобы отказ на середине не оставлял половину
    фактов. Параметр явный, потому что скрытый коммит в этом месте давал бы
    разное поведение у двух вызывающих без следа в вызове.
    """
    clean_name = " ".join(str(name or "").split())
    if not clean_name:
        raise DocGraphError("name_required", "у сущности нет имени")
    items = _checked_mentions(mentions, what=clean_name)
    if with_schema:
        ensure_schema(conn)
    eid = entity_id(clean_name, kind)
    first = sorted(items, key=lambda m: (str(m.path), int(m.start_line),
                                         int(m.char_start)))[0]
    conn.execute(
        "INSERT OR REPLACE INTO entity(rev_id, entity_id, name, norm_name, kind, "
        "mentions, first_path, first_line) VALUES(?,?,?,?,?,?,?,?)",
        (rev_id, eid, clean_name, norm_name(clean_name),
         str(kind or "").strip().casefold(), len(items), str(first.path),
         int(first.start_line)),
    )
    for m in items:
        conn.execute(
            "INSERT OR REPLACE INTO entity_mention(rev_id, entity_id, chunk_id, path, "
            "start_line, end_line, char_start, char_end, quote_hash) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (rev_id, eid, str(m.chunk_id), str(m.path), int(m.start_line),
             int(m.end_line), int(m.char_start), int(m.char_end), m.quote_hash),
        )
    if commit:
        conn.commit()
    return eid


def add_relation(conn: sqlite3.Connection, rev_id: str, *, src_entity_id: str,
                 dst_entity_id: str, kind: str, mention: Mention,
                 weight: float = 1.0, commit: bool = True,
                 with_schema: bool = True) -> str:
    """Записать отношение между сущностями. Адрес обязателен.

    Одно отношение — один адрес: то место, где связь ЗАЯВЛЕНА. Сущности,
    которые в нём участвуют, свои упоминания получают отдельно (`add_entity`),
    поэтому проверка целостности различает «связь без адреса» и «сущность без
    упоминаний».
    """
    items = _checked_mentions([mention], what=f"{src_entity_id} → {dst_entity_id}")
    if with_schema:
        ensure_schema(conn)
    rid = relation_id(src_entity_id, dst_entity_id, kind, items[0].chunk_id)
    conn.execute(
        "INSERT OR REPLACE INTO entity_relation(rev_id, rel_id, src_entity_id, "
        "dst_entity_id, kind, quote_hash, chunk_id, path, start_line, end_line, weight) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (rev_id, rid, str(src_entity_id), str(dst_entity_id),
         str(kind or "").strip().casefold(), items[0].quote_hash,
         str(items[0].chunk_id), str(items[0].path), int(items[0].start_line),
         int(items[0].end_line), float(weight)),
    )
    if commit:
        conn.commit()
    return rid


def add_theme(conn: sqlite3.Connection, rev_id: str, *, title: str,
              seed_entity_id: str, members: dict[str, float] | None,
              built_by: str = "", commit: bool = True,
              with_schema: bool = True) -> str:
    """Записать тему с участниками. Тема без участников — отказ.

    Участник темы — факт, и он опирается на упоминания своей сущности: сущность
    без упоминаний в слой попасть не может (`add_entity`), поэтому членство
    проверяется косвенно — и проверяется ещё раз в `check_integrity`, уже по
    содержимому хранилища.
    """
    weights = {str(k): float(v or 1.0) for k, v in (members or {}).items() if str(k).strip()}
    if not weights:
        raise DocGraphError("members_required",
                            f"тема «{title}» не создаётся без участников")
    if with_schema:
        ensure_schema(conn)
    tid = theme_id(seed_entity_id, weights)
    conn.execute(
        "INSERT OR REPLACE INTO theme(rev_id, theme_id, title, seed_entity_id, size, "
        "built_by) VALUES(?,?,?,?,?,?)",
        (rev_id, tid, " ".join(str(title or "").split()), str(seed_entity_id),
         len(weights), str(built_by or "")),
    )
    for entity, weight in sorted(weights.items()):
        conn.execute(
            "INSERT OR REPLACE INTO theme_member(rev_id, theme_id, entity_id, weight) "
            "VALUES(?,?,?,?)",
            (rev_id, tid, entity, float(weight)),
        )
    if commit:
        conn.commit()
    return tid


def check_integrity(conn: sqlite3.Connection, rev_id: str) -> dict[str, Any]:
    """Проверить правило «нет упоминания — нет факта» по содержимому слоя.

    Проверка смотрит таблицы напрямую: она обязана поймать и запись, сделанную
    в обход API. Именно поэтому она существует, хотя `add_*` уже отказывают.
    """
    counts = {"entities": 0, "mentions": 0, "relations": 0, "themes": 0,
              "members": 0, "summaries": 0}
    problems: list[dict[str, Any]] = []
    try:
        ensure_schema(conn)
        for key, table in (("entities", "entity"), ("mentions", "entity_mention"),
                           ("relations", "entity_relation"), ("themes", "theme"),
                           ("members", "theme_member"), ("summaries", "theme_summary")):
            row = conn.execute(
                f"SELECT COUNT(*) AS c FROM {table} WHERE rev_id = ?", (rev_id,)
            ).fetchone()
            counts[key] = int(row["c"] if hasattr(row, "keys") else row[0])
        rows = conn.execute(
            "SELECT e.entity_id, e.name FROM entity e WHERE e.rev_id = ? AND NOT EXISTS ("
            " SELECT 1 FROM entity_mention m WHERE m.rev_id = e.rev_id"
            " AND m.entity_id = e.entity_id)", (rev_id,)
        ).fetchall()
        for row in rows:
            problems.append({"kind": "entity_without_mention",
                             "entity_id": str(row["entity_id"]),
                             "name": str(row["name"])})
        rows = conn.execute(
            "SELECT rel_id FROM entity_relation WHERE rev_id = ? AND ("
            " quote_hash = '' OR chunk_id = '' OR path = '')", (rev_id,)
        ).fetchall()
        for row in rows:
            problems.append({"kind": "relation_without_address",
                             "rel_id": str(row["rel_id"])})
        rows = conn.execute(
            "SELECT m.theme_id, m.entity_id FROM theme_member m WHERE m.rev_id = ? AND NOT "
            "EXISTS (SELECT 1 FROM entity e WHERE e.rev_id = m.rev_id AND "
            "e.entity_id = m.entity_id)", (rev_id,)
        ).fetchall()
        for row in rows:
            problems.append({"kind": "member_without_entity",
                             "theme_id": str(row["theme_id"]),
                             "entity_id": str(row["entity_id"])})
        rows = conn.execute(
            "SELECT t.theme_id FROM theme t WHERE t.rev_id = ? AND NOT EXISTS ("
            " SELECT 1 FROM theme_member m WHERE m.rev_id = t.rev_id"
            " AND m.theme_id = t.theme_id)", (rev_id,)
        ).fetchall()
        for row in rows:
            problems.append({"kind": "theme_without_members",
                             "theme_id": str(row["theme_id"])})
    except sqlite3.Error as exc:
        return {"ok": False, "counts": counts, "problems": problems,
                "reason": f"хранилище не прочиталось: {type(exc).__name__}: {exc}"}
    return {"ok": not problems, "counts": counts, "problems": problems[:50],
            "problems_total": len(problems)}


# ---------------------------------------------------------------------------
# Отпечаток входа
# ---------------------------------------------------------------------------


def extraction_settings(values: dict[str, Any] | None) -> dict[str, Any]:
    """Набор настроек извлечения, входящий в отпечаток."""
    data = dict(settings_mod.DEFAULTS)
    data.update(values or {})
    out: dict[str, Any] = {}
    for key in EXTRACTION_KEYS:
        raw = data.get(key)
        try:
            out[key] = int(raw)
        except (TypeError, ValueError):
            out[key] = int(settings_mod.DEFAULTS.get(key) or 0)
    return out


def _temperature_text(temperature: float) -> str:
    """Форма температуры для отпечатка: фиксированная точность.

    `0`, `0.0` и `0.00` — одно значение, и отпечаток обязан это понимать,
    иначе «ничего не менял» выглядело бы как новый вход.
    """
    return f"{float(temperature):.4f}"


def fingerprint(*, model: str, prompt_version: str, temperature: float,
                extraction: dict[str, Any], provider: str = "") -> str:
    """Отпечаток слоя. Порядок частей фиксирован, вход сортируется."""
    parts = [
        f"provider={str(provider or '').strip().lower()}",
        f"model={str(model or '').strip()}",
        f"prompt_version={str(prompt_version or '').strip()}",
        f"temperature={_temperature_text(temperature)}",
    ]
    for key in sorted(extraction or {}):
        parts.append(f"{key}={extraction[key]}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def current_fingerprint(values: dict[str, Any] | None = None, *,
                        llm_config: llm_mod.LLMConfig | None = None,
                        temperature: float = llm_mod.DEFAULT_TEMPERATURE) -> dict[str, Any]:
    """Отпечаток по действующим настройкам вместе с разобранными частями.

    Части возвращаются рядом с хэшем намеренно: «отпечаток изменился» без
    перечня того, что именно изменилось, — непроверяемое утверждение.
    """
    data = dict(settings_mod.DEFAULTS)
    data.update(values or {})
    cfg = llm_config if llm_config is not None else llm_mod.config_from_settings(data)
    extraction = extraction_settings(data)
    digest = fingerprint(
        model=cfg.model, prompt_version=cfg.prompt_version,
        temperature=temperature, extraction=extraction, provider=cfg.provider,
    )
    return {
        "fingerprint": digest,
        "provider": cfg.provider,
        "model": cfg.model,
        "prompt_version": cfg.prompt_version,
        "temperature": float(temperature),
        "extraction": extraction,
    }


def _meta_key(rev_id: str) -> str:
    return f"docgraph_fingerprint:{rev_id}"


def _state_key(rev_id: str) -> str:
    return f"docgraph_state:{rev_id}"


def stored_fingerprint(control_conn: sqlite3.Connection, rev_id: str) -> str:
    """Записанный отпечаток ревизии. Пустая строка — не записан."""
    try:
        row = control_conn.execute("SELECT value FROM meta WHERE key = ?",
                                   (_meta_key(rev_id),)).fetchone()
    except sqlite3.Error:
        return ""
    if row is None:
        return ""
    return str(row["value"] if hasattr(row, "keys") else row[0])


def previous_fingerprint(control_conn: sqlite3.Connection, rev_id: str) -> tuple[str, str]:
    """Отпечаток предыдущей ревизии: (rev_id, отпечаток).

    Нужен, чтобы отличить «вход впервые записан» от «вход изменился»: первое —
    не расхождение, второе — расхождение, и оно обязано быть названо.
    """
    try:
        rows = control_conn.execute(
            "SELECT key, value FROM meta WHERE key LIKE 'docgraph_fingerprint:%'"
        ).fetchall()
    except sqlite3.Error:
        return "", ""
    best_rev, best_value = "", ""
    for row in rows:
        key = str(row["key"] if hasattr(row, "keys") else row[0])
        value = str(row["value"] if hasattr(row, "keys") else row[1])
        other = key.split(":", 1)[1] if ":" in key else ""
        if not other or other == rev_id:
            continue
        if other > best_rev:
            best_rev, best_value = other, value
    return best_rev, best_value


def _record_drift(cfg, control_conn: sqlite3.Connection, rev_id: str,
                  old: str, new: str, old_rev: str) -> bool:
    """Записать смену отпечатка в журнал расхождений. Append-only.

    Запись идёт тем же журналом, что и расхождения кода: «слой вдруг другой» —
    это расхождение в знаниях, и искать его следует там же, а не в отчёте
    прогона, который перезапишется следующим прогоном.
    """
    dedupe = util.stable_id("docgraph", rev_id, old, new)
    try:
        exists = control_conn.execute(
            "SELECT id FROM drift_entry WHERE dedupe_key = ?", (dedupe,)
        ).fetchone()
        if exists is not None:
            return False
        control_conn.execute(
            "INSERT INTO drift_entry(index_rev, ts, path, kind, old_hash, new_hash,"
            " symbols_added, symbols_removed, symbols_changed, edges_invalidated,"
            " detected_by, note, dedupe_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rev_id, util.now_iso(), "docgraph://layer", "docgraph_fingerprint",
             old, new, "[]", "[]", "[]", 0, "docgraph-fingerprint",
             f"отпечаток слоя документного графа изменился относительно {old_rev}",
             dedupe),
        )
        control_conn.commit()
    except sqlite3.Error:
        return False
    # Человекочитаемый журнал дописывается тем же текстом, что у остальных
    # записей: LEDGER.md — не второй источник истины, а его читаемая копия.
    try:
        from . import drift as drift_mod

        if not cfg.ledger_md.exists():
            util.write_text(cfg.ledger_md, drift_mod.LEDGER_HEADER + "\n")
        util.append_text(
            cfg.ledger_md,
            f"## {util.now_iso()} · {rev_id} · detected_by docgraph-fingerprint\n\n"
            f"- `docgraph://layer` — docgraph_fingerprint\n"
            f"  - было: `{old}` (ревизия {old_rev})\n"
            f"  - стало: `{new}`\n\n",
        )
    except OSError:
        pass
    return True


def record_fingerprint(cfg, rev_id: str, *,
                       control: sqlite3.Connection | None = None,
                       values: dict[str, Any] | None = None,
                       llm_config: llm_mod.LLMConfig | None = None,
                       temperature: float = llm_mod.DEFAULT_TEMPERATURE) -> dict[str, Any]:
    """Записать отпечаток в `meta` журнала и в `rev.json`, отметить смену.

    Образец — живой слой баз (`src/ragraf/db_writer.py`): отпечаток пишет САМА
    запись слоя, а не вызывающий путь, и факт записи проверяется перечитыванием.
    Иначе сбой записи выглядел бы как успех — ровно то расхождение
    «печатается вместо записывается», которое там и было дефектом.
    """
    from .revisions import ControlStore

    current = current_fingerprint(values, llm_config=llm_config,
                                  temperature=temperature)
    digest = str(current["fingerprint"])
    owner = control
    closer = None
    if owner is None:
        closer = ControlStore(cfg)
        owner = closer.conn
    try:
        old_rev, old = previous_fingerprint(owner, rev_id)
        previous = stored_fingerprint(owner, rev_id)
        try:
            owner.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                          (_meta_key(rev_id), digest))
            owner.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                (_state_key(rev_id), util.dumps(dict(current, recorded_at=util.now_iso()))),
            )
            owner.commit()
        except sqlite3.Error:
            pass
        redrift = False
        if old and old != digest:
            redrift = _record_drift(cfg, owner, rev_id, old, digest, old_rev)
        stored = stored_fingerprint(owner, rev_id)
        result = {
            "rev_id": rev_id,
            "fingerprint": digest,
            "previous": old,
            "previous_rev": old_rev,
            "changed": bool(old and old != digest),
            "stored": stored == digest,
            "was_recorded_before": bool(previous),
            "drift_entry": redrift,
            "parts": current,
        }
        # `rev.json` — паспорт АКТИВНОЙ ревизии. Запись в чужой паспорт была бы
        # подменой: поэтому блок пишется только когда паспорт принадлежит этой
        # ревизии, а иначе в ответе честно сказано, почему не записан.
        result["rev_json"] = _write_rev_json(cfg, rev_id, current)
        return result
    finally:
        if closer is not None:
            closer.close()


def _write_rev_json(cfg, rev_id: str, current: dict[str, Any]) -> dict[str, Any]:
    """Положить блок `docgraph` рядом с `corpus_hash` в `rev.json`."""
    path = Path(cfg.rev_file)
    data = util.read_json(path, default=None)
    if not isinstance(data, dict):
        return {"written": False, "reason": "rev.json отсутствует или не разбирается"}
    if str(data.get("rev_id") or "") != str(rev_id):
        return {"written": False,
                "reason": f"rev.json принадлежит {data.get('rev_id')}, а не {rev_id}"}
    block = {
        "fingerprint": str(current["fingerprint"]),
        "provider": str(current["provider"]),
        "model": str(current["model"]),
        "prompt_version": str(current["prompt_version"]),
        "temperature": float(current["temperature"]),
        "extraction": dict(current["extraction"]),
        "recorded_at": util.now_iso(),
    }
    if data.get("docgraph") == block:
        return {"written": False, "reason": "уже записан"}
    data["docgraph"] = block
    try:
        util.write_json(path, data)
    except OSError as exc:
        return {"written": False, "reason": f"запись не удалась: {type(exc).__name__}"}
    return {"written": True, "reason": ""}


# ---------------------------------------------------------------------------
# Состояние слоя и план прогона
# ---------------------------------------------------------------------------


def _active_rev(cfg) -> str:
    from .revisions import ControlStore

    try:
        control = ControlStore(cfg)
    except Exception:  # noqa: BLE001 — журнала нет: это состояние, а не падение
        return ""
    try:
        return str(control.current_rev() or "")
    finally:
        control.close()


def _layer_counts(cfg, rev_id: str) -> dict[str, int]:
    """Что уже собрано в активной ревизии. Нули — честный ответ, не догадка."""
    out = {"documents": 0, "chunks": 0, "entities": 0, "mentions": 0,
           "relations": 0, "themes": 0, "summaries": 0, "cache_rows": 0}
    path = Path(cfg.lexvec_db)
    if not rev_id or not path.is_file():
        return out
    try:
        conn = store.connect(path, readonly=True)
    except (OSError, sqlite3.Error):
        return out
    try:
        for key, table in (("documents", "document"), ("chunks", "document_chunk"),
                           ("entities", "entity"), ("mentions", "entity_mention"),
                           ("relations", "entity_relation"), ("themes", "theme"),
                           ("summaries", "theme_summary")):
            if not store.table_exists(conn, table):
                continue
            row = conn.execute(
                f"SELECT COUNT(*) AS c FROM {table} WHERE rev_id = ?", (rev_id,)
            ).fetchone()
            out[key] = int(row["c"] if hasattr(row, "keys") else row[0])
        if store.table_exists(conn, "llm_cache"):
            row = conn.execute("SELECT COUNT(*) AS c FROM llm_cache").fetchone()
            out["cache_rows"] = int(row["c"] if hasattr(row, "keys") else row[0])
    except sqlite3.Error:
        return out
    finally:
        conn.close()
    return out


def _fingerprint_block(cfg, rev_id: str, current: dict[str, Any]) -> dict[str, Any]:
    """Отпечаток: действующий, записанный в ревизию и сходятся ли они.

    Одна форма у обеих команд намеренно: читатель не должен помнить, что в
    `status` это словарь, а в плане — строка.
    """
    recorded = ""
    if rev_id:
        from .revisions import ControlStore

        try:
            control = ControlStore(cfg)
        except Exception:  # noqa: BLE001 — журнала нет: отпечатку негде лежать
            control = None
        if control is not None:
            try:
                recorded = stored_fingerprint(control.conn, rev_id)
            finally:
                control.close()
    digest = str(current.get("fingerprint") or "")
    return {
        "current": digest,
        "recorded": recorded,
        "matches": bool(recorded) and recorded == digest,
        "parts": current,
    }


def _integrity(cfg, rev_id: str) -> dict[str, Any]:
    path = Path(cfg.lexvec_db)
    if not rev_id or not path.is_file():
        return {"ok": True, "counts": {}, "problems": [],
                "reason": "слоя нет: проверять нечего"}
    try:
        conn = store.connect(path, readonly=True)
    except (OSError, sqlite3.Error) as exc:
        return {"ok": False, "counts": {}, "problems": [],
                "reason": f"хранилище не открылось: {type(exc).__name__}"}
    try:
        if not store.table_exists(conn, "entity"):
            return {"ok": True, "counts": {}, "problems": [],
                    "reason": "схемы слоя ещё нет"}
        return check_integrity(conn, rev_id)
    finally:
        conn.close()


def build_status(cfg, *, rev_id: str | None = None,
                 values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Состояние слоя: что настроено, что собрано, сходится ли отпечаток.

    Команда обязана отвечать осмысленно при неработающем LLM: настройка,
    отпечаток и содержимое слоя не зависят от доступности провайдера.
    """
    rev = str(rev_id if rev_id is not None else _active_rev(cfg))
    llm_state = llm_mod.status()
    fingerprint_now = current_fingerprint(values)
    counts = _layer_counts(cfg, rev)
    integrity = _integrity(cfg, rev)
    # Блок отпечатка считается ОДИН раз: чтение записанного значения открывает
    # журнал, и второй такой же вызов в том же ответе был бы лишним входом в базу.
    fingerprint_block = _fingerprint_block(cfg, rev, fingerprint_now)
    recorded = fingerprint_block["recorded"]
    built = counts["entities"] > 0 and counts["mentions"] > 0
    notes: list[str] = []
    if not rev:
        notes.append("в корпусе нет активной ревизии: сначала `ragraf index`")
    if not built and rev:
        notes.append("слой не собран: извлечение появляется этапом 2 "
                     "(сейчас есть схема, отпечаток и проверка целостности)")
    if recorded and recorded != fingerprint_now["fingerprint"]:
        notes.append("записанный отпечаток расходится с действующими настройками: "
                     "слой пересобирается только по решению, а не молча")
    capability = "built" if built else ("schema" if rev else "none")
    return {
        "op": "docgraph_status",
        "index_rev": rev,
        "capability": capability,
        "llm": llm_state,
        "fingerprint": fingerprint_block,
        "layer": dict(counts, built=built),
        "integrity": integrity,
        "collected": built,
        "found": built,
        "reason": "" if built else (notes[0] if notes else "слой не собран"),
        "notes": notes,
    }


def build_plan(cfg, *, rev_id: str | None = None, limit: int | None = None,
               values: dict[str, Any] | None = None) -> dict[str, Any]:
    """План прогона извлечения: сколько чанков, вызовов и знаков. Без сети.

    «Сухой прогон» здесь означает буквально это: считается ВХОД, а обращений к
    провайдеру нет ни одного. Поэтому план отвечает и при неработающем LLM —
    именно тогда он и нужен, чтобы решить, настраивать ли его вообще.
    """
    rev = str(rev_id if rev_id is not None else _active_rev(cfg))
    llm_state = llm_mod.status()
    plan = {"chunks": 0, "chunks_selected": 0, "calls_estimated": 0,
            "chars": 0, "tokens_estimated": 0, "documents": 0,
            "cached_rows": 0, "calls_avoidable": 0}
    notes: list[str] = []
    path = Path(cfg.lexvec_db)
    if rev and path.is_file():
        try:
            conn = store.connect(path, readonly=True)
        except (OSError, sqlite3.Error):
            conn = None
        if conn is not None:
            try:
                if store.table_exists(conn, "document_chunk"):
                    row = conn.execute(
                        "SELECT COUNT(*) AS c, COALESCE(SUM(LENGTH(text)), 0) AS chars "
                        "FROM document_chunk WHERE rev_id = ?", (rev,)
                    ).fetchone()
                    plan["chunks"] = int(row["c"] if hasattr(row, "keys") else row[0])
                    plan["chars"] = int(row["chars"] if hasattr(row, "keys") else row[1])
                if store.table_exists(conn, "document"):
                    row = conn.execute("SELECT COUNT(*) AS c FROM document WHERE rev_id = ?",
                                       (rev,)).fetchone()
                    plan["documents"] = int(row["c"] if hasattr(row, "keys") else row[0])
                if store.table_exists(conn, "llm_cache"):
                    row = conn.execute(
                        "SELECT COUNT(*) AS c FROM llm_cache WHERE model = ? AND "
                        "prompt_version = ?", (llm_state.get("model") or "",
                                               llm_state.get("prompt_version") or "")
                    ).fetchone()
                    plan["cached_rows"] = int(row["c"] if hasattr(row, "keys") else row[0])
            except sqlite3.Error:
                notes.append("документный слой не прочитался: числа плана неполные")
            finally:
                conn.close()
    else:
        notes.append("активной ревизии нет: плана нет, сначала `ragraf index`")

    selected = plan["chunks"]
    if limit is not None and limit >= 0:
        selected = min(selected, int(limit))
    plan["chunks_selected"] = selected
    # Один чанк — одно обращение: разбиение на партии появится вместе с
    # извлечением, а оценка «сколько запросов» обязана быть СЕЙЧАС.
    plan["calls_estimated"] = selected
    plan["calls_avoidable"] = min(selected, plan["cached_rows"])
    plan["tokens_estimated"] = int(plan["chars"] // 4)
    if not plan["chunks"]:
        notes.append("документных чанков в ревизии нет: извлекать не из чего")
    if not llm_state.get("configured"):
        notes.append(f"прогон не начнётся: {llm_state.get('reason')}")
    notes.append("сухой прогон: обращений к провайдеру не было")

    collected = bool(llm_state.get("configured")) and selected > 0
    return {
        "op": "docgraph_build_plan",
        "dry_run": True,
        "index_rev": rev,
        "capability": "schema" if rev else "none",
        "llm": llm_state,
        "plan": plan,
        "collected": collected,
        "found": bool(plan["chunks"]),
        "reason": "" if collected else (notes[0] if notes else "прогон невозможен"),
        "notes": notes,
        "fingerprint": _fingerprint_block(cfg, rev, current_fingerprint(values)),
    }
