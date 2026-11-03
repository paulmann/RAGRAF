"""Читатель текстовых форматов: `.txt`, `.md`, `.rst`, `.adoc`, `.log`.

Что здесь решается, а не берётся готовым.
Разметка Markdown, reStructuredText и AsciiDoc различается, но вопрос у
документного слоя один: где граница раздела и какая цепочка заголовков ведёт к
абзацу. Поэтому разбор — не парсер языка разметки, а извлечение ДВУХ фактов:
уровень заголовка и его текст. Всё остальное (жирный, ссылки, код) остаётся в
тексте абзаца: чанк должен звучать так, как написано в документе, иначе поиск
начнёт находить разметку вместо содержания.

Адрес абзаца — `path:первая-последняя` строка ФАЙЛА. Это единственная форма,
которую можно разрешить обратно: `doc_get` читает те же строки того же файла.
Для `.log` адрес записи — тоже строки файла, но границы берутся по признаку
начала записи, а не по пустой строке: в журнале пустая строка часто часть
записи (трассировка стека).

Заголовков нет — это НЕ ошибка. Тогда абзацы получают `no_heading_reason` с
причиной: спецификация требует либо цепочку, либо названную причину, и пустая
цепочка без причины — ровно тот случай, когда проверка «структура сохранена»
ничего не проверяет.
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import textdec
from ..docnode import DocumentNode, ReadResult, make_line

#: Расширение → вид разметки.
STYLES: dict[str, str] = {
    ".md": "md",
    ".markdown": "md",
    ".rst": "rst",
    ".adoc": "adoc",
    ".asciidoc": "adoc",
    ".log": "log",
    ".txt": "plain",
    ".text": "plain",
}

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_ADOC_HEADING = re.compile(r"^(={1,6})\s+(.+?)\s*$")
#: Символы подчёркивания заголовков в reStructuredText.
_RST_UNDERLINE = re.compile(r"^([=\-~^\"'`:._*+#])\1{2,}\s*$")
#: Начало записи журнала: дата, время в скобках, уровень или имя процесса.
_LOG_START = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}[ T]|\d{2}:\d{2}:\d{2}|\[\d{4}-\d{2}-\d{2}|"
    r"[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}|[A-Z]{3,}\s|"
    r"\d{4}/\d{2}/\d{2}\s)"
)


def _style_of(rel: str) -> str:
    return STYLES.get(Path(rel).suffix.lower(), "plain")


def _rst_heading(lines: list[str], index: int) -> tuple[int, str] | None:
    """Заголовок RST: строка, подчёркнутая строкой из одинаковых символов.

    Уровень выводится из СИМВОЛА подчёркивания порядком первого появления, как
    это делает сам RST. Порядок, а не фиксированный словарь: в разных проектах
    `=` и `-` меняются местами, и «правильного» порядка не существует.
    """
    if index + 1 >= len(lines):
        return None
    title = lines[index].strip()
    under = lines[index + 1].strip()
    if not title or not under:
        return None
    if not _RST_UNDERLINE.match(under):
        return None
    if len(under) < max(3, len(title) // 2):
        return None
    return (len(title), title, under[0])  # type: ignore[return-value]


def _heading_at(lines: list[str], index: int, style: str,
                rst_order: list[str]) -> tuple[int, str] | None:
    """Заголовок в строке `index`: `(уровень, текст)` или None."""
    line = lines[index]
    if style == "md":
        match = _MD_HEADING.match(line)
        if match:
            return (len(match.group(1)), match.group(2).strip())
        # Setext: «Заголовок» и строка из `===` под ним.
        if (index + 1 < len(lines) and line.strip()
                and re.match(r"^={3,}\s*$", lines[index + 1])
                and len(line.strip()) <= 200):
            return (1, line.strip())
        return None
    if style == "adoc":
        match = _ADOC_HEADING.match(line)
        if match:
            return (len(match.group(1)), match.group(2).strip())
        return None
    if style == "rst":
        found = _rst_heading(lines, index)
        if found is None:
            return None
        level_size, title, symbol = found  # type: ignore[misc]
        if symbol not in rst_order:
            rst_order.append(symbol)
        return (rst_order.index(symbol) + 1, title)
    return None


def _flush_paragraph(nodes: list[DocumentNode], chain: list[str], rel: str,
                     buffer: list[str], first_line: int, last_line: int) -> None:
    if not buffer:
        return
    body = "\n".join(buffer).strip("\n")
    if not body.strip():
        return
    nodes.append(
        DocumentNode(
            kind="paragraph",
            locator=make_line(rel, first_line, last_line),
            text=body,
            structure={"heading_chain": list(chain)},
        )
    )
    buffer.clear()


def read(rel: str, raw: bytes) -> ReadResult:
    """Узлы текстового документа: разделы, абзацы, записи журнала."""
    style = _style_of(rel)
    text, encoding = textdec.decode(raw, rel)
    lines = text.splitlines()
    metadata = {"encoding": encoding, "lines": len(lines), "style": style}
    if not lines:
        return ReadResult(nodes=[], status="ok", reason="файл пуст",
                          reader="text", metadata=metadata)
    if style == "log":
        return ReadResult(nodes=_log_nodes(rel, lines), status="ok",
                          reader="text", metadata=metadata)

    nodes: list[DocumentNode] = []
    chain: list[str] = []
    stack: list[tuple[int, str]] = []
    buffer: list[str] = []
    first_line = 0
    last_line = 0
    rst_order: list[str] = []
    headings_found = 0

    def flush() -> None:
        nonlocal buffer, first_line, last_line
        _flush_paragraph(nodes, [t for _, t in stack], rel, buffer,
                         first_line, last_line)
        buffer = []

    for index, line in enumerate(lines):
        heading = _heading_at(lines, index, style, rst_order)
        # В RST текст заголовка лежит строкой выше подчёркивания.
        if heading is not None and style == "rst":
            heading_line = index + 1
        else:
            heading_line = index + 1
        if heading is not None:
            level, title = heading
            flush()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            headings_found += 1
            nodes.append(
                DocumentNode(
                    kind="section",
                    locator=make_line(rel, heading_line),
                    text=title,
                    structure={
                        "heading_chain": [t for _, t in stack],
                        "level": level,
                    },
                )
            )
            last_line = index + 1
            continue
        if not line.strip():
            flush()
            last_line = index + 1
            continue
        if not buffer:
            first_line = index + 1
        buffer.append(line)
        last_line = index + 1
    flush()

    if headings_found == 0:
        # Причина обязательна: без неё проверка «структура сохранена» на файле
        # без заголовков либо падает на верном документе, либо молчит.
        for node in nodes:
            if node.kind == "paragraph" and not node.heading_chain:
                node.structure["no_heading_reason"] = "в документе нет заголовков"
    else:
        # Абзацы до первого заголовка: цепочки нет, но причина конкретнее.
        for node in nodes:
            if node.kind == "paragraph" and not node.heading_chain:
                node.structure["no_heading_reason"] = "текст до первого заголовка"
    return ReadResult(nodes=nodes, status="ok", reader="text", metadata=metadata)


def _log_nodes(rel: str, lines: list[str]) -> list[DocumentNode]:
    """Записи журнала: границы по признаку начала записи, а не по пустой строке.

    Почему так. В журнале продолжение записи (трассировка, многострочное
    сообщение) не имеет ни даты, ни уровня. Разрезание по пустым строкам рвало
    бы одну запись на несколько чанков, и поиск находил бы обрывок без контекста.
    """
    nodes: list[DocumentNode] = []
    start = 0
    started = False
    for index, line in enumerate(lines):
        if _LOG_START.match(line.strip()) and started:
            nodes.append(_log_node(rel, lines, start, index))
            start = index
        elif not started:
            start = index
            started = True
    if started:
        nodes.append(_log_node(rel, lines, start, len(lines)))
    return nodes


def _log_node(rel: str, lines: list[str], start: int, end: int) -> DocumentNode:
    body = "\n".join(lines[start:end]).strip("\n")
    return DocumentNode(
        kind="record",
        locator=make_line(rel, start + 1, end),
        text=body,
        structure={"record_index": start, "no_heading_reason": "журнал без заголовков"},
    )
