"""Читатель EPUB: глава и её абзацы.

Почему свой разбор, а не библиотека.

`ebooklib` в окружении отсутствует, `pandoc` тоже (замер 29.10.2026). Ставить их
из сети при индексации запрещено. Зато EPUB — это обычный zip с XHTML внутри, а
`lxml` установлен: разбор контейнера (`META-INF/container.xml` → OPF → `spine`)
занимает несколько десятков строк и не тянет зависимостей.

Адреса: глава — `path#chapter=N` (порядок spine — это порядок чтения, а не порядок
файлов в архиве), абзац — `path#chapter=N#para=M`. Оба разрешимы: повторный разбор
даёт тот же элемент.

Чего здесь нет: обложка как изображение, стили, сноски EPUB. Текст глав и их
структура — есть; остальное названо, а не подразумевается.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from ..docnode import DocumentNode, ReadResult

#: Сколько глав читать. Книга на 2000 глав — это не документ корпуса, а свалка;
#: предел назван, а не молча обрезан.
MAX_CHAPTERS = 400

_NS_CONTAINER = "{urn:oasis:names:tc:opendocument:xmlns:container}"
_NS_OPF = "{http://www.idpf.org/2007/opf}"

HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
TEXT_TAGS = ("p", "li", "blockquote", "dd", "dt", "figcaption", "pre")


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _parse(xml: bytes):
    from lxml import etree

    parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True)
    return etree.fromstring(xml, parser=parser)


def _rootfile(archive: zipfile.ZipFile) -> str:
    data = archive.read("META-INF/container.xml")
    root = _parse(data)
    for element in root.iter(f"{_NS_CONTAINER}rootfile"):
        path = element.get("full-path") or ""
        if path:
            return path
    raise ValueError("в container.xml нет rootfile")


def _spine(archive: zipfile.ZipFile, opf_path: str) -> tuple[list[str], str]:
    """Порядок чтения (spine) и заголовок книги."""
    opf = _parse(archive.read(opf_path))
    manifest: dict[str, str] = {}
    for item in opf.iter(f"{_NS_OPF}item"):
        item_id = item.get("id") or ""
        href = item.get("href") or ""
        if item_id and href:
            manifest[item_id] = href
    base = str(Path(opf_path).parent).replace("\\", "/")
    if base == ".":
        base = ""
    order: list[str] = []
    for itemref in opf.iter(f"{_NS_OPF}itemref"):
        idref = itemref.get("idref") or ""
        href = manifest.get(idref)
        if not href:
            continue
        full = f"{base}/{href}" if base else href
        order.append(full.replace("//", "/"))
    title = ""
    for element in opf.iter(f"{_NS_OPF}title"):
        title = _clean(element.text or "")
        if title:
            break
    return (order, title)


def read(rel: str, raw: bytes) -> ReadResult:
    try:
        from lxml import etree  # noqa: F401 — проверка наличия, не использование
    except Exception as exc:  # noqa: BLE001
        return ReadResult(status="degraded",
                          reason=f"нет библиотеки lxml: EPUB не разобран "
                                 f"({type(exc).__name__})",
                          reader="epub")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        return ReadResult(status="failed",
                          reason=f"EPUB не является zip-контейнером: {exc}",
                          reader="epub")
    try:
        opf_path = _rootfile(archive)
        order, title = _spine(archive, opf_path)
    except Exception as exc:  # noqa: BLE001
        archive.close()
        return ReadResult(status="failed",
                          reason=f"структура EPUB: {type(exc).__name__}: {exc}",
                          reader="epub")

    nodes: list[DocumentNode] = []
    warnings: list[str] = []
    chapter_no = 0
    for href in order[:MAX_CHAPTERS]:
        chapter_no += 1
        try:
            data = archive.read(href)
        except KeyError:
            warnings.append(f"глава {chapter_no}: файл {href} отсутствует в архиве")
            continue
        try:
            tree = _parse(data)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"глава {chapter_no}: не разобрана ({type(exc).__name__})")
            continue
        # Заголовок ищется по ЛОКАЛЬНОМУ имени, а не по тегу с пространством имён:
        # XHTML в EPUB почти всегда объявлен в пространстве `xhtml`, и `find(".//h1")`
        # тогда не находит ничего — глава молча оставалась без заголовка (измерено
        # на фикстуре: узлы абзацев были, узла главы не было вовсе).
        heading = ""
        first_level = 99
        for element in tree.iter():
            if not isinstance(element.tag, str):
                continue
            local = element.tag.split("}")[-1].lower()
            if local in HEADINGS and HEADINGS[local] < first_level:
                text = _clean(" ".join(element.itertext()))
                if text:
                    heading = text
                    first_level = HEADINGS[local]
                    break
        if heading:
            nodes.append(DocumentNode(
                kind="section",
                locator=f"{rel}#chapter={chapter_no}",
                text=heading,
                structure={"heading_chain": [heading], "chapter": chapter_no,
                           "href": href},
            ))
        paragraph_no = 0
        for element in tree.iter():
            tag = element.tag if isinstance(element.tag, str) else ""
            local = tag.split("}")[-1].lower()
            if local not in TEXT_TAGS:
                continue
            text = _clean(" ".join(element.itertext()))
            if not text:
                continue
            paragraph_no += 1
            nodes.append(DocumentNode(
                kind="paragraph",
                locator=f"{rel}#chapter={chapter_no}#para={paragraph_no}",
                text=text,
                structure={"heading_chain": [heading] if heading else [],
                           "chapter": chapter_no,
                           "href": href} if heading else
                          {"heading_chain": [],
                           "no_heading_reason": "глава без заголовка",
                           "chapter": chapter_no, "href": href},
            ))
    archive.close()
    if chapter_no > MAX_CHAPTERS:
        warnings.append(f"глав больше предела {MAX_CHAPTERS}: прочитано {MAX_CHAPTERS}")
    if not nodes:
        return ReadResult(status="degraded",
                          reason="в книге нет текста глав", reader="epub",
                          metadata={"title": title}, warnings=warnings)
    return ReadResult(nodes=nodes, status="ok", reader="epub",
                      metadata={"title": title, "chapters": min(chapter_no, MAX_CHAPTERS)},
                      warnings=warnings)
