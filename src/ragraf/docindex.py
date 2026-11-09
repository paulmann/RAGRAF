"""Хранение документного слоя и валидатор чанкинга.

Почему таблицы здесь, а не в `lexvec.py`.
Поиск и векторы — общие: документные чанки идут в те же `ft_chunk`, `chunk_text`,
`chunk_map`, `vec_meta`, через тот же `LexVecStore.add_chunks`. Адрес и структура
документа — своё: у кодового чанка адрес это `path:line`, у документа он бывает
`sheet=Данные!A1:C20` или `#/items/0`. Класть это в `chunk_map` значило бы
добавлять колонки в таблицу, которую читают все операции поиска.

Почему три таблицы, а не одна.
  `document`      — паспорт файла: статус извлечения и причина. Документ,
                    который НЕ разобран, обязан быть виден: иначе `doc_status`
                    показывал бы только успехи, а «тихий пропуск» — ровно то,
                    что спецификация запрещает.
  `document_node` — узлы: структура и провенанс разбора.
  `document_chunk`— чанки: адрес, связь частей, статус, сколько символов ушло
                    в эмбеддер.

Почему валидатор живёт здесь. Его вызывает и индексатор (перед закреплением
ревизии), и операция `doc_validate`. Один код на два вызова: проверка, которой
пользуется только операция, к закреплению отношения не имеет, а проверка,
встроенная только в индексатор, недоступна для разбора после факта.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from . import util
from .docchunk import DocChunk, stats
from .docnode import (
    STATUSES,
    DocumentNode,
    doc_id_for,
    locator_problems,
    split_locator,
)


@dataclass
class Document:
    """Один документ в ревизии: паспорт плюс узлы и чанки."""

    path: str
    status: str
    reason: str = ""
    reader: str = ""
    converter: str = ""
    sha256: str = ""
    #: Число строк ИСХОДНОГО файла (для бинарных форматов — 0): в `file.lines`
    #: пишется именно оно, чтобы «страниц 12» не выдавалось за «12 строк».
    lines: int = 0
    warnings: list[str] = field(default_factory=list)
    nodes: list[DocumentNode] = field(default_factory=list)
    chunks: list[DocChunk] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    #: Интервал действия редакции (провенанс). Берётся только из явных метаданных
    #: документа (`provenance.parse_interval`); пусто — интервал неизвестен, и это
    #: отличается от «действует всегда». Поля заполняет индексатор до записи:
    #: решение о текущей редакции принимается по данным, а не по имени файла.
    valid_from: str | None = None
    valid_to: str | None = None
    superseded_by: str = ""
    logical_id: str = ""
    validity_source: str = "unknown"
    validity_warnings: list[str] = field(default_factory=list)

    @property
    def doc_id(self) -> str:
        return doc_id_for(self.path)

    @property
    def ext(self) -> str:
        return ("." + self.path.rsplit(".", 1)[-1].lower()) if "." in self.path else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "path": self.path,
            "status": self.status,
            "reason": self.reason,
            "reader": self.reader,
            "converter": self.converter,
            "nodes": len(self.nodes),
            "chunks": len(self.chunks),
            "lines": self.lines,
            "sha256": self.sha256,
            "warnings": list(self.warnings),
            "validity": {
                "valid_from": self.valid_from,
                "valid_to": self.valid_to,
                "superseded_by": self.superseded_by,
                "logical_id": self.logical_id,
                "source": self.validity_source,
                "warnings": list(self.validity_warnings),
            },
        }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _loads(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        data = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


DOCUMENT_DDL = """
CREATE TABLE IF NOT EXISTS document (
  rev_id TEXT NOT NULL,
  doc_id TEXT NOT NULL,
  path TEXT NOT NULL,
  ext TEXT,
  reader TEXT,
  converter TEXT,
  status TEXT NOT NULL,
  reason TEXT,
  sha256 TEXT,
  nodes INTEGER,
  chunks INTEGER,
  warnings_json TEXT,
  metadata_json TEXT,
  PRIMARY KEY (rev_id, doc_id)
);
CREATE INDEX IF NOT EXISTS idx_document_path ON document(rev_id, path);
CREATE INDEX IF NOT EXISTS idx_document_status ON document(rev_id, status);

