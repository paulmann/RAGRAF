"""Провенанс документа и вывода: версия, интервал действия, рёбра деривации.

Зачем отдельный модуль, а не поля в `document_chunk`.
У чанка есть адрес и текст, но нет ответа на два вопроса: «на какую редакцию
документа опирается этот текст» и «из чего выведено это утверждение». Без первого
устаревшая редакция возвращается с той же уверенностью, что и текущая; без второго
на вопрос «что пересчитать после смены допущения» ответить нечем. Оба ответа —
данные, а не украшение ответа, поэтому они лежат в таблицах.

Почему в том же хранилище, где `document*`.
Документы, чанки и документный граф живут в лексическом SQLite
(`facade._doc_conn`, `docgraph_extract._write`). Второй файл дал бы второй источник
правды о документе и потребовал бы синхронной чистки при ротации ревизий: политика
хранения удаляет строки таблиц с колонкой `rev_id` в двух известных файлах
(`storage.tables_with_rev`, `storage._purge_revisions`), и третий файл она бы не
увидела. Поэтому все таблицы здесь несут `rev_id` и попадают под ротацию сами.

Что считается фактом.
  * `chunk`    — утверждение документа: текст по адресу `path:строки`. Провенанс
                 `stated`: это написано в документе, а не выведено индексом.
  * `relation` — связь, извлечённая документным графом. Провенанс `derived`: это
                 вывод модели по тексту, и он обязан называть входы. Вход — факт
                 чанка, из которого взята цитата (`entity_relation.chunk_id`).

Границы, названные прямо.
  * Интервал берётся ТОЛЬКО из явных метаданных (front matter, поля `valid_from` /
    `valid_to`, реквизиты в первых строках). Угадывание по имени файла запрещено:
    выдуманная дата хуже отсутствующей. Нет метаданных — `validity="unknown"`.
  * «Текущая редакция» вычисляется по данным, без часов: версия с наибольшим
    `valid_from` в своей логической группе вытесняет более ранние. Часы в расчёте
    дали бы разный ответ на один и тот же индекс в разные дни.
  * Заменённые факты не удаляются: помечаются `superseded` и остаются доступными
    явным запросом (`include_superseded`). Удаление уничтожило бы доказательство.
  * Утверждение без адреса в ответ не отдаётся и считается в
    `rejected_no_citation` — как у остальных операций проекта.

Автор: Mikhail Deynekin
Сайт: https://Deynekin.com
Почта: Mikhail@Deynekin.com
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from . import textdec, util

#: Версия схемы провенанса. Другая версия — не «поймём как получится»: строки
#: неизвестной версии не читаются, причина называется.
SCHEMA = 1

#: Виды утверждений. Закрытый список: вид вне его означал бы, что читатель не
#: знает, как трактовать провенанс.
FACT_KINDS = ("chunk", "relation")

#: Значения провенанса. `stated` — написано в документе; `derived` — выведено.
PROVENANCES = ("stated", "derived")

#: Состояния интервала. `unknown` — не «просрочен», а «объявленной даты нет».
VALIDITIES = ("current", "superseded", "unknown", "out_of_interval")

#: Поля метаданных документа, из которых берётся интервал. Ключи сравниваются в
#: нижнем регистре, пробелы и дефисы приводятся к подчёркиванию.
INTERVAL_KEYS: dict[str, str] = {
    "valid_from": "valid_from",
    "действует_с": "valid_from",
    "вступает_в_силу": "valid_from",
    "effective_from": "valid_from",
    "valid_to": "valid_to",
    "действует_по": "valid_to",
    "действует_до": "valid_to",
    "effective_to": "valid_to",
    "superseded_by": "superseded_by",
    "заменён": "superseded_by",
    "заменен": "superseded_by",
    "document_id": "logical_id",
    "логический_ид": "logical_id",
}

#: Расширения, у которых имеет смысл читать front matter и первые строки как
#: текст. Бинарные форматы декодировать нельзя: это и дорого, и бессмысленно.
TEXT_EXTS = (".md", ".markdown", ".txt", ".rst", ".adoc", ".asciidoc", ".text")

_DATE_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ].*)?$")
_DATE_RU = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")

PROVENANCE_DDL = """
CREATE TABLE IF NOT EXISTS document_version (
  rev_id TEXT NOT NULL,
  doc_id TEXT NOT NULL,
  logical_id TEXT NOT NULL,
  version_id TEXT NOT NULL,
  path TEXT NOT NULL,
  valid_from TEXT,
  valid_to TEXT,
  superseded_by TEXT,
  superseded INTEGER NOT NULL DEFAULT 0,
  supersede_reason TEXT,
  source TEXT NOT NULL DEFAULT 'unknown',
  sha256 TEXT,
  declared_at TEXT,
  warnings_json TEXT,
  PRIMARY KEY (rev_id, doc_id)
);
CREATE INDEX IF NOT EXISTS idx_docver_logical ON document_version(rev_id, logical_id);

