"""Резка узлов документа на чанки: границы структуры и бюджеты в токенах.

Три правила, которые решают всё остальное.

1. ГРАНИЦА ВАЖНЕЕ РАЗМЕРА. Сначала чанк = узел. Режется только тот узел, который
   не влез в максимум бюджета. Так абзац, пункт и таблица остаются целыми, а
   «склейка соседних мелочей» не делается вовсе: склейка через границу вида
   сделала бы чанк, у которого нет одного адреса, — то есть вернула бы ровно тот
   дефект, ради которого слой и заведён.

2. РАЗРЕЗ НЕ ТЕРЯЕТ АДРЕС. Если узел адресован строками файла, каждая часть
   получает СВОЙ диапазон строк (`path:12-24`): такой адрес разрешается обратно.
   Если адрес не устроен строками (якорь, xpath, лист, слайд), часть сохраняет
   адрес родителя и помечается `part_of` + номером части: адрес по-прежнему
   разрешим, а «это кусок» видно в структуре.

3. БЮДЖЕТ — В ТОКЕНАХ, А НЕ В СИМВОЛАХ. Единица проекта — оценка
   `ceil(len/4.2)` (`lexvec.py:241`): своего токенизатора у слоя нет, и
   выдавать оценку за измерение нельзя. Отдельно считается `vector_chars` —
   сколько символов реально уйдёт в эмбеддер: вход обрезается на 4000 символов
   (`embeddings.py:341`), и чанк длиннее этого предела кодируется ЧАСТИЧНО.
   Это не скрывается: у чанка с `vector_partial` в провенансе стоит причина, и
   `doc_validate` называет такие чанки числом.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from . import util
from .docnode import DocumentNode, path_of, split_locator

#: Символов на «токен» в оценке проекта. Одно место правды: то же число стоит в
#: `lexvec.py:241`, и разойтись они не должны.
CHARS_PER_TOKEN = 4.2

#: Предел входа эмбеддера в символах — из `embeddings.truncate_for_embedding`.
EMBED_MAX_CHARS = 4000

#: Бюджеты по виду узла: (целевой минимум, максимум) в токенах.
#: Минимум не склеивает: он нужен отчёту («сколько чанков мельче цели») и
#: порогу, ниже которого дробление бессмысленно.
BUDGETS: dict[str, tuple[int, int]] = {
    "document": (300, 950),
    "section": (500, 1200),
    "clause": (500, 1200),
    "paragraph": (500, 1200),
    "message": (500, 1200),
    "table": (300, 1200),
    "table_row": (300, 1200),
    # Лист и старый документ: бюджет ниже общего, потому что именно у них
    # чанк длиннее предела эмбеддера (4000 символов), и вектор перестаёт
    # видеть хвост данных. Замер 29.10.2026: 3699 из 3765 частичных векторов
    # приходились на эти два вида.
    "sheet": (300, 950),
    "record": (300, 1200),
    "slide": (200, 800),
    "image": (200, 800),
}

#: Бюджет по умолчанию: вид без своего числа не должен падать.
DEFAULT_BUDGET: tuple[int, int] = (300, 1200)


def estimate_tokens(text: str) -> int:
    """Оценка токенов чанка — та же формула, что у кодового индекса."""
    return int(math.ceil(len(text or "") / CHARS_PER_TOKEN))


@dataclass
class DocChunk:
    """Кусок документа: адрес, текст, структура, провенанс."""

    chunk_id: str
    doc_id: str
    path: str
    locator: str
    kind: str
    text: str
    tokens: int
    part_of: str = ""
    structure: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    extraction_status: str = "ok"
    warnings: list[str] = field(default_factory=list)
    start_line: int | None = None
    end_line: int | None = None

    @property
    def vector_chars(self) -> int:
        """Сколько символов реально попадёт в эмбеддер."""
        return min(len(self.text), EMBED_MAX_CHARS)

    @property
    def vector_partial(self) -> bool:
        return len(self.text) > EMBED_MAX_CHARS

    def to_dict(self) -> dict[str, Any]:
        data = {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "path": self.path,
            "locator": self.locator,
            "kind": self.kind,
            "tokens": self.tokens,
            "part_of": self.part_of,
            "structure": dict(self.structure),
            "provenance": dict(self.provenance),
            "extraction_status": self.extraction_status,
            "warnings": list(self.warnings),
            "vector_chars": self.vector_chars,
        }
        if self.vector_partial:
            data["vector_partial"] = True
        if self.start_line is not None:
            data["start_line"] = self.start_line
            data["end_line"] = self.end_line
        return data


def budget_for(kind: str) -> tuple[int, int]:
    return BUDGETS.get(kind, DEFAULT_BUDGET)


def _line_window(node: DocumentNode) -> tuple[int, int] | None:
    """Диапазон строк узла, если адрес устроен строками файла."""
    form, _path, fields = split_locator(node.locator)
    if form != "line":
        return None
    start = int(fields.get("start") or 0)
    end = int(fields.get("end") or start)
    if start <= 0:
        return None
    return (start, end)


def _split_lines(node: DocumentNode, max_tokens: int,
                 reserve_tokens: int = 0) -> list[tuple[int, int, str]]:
    """Разрезать текст узла по строкам с учётом резерва под заголовок.

    `reserve_tokens` — место, которое займёт повторяемый заголовок колонок в
    каждой части. Без резерва часть выходила на 1204 «токена» при бюджете 1200:
    заголовок приклеивался ПОСЛЕ упаковки (замер 29.10.2026).
    """
    """Разрезать текст узла по строкам: `(смещение, число строк, текст)`."""
    lines = node.text.splitlines() or [node.text]
    # Строка, которая сама больше бюджета, режется по символам. Без этого один
    # XML- или JSON-узел, записанный одной строкой, давал чанк на 2235 «токенов»
    # при максимуме 1200 (замерено на этом корпусе 29.10.2026): разрезать по
    # строкам было нечего, и бюджет молча нарушался.
    # Бюджет упаковки уменьшается на резерв, но не ниже четверти: у нулевого
    # бюджета упаковка перестала бы двигаться вовсе.
    pack_tokens = max(1, max_tokens - max(0, int(reserve_tokens)), max_tokens // 4)
    max_chars = int(pack_tokens * CHARS_PER_TOKEN)
    expanded: list[str] = []
    for line in lines:
        if estimate_tokens(line) <= pack_tokens:
            expanded.append(line)
            continue
        for offset in range(0, len(line), max(1, max_chars)):
            expanded.append(line[offset:offset + max_chars])
    lines = expanded
    parts: list[tuple[int, int, str]] = []
    buffer: list[str] = []
    start = 0
    for offset, line in enumerate(lines):
        candidate = "\n".join(buffer + [line])
        if buffer and estimate_tokens(candidate) > pack_tokens:
            parts.append((start, len(buffer), "\n".join(buffer)))
            buffer = [line]
            start = offset
            continue
        if not buffer:
            start = offset
        buffer.append(line)
    if buffer:
        parts.append((start, len(buffer), "\n".join(buffer)))
    return parts


def chunk_node(node: DocumentNode, doc_id: str) -> list[DocChunk]:
    """Чанки одного узла. Один узел — один чанк, если он влез в бюджет."""
    path = node.path or path_of(node.locator)
    _target_min, max_tokens = budget_for(node.kind)
    base_structure = dict(node.structure)
    base_provenance = dict(node.provenance)
    base_provenance.setdefault("budget_max_tokens", max_tokens)
    base_provenance.setdefault("tokens_estimate", f"ceil(chars/{CHARS_PER_TOKEN})")
    common = dict(
        doc_id=doc_id, path=path, kind=node.kind,
        provenance=base_provenance, extraction_status=node.extraction_status,
    )

    tokens = estimate_tokens(node.text)
    window = _line_window(node)
    # Статус узла НЕ отменяет бюджет. Прежнее условие «режем только ok» давало
    # чанк на 190 303 «токена» из одного старого .xls (замер 29.10.2026): текст
    # есть, а бюджет нарушен. Статус при этом не завышается — он переносится на
    # каждую часть и виден в отчёте.
    if tokens <= max_tokens:
        structure = dict(base_structure)
        chunk = DocChunk(
            chunk_id=util.stable_id("doc_chunk", doc_id, node.locator, 0),
            locator=node.locator, text=node.text, tokens=tokens,
            structure=structure, warnings=list(node.extraction_warnings),
            **common,
        )
        if window:
            chunk.start_line, chunk.end_line = window
        return [chunk]

    header = str(base_structure.get("repeat_header") or "")
    # Заголовок повторяется в каждой части, но он не имеет права съесть бюджет:
    # у широкого листа (сотни колонок) строка заголовка сама больше 1200 токенов,
    # и часть выходила на 55 919 токенов при бюджете 1200 (замер 29.10.2026).
    # Доля четверти бюджета — компромисс: контекст колонок сохраняется, размер
    # части остаётся в бюджете. Усечение называется в структуре, а не молчит.
    header_cap = max_tokens // 4
    header_cut = header
    if header and estimate_tokens(header) > header_cap:
        header_cut = header[: int(header_cap * CHARS_PER_TOKEN)]
    parts = _split_lines(node, max_tokens, estimate_tokens(header_cut))
    out: list[DocChunk] = []
    # Заголовок колонок повторяется в КАЖДОЙ части, кроме первой (в ней он уже
    # есть как первая строка). Без этого значение в разрезанном листе остаётся
    # числом без имени колонки — проверка «значение не без контекста» иначе
    # проходит формально: текст есть, а смысла в нём нет.
    header = str(base_structure.get("repeat_header") or "")
    for part_index, (offset, count, body) in enumerate(parts):
        structure = dict(base_structure)
        if header_cut and part_index > 0:
            body = f"{header_cut}\n{body}"
            structure["header_repeated"] = True
            if header_cut != header:
                structure["header_truncated"] = True
        # Последняя гарантия бюджета: упаковка считает токены приблизительно
        # (ceil по символам), и часть может выйти на 1 токен больше предела.
        # Обрезаем по символам, а не «примерно»: свойство «часть в бюджете»
        # обязано держаться самим кодом, а не удачной арифметикой.
        if estimate_tokens(body) > max_tokens:
            body = body[: int(max_tokens * CHARS_PER_TOKEN)]
        structure["part"] = part_index + 1
        structure["parts"] = len(parts)
        structure["part_of"] = node.locator
        locator = node.locator
        start_line: int | None = None
        end_line: int | None = None
        if window is not None:
            start_line = window[0] + offset
            end_line = min(start_line + count - 1, window[1])
            locator = f"{path}:{start_line}-{end_line}"
            structure["part_locator"] = locator
        else:
            # Адрес не строится строками: часть сохраняет адрес родителя, а
            # «это кусок» видно в структуре. Так адрес остаётся разрешимым.
            structure["part_span"] = f"строки {offset + 1}-{offset + count} узла"
        chunk = DocChunk(
            chunk_id=util.stable_id("doc_chunk", doc_id, node.locator,
                                    part_index + 1),
            locator=locator, text=body, tokens=estimate_tokens(body),
            # Связь с родительским узлом заполняется ВСЕГДА, когда узел разрезан:
            # даже если адрес части совпадает с адресом узла (лист, слайд), читатель
            # обязан видеть, что это часть, а не самостоятельная единица. Пустое
            # поле здесь — не «нет связи», а «связь не записана».
            part_of=node.locator,
            structure=structure, warnings=list(node.extraction_warnings),
            **common,
        )
        chunk.start_line = start_line
        chunk.end_line = end_line
        out.append(chunk)
    return out


def chunk_nodes(nodes: list[DocumentNode], doc_id: str) -> list[DocChunk]:
    """Чанки документа: по порядку узлов, без склейки через границы видов."""
    out: list[DocChunk] = []
    for node in nodes:
        out.extend(chunk_node(node, doc_id))
    return out


def stats(chunks: list[DocChunk]) -> dict[str, Any]:
    """Сводка по бюджетам: числа для отчёта, а не впечатление."""
    if not chunks:
        return {"chunks": 0}
    tokens = sorted(chunk.tokens for chunk in chunks)
    tiny = sum(1 for chunk in chunks if chunk.tokens < 50)
    over = sum(1 for chunk in chunks if chunk.vector_partial)
    return {
        "chunks": len(chunks),
        "tokens_avg": round(sum(tokens) / len(tokens), 1),
        "tokens_p95": tokens[min(len(tokens) - 1, int(len(tokens) * 0.95))],
        "tokens_max": tokens[-1],
        "chunks_tiny": tiny,
        "chunks_vector_partial": over,
    }
