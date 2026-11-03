"""Читатель устаревших и «текстовых» офисных форматов: `.doc`, `.xls`, `.ppt`,
`.rtf`, `.odt`, `.ods`, `.odp`.

Почему они здесь, а не рядом с DOCX/XLSX/PPTX.

У этих форматов нет надёжного разделения на узлы в этой сборке:
  * `.doc`/`.xls`/`.ppt` — двоичные контейнеры OLE; собственный разбор
    (`officebinary`) извлекает ТЕКСТ и маркеры страниц, но не стили заголовков,
    поэтому «раздел» и «таблица» из них не восстанавливаются;
  * `.rtf` и ODF читаются собственным разбором текста: структура в тексте есть,
    но её извлечение — отдельная работа, а не побочный результат.

Спецификация требует для legacy-пути LibreOffice → DOCX → DOCX-читатель. Замер
29.10.2026: `soffice` в PATH отсутствует, `libreoffice` тоже. Поэтому конвертация
НЕ выполняется, а путь назван: собственный разбор, статус `degraded`, конвертер
`builtin:officebinary` в провенансе. Выдавать это за полноценный разбор значило бы
обещать структуру, которой нет.

Адрес такого документа — `path` целиком (форма `document`). Это не «нет адреса»,
а честный адрес: он разрешим — `doc_get` вернёт тот же текст.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from .. import documents, officebinary
from ..docnode import DocumentNode, ReadResult

LEGACY_WORD = (".doc", ".dot", ".wri")
LEGACY_SHEET = (".xls", ".xlt")
LEGACY_SLIDE = (".ppt", ".pps", ".pot")
TEXT_OFFICE = (".rtf", ".odt", ".ods", ".odp")


def _suffix(rel: str) -> str:
    return Path(rel).suffix.lower()


CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_MAGIC = b"PK\x03\x04"


def sniff_family(raw: bytes) -> str:
    """Семейство содержимого по сигнатуре: ooxml, ole, rtf, html, unknown.

    Нужен, потому что расширение врёт: на массовом корпусе 14 файлов `.doc`
    оказались RTF или HTML, 5 `.docx` — старым OLE, 8 `.xls` — без книги.
    Отказ такого файла — потеря покрытия: текст в нём есть.
    """
    head = raw[:8]
    if head.startswith(ZIP_MAGIC):
        return "ooxml"
    if head.startswith(CFB_MAGIC):
        return "ole"
    probe = raw[:2048].lstrip()
    if probe.startswith(b"{\\rtf"):
        return "rtf"
    if probe[:1] == b"<":
        return "html"
    return "unknown"


def _via_officebinary(rel: str, raw: bytes) -> tuple[str, str]:
    """Текст и имя конвертера для OLE-форматов."""
    suffix = _suffix(rel)
    if suffix in LEGACY_WORD:
        result = officebinary.word_text(raw)
        return (result.text, f"builtin:officebinary:word ({result.note or 'ok'})")
    if suffix in LEGACY_SHEET:
        result = officebinary.excel_text(raw)
        return (result.text, f"builtin:officebinary:excel ({result.note or 'ok'})")
    result = officebinary.powerpoint_text(raw)
    return (result.text, f"builtin:officebinary:powerpoint ({result.note or 'ok'})")


def _via_documents(rel: str, raw: bytes, suffix: str = "") -> tuple[str, str, str]:
    """Текст, конвертер и причина для RTF/ODF через общий извлекатель.

    `documents.extract` принимает путь, поэтому байты кладутся во временный файл
    с тем же расширением. Читатель по-прежнему не ходит по исходному пути: он
    получает байты и работает с ними.
    """
    suffix = suffix or _suffix(rel)
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        handle.write(raw)
        handle.close()
        extracted = documents.extract(Path(handle.name))
    finally:
        try:
            Path(handle.name).unlink()
        except OSError:
            pass
    converter = f"builtin:{extracted.kind}"
    return (extracted.text, converter, extracted.reason)


def read(rel: str, raw: bytes) -> ReadResult:
    suffix = _suffix(rel)
    family = sniff_family(raw)
    sniffed = ""
    if family == "ooxml" and suffix not in (".docx", ".docm", ".xlsx", ".xlsm",
                                            ".pptx"):
        # Старый формат переименован в новый: разбираем как zip-пакет.
        from . import ooxml

        members = set()
        try:
            import io as _io
            import zipfile as _zip

            with _zip.ZipFile(_io.BytesIO(raw)) as archive:
                members = set(archive.namelist())
        except Exception:  # noqa: BLE001 — не разобрался, решает читатель ooxml
            members = set()
        if any(name.startswith("word/") for name in members):
            return ooxml.read_docx(rel, raw)
        if any(name.startswith("xl/") for name in members):
            return ooxml.read_xlsx(rel, raw)
        if any(name.startswith("ppt/") for name in members):
            return ooxml.read_pptx(rel, raw)
    if family in ("rtf", "html") and suffix in LEGACY_WORD + LEGACY_SHEET + LEGACY_SLIDE:
        # `.doc`, который на самом деле RTF или HTML: текст есть, структуры OLE нет.
        sniffed = f"содержимое {family}, расширение {suffix}"
        suffix = ".rtf" if family == "rtf" else ".html"
    if family == "html" and suffix == ".html":
        # HTML под офисным расширением разбирает HTML-читатель: он даёт
        # разделы и адреса `#xpath(...)`, а не плоский текст.
        from . import html as html_reader

        result = html_reader.read(rel, raw)
        if result.nodes:
            result.reason = ((result.reason or "") + "; " + sniffed).strip("; ")
            result.metadata = {**(result.metadata or {}), "sniffed": sniffed}
        return result
    try:
        if suffix in LEGACY_WORD + LEGACY_SHEET + LEGACY_SLIDE:
            text, converter = _via_officebinary(rel, raw)
            reason = ("устаревший двоичный формат: извлечён текст, структура "
                      "(разделы, таблицы) не восстановлена; LibreOffice не найден")
        else:
            text, converter, extracted_reason = _via_documents(rel, raw, suffix)
            reason = ("собственный разбор текста: разделы и таблицы не выделены; "
                      "LibreOffice не найден")
            if extracted_reason:
                reason = f"{reason}; {extracted_reason}"
    except Exception as exc:  # noqa: BLE001 — причина важнее трейсбека
        return ReadResult(status="failed",
                          reason=f"разбор {suffix}: {type(exc).__name__}: {exc}",
                          reader="legacy")
    text = "\n".join(line.rstrip() for line in str(text or "").splitlines()).strip()
    if not text:
        return ReadResult(status="degraded",
                          reason=f"текста не извлечено: {reason}"
                                 + (f"; {sniffed}" if sniffed else ""),
                          reader="legacy", converter=converter,
                          metadata={"sniffed": sniffed} if sniffed else {})
    node = DocumentNode(
        kind="document",
        locator=rel,
        text=text,
        structure={"no_heading_reason": "структура не восстановлена: см. провенанс"},
        metadata={"ext": suffix},
        provenance={"reader": "legacy", "converter": converter},
    )
    if sniffed:
        reason = f"{reason}; {sniffed}"
        node.provenance["sniffed"] = sniffed
    return ReadResult(nodes=[node], status="degraded", reason=reason,
                      reader="legacy", converter=converter, warnings=[reason],
                      metadata={"sniffed": sniffed} if sniffed else {})
