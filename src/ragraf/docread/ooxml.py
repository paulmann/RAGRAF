"""Читатель офисных форматов: DOCX, XLSX, PPTX.

Что здесь решается, а не берётся готовым.

1. **Границы узлов заданы спецификацией, а не удобством.** В DOCX основной текст,
   таблицы, сноски и колонтитулы — РАЗНЫЕ узлы. Причина не в аккуратности: смешать
   их значит потерять адрес. Абзац основного текста адресуется `path#para=12`,
   сноска — `path#footnote=3`, колонтитул — `path#header=1`. Пока это один узел,
   «где именно» ответить нельзя.

2. **Лист книги — сущность, а не страница.** Вся книга одним чанком не
   индексируется: у листа своё имя, свой диапазон, свой `table_id`. Заголовок
   колонок повторяется в каждой части разрезанного листа (`repeat_header`):
   значение без имени колонки — это число без смысла.

3. **Слайд ≠ заметки.** Заметки докладчика — отдельный узел с адресом
   `path:slide=N#notes`. Таблица на слайде — тоже отдельный узел
   (`path:slide=N#table=M`), иначе `doc_get` по адресу слайда склеил бы текст,
   заметки и таблицу.

4. **Отсутствие библиотеки не даёт пустоты.** Если `python-docx`/`openpyxl`/
   `python-pptx` нет, работает собственный разбор OOXML (`documents.py`), но
   структура при этом теряется — и это НАЗВАНО: статус `degraded`, причина в
   провенансе. Молча отдать «файл не проиндексирован» значило бы потерять текст,
   который в этом окружении извлекается.

Библиотека получает БАЙТЫ (`BytesIO`), а не путь: иначе читатель начнёт читать
файл сам, и «какой текст увидел поиск» перестанет быть одним и тем же.

API проверены вызовом 29.10.2026 на фикстурах, а не по памяти о версиях:
`docx.Document(stream).paragraphs[i].style.name`, `openpyxl.load_workbook(stream,
data_only=True)[name].iter_rows(values_only=True)`, `Presentation(stream).slides[i]`
с `has_notes_slide` / `shapes[i].has_table`.
"""

from __future__ import annotations

import io
import re
import tempfile
import zipfile
from pathlib import Path

from ..docnode import DocumentNode, ReadResult

#: Заголовок в DOCX: имя стиля «Heading 1» или «Заголовок 1», либо style_id
#: «Heading1». Три варианта, потому что документ мог быть создан в русской
#: локали, а стиль переименован вручную — и тогда ни имя, ни id не совпадают
#: между собой, но `outlineLvl` остаётся. Опираться на один признак мало.
_HEADING_NAME = re.compile(r"^(?:Heading|Заголовок|Titre|Überschrift)\s*(\d)$", re.I)
_HEADING_ID = re.compile(r"^Heading(\d)$", re.I)

#: Имена стилей, которые спецификация велит НЕ мешать с основным текстом.
_NOT_BODY = re.compile(r"^(?:Caption|Title|TOC|Содержание|Оглавление)", re.I)

MAX_SHEET_ROWS = 5000


def _docx_outline_level(paragraph) -> int:
    """Уровень плана абзаца (`w:outlineLvl`) или 0.

    Замер 29.10.2026: 3 из 12 DOCX массового корпуса размечены ЗАГОЛОВКАМИ через
    `w:outlineLvl` при собственных именах стилей. Без этой проверки читатель
    находил заголовки у 0 документов, и весь текст попадал в «вне разделов».
    Значение 9 в OOXML означает «обычный текст» — оно заголовком не считается.
    """
    element = getattr(paragraph, "_p", None)
    properties = getattr(element, "pPr", None) if element is not None else None
    if properties is None:
        return 0
    for child in properties:
        tag = str(getattr(child, "tag", ""))
        if not tag.endswith("}outlineLvl"):
            continue
        raw = str(child.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val")
                  or child.get("val") or "").strip()
        if not raw.isdigit():
            return 0
        value = int(raw)
        return value + 1 if value < 9 else 0
    return 0


def _docx_heading_level(paragraph) -> int:
    """Уровень заголовка абзаца или 0.

    Порядок проверок: имя стиля, затем style_id, затем уровень плана
    (`w:outlineLvl`), затем `base_style` (стиль мог быть создан «на основе
    Heading 1» и назван по-своему).
    """
    style = getattr(paragraph, "style", None)
    seen = 0
    while style is not None and seen < 4:
        name = str(getattr(style, "name", "") or "")
        match = _HEADING_NAME.match(name.strip())
        if match:
            return int(match.group(1))
        style_id = str(getattr(style, "style_id", "") or "")
        match = _HEADING_ID.match(style_id.strip())
        if match:
            return int(match.group(1))
        outline = _docx_outline_level(paragraph)
        if outline:
            return outline
        style = getattr(style, "base_style", None)
        seen += 1
    return 0


