"""Извлечение текста из документов: PDF, Word, Excel, PowerPoint, ODF, EPUB, почта.

Зачем отдельный модуль
    Индексатор читает код и текст байтами. Документы так читать нельзя: PDF и
    Office — контейнеры, и «текст» из них получается только разбором формата.
    Здесь один вход `extract(path)` и один выход `Extracted`, поэтому индексатор
    не знает, какой библиотекой получен текст.

Границы и честность
    * нет библиотеки — `status="unavailable"` с причиной и подсказкой, а не
      исключение: корпус индексируется дальше, документ помечается
      `metadata_only`;
    * нет OCR: сканы без текстового слоя дают пустой текст и причину, а не
      выдуманное содержимое;
    * старые двоичные `.doc/.xls/.ppt` разбираются собственным читателем
      контейнера OLE (`officebinary.py`) — без LibreOffice и без внешних
      библиотек. Если контейнер битый, это названо причиной;
    * файл больше лимита не читается вовсе;
    * сеть не используется ни на одном шаге.

Про `path:line` для документов
    Строки текста — это строки ИЗВЛЕЧЁННОГО текста, а не файла на диске.
    Чтобы адрес оставался осмысленным, в текст вставляются маркеры страницы,
    листа и слайда (`--- page 3 ---`). Тот же маркер видит и `live_read`, когда
    перечитывает документ, поэтому нумерация совпадает.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from pathlib import Path

# Пределы. Документы бывают большими, а индексация не должна упираться в
# таймаут из-за одного PDF на 900 страниц.
MAX_BYTES = 200 * 1024 * 1024
MAX_PDF_PAGES = 400
MAX_DOCX_TABLES = 200
MAX_SHEETS = 50
MAX_ROWS_PER_SHEET = 2000
MAX_SLIDES = 300

#: Word: современный OOXML во всех вариантах. python-docx открывает только
#: .docx, поэтому остальные читаются разбором zip+XML (word/document.xml) —
#: структура у них та же, отличается тип содержимого в [Content_Types].xml.
WORD_OOXML = (".docx", ".docm", ".dotx", ".dotm")
#: Excel: книга, макросы, шаблон. .xlsb сюда НЕ входит: это двоичный формат
#: внутри zip, и текстовый слой в нём не XML.
SHEET_OOXML = (".xlsx", ".xlsm", ".xltx", ".xltm")
#: PowerPoint: презентация, макросы, показ, шаблон.
SLIDE_OOXML = (".pptx", ".pptm", ".ppsx", ".potx")
#: OpenDocument: текст, таблицы, презентации, рисунок.
ODF = (".odt", ".ods", ".odp", ".odg")
#: Старые двоичные форматы Office 97-2003 и их аналоги в WPS Office.
LEGACY_WORD = (".doc", ".wps")
LEGACY_SHEET = (".xls", ".et")
LEGACY_SLIDE = (".ppt", ".dps")

SUPPORTED = (".pdf",) + WORD_OOXML + SHEET_OOXML + SLIDE_OOXML + ODF + (
    ".rtf", ".epub", ".eml", ".msg",
) + LEGACY_WORD + LEGACY_SHEET + LEGACY_SLIDE
LEGACY = LEGACY_WORD + LEGACY_SHEET + LEGACY_SLIDE

#: Форматы, которые известны, но разборщика не имеют. Причина — в тексте,
#: а не в молчании: вопрос «а .pages вы индексируете?» должен иметь ответ.
NO_PARSER: dict[str, str] = {
    ".pages": "Apple Pages: закрытый формат iWork (IWA), текстового слоя без macOS-разборщика нет",
    ".numbers": "Apple Numbers: закрытый формат iWork (IWA), текста без разборщика нет",
    ".key": "Apple Keynote: закрытый формат iWork (IWA), текста без разборщика нет",
    ".one": "Microsoft OneNote: двоичный формат, разборщика нет",
    ".chm": "CHM: сжатый ITS-контейнер, разборщика нет",
    ".djvu": "DjVu: скан без текстового слоя, нужен OCR — он не выполняется",
    ".xlsb": "Excel .xlsb: двоичные книги внутри zip, текстового слоя XML нет",
}

# Маркеры страницы/листа/слайда — служебные строки. Они нужны для адреса
# `path:line`, но «текстом документа» не являются: пустой скан не должен
# выглядеть как документ с содержимым.
_MARKER_RE = re.compile(r"^---.*---$", re.MULTILINE)


def _has_body(text: str) -> bool:
    return bool(_MARKER_RE.sub("", text).strip())


@dataclass
class Extracted:
    status: str                 # ok | unavailable | error
    kind: str                   # pdf | docx | xlsx | pptx | eml | legacy | unknown
    text: str = ""
    reason: str = ""
    pages: int = 0
    units: int = 0

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "kind": self.kind,
            "chars": len(self.text),
            "lines": len(self.text.splitlines()),
            "reason": self.reason,
            "pages": self.pages,
            "units": self.units,
        }


def available() -> dict[str, bool]:
    """Что реально доступно в этом окружении. Проверка импортом, не памятью."""
    out: dict[str, bool] = {}
    for module, key in (("pypdf", "pdf"), ("docx", "docx"),
                        ("openpyxl", "xlsx"), ("pptx", "pptx"),
                        ("extract_msg", "msg_extract")):
        try:
            __import__(module)
            out[key] = True
        except Exception:  # noqa: BLE001 — отсутствие библиотеки штатно
            out[key] = False
    # Эти пути не зависят от установленных пакетов: разбор написан здесь.
    # Поэтому True — это факт, а не надежда.
    out["eml"] = True
    out["ooxml"] = True       # docm/dotx/xlsm/pptm/ppsx — zip+XML
    out["odf"] = True
    out["epub"] = True
    out["msg"] = True         # OLE-свойства через officebinary
    out["legacy_doc"] = True  # Word 6/95/97-2003
    out["legacy_xls"] = True  # BIFF8
    out["legacy_ppt"] = True  # записи PowerPoint
    return out


def extract(path: str | Path) -> Extracted:
    """Извлечь текст документа. Никогда не бросает: причина в `status`."""
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    try:
        size = file_path.stat().st_size
    except OSError as exc:
        return Extracted("error", "unknown", reason=f"файл недоступен: {exc}")
    if size > MAX_BYTES:
        return Extracted(
            "unavailable", suffix.lstrip(".") or "unknown",
            reason=f"файл больше {MAX_BYTES // (1024 * 1024)} МБ: не читается",
        )
    if suffix in NO_PARSER:
        return Extracted("unavailable", suffix.lstrip(".") or "unknown",
                         reason=NO_PARSER[suffix])
    if suffix == ".pdf":
        return _pdf(file_path)
    if suffix in WORD_OOXML:
        return _docx(file_path)
    if suffix in SHEET_OOXML:
        return _xlsx(file_path)
    if suffix in SLIDE_OOXML:
        return _pptx(file_path)
    if suffix in LEGACY_WORD:
        return _legacy_word(file_path)
    if suffix in LEGACY_SHEET:
        return _legacy_sheet(file_path)
    if suffix in LEGACY_SLIDE:
        return _legacy_slide(file_path)
    if suffix in ODF:
        return _odf(file_path)
    if suffix == ".epub":
        return _epub(file_path)
    if suffix == ".eml":
        return _eml(file_path)
    if suffix == ".rtf":
        return _rtf(file_path)
    if suffix == ".msg":
        return _msg(file_path)
    return Extracted("unavailable", "unknown", reason=f"формат не поддержан: {suffix}")


def _pdf(path: Path) -> Extracted:
    try:
        from pypdf import PdfReader
    except Exception:  # noqa: BLE001
        return Extracted("unavailable", "pdf",
                         reason="нет библиотеки pypdf: установите `pip install pypdf`")
    try:
        reader = PdfReader(str(path))
        total = len(reader.pages)
        limit = min(total, MAX_PDF_PAGES)
        parts: list[str] = []
        for index in range(limit):
            try:
                page_text = reader.pages[index].extract_text() or ""
            except Exception as exc:  # noqa: BLE001 — одна страница не роняет документ
                page_text = ""
                if index == 0:
                    parts.append(f"[страница {index + 1} не прочитана: {type(exc).__name__}]")
            parts.append(f"\n--- page {index + 1} ---\n{page_text.strip()}\n")
        text = "".join(parts).strip()
        reason = ""
        if total > limit:
            reason = f"страниц {total}, прочитано {limit}: остальные за пределом"
        if not _has_body(text):
            reason = (reason + "; " if reason else "") + (
                "текстового слоя нет: скан без OCR не индексируется"
            )
        return Extracted("ok", "pdf", text=text, reason=reason, pages=total, units=limit)
    except Exception as exc:  # noqa: BLE001
        return Extracted("error", "pdf", reason=f"разбор PDF: {type(exc).__name__}: {exc}")


def _docx(path: Path) -> Extracted:
    try:
        import docx
    except Exception:  # noqa: BLE001
        # Библиотеки нет — это не отказ: собственный разбор zip+XML даёт текст
        # из любого варианта OOXML, включая .docm и .dotx.
        return _ooxml_word(path, path.suffix.lstrip(".") or "docx")
    if path.suffix.lower() != ".docx":
        # python-docx рассчитан на тип содержимого «документ»: .docm и шаблоны
        # он отвергает. Разбираем сами, а не сообщаем «формат не поддержан».
        return _ooxml_word(path, path.suffix.lstrip("."))
    try:
        document = docx.Document(str(path))
        parts: list[str] = [f"--- document: {path.name} ---"]
        for paragraph in document.paragraphs:
            line = paragraph.text.strip()
            if line:
                parts.append(line)
        tables = 0
        for table in document.tables:
            if tables >= MAX_DOCX_TABLES:
                break
            tables += 1
            parts.append(f"--- table {tables} ---")
            for row in table.rows:
                cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                parts.append(" | ".join(cells))
        text = "\n".join(parts).strip()
        reason = "" if _has_body(text) else "документ не содержит текста"
        return Extracted("ok", "docx", text=text, reason=reason,
                         units=len(document.paragraphs))
    except Exception as exc:  # noqa: BLE001
        return Extracted("error", "docx", reason=f"разбор DOCX: {type(exc).__name__}: {exc}")


def _xlsx(path: Path) -> Extracted:
    try:
        import openpyxl
    except Exception:  # noqa: BLE001
        return Extracted("unavailable", "xlsx",
                         reason="нет библиотеки openpyxl: установите `pip install openpyxl`")
    try:
        book = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        parts: list[str] = []
        sheets = 0
        for sheet in book.worksheets:
            if sheets >= MAX_SHEETS:
                break
            sheets += 1
            parts.append(f"--- sheet: {sheet.title} ---")
            rows = 0
            for row in sheet.iter_rows(values_only=True):
                rows += 1
                if rows > MAX_ROWS_PER_SHEET:
                    parts.append(f"[лист обрезан: показано {MAX_ROWS_PER_SHEET} строк]")
                    break
                cells = ["" if value is None else str(value) for value in row]
                line = "\t".join(cells).rstrip()
                if line.strip():
                    parts.append(line)
        book.close()
        text = "\n".join(parts).strip()
        reason = "" if _has_body(text) else "книга не содержит значений"
        return Extracted("ok", "xlsx", text=text, reason=reason,
                         pages=len(book.worksheets), units=sheets)
    except Exception as exc:  # noqa: BLE001
        return Extracted("error", "xlsx", reason=f"разбор XLSX: {type(exc).__name__}: {exc}")


def _pptx(path: Path) -> Extracted:
    try:
        from pptx import Presentation
    except Exception:  # noqa: BLE001
        return Extracted("unavailable", "pptx",
                         reason="нет библиотеки python-pptx: установите `pip install python-pptx`")
    try:
        deck = Presentation(str(path))
        parts: list[str] = []
        slides = 0
        for slide in deck.slides:
            if slides >= MAX_SLIDES:
                break
            slides += 1
            parts.append(f"--- slide {slides} ---")
            for shape in slide.shapes:
                if getattr(shape, "has_text_frame", False):
                    for paragraph in shape.text_frame.paragraphs:
                        line = "".join(run.text for run in paragraph.runs).strip()
                        if line:
                            parts.append(line)
            notes = getattr(slide, "notes_slide", None)
            if notes is not None and getattr(notes, "notes_text_frame", None) is not None:
                note_text = notes.notes_text_frame.text.strip()
                if note_text:
                    parts.append(f"[заметки] {note_text}")
        text = "\n".join(parts).strip()
        reason = "" if _has_body(text) else "презентация не содержит текста"
        return Extracted("ok", "pptx", text=text, reason=reason,
                         pages=len(deck.slides), units=slides)
    except Exception as exc:  # noqa: BLE001
        return Extracted("error", "pptx", reason=f"разбор PPTX: {type(exc).__name__}: {exc}")


def _odt(path: Path) -> Extracted:
    """ODT — это zip с `content.xml`; текст извлекается без внешних библиотек."""
    import zipfile

    try:
        with zipfile.ZipFile(path) as archive:
            raw = archive.read("content.xml")
        markup = raw.decode("utf-8", errors="replace")
        # Структурные элементы превращаются в переводы строк/табуляции ДО
        # удаления тегов: иначе абзацы слипнутся в одну строку.
        markup = re.sub(r"<text:line-break\s*/>", "\n", markup)
        markup = re.sub(r"<text:tab\s*/>", "\t", markup)
        markup = re.sub(r"</text:p\s*>", "\n", markup)
        markup = re.sub(r"</text:h\s*>", "\n", markup)
        text = re.sub(r"<[^>]+>", "", markup)
        text = html.unescape(text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        body = f"--- document: {path.name} ---\n{text}" if text else ""
        return Extracted("ok", "odt", text=body.strip(),
                         reason="" if _has_body(body) else "документ не содержит текста")
    except Exception as exc:  # noqa: BLE001
        return Extracted("error", "odt", reason=f"разбор ODT: {type(exc).__name__}: {exc}")


def _rtf(path: Path) -> Extracted:
    """RTF: приблизительное извлечение — управляющие слова снимаются.

    Это не полноценный разбор RTF: таблицы и вложенные объекты теряются.
    Причина названа в `reason`, чтобы грубый текст не выдавался за точный.
    """
    try:
        raw = path.read_bytes().decode("latin-1", errors="replace")
        text = re.sub(r"\{\\\*[^{}]*\}", "", raw)
        text = text.replace("\\par", "\n").replace("\\line", "\n").replace("\\tab", "\t")

        def hex_char(match: re.Match) -> str:
            try:
                return bytes([int(match.group(1), 16)]).decode("cp1251", errors="replace")
            except ValueError:
                return ""

        text = re.sub(r"\\'([0-9a-fA-F]{2})", hex_char, text)
        text = text.replace("\\\\", "\\").replace("\\{", "{").replace("\\}", "}")
        text = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", text)
        text = text.replace("{", "").replace("}", "")
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        body = f"--- document: {path.name} ---\n{text}" if text else ""
        reason = "приблизительно: управляющие слова RTF удалены, таблицы не разбираются"
        if not _has_body(body):
            reason = "документ не содержит текста"
        return Extracted("ok", "rtf", text=body.strip(), reason=reason)
    except Exception as exc:  # noqa: BLE001
        return Extracted("error", "rtf", reason=f"разбор RTF: {type(exc).__name__}: {exc}")


def _msg(path: Path) -> Extracted:
    """MSG (Outlook): собственный разбор OLE, библиотека — как запасной путь.

    Порядок именно такой. `extract-msg` на этой машине не установлен, а
    письма .msg — обычный формат в рабочих каталогах. Поэтому сначала
    читаются свойства прямо из контейнера (тема, отправитель, тело), и только
    если это не удалось — используется библиотека, когда она есть.
    """
    from . import officebinary as off

    try:
        raw = path.read_bytes()
        result = off.msg_text(raw)
        text = f"--- email: {path.name} ---\n{result.text}".strip()
        if _has_body(result.text):
            return Extracted("ok", "msg", text=text, reason=result.note)
        reason = result.note or "письмо не содержит текста"
    except off.OfficeError as exc:
        reason = f"контейнер письма не разобран: {exc}"
    except Exception as exc:  # noqa: BLE001 — контейнер может быть любым
        reason = f"разбор MSG: {type(exc).__name__}: {exc}"
    try:
        import extract_msg
    except Exception:  # noqa: BLE001
        return Extracted("unavailable", "msg",
                         reason=reason + "; библиотека extract-msg не установлена")
    try:
        message = extract_msg.Message(str(path))
        try:
            parts = [f"--- email: {path.name} ---"]
            for label, value in (("Subject", getattr(message, "subject", "")),
                                 ("From", getattr(message, "sender", "")),
                                 ("Date", getattr(message, "date", ""))):
                if value:
                    parts.append(f"{label}: {value}")
            body = str(getattr(message, "body", "") or "").strip()
            if body:
                parts.append(body)
            attachments = [str(getattr(item, "longFilename", "") or getattr(item, "shortFilename", "") or "")
                           for item in getattr(message, "attachments", [])]
            attachments = [name for name in attachments if name]
            if attachments:
                parts.append("--- attachments ---")
                parts.extend(attachments)
            text = "\n".join(parts).strip()
            return Extracted("ok", "msg", text=text,
                             reason="" if _has_body(text) else "письмо не содержит текста",
                             units=len(attachments) + 1)
        finally:
            close = getattr(message, "close", None)
            if callable(close):
                close()
    except Exception as exc:  # noqa: BLE001
        return Extracted("error", "msg", reason=f"разбор MSG: {type(exc).__name__}: {exc}")


def _html_to_text(raw: str) -> str:
    """Грубое, но детерминированное превращение HTML в текст (без сети)."""
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return re.sub(r"[ \t]+", " ", html.unescape(raw)).strip()


def _eml(path: Path) -> Extracted:
    try:
        raw = path.read_bytes()
        message = BytesParser(policy=policy.default).parsebytes(raw)
        parts: list[str] = [f"--- email: {path.name} ---"]
        for header in ("Subject", "From", "To", "Cc", "Date"):
            value = message.get(header)
            if value:
                parts.append(f"{header}: {value}")
        body = ""
        attachments: list[str] = []
        if message.is_multipart():
            for part in message.walk():
                disposition = (part.get("Content-Disposition") or "").lower()
                if "attachment" in disposition:
                    attachments.append(part.get_filename() or "(без имени)")
                    continue
                if part.get_content_type() == "text/plain":
                    body += part.get_content() + "\n"
                elif part.get_content_type() == "text/html" and not body:
                    body += _html_to_text(part.get_content()) + "\n"
        else:
            content = message.get_content()
            body = (_html_to_text(content)
                    if message.get_content_type() == "text/html" else content)
        if body.strip():
            parts.append(body.strip())
        if attachments:
            # Только имена: содержимое вложений не читается.
            parts.append("--- attachments ---")
            parts.extend(attachments)
        text = "\n".join(parts).strip()
        return Extracted("ok", "eml", text=text, reason="" if _has_body(text) else "письмо не содержит текста",
                         units=len(attachments) + 1)
    except Exception as exc:  # noqa: BLE001
        return Extracted("error", "eml", reason=f"разбор EML: {type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
# Word / Excel / PowerPoint в формате OOXML
#
# Зачем свой разбор при наличии python-docx и openpyxl. Библиотеки открывают
# ТОЛЬКО базовые варианты: python-docx не откроет .docm и .dotx (другой тип
# содержимого в [Content_Types].xml), openpyxl — .xltm. А внутри это тот же
# zip с тем же XML, и текст из него достаётся одинаково. Поэтому базовый
# вариант читается библиотекой (она даёт таблицы и порядок), а остальные —
# разбором zip+XML без единой внешней зависимости.
# --------------------------------------------------------------------------
def _zip_names(path: Path) -> list[str]:
    import zipfile

    with zipfile.ZipFile(path) as archive:
        return archive.namelist()


def _read_xml(path: Path, member: str):
    import zipfile
    from xml.etree import ElementTree as ET

    with zipfile.ZipFile(path) as archive:
        return ET.fromstring(archive.read(member))


def _ooxml_word(path: Path, kind: str = "docx") -> Extracted:
    """Абзацы и таблицы из word/document.xml."""
    import zipfile

    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        with zipfile.ZipFile(path) as archive:
            candidates = [n for n in archive.namelist()
                          if n.startswith("word/document") and n.endswith(".xml")]
            if not candidates:
                return Extracted("unavailable", kind,
                                 reason="в контейнере нет word/document.xml")
            from xml.etree import ElementTree as ET

            root = ET.fromstring(archive.read(sorted(candidates)[0]))
    except Exception as exc:  # noqa: BLE001
        return Extracted("error", kind, reason=f"разбор OOXML: {type(exc).__name__}: {exc}")

    parts: list[str] = [f"--- document: {path.name} ---"]
    tables = 0

    def text_of(node) -> str:
        return "".join(t.text or "" for t in node.iter(f"{ns}t"))

    def walk(node, depth: int = 0) -> None:
        nonlocal tables
        if depth > 20:
            return
        for child in node:
            tag = child.tag
            if tag == f"{ns}p":
                line = text_of(child).strip()
                if line:
                    parts.append(line)
            elif tag == f"{ns}tbl":
                if tables >= MAX_DOCX_TABLES:
                    return
                tables += 1
                for row in child.findall(f"{ns}tr"):
                    cells = []
                    for cell in row.findall(f"{ns}tc"):
                        cells.append(" ".join(
                            text_of(p).strip() for p in cell.findall(f"{ns}p")
                        ).strip())
                    parts.append(" | ".join(cells))
            else:
                walk(child, depth + 1)

    walk(root)
    body = "\n".join(p for p in parts if p is not None).strip()
    reason = "" if _has_body(body) else "документ не содержит текста"
    return Extracted("ok", kind, text=body, reason=reason, units=tables)


def _ooxml_sheet(path: Path, kind: str = "xlsx") -> Extracted:
    """Листы из xl/worksheets/sheetN.xml с общей таблицей строк."""
    import zipfile

    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            shared: list[str] = []
            if "xl/sharedStrings.xml" in names:
                from xml.etree import ElementTree as ET

                root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                for item in root.iter(f"{ns}si"):
                    shared.append("".join(t.text or "" for t in item.iter(f"{ns}t")))
            sheets = sorted(
                (n for n in names
                 if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")),
                key=lambda n: (len(n), n),
            )
            if not sheets:
                return Extracted("unavailable", kind,
                                 reason="в контейнере нет листов xl/worksheets")
            from xml.etree import ElementTree as ET

            parts: list[str] = [f"--- document: {path.name} ---"]
            rows_total = 0
            for index, member in enumerate(sheets[:MAX_SHEETS], start=1):
                root = ET.fromstring(archive.read(member))
                parts.append(f"--- sheet {index} ---")
                stored = 0
                for row in root.iter(f"{ns}row"):
                    values: list[str] = []
                    for cell in row.findall(f"{ns}c"):
                        kind_attr = cell.get("t")
                        if kind_attr == "s":
                            raw = cell.findtext(f"{ns}v")
                            try:
                                values.append(shared[int(raw or "-1")])
                            except (ValueError, IndexError):
                                continue
                        elif kind_attr == "inlineStr":
                            values.append("".join(
                                t.text or "" for t in cell.iter(f"{ns}t")
                            ))
                        else:
                            raw = cell.findtext(f"{ns}v")
                            if raw:
                                values.append(raw)
                    line = " | ".join(v.strip() for v in values if v and v.strip())
                    if line:
                        parts.append(line)
                        stored += 1
                        rows_total += 1
                        if rows_total >= MAX_ROWS_PER_SHEET * MAX_SHEETS:
                            break
                if stored == 0:
                    parts.append("[лист без значений]")
    except Exception as exc:  # noqa: BLE001
        return Extracted("error", kind, reason=f"разбор OOXML: {type(exc).__name__}: {exc}")
    body = "\n".join(parts).strip()
    reason = "" if _has_body(body) else "книга не содержит значений"
    return Extracted("ok", kind, text=body, reason=reason, units=rows_total)


def _ooxml_slides(path: Path, kind: str = "pptx") -> Extracted:
    """Текст слайдов из ppt/slides/slideN.xml."""
    import zipfile

    ns = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    try:
        with zipfile.ZipFile(path) as archive:
            names = sorted(
                (n for n in archive.namelist()
                 if n.startswith("ppt/slides/slide") and n.endswith(".xml")),
                key=lambda n: (len(n), n),
            )
            if not names:
                return Extracted("unavailable", kind,
                                 reason="в контейнере нет слайдов ppt/slides")
            from xml.etree import ElementTree as ET

            parts: list[str] = [f"--- document: {path.name} ---"]
            for index, member in enumerate(names[:MAX_SLIDES], start=1):
                root = ET.fromstring(archive.read(member))
                lines = [(t.text or "").strip() for t in root.iter(f"{ns}t")]
                lines = [line for line in lines if line]
                parts.append(f"--- slide {index} ---")
                parts.extend(lines)
    except Exception as exc:  # noqa: BLE001
        return Extracted("error", kind, reason=f"разбор OOXML: {type(exc).__name__}: {exc}")
    body = "\n".join(parts).strip()
    reason = "" if _has_body(body) else "презентация не содержит текста"
    return Extracted("ok", kind, text=body, reason=reason, units=len(names))


def _odf(path: Path) -> Extracted:
    """OpenDocument любого вида: текст, таблицы, презентации, рисунок."""
    import zipfile

    try:
        with zipfile.ZipFile(path) as archive:
            raw = archive.read("content.xml")
        markup = raw.decode("utf-8", errors="replace")
        markup = re.sub(r"<text:line-break\s*/>", "\n", markup)
        markup = re.sub(r"<text:tab\s*/>", "\t", markup)
        markup = re.sub(r"</text:p\s*>", "\n", markup)
        markup = re.sub(r"</text:h\s*>", "\n", markup)
        markup = re.sub(r"</table:table-cell\s*>", " | ", markup)
        markup = re.sub(r"</table:table-row\s*>", "\n", markup)
        text = re.sub(r"<[^>]+>", "", markup)
        text = html.unescape(text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        body = f"--- document: {path.name} ---\n{text}" if text else ""
        return Extracted("ok", path.suffix.lstrip("."), text=body.strip(),
                         reason="" if _has_body(body) else "документ не содержит текста")
    except Exception as exc:  # noqa: BLE001
        return Extracted("error", "odf", reason=f"разбор ODF: {type(exc).__name__}: {exc}")


def _epub(path: Path) -> Extracted:
    """EPUB: контейнер → OPF → документы в порядке чтения (spine)."""
    import zipfile

    OPF = "{http://www.idpf.org/2007/opf}"
    try:
        with zipfile.ZipFile(path) as archive:
            container = archive.read("META-INF/container.xml").decode("utf-8", "replace")
            match = re.search(r'full-path="([^"]+)"', container)
            if not match:
                return Extracted("unavailable", "epub",
                                 reason="в контейнере нет пути к OPF")
            opf_path = match.group(1)
            from xml.etree import ElementTree as ET

            opf = ET.fromstring(archive.read(opf_path))
            base = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""
            manifest = {}
            for item in opf.iter(f"{OPF}item"):
                manifest[item.get("id")] = item.get("href") or ""
            order = [ref.get("idref") for ref in opf.iter(f"{OPF}itemref")]
            files = [manifest.get(ref or "") for ref in order]
            files = [f for f in files if f] or list(manifest.values())
            parts: list[str] = [f"--- document: {path.name} ---"]
            for member in files[:MAX_SLIDES]:
                full = base + member if not member.startswith("/") else member.lstrip("/")
                try:
                    markup = archive.read(full).decode("utf-8", "replace")
                except KeyError:
                    continue
                parts.append(_html_to_text(markup))
    except Exception as exc:  # noqa: BLE001
        return Extracted("error", "epub", reason=f"разбор EPUB: {type(exc).__name__}: {exc}")
    body = "\n\n".join(p for p in parts if p.strip()).strip()
    reason = "" if _has_body(body) else "книга не содержит текста"
    return Extracted("ok", "epub", text=body, reason=reason, units=len(files))


# --------------------------------------------------------------------------
# Старые двоичные форматы Office через собственный разборщик OLE
# --------------------------------------------------------------------------
def _legacy_word(path: Path) -> Extracted:
    from . import officebinary as off

    try:
        result = off.word_text(path.read_bytes())
    except off.OfficeError as exc:
        return Extracted("unavailable", "doc",
                         reason=f"старый Word не разобран: {exc}")
    except Exception as exc:  # noqa: BLE001
        return Extracted("error", "doc", reason=f"разбор .doc: {type(exc).__name__}: {exc}")
    body = f"--- document: {path.name} ---\n{result.text}".strip()
    reason = result.note if _has_body(result.text) else (
        result.note + "; текста не найдено"
    )
    return Extracted("ok", "doc", text=body, reason=reason)


def _legacy_sheet(path: Path) -> Extracted:
    from . import officebinary as off

    try:
        result = off.excel_text(path.read_bytes())
    except off.OfficeError as exc:
        return Extracted("unavailable", "xls",
                         reason=f"старая книга Excel не разобрана: {exc}")
    except Exception as exc:  # noqa: BLE001
        return Extracted("error", "xls", reason=f"разбор .xls: {type(exc).__name__}: {exc}")
    body = f"--- document: {path.name} ---\n{result.text}".strip()
    return Extracted("ok", "xls", text=body,
                     reason=result.note if _has_body(result.text)
                     else result.note + "; значений не найдено")


def _legacy_slide(path: Path) -> Extracted:
    from . import officebinary as off

    try:
        result = off.powerpoint_text(path.read_bytes())
    except off.OfficeError as exc:
        return Extracted("unavailable", "ppt",
                         reason=f"старая презентация не разобрана: {exc}")
    except Exception as exc:  # noqa: BLE001
        return Extracted("error", "ppt", reason=f"разбор .ppt: {type(exc).__name__}: {exc}")
    body = f"--- document: {path.name} ---\n{result.text}".strip()
    return Extracted("ok", "ppt", text=body,
                     reason=result.note if _has_body(result.text)
                     else result.note + "; текста не найдено")