CREATE TABLE IF NOT EXISTS fact_assertion (
  rev_id TEXT NOT NULL,
  fact_id TEXT NOT NULL,
  doc_id TEXT NOT NULL,
  version_id TEXT,
  chunk_id TEXT,
  path TEXT NOT NULL,
  locator TEXT NOT NULL,
  kind TEXT NOT NULL,
  provenance TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1.0,
  valid_from TEXT,
  valid_to TEXT,
  superseded INTEGER NOT NULL DEFAULT 0,
  superseded_by TEXT,
  inputs_count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (rev_id, fact_id)
);
CREATE INDEX IF NOT EXISTS idx_fact_doc ON fact_assertion(rev_id, doc_id);
CREATE INDEX IF NOT EXISTS idx_fact_chunk ON fact_assertion(rev_id, chunk_id);
CREATE INDEX IF NOT EXISTS idx_fact_locator ON fact_assertion(rev_id, locator);

CREATE TABLE IF NOT EXISTS derivation_edge (
  rev_id TEXT NOT NULL,
  derived_fact_id TEXT NOT NULL,
  input_fact_id TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'derived_from',
  PRIMARY KEY (rev_id, derived_fact_id, input_fact_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_derivation_input ON derivation_edge(rev_id, input_fact_id);

-- Пометки устаревания по рёбрам факт -> факт. Отдельная таблица, а не
-- колонка в `stale_edge` журнала управления: факты считаются по `rev_id`
-- в документном хранилище, рёбра кода — по `index_rev` в другом файле.
-- Одна таблица на два хранилища склеила бы разные часы.
CREATE TABLE IF NOT EXISTS fact_stale (
  rev_id        TEXT NOT NULL,
  fact_id       TEXT NOT NULL,
  cause_fact_id TEXT NOT NULL,
  input_fact_id TEXT NOT NULL,
  distance      INTEGER NOT NULL,
  provenance    TEXT NOT NULL,
  reason        TEXT NOT NULL,
  marked_at     TEXT NOT NULL,
  acked_at      TEXT,
  PRIMARY KEY (rev_id, fact_id, cause_fact_id)
);
CREATE INDEX IF NOT EXISTS idx_fact_stale_rev ON fact_stale(rev_id, fact_id);
"""


@dataclass
class Interval:
    """Объявленный интервал действия документа и его происхождение."""

    valid_from: str | None = None
    valid_to: str | None = None
    superseded_by: str = ""
    logical_id: str = ""
    source: str = "unknown"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "superseded_by": self.superseded_by,
            "logical_id": self.logical_id,
            "source": self.source,
            "warnings": list(self.warnings),
        }


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(PROVENANCE_DDL)


def tables_present(conn: sqlite3.Connection) -> bool:
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='fact_assertion'"
        ).fetchone()
    except sqlite3.Error:
        return False
    return bool(row)


def _norm_key(raw: str) -> str:
    return re.sub(r"[\s\-]+", "_", str(raw).strip().lower())


def _clean_value(raw: str) -> str:
    text = str(raw).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1].strip()
    return text


def parse_date(raw: Any) -> tuple[str | None, str]:
    """Дата в ISO или пустая строка. Возвращает (значение, причина отказа)."""
    text = str(raw or "").strip()
    if not text:
        return None, ""
    match = _DATE_ISO.match(text)
    if match:
        year, month, day = (int(part) for part in match.groups())
    else:
        match = _DATE_RU.match(text)
        if not match:
            return None, f"дата не разобрана: {text!r} (ожидается ГГГГ-ММ-ДД или ДД.ММ.ГГГГ)"
        day, month, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day).isoformat(), ""
    except ValueError:
        return None, f"дата не существует: {text!r}"


def _front_matter(text: str) -> tuple[dict[str, str], str]:
    """Плоский front matter `--- … ---`. Возвращает (поля, причина отказа)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, ""
    fields: dict[str, str] = {}
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            return fields, ""
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            return {}, "front matter не разобран: строка без ':'"
        key, value = stripped.split(":", 1)
        fields[_norm_key(key)] = _clean_value(value)
    return {}, "front matter не закрыт строкой '---'"


def _head_fields(text: str, limit_lines: int = 60) -> dict[str, str]:
    """Поля `ключ: значение` в первых строках документа — только известные ключи."""
    fields: dict[str, str] = {}
    for line in text.splitlines()[:limit_lines]:
        stripped = line.strip()
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        norm = _norm_key(key)
        if norm in INTERVAL_KEYS:
            fields.setdefault(norm, _clean_value(value))
    return fields


def _text_of(rel: str, raw: bytes | None) -> str:
    if not raw:
        return ""
    if Path(rel).suffix.lower() not in TEXT_EXTS:
        return ""
    try:
        text, _enc = textdec.decode(raw[:65536], rel)
    except Exception:  # noqa: BLE001 — нечитаемый текст не должен ронять индексацию
        return ""
    return text[:65536]


def parse_interval(rel: str, raw: bytes | None = None,
                   metadata: dict[str, Any] | None = None) -> Interval:
    """Интервал действия документа из явных метаданных.

    Порядок источников: метаданные читателя формата → front matter → поля в первых
    строках текста. Первый найденный источник и называется в ответе; смешивать их
    молча нельзя, иначе «дата из front matter» и «дата из реквизитов» выглядели бы
    одинаково.
    """
    out = Interval(logical_id="")
    meta = {_norm_key(k): v for k, v in (metadata or {}).items()}
    picked: dict[str, str] = {}
    source = ""
    for key, value in meta.items():
        if key in INTERVAL_KEYS and value not in (None, ""):
            picked.setdefault(INTERVAL_KEYS[key], str(value))
            source = source or "metadata"
    text = "" if picked else _text_of(rel, raw)
    if text and not picked:
        front, problem = _front_matter(text)
        if problem:
            out.warnings.append(problem)
        for key, value in front.items():
            if key in INTERVAL_KEYS:
                picked.setdefault(INTERVAL_KEYS[key], value)
                source = source or "front_matter"
        if not picked:
            head = _head_fields(text)
            for key, value in head.items():
                picked.setdefault(INTERVAL_KEYS[key], value)
                source = source or "head"
    if not picked:
        out.source = "unknown"
        out.logical_id = ""
        return out
    out.source = source or "unknown"
    out.valid_from, problem_from = parse_date(picked.get("valid_from"))
    if problem_from:
        out.warnings.append(problem_from)
    out.valid_to, problem_to = parse_date(picked.get("valid_to"))
    if problem_to:
        out.warnings.append(problem_to)
    out.superseded_by = str(picked.get("superseded_by") or "").strip()
    out.logical_id = str(picked.get("logical_id") or "").strip()
    if out.valid_from and out.valid_to and out.valid_from > out.valid_to:
        out.warnings.append(
            f"интервал перевёрнут: valid_from {out.valid_from} позже valid_to {out.valid_to}"
        )
        out.valid_from = None
        out.valid_to = None
        out.source = "invalid"
    return out


def contains(row: dict[str, Any], day: str) -> bool:
    """Входит ли дата в объявленный интервал (границы включительно)."""
    start = row.get("valid_from")
    end = row.get("valid_to")
    if start and day < str(start):
        return False
    if end and day > str(end):
        return False
    return True


def version_id_of(doc_id: str, sha256: str, valid_from: str | None) -> str:
    return util.stable_id("docver", doc_id, sha256, valid_from or "")


def fact_id_of_chunk(chunk_id: str) -> str:
    return util.stable_id("chunkfact", chunk_id)


def fact_id_of_relation(rel_id: str) -> str:
    return util.stable_id("relfact", rel_id)


def _iso_now() -> str:
    # Время объявления записи — метаданные, а не часть расчёта: на решение о
    # текущей редакции оно не влияет (иначе ответ зависел бы от часов).
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_layer(conn: sqlite3.Connection, rev_id: str, documents: Iterable[Any]) -> dict[str, int]:
    """Записать версии документов и факты чанков одной ревизии.

    `INSERT OR REPLACE` + предварительное удаление строк ревизии: повторный прогон
    той же ревизии не должен добавлять второй набор. Удаляем только свою ревизию.
    """
    ensure_schema(conn)
    conn.execute("DELETE FROM document_version WHERE rev_id = ?", (rev_id,))
    conn.execute("DELETE FROM fact_assertion WHERE rev_id = ?", (rev_id,))
    conn.execute("DELETE FROM derivation_edge WHERE rev_id = ?", (rev_id,))
    counts = {"versions": 0, "facts": 0, "dated": 0, "unknown": 0}
    staged: list[dict[str, Any]] = []
    for doc in documents:
        interval = Interval(
            valid_from=getattr(doc, "valid_from", None),
            valid_to=getattr(doc, "valid_to", None),
            superseded_by=getattr(doc, "superseded_by", "") or "",
            logical_id=getattr(doc, "logical_id", "") or "",
            source=getattr(doc, "validity_source", "unknown") or "unknown",
            warnings=list(getattr(doc, "validity_warnings", []) or []),
        )
        doc_id = doc.doc_id
        v_id = version_id_of(doc_id, str(getattr(doc, "sha256", "") or ""),
                             interval.valid_from)
        logical = interval.logical_id or doc_id
        staged.append({
            "doc_id": doc_id,
            "path": doc.path,
            "logical_id": logical,
            "version_id": v_id,
            "interval": interval,
            "sha256": str(getattr(doc, "sha256", "") or ""),
            "chunks": list(getattr(doc, "chunks", []) or []),
        })
        counts["versions"] += 1
        counts["dated" if interval.valid_from else "unknown"] += 1

    # Вытеснение: в логической группе текущая — версия с наибольшим valid_from.
    # Часы не участвуют, поэтому ответ воспроизводим на неизменном индексе.
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in staged:
        groups.setdefault(item["logical_id"], []).append(item)
    for _logical, items in groups.items():
        dated = [item for item in items if item["interval"].valid_from]
        if len(dated) > 1:
            current = max(
                dated,
                key=lambda item: (str(item["interval"].valid_from),
                                  item["interval"].valid_to is None, item["path"]),
            )
            for item in dated:
                if item is current:
                    continue
                if str(item["interval"].valid_from) >= str(current["interval"].valid_from):
                    continue
                item["superseded"] = 1
                item["superseded_by"] = current["version_id"]
                item["supersede_reason"] = "newer_version"
        for item in items:
            pointer = item["interval"].superseded_by
            if pointer:
                item["superseded"] = 1
                item["superseded_by"] = _resolve_pointer(items, pointer) or pointer
                item["supersede_reason"] = "declared"

    for item in staged:
        interval: Interval = item["interval"]
        warnings = list(interval.warnings)
        pointer = interval.superseded_by
        if pointer and not item["superseded_by"]:
            warnings.append(
                f"superseded_by указывает на {pointer!r}, но такого документа в ревизии нет"
            )
        conn.execute(
            "INSERT OR REPLACE INTO document_version(rev_id, doc_id, logical_id,"
            " version_id, path, valid_from, valid_to, superseded_by, superseded,"
            " supersede_reason, source, sha256, declared_at, warnings_json)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rev_id, item["doc_id"], item["logical_id"], item["version_id"],
             item["path"], interval.valid_from, interval.valid_to,
             item["superseded_by"] if item.get("superseded") else pointer,
             int(item.get("superseded", 0)), item.get("supersede_reason", ""),
             interval.source, item["sha256"], _iso_now(),
             json.dumps(warnings, ensure_ascii=False, sort_keys=True)),
        )
        for chunk in item["chunks"]:
            chunk_id = str(getattr(chunk, "chunk_id", "") or "")
            if not chunk_id:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO fact_assertion(rev_id, fact_id, doc_id,"
                " version_id, chunk_id, path, locator, kind, provenance,"
                " confidence, valid_from, valid_to, superseded, superseded_by,"
                " inputs_count) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                (rev_id, fact_id_of_chunk(chunk_id), item["doc_id"],
                 item["version_id"], chunk_id, item["path"],
                 str(getattr(chunk, "locator", "") or ""), "chunk", "stated",
                 1.0, interval.valid_from, interval.valid_to,
                 int(item.get("superseded", 0)),
                 item["superseded_by"] if item.get("superseded") else pointer),
            )
            counts["facts"] += 1
    conn.commit()
    return counts