def _clean(value: str) -> str:
    return re.sub(r"[ \t\u00a0]+", " ", str(value or "")).strip()


def _table_text(table) -> tuple[str, int]:
    """Текст таблицы построчно и число строк."""
    rows: list[str] = []
    for row in table.rows:
        cells = [_clean(cell.text).replace("\n", " ") for cell in row.cells]
        if any(cells):
            rows.append(" | ".join(cells))
    return ("\n".join(rows), len(rows))


def _footnotes(raw: bytes) -> list[tuple[str, str]]:
    """Сноски из `word/footnotes.xml`: список `(номер, текст)`.

    python-docx сносок не отдаёт вовсе, поэтому они читаются из самого пакета.
    Служебные разделители (`separator`, `continuationSeparator`) пропускаются:
    это не сноски, а линии оформления.
    """
    try:
        from lxml import etree
    except Exception:  # noqa: BLE001 — нет lxml: сноски не извлекаются, и это названо
        return []
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = archive.namelist()
            if "word/footnotes.xml" not in names:
                return []
            data = archive.read("word/footnotes.xml")
    except (zipfile.BadZipFile, KeyError, OSError):
        return []
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError:
        return []
    out: list[tuple[str, str]] = []
    for note in root.iter(f"{ns}footnote"):
        kind = note.get(f"{ns}type") or ""
        if kind in ("separator", "continuationSeparator", "continuationNotice"):
            continue
        number = note.get(f"{ns}id") or ""
        text = "".join(node.text or "" for node in note.iter(f"{ns}t"))
        text = _clean(text)
        if text and number:
            out.append((number, text))
    return out


def _fallback(rel: str, raw: bytes, reason: str) -> ReadResult:
    """Собственный разбор OOXML без библиотеки: текст есть, структуры нет.

    Библиотеки нет — это не повод отдавать пустой документ: текст в этом
    окружении извлекается своим разбором. Но структура теряется, поэтому статус
    `degraded` и причина названы, а не выданы за успех.
    """
    from .. import documents

    suffix = Path(rel).suffix.lower()
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        handle.write(raw)
        handle.close()
        doc = documents.extract(Path(handle.name))
    finally:
        try:
            Path(handle.name).unlink()
        except OSError:
            pass
    if doc.status != "ok" or not doc.text.strip():
        return ReadResult(
            status="failed" if doc.status == "error" else "degraded",
            reason=f"{reason}; собственный разбор: {doc.reason or 'текста нет'}",
            reader="ooxml",
        )
    return ReadResult(
        nodes=[DocumentNode(
            kind="document",
            locator=rel,
            text=doc.text,
            structure={"no_heading_reason": "структура недоступна: нет офисной библиотеки"},
            metadata={"ext": suffix},
            provenance={"reader": "ooxml", "converter": "builtin:ooxml"},
        )],
        status="degraded",
        reason=reason,
        reader="ooxml",
        converter="builtin:ooxml",
        warnings=[reason],
    )


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------


