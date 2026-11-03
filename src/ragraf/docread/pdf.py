"""Читатель PDF: страница — единица адреса.

Почему страница, а не абзац. У PDF нет структуры абзаца: есть поток команд рисования,
в котором «абзац» — это результат разметки, а не сущность файла. Адрес `path:page=3`
разрешим: страницу можно перечитать и получить тот же текст. Адрес `path:12` —
нет, и именно он был прежним поведением (строка извлечённого текста).

Что здесь сказано честно, а не замазано:

* **Скан без текстового слоя.** Текст не извлекается, и это не ошибка разбора:
  нужен OCR, которого в сборке нет. Статус `degraded` с причиной; нулём страниц
  документ не притворяется.
* **Колонтитулы и повторяющаяся шапка не отделяются.** У PDF нет понятия
  «колонтитул»: это текст на странице. Отделять их пришлось бы эвристикой по
  повтору строк, а эвристика без замера — это догадка. Поэтому страница остаётся
  целой, а ограничение названо в провенансе.
* **Порядок колонок не восстанавливается.** Двухколоночный текст приходит
  построчно так, как он лежит в потоке. Обещать «правильный порядок чтения»
  без замера нельзя.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from ..docnode import DocumentNode, ReadResult

#: Предел страниц: 4000 страниц одного файла — это уже не документ корпуса,
#: а свалка; при превышении причина называется, а не молча обрезается.
MAX_PAGES = 4000


def _clean(value: str) -> str:
    return "\n".join(line.rstrip() for line in str(value or "").splitlines()).strip()


def _read_via_pymupdf(rel: str, raw: bytes, limit: int) -> ReadResult | None:
    """Запасной разбор через PyMuPDF. None — библиотеки нет или файл не открылся."""
    try:
        import pymupdf
    except Exception:
        try:
            import fitz as pymupdf  # type: ignore
        except Exception:
            return None
    try:
        document = pymupdf.open(stream=raw, filetype="pdf")
    except Exception:
        return None
    nodes: list[DocumentNode] = []
    empty_pages = 0
    try:
        total = document.page_count
        for index in range(min(total, limit)):
            try:
                page_text = _clean(document.load_page(index).get_text("text") or "")
            except Exception:
                empty_pages += 1
                continue
            if not page_text:
                empty_pages += 1
                continue
            nodes.append(DocumentNode(
                kind="section",
                locator=f"{rel}:page={index + 1}",
                text=page_text,
                structure={"heading_chain": [],
                           "no_heading_reason": "PDF: структура разделов в потоке не размечена",
                           "page": index + 1},
                metadata={"page": index + 1},
            ))
    finally:
        document.close()
    if not nodes:
        return None
    return ReadResult(nodes=nodes, status="ok", reader="pdf",
                      converter="pymupdf",
                      metadata={"pages": total, "pages_read": min(total, limit),
                                "pages_empty": empty_pages, "fallback": "pymupdf"})


def read(rel: str, raw: bytes) -> ReadResult:
    try:
        from pypdf import PdfReader
    except Exception as exc:  # noqa: BLE001
        return ReadResult(
            status="degraded",
            reason=("нет библиотеки pypdf: текстовый слой PDF не извлечён "
                    f"({type(exc).__name__})"),
            reader="pdf",
        )
    try:
        reader = PdfReader(io.BytesIO(raw))
        total = len(reader.pages)
    except Exception as exc:  # noqa: BLE001
        # Отказ основного извлекателя — повод попробовать запасной, а не
        # объявлять файл нечитаемым. Замер 29.10.2026: 2 PDF из 626
        # (AES-шифрование и отсутствие startxref) pypdf не открывает,
        # PyMuPDF открывает и читает.
        fallback = _read_via_pymupdf(rel, raw, MAX_PAGES)
        if fallback is not None:
            fallback.warnings = list(fallback.warnings) + [
                f"основной извлекатель отказал ({type(exc).__name__}), "
                f"текст получен запасным (pymupdf)"]
            return fallback
        return ReadResult(status="failed",
                          reason=(f"разбор PDF: {type(exc).__name__}: {exc}"
                                  + "; запасной извлекатель (pymupdf) тоже не открыл"),
                          reader="pdf")
    limit = min(total, MAX_PAGES)
    nodes: list[DocumentNode] = []
    warnings: list[str] = []
    empty_pages = 0
    for index in range(limit):
        try:
            page_text = _clean(reader.pages[index].extract_text() or "")
        except Exception as exc:  # noqa: BLE001 — одна страница не роняет документ
            warnings.append(f"страница {index + 1}: не прочитана ({type(exc).__name__})")
            continue
        if not page_text:
            empty_pages += 1
            continue
        nodes.append(DocumentNode(
            kind="section",
            locator=f"{rel}:page={index + 1}",
            text=page_text,
            structure={"heading_chain": [],
                       "no_heading_reason": "PDF: структура разделов в потоке не размечена",
                       "page": index + 1},
            metadata={"page": index + 1},
        ))
    metadata: dict[str, Any] = {"pages": total, "pages_read": limit,
                                "pages_empty": empty_pages}
    if total > limit:
        warnings.append(f"страниц {total}, прочитано {limit}: остальные за пределом")
    if not nodes:
        # Скана это больше не тупик: если OCR включён и движок найден,
        # страницы растеризуются и распознаются. Если нет — причина называет,
        # ЧЕГО не хватает, а не просто «не разбирается».
        from . import ocr as ocr_reader

        ocr_state = ocr_reader.state()
        if ocr_state["status"] == "ready":
            ocr_result = ocr_reader.read_pdf_pages(rel, raw,
                                                   list(range(1, min(total, limit) + 1)))
            if ocr_result.nodes:
                metadata["ocr"] = ocr_result.metadata
                return ReadResult(nodes=ocr_result.nodes, status="degraded",
                                  reason=("текстового слоя нет; "
                                          + (ocr_result.reason or "")),
                                  reader="pdf", converter="pypdf+ocr",
                                  metadata=metadata,
                                  warnings=list(warnings) + list(ocr_result.warnings))
            metadata["ocr"] = {"attempted": True,
                               "reason": ocr_result.reason or ""}
            return ReadResult(
                status="degraded",
                reason=(f"текстового слоя нет (страниц {total}); распознавание "
                        f"не дало текста: {ocr_result.reason or 'нет данных'}"),
                reader="pdf", metadata=metadata, warnings=warnings,
            )
        return ReadResult(
            status="degraded",
            reason=(f"текстового слоя нет (страниц {total}); OCR недоступен: "
                    f"{ocr_state['reason']}"),
            reader="pdf", metadata=metadata, warnings=warnings,
        )
    if empty_pages:
        warnings.append(f"страниц без текста: {empty_pages} из {limit}")
    return ReadResult(nodes=nodes, status="ok", reader="pdf", converter="pypdf",
                      metadata=metadata, warnings=warnings)


def suffix_supported(rel: str) -> bool:
    return Path(rel).suffix.lower() == ".pdf"