def _resolve_pointer(items: list[dict[str, Any]], pointer: str) -> str:
    for item in items:
        if pointer in (item["doc_id"], item["path"], item["logical_id"]):
            return str(item["version_id"])
    return ""


def build_derived(conn: sqlite3.Connection, rev_id: str) -> dict[str, int]:
    """Пересобрать derived-факты из документного графа и их рёбра к входам.

    Вызывается после записи слоя графа (индексация и `docgraph build`). Идемпотентно:
    прежние derived-факты ревизии удаляются вместе с их рёбрами.
    """
    ensure_schema(conn)
    tables = {str(row[0]) for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN"
        " ('entity_relation','document_version','fact_assertion')"
    )}
    if "entity_relation" not in tables:
        return {"derived": 0, "edges": 0, "inputs_missing": 0, "skipped": 0}
    conn.execute(
        "DELETE FROM derivation_edge WHERE rev_id = ? AND derived_fact_id IN"
        " (SELECT fact_id FROM fact_assertion WHERE rev_id = ? AND kind = 'relation')",
        (rev_id, rev_id),
    )
    conn.execute(
        "DELETE FROM fact_assertion WHERE rev_id = ? AND kind = 'relation'", (rev_id,)
    )
    known_chunks = {str(row[0]) for row in conn.execute(
        "SELECT fact_id FROM fact_assertion WHERE rev_id = ? AND kind = 'chunk'",
        (rev_id,),
    )}
    versions = {str(row["path"]): row for row in conn.execute(
        "SELECT path, doc_id, version_id, valid_from, valid_to, superseded,"
        " superseded_by FROM document_version WHERE rev_id = ?", (rev_id,)
    )}
    stats = {"derived": 0, "edges": 0, "inputs_missing": 0, "skipped": 0}
    rows = conn.execute(
        "SELECT rel_id, chunk_id, path, start_line, end_line, kind, weight"
        " FROM entity_relation WHERE rev_id = ?"
        " ORDER BY path, start_line, end_line, kind, rel_id", (rev_id,)
    ).fetchall()
    for row in rows:
        path = str(row["path"] or "")
        version = versions.get(path)
        if not path or version is None:
            stats["skipped"] += 1
            continue
        start = int(row["start_line"] or 0)
        end = int(row["end_line"] or start)
        locator = f"{path}:{start}" if end <= start else f"{path}:{start}-{end}"
        fact_id = fact_id_of_relation(str(row["rel_id"] or locator))
        chunk_id = str(row["chunk_id"] or "")
        input_fact = fact_id_of_chunk(chunk_id) if chunk_id else ""
        has_input = bool(input_fact) and input_fact in known_chunks
        conn.execute(
            "INSERT OR REPLACE INTO fact_assertion(rev_id, fact_id, doc_id,"
            " version_id, chunk_id, path, locator, kind, provenance, confidence,"
            " valid_from, valid_to, superseded, superseded_by, inputs_count)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rev_id, fact_id, str(version["doc_id"]), version["version_id"],
             chunk_id, path, locator, "relation", "derived",
             float(row["weight"] or 1.0), version["valid_from"],
             version["valid_to"], int(version["superseded"] or 0),
             version["superseded_by"] or "", 1 if has_input else 0),
        )
        stats["derived"] += 1
        if has_input:
            conn.execute(
                "INSERT OR REPLACE INTO derivation_edge(rev_id, derived_fact_id,"
                " input_fact_id, kind) VALUES(?,?,?,?)",
                (rev_id, fact_id, input_fact, "derived_from"),
            )
            stats["edges"] += 1
        else:
            stats["inputs_missing"] += 1
    conn.commit()
    return stats