def read_docx(rel: str, raw: bytes) -> ReadResult:
    if raw[:8].startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        # Старый OLE-документ с расширением `.docx`: разбираем как `.doc`.
        from . import legacy

        result = legacy.read(rel.replace(".docx", ".doc") if rel.endswith(".docx") else rel,
                             raw)
        if result.nodes:
            result.reason = (result.reason or "") + "; содержимое OLE, расширение .docx"
            result.metadata = {**(result.metadata or {}), "sniffed": "ole-in-docx"}
        return result
    try:
        import docx
    except Exception as exc:  # noqa: BLE001
        return _fallback(rel, raw,
                         f"нет библиотеки python-docx: структура DOCX не восстановлена "
                         f"({type(exc).__name__})")
    try:
        document = docx.Document(io.BytesIO(raw))
    except Exception as exc:  # noqa: BLE001
        return ReadResult(status="failed",
                          reason=f"разбор DOCX: {type(exc).__name__}: {exc}",
                          reader="docx")

    nodes: list[DocumentNode] = []
    stack: list[tuple[int, str]] = []
    warnings: list[str] = []
    tables = 0

    for index, paragraph in enumerate(document.paragraphs):
        text = _clean(paragraph.text)
        level = _docx_heading_level(paragraph)
        if level:
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, text or f"Раздел {index + 1}"))
            nodes.append(DocumentNode(
                kind="section",
                locator=f"{rel}#para={index}",
                text=text or f"Раздел {index + 1}",
                structure={"heading_chain": [title for _, title in stack],
                           "level": level, "style": str(paragraph.style.name)},
            ))
            continue
        if not text:
            continue
        style_name = str(getattr(paragraph.style, "name", "") or "")
        if _NOT_BODY.match(style_name):
            # Подпись/название — отдельный вид, чтобы не выдавать их за абзац
            # основного текста: у них своя роль в документе.
            nodes.append(DocumentNode(
                kind="clause",
                locator=f"{rel}#para={index}",
                text=text,
                structure={"heading_chain": [title for _, title in stack],
                           "style": style_name, "role": "caption"},
            ))
            continue
        nodes.append(DocumentNode(
            kind="paragraph",
            locator=f"{rel}#para={index}",
            text=text,
            structure={"heading_chain": [title for _, title in stack],
                       "style": style_name} if stack else
                      {"heading_chain": [], "no_heading_reason": "текст до первого заголовка",
                       "style": style_name},
        ))

    for table in document.tables:
        tables += 1
        text, rows = _table_text(table)
        if not text.strip():
            continue
        nodes.append(DocumentNode(
            kind="table",
            locator=f"{rel}:table={tables}",
            text=text,
            structure={"table_id": f"docx:t{tables}", "rows": rows,
                       "heading_chain": [title for _, title in stack]},
        ))

    for number, text in _footnotes(raw):
        nodes.append(DocumentNode(
            kind="clause",
            locator=f"{rel}#footnote={number}",
            text=text,
            structure={"heading_chain": [], "no_heading_reason": "сноска вне разделов",
                       "role": "footnote"},
        ))

    try:
        sections = list(document.sections)
    except Exception:  # noqa: BLE001
        sections = []
    for position, section in enumerate(sections, start=1):
        for kind, part in (("header", getattr(section, "header", None)),
                           ("footer", getattr(section, "footer", None))):
            if part is None:
                continue
            text = _clean(" ".join(p.text for p in part.paragraphs))
            if not text:
                continue
            nodes.append(DocumentNode(
                kind="clause",
                locator=f"{rel}#{kind}={position}",
                text=text,
                structure={"heading_chain": [], "role": kind,
                           "no_heading_reason": f"{kind}: колонтитул вне разделов"},
            ))
    if not sections:
        warnings.append("колонтитулы не прочитаны: разделы документа недоступны")

    if not nodes:
        return ReadResult(status="degraded",
                          reason="в документе нет текста после разбора",
                          reader="docx", warnings=warnings)
    return ReadResult(nodes=nodes, status="ok", reader="docx",
                      metadata={"paragraphs": len(document.paragraphs),
                                "tables": tables, "sections": len(sections)},
                      warnings=warnings)


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------


def read_xlsx(rel: str, raw: bytes) -> ReadResult:
    if raw[:8].startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        from . import legacy

        result = legacy.read(rel.replace(".xlsx", ".xls"), raw)
        if result.nodes:
            result.reason = (result.reason or "") + "; содержимое OLE, расширение .xlsx"
            result.metadata = {**(result.metadata or {}), "sniffed": "ole-in-xlsx"}
        return result
    try:
        import openpyxl
    except Exception as exc:  # noqa: BLE001
        return _fallback(rel, raw,
                         f"нет библиотеки openpyxl: листы не разделены "
                         f"({type(exc).__name__})")
    try:
        book = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
    except Exception as exc:  # noqa: BLE001
        return ReadResult(status="failed",
                          reason=f"разбор XLSX: {type(exc).__name__}: {exc}",
                          reader="xlsx")

    nodes: list[DocumentNode] = []
    warnings: list[str] = []
    try:
        names = list(book.sheetnames)
    except Exception:  # noqa: BLE001
        names = []
    for name in names:
        try:
            sheet = book[name]
            rows: list[str] = []
            header = ""
            for offset, row in enumerate(sheet.iter_rows(values_only=True)):
                if offset >= MAX_SHEET_ROWS:
                    warnings.append(
                        f"лист «{name}»: прочитано {MAX_SHEET_ROWS} строк, остальные "
                        f"за пределом"
                    )
                    break
                cells = ["" if value is None else str(value).strip() for value in row]
                if not any(cells):
                    continue
                line = " | ".join(cells)
                if not header:
                    header = line
                rows.append(line)
        except Exception as exc:  # noqa: BLE001 — один лист не роняет книгу
            warnings.append(f"лист «{name}» не прочитан: {type(exc).__name__}")
            continue
        if not rows:
            continue
        nodes.append(DocumentNode(
            kind="sheet",
            locator=f"{rel}:sheet={name}",
            text="\n".join(rows),
            structure={
                "table_id": f"sheet:{name}",
                "sheet": name,
                "rows": len(rows),
                "columns": header,
                # Заголовок колонок повторяется в каждой части разрезанного
                # листа: значение без имени колонки — число без смысла.
                "repeat_header": header,
            },
        ))
    try:
        book.close()
    except Exception:  # noqa: BLE001
        pass
    if not nodes:
        return ReadResult(status="degraded", reason="в книге нет непустых листов",
                          reader="xlsx", warnings=warnings)
    return ReadResult(nodes=nodes, status="ok", reader="xlsx",
                      metadata={"sheets": len(names)}, warnings=warnings)


