"""Читатель HTML: разделы, абзацы и таблицы без навигации и обвязки.

Почему не BeautifulSoup и не trafilatura. Их в окружении нет (замер вызовом
28.10.2026: `ModuleNotFoundError`), а тянуть пакет из сети при индексации
запрещено — локальность слоя. `lxml` установлен и разбирает HTML с восстановлением
структуры, поэтому читатель написан на нём.

Почему обвязку надо убирать, а не «пусть остаётся». Навигация, подвал, баннер
согласия и блок «поделиться» повторяются на каждой странице корпуса. В индексе
они дают тысячи почти одинаковых чанков: поиск начинает находить меню вместо
текста статьи, а схлопывание дублей не спасает — тексты различаются одним
пунктом меню. Поэтому блоки удаляются по тегу И по признаку в имени класса.

Чего этот читатель НЕ делает: он не вычисляет видимость (CSS не применяется) и
не угадывает «главный блок статьи» эвристикой плотности текста. Удаление
названо правилами, а список правил виден в ответе `doc_status`, поэтому
«почему в чанке нет футера» имеет ответ, а не является побочным эффектом.
"""

from __future__ import annotations

import re

from ..docnode import DocumentNode, ReadResult

#: Теги, которых в тексте документа быть не должно.
DROP_TAGS: tuple[str, ...] = (
    "script", "style", "noscript", "template", "iframe", "svg", "canvas",
    "form", "nav", "footer", "header", "aside", "object", "embed", "button",
)

#: Признак обвязки в имени класса/идентификаторе. Сопоставление по подстроке:
#: `cookie-consent`, `site-footer`, `main-nav`, `breadcrumbs` — всё это обвязка.
DROP_ATTR = re.compile(
    r"(?:^|[-_\s])(?:nav|menu|navbar|footer|header|sidebar|breadcrumb|cookie|"
    r"consent|banner|advert|ads|social|share|subscribe|popup|modal|toolbar)"
    r"(?:$|[-_\s])",
    re.IGNORECASE,
)

HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
TEXT_TAGS = ("p", "li", "pre", "blockquote", "dd", "dt", "figcaption")
ANCHOR_OK = re.compile(r"[^A-Za-z0-9_.:\-]+")


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _anchor_of(element) -> str:
    """Якорь элемента: `id`, затем `name` у ссылки. Пусто — если нет обоих."""
    for attr in ("id", "name"):
        raw = element.get(attr)
        if raw:
            cleaned = ANCHOR_OK.sub("-", _clean(raw)).strip("-")
            if cleaned:
                return cleaned
    return ""


def _xpath_of(element) -> str:
    try:
        return element.getroottree().getpath(element)
    except Exception:  # noqa: BLE001 — адрес нужен, но разбор пути не критичен
        return ""


def _locator(rel: str, element) -> str:
    anchor = _anchor_of(element)
    if anchor:
        return f"{rel}#{anchor}"
    xpath = _xpath_of(element)
    return f"{rel}#xpath({xpath})" if xpath else f"{rel}"


def _drop(tree) -> int:
    """Удалить обвязку. Возвращает число удалённых элементов."""
    dropped = 0
    for tag in DROP_TAGS:
        for element in list(tree.iter(tag)):
            parent = element.getparent()
            if parent is None:
                continue
            parent.remove(element)
            dropped += 1
    for element in list(tree.iter()):
        if not isinstance(element.tag, str):
            continue
        name = f"{element.get('class') or ''} {element.get('id') or ''}"
        if not name.strip():
            continue
        if DROP_ATTR.search(name):
            parent = element.getparent()
            if parent is None:
                continue
            parent.remove(element)
            dropped += 1
    return dropped