def version_index(conn: sqlite3.Connection, rev_id: str) -> dict[str, dict[str, Any]]:
    """Версии документов ревизии: doc_id → строка."""
    if not tables_present(conn):
        return {}
    try:
        rows = conn.execute(
            "SELECT doc_id, logical_id, version_id, path, valid_from, valid_to,"
            " superseded, superseded_by, supersede_reason, source, warnings_json"
            " FROM document_version WHERE rev_id = ?", (rev_id,)
        ).fetchall()
    except sqlite3.Error:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = {key: row[key] for key in row.keys()}
        try:
            item["warnings"] = json.loads(item.pop("warnings_json") or "[]")
        except (TypeError, ValueError):
            item["warnings"] = []
        out[str(item["doc_id"])] = item
    return out


def path_index(conn: sqlite3.Connection, rev_id: str) -> dict[str, dict[str, Any]]:
    """Версии по пути документа: путь → строка (для графовых операций)."""
    index = version_index(conn, rev_id)
    return {str(item.get("path")): item for item in index.values()}


def state_of(row: dict[str, Any] | None, asof: str | None = None) -> str:
    if not row:
        return "unknown"
    if asof:
        if not row.get("valid_from") and not row.get("valid_to"):
            return "unknown"
        return "current" if contains(row, asof) else "out_of_interval"
    return "superseded" if int(row.get("superseded") or 0) else "current"