# ---------------------------------------------------------------------------
# PPTX
# ---------------------------------------------------------------------------


def _shape_table(shape) -> tuple[str, int]:
    rows: list[str] = []
    table = shape.table
    for row in table.rows:
        cells = [_clean(cell.text).replace("\n", " ") for cell in row.cells]
        if any(cells):
            rows.append(" | ".join(cells))
    return ("\n".join(rows), len(rows))


def read_pptx(rel: str, raw: bytes) -> ReadResult:
    try:
        from pptx import Presentation
    except Exception as exc:  # noqa: BLE001
        return _fallback(rel, raw,
                         f"нет библиотеки python-pptx: слайды не разделены "
                         f"({type(exc).__name__})")
    try:
        deck = Presentation(io.BytesIO(raw))
    except Exception as exc:  # noqa: BLE001
        return ReadResult(status="failed",
                          reason=f"разбор PPTX: {type(exc).__name__}: {exc}",
                          reader="pptx")

    nodes: list[DocumentNode] = []
    warnings: list[str] = []
    body_parts = 0
    notes_parts = 0
    for position, slide in enumerate(deck.slides, start=1):
        title = ""
        body: list[str] = []
        tables: list[tuple[int, str, int]] = []
        table_no = 0
        for shape in slide.shapes:
            if getattr(shape, "has_table", False):
                text, rows = _shape_table(shape)
                if text.strip():
                    table_no += 1
                    tables.append((table_no, text, rows))
                continue
            if not getattr(shape, "has_text_frame", False):
                continue
            text = _clean(shape.text_frame.text)
            if not text:
                continue
            if slide.shapes.title is not None and shape.shape_id == slide.shapes.title.shape_id:
                title = text
                continue
            body.append(text)
        chain = [title] if title else []
        slide_text = "\n".join(body)
        if title or slide_text:
            nodes.append(DocumentNode(
                kind="slide",
                locator=f"{rel}:slide={position}",
                text="\n".join(part for part in (title, slide_text) if part),
                structure={"heading_chain": chain, "slide": position,
                           "tables": len(tables)},
            ))
            body_parts += 1
        for number, text, rows in tables:
            nodes.append(DocumentNode(
                kind="table",
                locator=f"{rel}:slide={position}#table={number}",
                text=text,
                structure={"table_id": f"slide{position}:t{number}", "rows": rows,
                           "heading_chain": chain},
            ))
        # Заметки — ОТДЕЛЬНЫЙ узел и отдельный адрес. Смешать их со слайдом
        # значило бы потерять и то, что сказано на слайде, и то, что сказано вслух.
        notes = ""
        try:
            if slide.has_notes_slide:
                notes = _clean(slide.notes_slide.notes_text_frame.text)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"слайд {position}: заметки не прочитаны "
                            f"({type(exc).__name__})")
        if notes:
            nodes.append(DocumentNode(
                kind="paragraph",
                locator=f"{rel}:slide={position}#notes",
                text=notes,
                structure={"heading_chain": chain, "from_notes": True,
                           "slide": position},
            ))
            notes_parts += 1
    if not nodes:
        return ReadResult(status="degraded", reason="в презентации нет текста",
                          reader="pptx", warnings=warnings)
    return ReadResult(nodes=nodes, status="ok", reader="pptx",
                      metadata={"slides": body_parts, "notes": notes_parts},
                      warnings=warnings)


READER_BY_SUFFIX = {
    ".docx": read_docx,
    ".docm": read_docx,
    ".dotx": read_docx,
    ".dotm": read_docx,
    ".xlsx": read_xlsx,
    ".xlsm": read_xlsx,
    ".xltx": read_xlsx,
    ".xltm": read_xlsx,
    ".pptx": read_pptx,
    ".pptm": read_pptx,
    ".ppsx": read_pptx,
    ".potx": read_pptx,
}


def read(rel: str, raw: bytes) -> ReadResult:
    func = READER_BY_SUFFIX.get(Path(rel).suffix.lower())
    if func is None:
        return ReadResult(status="failed",
                          reason=f"ooxml-читатель не знает формат: {rel}",
                          reader="ooxml")
    return func(rel, raw)