def read(rel: str, raw: bytes) -> ReadResult:
    """Узлы HTML-документа."""
    try:
        from lxml import html as lhtml
    except Exception as exc:  # noqa: BLE001
        return ReadResult(
            status="degraded",
            reason=f"нет библиотеки lxml для разбора HTML: {type(exc).__name__}",
            reader="html",
        )
    # Кодировка определяется ЗДЕСЬ, а не внутри lxml. Замер 29.10.2026:
    # `HTMLParser(encoding="utf-8")` на cp1251 бросает UnicodeDecodeError, и 10 файлов
    # (HTML под именем `.doc`) терялись целиком, хотя текст в них есть. Порядок проб:
    # utf-8 (строго) → cp1251 (русская доля корпуса) → windows-1252 → utf-8 с заменой.
    # Выбранная кодировка попадает в ответ: «прочитано как cp1251» — факт, не догадка.
    encoding_used = "utf-8"
    decoded: str | None = None
    for candidate in ("utf-8", "cp1251", "windows-1252"):
        try:
            decoded = raw.decode(candidate)
            encoding_used = candidate
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        decoded = raw.decode("utf-8", errors="replace")
        encoding_used = "utf-8/replace"
    try:
        parser = lhtml.HTMLParser(recover=True, remove_comments=True)
        tree = lhtml.fromstring(decoded, parser=parser)
    except Exception as exc:  # noqa: BLE001
        return ReadResult(status="failed",
                          reason=f"разбор HTML: {type(exc).__name__}: {exc}",
                          reader="html")
    dropped = _drop(tree)
    title = ""
    try:
        found = tree.xpath("//title/text()")
        title = _clean(found[0]) if found else ""
    except Exception:  # noqa: BLE001
        title = ""

    nodes: list[DocumentNode] = []
    stack: list[tuple[int, str]] = []
    tables = 0
    body_chain: list[str] = []

    for element in tree.iter():
        if not isinstance(element.tag, str):
            continue
        tag = element.tag.lower()
        inside_table = bool(element.xpath("ancestor::table"))
        if tag in HEADINGS:
            text = _clean(" ".join(element.itertext()))
            if not text:
                continue
            level = HEADINGS[tag]
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, text))
            body_chain = [t for _, t in stack]
            nodes.append(DocumentNode(
                kind="section",
                locator=_locator(rel, element),
                text=text,
                structure={"heading_chain": list(body_chain), "level": level},
            ))
            continue
        if tag == "table" and not inside_table:
            tables += 1
            rows: list[str] = []
            for row in element.xpath(".//tr"):
                cells = [_clean(" ".join(cell.itertext())) for cell in row.xpath("./th|./td")]
                if any(cells):
                    rows.append(" | ".join(cells))
            text = "\n".join(rows).strip()
            if not text:
                continue
            nodes.append(DocumentNode(
                kind="table",
                locator=_locator(rel, element),
                text=text,
                structure={
                    "table_id": f"t{tables}",
                    "heading_chain": list(body_chain),
                    "rows": len(rows),
                },
            ))
            continue
        if tag in TEXT_TAGS and not inside_table:
            text = _clean(" ".join(element.itertext()))
            if not text:
                continue
            nodes.append(DocumentNode(
                kind="paragraph",
                locator=_locator(rel, element),
                text=text,
                structure={"heading_chain": list(body_chain), "tag": tag},
            ))

    if not nodes:
        return ReadResult(
            nodes=[], status="ok",
            reason="в документе нет текста после удаления обвязки",
            reader="html", converter="lxml",
            metadata={"title": title, "dropped": dropped, "encoding": encoding_used},
            warnings=[f"удалено блоков обвязки: {dropped}"] if dropped else [],
        )
    for node in nodes:
        if node.kind == "paragraph" and not node.heading_chain:
            node.structure.setdefault("no_heading_reason", "текст вне разделов")
    return ReadResult(
        nodes=nodes, status="ok", reader="html", converter="lxml",
        metadata={"title": title, "dropped": dropped, "tables": tables,
                  "encoding": encoding_used},
        warnings=[f"удалено блоков обвязки: {dropped}"] if dropped else [],
    )