CREATE TABLE IF NOT EXISTS document_node (
  rev_id TEXT NOT NULL,
  doc_id TEXT NOT NULL,
  node_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  locator TEXT NOT NULL,
  node_index INTEGER,
  text TEXT,
  structure_json TEXT,
  metadata_json TEXT,
  provenance_json TEXT,
  extraction_status TEXT NOT NULL,
  warnings_json TEXT,
  PRIMARY KEY (rev_id, node_id)
);
CREATE INDEX IF NOT EXISTS idx_docnode_doc ON document_node(rev_id, doc_id);

CREATE TABLE IF NOT EXISTS document_chunk (
  rev_id TEXT NOT NULL,
  chunk_id TEXT NOT NULL,
  doc_id TEXT NOT NULL,
  path TEXT NOT NULL,
  locator TEXT NOT NULL,
  kind TEXT NOT NULL,
  text TEXT NOT NULL,
  tokens INTEGER,
  part_of TEXT,
  structure_json TEXT,
  provenance_json TEXT,
  extraction_status TEXT NOT NULL,
  vector_chars INTEGER,
  PRIMARY KEY (rev_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_docchunk_doc ON document_chunk(rev_id, doc_id);
CREATE INDEX IF NOT EXISTS idx_docchunk_locator ON document_chunk(rev_id, locator);
"""


# ---------------------------------------------------------------------------
# Запись
# ---------------------------------------------------------------------------


def write(conn, rev_id: str, documents: list[Document]) -> dict[str, int]:
    """Записать паспорта, узлы и чанки одной ревизии.

    `INSERT OR REPLACE` — по той же причине, что и в графовых таблицах: прогон
    может повторяться, а ключ `(rev_id, ...)` делает повтор идемпотентным.
    """
    counts = {"documents": 0, "nodes": 0, "chunks": 0}
    for doc in documents:
        doc_id = doc.doc_id
        conn.execute(
            "INSERT OR REPLACE INTO document(rev_id, doc_id, path, ext, reader,"
            " converter, status, reason, sha256, nodes, chunks, warnings_json,"
            " metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rev_id, doc_id, doc.path, doc.ext, doc.reader, doc.converter,
             doc.status, doc.reason, doc.sha256, len(doc.nodes),
             len(doc.chunks), _json(doc.warnings), _json(doc.metadata)),
        )
        counts["documents"] += 1
        for node in doc.nodes:
            node_id = util.stable_id("doc_node", doc_id, node.locator, node.index)
            conn.execute(
                "INSERT OR REPLACE INTO document_node(rev_id, doc_id, node_id,"
                " kind, locator, node_index, text, structure_json, metadata_json,"
                " provenance_json, extraction_status, warnings_json)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (rev_id, doc_id, node_id, node.kind, node.locator, node.index,
                 node.text, _json(node.structure), _json(node.metadata),
                 _json(node.provenance), node.extraction_status,
                 _json(node.extraction_warnings)),
            )
            counts["nodes"] += 1
        for chunk in doc.chunks:
            conn.execute(
                "INSERT OR REPLACE INTO document_chunk(rev_id, chunk_id, doc_id,"
                " path, locator, kind, text, tokens, part_of, structure_json,"
                " provenance_json, extraction_status, vector_chars)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rev_id, chunk.chunk_id, doc_id, chunk.path, chunk.locator,
                 chunk.kind, chunk.text, chunk.tokens, chunk.part_of,
                 _json(chunk.structure), _json(chunk.provenance),
                 chunk.extraction_status, chunk.vector_chars),
            )
            counts["chunks"] += 1
    return counts


# ---------------------------------------------------------------------------
# Чтение
# ---------------------------------------------------------------------------


def documents(conn, rev_id: str, doc_id: str | None = None) -> list[dict[str, Any]]:
    sql = ("SELECT doc_id, path, ext, reader, converter, status, reason, sha256,"
           " nodes, chunks, warnings_json, metadata_json FROM document"
           " WHERE rev_id = ?")
    args: list[Any] = [rev_id]
    if doc_id:
        sql += " AND (doc_id = ? OR path = ?)"
        args.extend([doc_id, doc_id])
    sql += " ORDER BY path"
    return [dict(row) for row in conn.execute(sql, args).fetchall()]


def chunks(conn, rev_id: str, doc_id: str | None = None,
           locator: str | None = None, limit: int = 0) -> list[dict[str, Any]]:
    sql = ("SELECT chunk_id, doc_id, path, locator, kind, text, tokens, part_of,"
           " structure_json, provenance_json, extraction_status, vector_chars"
           " FROM document_chunk WHERE rev_id = ?")
    args: list[Any] = [rev_id]
    if doc_id:
        sql += " AND (doc_id = ? OR path = ?)"
        args.extend([doc_id, doc_id])
    if locator:
        sql += " AND locator = ?"
        args.append(locator)
    sql += " ORDER BY path, locator, part_of"
    if limit:
        sql += " LIMIT ?"
        args.append(int(limit))
    out = []
    for row in conn.execute(sql, args).fetchall():
        item = dict(row)
        # Разбор JSON, а НЕ выбрасывание: `doc_get` обязан отдать провенанс
        # разбора, иначе «чем прочитан этот текст» остаётся без ответа. Первый
        # вариант выбрасывал поля «клиенту не нужны», и провенанс приходил
        # пустым — то есть доверие к факту нельзя было проверить.
        item["structure"] = _loads(item.pop("structure_json", ""))
        item["provenance"] = _loads(item.pop("provenance_json", ""))
        out.append(item)
    return out


def rows_by_chunk_ids(conn, rev_id: str,
                      chunk_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Чанки по идентификаторам — пакетом, для поиска с адресом и провенансом."""
    wanted = [str(c) for c in chunk_ids if c]
    if not wanted:
        return {}
    out: dict[str, dict[str, Any]] = {}
    step = 400
    for start in range(0, len(wanted), step):
        part = wanted[start:start + step]
        marks = ",".join("?" * len(part))
        sql = (f"SELECT chunk_id, doc_id, path, locator, kind, tokens, part_of,"
               f" text, structure_json, provenance_json, extraction_status,"
               f" vector_chars"
               f" FROM document_chunk WHERE rev_id = ? AND chunk_id IN ({marks})")
        for row in conn.execute(sql, [rev_id, *part]).fetchall():
            item = dict(row)
            item["structure"] = _loads(item.pop("structure_json", ""))
            item["provenance"] = _loads(item.pop("provenance_json", ""))
            out[str(item["chunk_id"])] = item
    return out


# ---------------------------------------------------------------------------
# Валидатор
# ---------------------------------------------------------------------------

#: Проверки валидатора — закрытый список номеров спецификации. Номер нужен
#: отчёту: «какое правило нарушено» отвечает на вопрос, а текст ответа — нет.
CHECKS = {
    1: "у каждого чанка валидный локатор",
    2: "у текстового чанка есть heading_chain или названная причина",
    3: "таблица не разорвана без table_id и связи частей",
    4: "значение CSV не проиндексировано без контекста колонок",
    5: "слайд не смешан с заметками докладчика",
    6: "фрагмент JSON интерпретируем или содержит parent_path",
    7: "ошибка читателя не подавлена: документ в отчёте как extraction_failed",
    8: "формат вне издания помечен not_indexed_by_edition и не удалён",
}


def validate(conn, rev_id: str, doc_id: str | None = None) -> dict[str, Any]:
    """Проверить целостность чанкинга. Пустой `problems` — индекс годен.

    Проверки 1, 2, 7, 8 работают на данных ревизии, а не на памяти прогона:
    `doc_validate` вызывается ПОСЛЕ индексации, и «в прогоне было хорошо» не
    доказывает, что в индексе лежит то же самое.
    """
    problems: list[dict[str, Any]] = []
    docs = documents(conn, rev_id, doc_id)
    if doc_id and not docs:
        problems.append({
            "check": 1, "kind": "missing_document",
            "detail": f"документ не найден в ревизии: {doc_id}",
        })
    for doc in docs:
        status = str(doc.get("status") or "")
        path = str(doc.get("path") or "")
        if status not in STATUSES:
            problems.append({
                "check": 7, "kind": "unknown_status", "path": path,
                "detail": f"неизвестный статус извлечения: {status!r}",
            })
        if status in ("failed", "not_indexed_by_edition") and int(doc.get("chunks") or 0):
            # Чанки у неразобранного документа означают, что статус выставлен
            # «для отчёта», а в индекс попало старое или чужое содержимое.
            problems.append({
                "check": 7, "kind": "chunks_without_extraction", "path": path,
                "detail": (f"статус {status}, но чанков "
                           f"{doc.get('chunks')} — статус не соответствует данным"),
            })
        if status == "failed" and not str(doc.get("reason") or "").strip():
            problems.append({
                "check": 7, "kind": "failed_without_reason", "path": path,
                "detail": "статус failed без причины: отказ обязан называть причину",
            })
        if status == "not_indexed_by_edition" and not str(doc.get("reason") or "").strip():
            problems.append({
                "check": 8, "kind": "edition_without_reason", "path": path,
                "detail": ("статус not_indexed_by_edition без причины: "
                           "«не умеем» обязано называть, чего не умеем"),
            })

    # Структура читается ОТДЕЛЬНЫМ запросом, а не из `chunks()`: та функция
    # отдаёт ответ операции и выбрасывает `structure_json` (клиенту он не нужен).
    # Первый вариант валидатора брал данные именно оттуда и поэтому ЛОЖНО
    # объявлял нарушением проверку 2 на каждом абзаце — гейт был не пустым, а
    # слепым. Разделение обязательно: ответ операции и вход валидатора — разные
    # выборки, и совпадение их формы не должно быть условием работы проверки.
    sql = ("SELECT chunk_id, doc_id, path, locator, kind, tokens, part_of,"
           " structure_json FROM document_chunk WHERE rev_id = ?")
    args: list[Any] = [rev_id]
    if doc_id:
        sql += " AND (doc_id = ? OR path = ?)"
        args.extend([doc_id, doc_id])
    for row in conn.execute(sql, args).fetchall():
        chunk = dict(row)
        locator = str(chunk.get("locator") or "")
        kind = str(chunk.get("kind") or "")
        structure = _loads(chunk.get("structure_json"))
        path = str(chunk.get("path") or "")
        for text in locator_problems(kind, locator):
            problems.append({
                "check": 1, "kind": "bad_locator", "path": path,
                "locator": locator, "chunk_id": chunk.get("chunk_id"),
                "detail": text,
            })
        if kind in ("section", "clause", "paragraph"):
            chain = structure.get("heading_chain") or []
            reason = str(structure.get("no_heading_reason") or "")
            if not chain and not reason:
                problems.append({
                    "check": 2, "kind": "no_heading", "path": path,
                    "locator": locator, "chunk_id": chunk.get("chunk_id"),
                    "detail": "нет heading_chain и не названа причина",
                })
        if kind in ("table", "table_row"):
            if not str(structure.get("table_id") or ""):
                problems.append({
                    "check": 3, "kind": "table_without_id", "path": path,
                    "locator": locator, "chunk_id": chunk.get("chunk_id"),
                    "detail": "табличный чанк без table_id",
                })
            if structure.get("parts") and not structure.get("part_of"):
                problems.append({
                    "check": 3, "kind": "table_part_without_link", "path": path,
                    "locator": locator, "chunk_id": chunk.get("chunk_id"),
                    "detail": "часть таблицы без связи part_of",
                })
        if kind == "slide" and structure.get("from_notes"):
            problems.append({
                "check": 5, "kind": "notes_inside_slide", "path": path,
                "locator": locator, "chunk_id": chunk.get("chunk_id"),
                "detail": "текст заметок попал в узел слайда",
            })
        if structure.get("from_notes") and not locator.endswith("#notes"):
            problems.append({
                "check": 5, "kind": "notes_without_address", "path": path,
                "locator": locator, "chunk_id": chunk.get("chunk_id"),
                "detail": "узел заметок докладчика без адреса `#notes`",
            })
        # Проверка 4: значение CSV без имён колонок — число без смысла.
        if path.lower().endswith((".csv", ".tsv")) and not str(
                structure.get("columns") or "").strip():
            problems.append({
                "check": 4, "kind": "csv_without_columns", "path": path,
                "locator": locator, "chunk_id": chunk.get("chunk_id"),
                "detail": "запись CSV без имён колонок в структуре",
            })
        # Проверка 6: фрагмент структурированных данных обязан быть либо
        # разбираемым, либо с названным родителем. Иначе `#/items/0` — адрес
        # в никуда: непонятно, часть чего этот кусок.
        if path.lower().endswith((".json", ".jsonl", ".ndjson", ".yaml", ".yml",
                                  ".toml", ".xml")):
            parent = str(structure.get("parent_path") or "")
            text_value = str(chunk.get("text") or "").strip()
            parseable = False
            if text_value.startswith(("{", "[")):
                try:
                    json.loads(text_value)
                    parseable = True
                except (ValueError, TypeError):
                    parseable = False
            # Для XML полный путь в дереве (`#xpath(...)`) И ЕСТЬ адрес фрагмента:
            # родитель у него выражен самим путём, а не отдельным полем. Требовать
            # ещё и `parent_path` значило бы требовать дважды одно и то же.
            addressed = (locator.find("#/") >= 0 or "#xpath(" in locator)
            if not parent and not parseable and not addressed:
                problems.append({
                    "check": 6, "kind": "fragment_without_parent", "path": path,
                    "locator": locator, "chunk_id": chunk.get("chunk_id"),
                    "detail": ("фрагмент структурированных данных без parent_path"
                               " и не разбирается как JSON"),
                })
    return {
        "rev_id": rev_id,
        "documents": len(docs),
        "checks": len(CHECKS),
        "problems": problems,
        "ok": not problems,
    }


def status(conn, rev_id: str, doc_id: str | None = None) -> dict[str, Any]:
    """Состояние документного слоя: по статусам, с бюджетами и причинами."""
    docs = documents(conn, rev_id, doc_id)
    by_status: dict[str, int] = {}
    for doc in docs:
        key = str(doc.get("status") or "unknown")
        by_status[key] = by_status.get(key, 0) + 1
    chunk_rows = chunks(conn, rev_id, doc_id)
    tokens = [int(row.get("tokens") or 0) for row in chunk_rows]
    partial = sum(1 for row in chunk_rows
                  if int(row.get("vector_chars") or 0) >= 4000
                  and int(row.get("tokens") or 0) * 4.2 > 4000)
    locators = sorted({split_locator(str(row.get("locator") or ""))[0]
                       for row in chunk_rows})
    return {
        "rev_id": rev_id,
        "documents": len(docs),
        "by_status": by_status,
        "chunks": len(chunk_rows),
        "tokens_avg": round(sum(tokens) / len(tokens), 1) if tokens else 0,
        "tokens_p95": (sorted(tokens)[min(len(tokens) - 1, int(len(tokens) * 0.95))]
                       if tokens else 0),
        "tokens_max": max(tokens) if tokens else 0,
        "chunks_vector_partial": partial,
        "locator_forms": [form for form in locators if form],
        "failed": [{"path": d.get("path"), "reason": d.get("reason")}
                   for d in docs if d.get("status") == "failed"],
        "not_indexed_by_edition": [
            {"path": d.get("path"), "reason": d.get("reason")}
            for d in docs if d.get("status") == "not_indexed_by_edition"
        ],
    }


def summary(documents: list[Document]) -> dict[str, Any]:
    """Сводка по пачке документов ДО записи — для отчёта прогона."""
    all_chunks = [chunk for doc in documents for chunk in doc.chunks]
    return {
        "documents": len(documents),
        "failed": sum(1 for doc in documents if doc.status == "failed"),
        "not_indexed_by_edition": sum(
            1 for doc in documents if doc.status == "not_indexed_by_edition"
        ),
        "degraded": sum(1 for doc in documents if doc.status == "degraded"),
        **stats(all_chunks),
    }