def annotate(row: dict[str, Any], version: dict[str, Any] | None,
             asof: str | None = None) -> dict[str, Any]:
    """Добавить к строке выдачи поля версии и состояния."""
    row["validity"] = state_of(version, asof)
    if version:
        row["version_id"] = version.get("version_id")
        row["valid_from"] = version.get("valid_from")
        row["valid_to"] = version.get("valid_to")
        row["superseded"] = bool(int(version.get("superseded") or 0))
        row["superseded_by"] = version.get("superseded_by") or ""
        row["validity_source"] = version.get("source")
        row["validity_warnings"] = list(version.get("warnings") or [])
    else:
        row["superseded"] = False
    return row


def keep(row: dict[str, Any], version: dict[str, Any] | None, *,
         asof: str | None = None, include_superseded: bool = False) -> bool:
    """Оставить ли строку в выдаче по правилам провенанса."""
    state = state_of(version, asof)
    if asof:
        # Неизвестный интервал не выбрасывается: молчание о документе читалось бы
        # как «документа нет», а это не одно и то же.
        return state in ("current", "unknown")
    if state == "superseded" and not include_superseded:
        return False
    return True


def facts_by_ids(conn: sqlite3.Connection, rev_id: str,
                 fact_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    wanted = [str(item) for item in fact_ids if item]
    if not wanted or not tables_present(conn):
        return {}
    out: dict[str, dict[str, Any]] = {}
    step = 400
    for start in range(0, len(wanted), step):
        part = wanted[start:start + step]
        marks = ",".join("?" for _ in part)
        rows = conn.execute(
            "SELECT fact_id, doc_id, version_id, chunk_id, path, locator, kind,"
            " provenance, confidence, valid_from, valid_to, superseded,"
            " superseded_by, inputs_count FROM fact_assertion"
            f" WHERE rev_id = ? AND fact_id IN ({marks})", (rev_id, *part)
        ).fetchall()
        for row in rows:
            item = {key: row[key] for key in row.keys()}
            item["superseded"] = bool(int(item.get("superseded") or 0))
            out[str(item["fact_id"])] = item
    return out


def resolve_fact(conn: sqlite3.Connection, rev_id: str,
                 ref: str) -> tuple[list[dict[str, Any]], str]:
    """Найти факты по идентификатору или адресу. Возвращает (факты, способ)."""
    ref = str(ref or "").strip()
    if not ref:
        return [], ""
    direct = facts_by_ids(conn, rev_id, [ref])
    if direct:
        return [direct[ref]], "fact_id"
    rows = conn.execute(
        "SELECT fact_id FROM fact_assertion WHERE rev_id = ? AND (locator = ? OR path = ?)"
        " ORDER BY path, locator", (rev_id, ref, ref)
    ).fetchall()
    if not rows:
        return [], ""
    found = facts_by_ids(conn, rev_id, [str(row[0]) for row in rows])
    return [found[str(row[0])] for row in rows if str(row[0]) in found], "locator"


def _walk(conn: sqlite3.Connection, rev_id: str, seed_ids: set[str],
          depth: int) -> tuple[list[dict[str, Any]], set[str], int, dict[str, str]]:
    """Обход рёбер деривации в ширину до `depth`.

    Возвращает (строки, за_пределом, отвергнуто, откуда_пришли). Общий обход для
    `dependents` и `cascade`: два обхода одного графа расходятся на первой правке
    — один считает `outside_cap`, другой молча обрезает, и «ничего не зависит»
    становится неотличимо от «не смотрели».

    `outside_cap` называется отдельно, потому что «обрезано» и «не зависит» —
    разные ответы. Строка без адреса не выдаётся: её нельзя открыть.
    """
    seen: set[str] = set(seed_ids)
    frontier = sorted(seed_ids)
    out: list[dict[str, Any]] = []
    outside: set[str] = set()
    rejected = 0
    where_from: dict[str, str] = {}
    for step in range(1, depth + 1):
        nxt: list[str] = []
        for current in frontier:
            rows = conn.execute(
                "SELECT derived_fact_id FROM derivation_edge WHERE rev_id = ?"
                " AND input_fact_id = ? ORDER BY derived_fact_id",
                (rev_id, current),
            ).fetchall()
            for row in rows:
                dep = str(row[0])
                if dep in seen:
                    continue
                seen.add(dep)
                nxt.append(dep)
                where_from.setdefault(dep, current)
        if not nxt:
            break
        found = facts_by_ids(conn, rev_id, nxt)
        for dep in nxt:
            item = found.get(dep)
            if item is None:
                rejected += 1
                continue
            locator = str(item.get("locator") or "")
            path = str(item.get("path") or "")
            if not locator or not path:
                # Утверждение без адреса не выдаётся: его нельзя проверить.
                rejected += 1
                continue
            out.append({
                "fact_id": dep, "kind": item.get("kind"),
                "provenance": item.get("provenance"), "path": path,
                "locator": locator, "distance": step,
                "superseded": bool(item.get("superseded")),
                "superseded_by": item.get("superseded_by") or "",
            })
        # Следующий шаг — по всем найденным: и вывод, и утверждение человека могут
        # иметь своих зависимых. Разница только в `action`, а не в видимости.
        frontier = sorted(nxt)
        if step == depth:
            for dep in nxt:
                deeper = conn.execute(
                    "SELECT 1 FROM derivation_edge WHERE rev_id = ? AND input_fact_id = ?"
                    " LIMIT 1", (rev_id, dep)
                ).fetchone()
                if deeper:
                    outside.add(dep)
    return out, outside, rejected, where_from


def dependents(conn: sqlite3.Connection, rev_id: str, ref: str, *,
               depth: int = 3, include_stated: bool = True) -> dict[str, Any]:
    """Что опиралось на факт: обход рёбер деривации в ширину до `depth`.

    За глубиной факты не теряются: они считаются в `outside_cap`, потому что
    «обрезано» и «не зависит» — разные ответы.
    """
    depth = max(1, min(int(depth or 3), 10))
    seeds, how = resolve_fact(conn, rev_id, ref)
    if not seeds:
        return {
            "query": {"fact": ref, "depth": depth, "include_stated": include_stated},
            "fact": None, "dependents": [], "direct": 0, "total": 0,
            "outside_cap": 0, "classified": {"derived": 0, "stated": 0},
            "skipped_stated": 0, "rejected_no_citation": 0, "resolved_by": "",
            "reason": (f"факт {ref!r} в ревизии не найден: ни по идентификатору, "
                       f"ни по адресу"),
        }
    seed_ids = {str(item["fact_id"]) for item in seeds}
    out, outside, rejected, _ = _walk(conn, rev_id, seed_ids, depth)
    for item in out:
        item["action"] = ("пересчитать" if item.get("provenance") == "derived"
                          else "решение человека")
    stated = [item for item in out if item.get("provenance") == "stated"]
    shown = out if include_stated else [item for item in out
                                        if item.get("provenance") != "stated"]
    shown.sort(key=lambda item: (int(item["distance"]), str(item["path"]),
                                 str(item["locator"])))
    return {
        "query": {"fact": ref, "depth": depth, "include_stated": include_stated},
        "fact": seeds[0], "dependents": shown,
        "direct": len([item for item in out if item["distance"] == 1]),
        "total": len(shown), "outside_cap": len(outside),
        "classified": {"derived": len(out) - len(stated), "stated": len(stated)},
        "skipped_stated": 0 if include_stated else len(stated),
        "rejected_no_citation": rejected, "resolved_by": how, "reason": "",
    }


# ---------------------------------------------------------------------------
# Каскад устаревания и отчёт «что пересчитать»
# ---------------------------------------------------------------------------

#: Причины пометки. Закрытый список: причина вне его читалась бы как «неизвестно,
#: что с этим делать».
STALE_REASONS: tuple[str, ...] = ("cascade", "input_changed", "manual")


def split_refs(raw: Any) -> list[str]:
    """Разобрать перечисление ссылок: запятая, точка с запятой, пробел."""
    if isinstance(raw, (list, tuple, set)):
        parts = [str(item) for item in raw]
    else:
        parts = re.split(r"[,;\s]+", str(raw or ""))
    return [item.strip() for item in parts if item and item.strip()]


def plan_cascade(conn: sqlite3.Connection, rev_id: str, changed: Any, *,
                 depth: int = 3) -> dict[str, Any]:
    """Что пересчитать: обход от изменённых входов. Ничего не записывает.

    Отличие от `dependents` не в обходе, а в вопросе: там «что зависит от одного
    факта», здесь «что пересчитать, если изменился этот набор». Ссылка, которую
    разрешить не удалось, называется в `unresolved`, а не исчезает: молча
    проглоченная опечатка выглядит как «зависимостей нет».
    """
    depth = max(1, min(int(depth or 3), 10))
    items = split_refs(changed)
    seeds: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for ref in items:
        found, how = resolve_fact(conn, rev_id, ref)
        if not found:
            unresolved.append(ref)
            continue
        for fact in found:
            seeds.append({
                "fact_id": str(fact["fact_id"]), "path": str(fact.get("path") or ""),
                "locator": str(fact.get("locator") or ""),
                "provenance": str(fact.get("provenance") or ""),
                "resolved_by": how, "ref": ref,
            })
    seed_ids = {str(item["fact_id"]) for item in seeds}
    rows, outside, rejected, where_from = _walk(conn, rev_id, seed_ids, depth)
    plan: list[dict[str, Any]] = []
    for item in rows:
        derived = item.get("provenance") == "derived"
        parent = str(where_from.get(str(item["fact_id"])) or "")
        # Корень — изменённый вход, а не ближайший сосед: именно он объясняет,
        # почему факт пересчитывается. Ближайший вход остаётся отдельным полем.
        root = parent
        guard = 0
        while root and root not in seed_ids and guard < 12:
            root = str(where_from.get(root) or "")
            guard += 1
        plan.append({
            "fact_id": str(item["fact_id"]), "kind": item.get("kind"),
            "provenance": item.get("provenance"), "path": item["path"],
            "locator": item["locator"], "distance": int(item["distance"]),
            "cause_fact_id": root if root in seed_ids else (parent or ""),
            "input_fact_id": parent,
            "action": "пересчитать" if derived else "решение человека",
        })
    plan.sort(key=lambda row: (int(row["distance"]), str(row["path"]),
                               str(row["locator"])))
    recompute = [row for row in plan if row["provenance"] == "derived"]
    return {
        "changed": items, "seeds": seeds, "unresolved": unresolved,
        "plan": plan,
        "counts": {
            "seeds": len(seeds), "to_recompute": len(recompute),
            "stated": len(plan) - len(recompute),
            "outside_cap": len(outside), "rejected_no_citation": rejected,
            "unresolved": len(unresolved),
        },
    }


def apply_marks(conn: sqlite3.Connection, rev_id: str, plan: list[dict[str, Any]],
                *, reason: str = "cascade") -> dict[str, int]:
    """Записать пометки. Помечаются только выводы: утверждение человека не
    пересчитывается автоматически — за него отвечает человек."""
    if reason not in STALE_REASONS:
        raise ValueError(f"причина {reason!r} вне списка: {', '.join(STALE_REASONS)}")
    ensure_schema(conn)
    now = _iso_now()
    before = conn.total_changes
    skipped_stated = 0
    for row in plan:
        if row.get("provenance") != "derived":
            skipped_stated += 1
            continue
        conn.execute(
            "INSERT OR IGNORE INTO fact_stale(rev_id, fact_id, cause_fact_id,"
            " input_fact_id, distance, provenance, reason, marked_at, acked_at)"
            " VALUES(?,?,?,?,?,?,?,?,NULL)",
            (rev_id, str(row["fact_id"]), str(row.get("cause_fact_id") or ""),
             str(row.get("input_fact_id") or ""), int(row.get("distance") or 0),
             str(row.get("provenance") or ""), reason, now),
        )
    conn.commit()
    inserted = conn.total_changes - before
    return {"marked": int(inserted),
            "repeated": len(plan) - int(inserted) - skipped_stated,
            "skipped_stated": skipped_stated}


def stale_marks(conn: sqlite3.Connection, rev_id: str, *,
                limit: int = 50) -> dict[str, Any]:
    """Поставленные пометки ревизии: «что пересчитать». Только неподтверждённые."""
    if not tables_present(conn):
        return {"rows": [], "total": 0, "acked": 0, "rejected_no_citation": 0,
                "reason": "слой провенанса не построен в этой ревизии"}
    try:
        rows = conn.execute(
            "SELECT fact_id, cause_fact_id, input_fact_id, distance, provenance,"
            " reason, marked_at FROM fact_stale WHERE rev_id = ? AND acked_at IS NULL"
            " ORDER BY distance, fact_id", (rev_id,)
        ).fetchall()
        acked = conn.execute(
            "SELECT COUNT(*) FROM fact_stale WHERE rev_id = ? AND acked_at IS NOT NULL",
            (rev_id,)).fetchone()[0]
    except sqlite3.Error:
        return {"rows": [], "total": 0, "acked": 0, "rejected_no_citation": 0,
                "reason": "таблицы пометок нет: слой проверит её при следующей сборке"}
    ids = [str(row[0]) for row in rows]
    found = facts_by_ids(conn, rev_id, ids)
    out: list[dict[str, Any]] = []
    rejected = 0
    for row in rows:
        fact = found.get(str(row[0])) or {}
        path = str(fact.get("path") or "")
        locator = str(fact.get("locator") or "")
        if not path or not locator:
            rejected += 1
            continue
        out.append({
            "fact_id": str(row[0]), "cause_fact_id": str(row[1] or ""),
            "input_fact_id": str(row[2] or ""), "distance": int(row[3] or 0),
            "provenance": str(row[4] or ""), "reason": str(row[5] or ""),
            "marked_at": str(row[6] or ""), "path": path, "locator": locator,
            "action": "пересчитать",
        })
    return {"rows": out[:max(1, int(limit or 50))], "total": len(out),
            "acked": int(acked), "rejected_no_citation": rejected, "reason": ""}


def clear_marks(conn: sqlite3.Connection, rev_id: str) -> dict[str, int]:
    """Закрыть пометки: пересчёт выполнен. Строки не удаляются — остаётся след."""
    try:
        cur = conn.execute(
            "UPDATE fact_stale SET acked_at = ? WHERE rev_id = ? AND acked_at IS NULL",
            (_iso_now(), rev_id))
        conn.commit()
    except sqlite3.Error:
        return {"cleared": 0}
    return {"cleared": int(cur.rowcount or 0)}


def cascade(conn: sqlite3.Connection, rev_id: str, changed: Any = "", *,
            depth: int = 3, apply: bool = False, clear: bool = False,
            reason: str = "cascade", limit: int = 50) -> dict[str, Any]:
    """Отчёт «что пересчитать» и постановка пометок.

    Три вида вызова и только один на вызов: `apply` пишет, `clear` закрывает,
    пустой `changed` без них читает уже поставленное. `apply` и `clear` вместе —
    отказ: «пометить и закрыть» звучит как одно действие, а делает разное.
    """
    if apply and clear:
        raise ValueError("одно действие на вызов: apply или clear")
    if reason not in STALE_REASONS:
        raise ValueError(f"причина {reason!r} вне списка: {', '.join(STALE_REASONS)}")
    depth = max(1, min(int(depth or 3), 10))
    ensure_schema(conn)
    if clear:
        cleared = clear_marks(conn, rev_id)["cleared"]
        return {"query": {"changed": [], "depth": depth, "apply": False,
                          "clear": True},
                "index_rev": rev_id, "cleared": cleared,
                "plan": [], "seeds": [], "unresolved": [],
                "counts": {"cleared": cleared}, "reason": ""}
    items = split_refs(changed)
    if not items:
        if apply:
            raise ValueError("не названо, что изменилось: `apply` без `changed` "
                             "помечать нечего")
        marks = stale_marks(conn, rev_id, limit=limit)
        return {"query": {"changed": [], "depth": depth, "apply": False,
                          "clear": False},
                "index_rev": rev_id, "plan": marks["rows"], "seeds": [],
                "unresolved": [], "counts": {"to_recompute": marks["total"],
                                             "acked": marks["acked"],
                                             "rejected_no_citation": marks[
                                                 "rejected_no_citation"]},
                "reason": marks["reason"]}
    planned = plan_cascade(conn, rev_id, items, depth=depth)
    counts = dict(planned["counts"])
    if apply:
        written = apply_marks(conn, rev_id, planned["plan"], reason=reason)
        counts.update(written)
    return {"query": {"changed": items, "depth": depth, "apply": bool(apply),
                      "clear": False},
            "index_rev": rev_id, "plan": planned["plan"], "seeds": planned["seeds"],
            "unresolved": planned["unresolved"], "counts": counts, "reason": ""}


def auto_cascade(conn: sqlite3.Connection, rev_id: str, *,
                 depth: int = 3) -> dict[str, Any]:
    """Пометки при индексации: что изменилось и что из-за этого пересчитать.

    Изменение входа доказывается `sha256` версии документа в предыдущей ревизии
    против текущей — это замер, а не догадка. Нет предыдущей ревизии в хранилище
    (её убрала ротация) — каскад не выдумывает изменение и говорит об этом.
    """
    ensure_schema(conn)
    empty = {"rev": rev_id, "previous": "", "changed_documents": 0,
             "added_documents": 0, "removed_documents": 0, "seeds": 0,
             "marked": 0, "repeated": 0, "outside_cap": 0, "reason": ""}
    try:
        row = conn.execute(
            "SELECT rev_id FROM document_version WHERE rev_id < ?"
            " ORDER BY rev_id DESC LIMIT 1", (rev_id,)).fetchone()
    except sqlite3.Error:
        return {**empty, "reason": "таблицы версий нет: изменение входов не доказать"}
    if row is None:
        return {**empty,
                "reason": ("предыдущей ревизии в хранилище нет: изменение входов "
                           "доказать нечем (первый прогон или ротация)")}
    previous = str(row[0])

    def versions(rev: str) -> dict[str, str]:
        try:
            return {str(item[0]): str(item[1] or "") for item in conn.execute(
                "SELECT path, sha256 FROM document_version WHERE rev_id = ?", (rev,))}
        except sqlite3.Error:
            return {}

    now_versions = versions(rev_id)
    before_versions = versions(previous)
    changed = sorted(path for path, digest in now_versions.items()
                     if path in before_versions and before_versions[path] != digest)
    added = sorted(path for path in now_versions if path not in before_versions)
    removed = sorted(path for path in before_versions if path not in now_versions)
    if not changed:
        return {**empty, "previous": previous,
                "added_documents": len(added), "removed_documents": len(removed),
                "reason": "изменённых документов нет: входы выводов те же"}
    try:
        seed_rows = conn.execute(
            "SELECT fact_id FROM fact_assertion WHERE rev_id = ? AND kind = 'chunk'"
            " AND path IN (" + ",".join("?" for _ in changed) + ")",
            (rev_id, *changed)).fetchall()
    except sqlite3.Error:
        return {**empty, "previous": previous,
                "changed_documents": len(changed),
                "reason": "фактов чанков нет: каскад начинать не с чего"}
    seeds = ",".join(str(item[0]) for item in seed_rows)
    if not seeds:
        return {**empty, "previous": previous,
                "changed_documents": len(changed),
                "added_documents": len(added), "removed_documents": len(removed),
                "reason": "у изменённых документов нет фактов чанков"}
    planned = plan_cascade(conn, rev_id, seeds, depth=depth)
    written = apply_marks(conn, rev_id, planned["plan"], reason="input_changed")
    return {"rev": rev_id, "previous": previous,
            "changed_documents": len(changed), "added_documents": len(added),
            "removed_documents": len(removed), "seeds": len(planned["seeds"]),
            "marked": written["marked"], "repeated": written["repeated"],
            "outside_cap": int(planned["counts"].get("outside_cap") or 0),
            "reason": ""}
