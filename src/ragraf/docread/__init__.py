"""Читатели документного слоя: реестр форматов, разбор, разрешение локатора.

Три обязанности, и все три — про границу:
  1. `read(rel, raw)` — получить УЗЛЫ (`docnode.DocumentNode`), а не плоский текст;
  2. `resolve(rel, raw, locator)` — по адресу вернуть ровно тот текст, который
     по этому адресу лежит;
  3. `supported(rel)` — честно сказать, разбирает ли сборка этот формат.

Почему читатель получает БАЙТЫ, а не путь. Читатель, которому дали путь, рано
или поздно начнёт читать файл сам — и тогда «сколько раз файл прочитан» и
«какой текст увидел поиск» перестанут быть одним и тем же. Байты приносит
вызывающий: индексатор (из скана) и `doc_get` (из диска), оба через один
`textdec`.

Почему реестр — данные. Список расширений и их читателей объявлен здесь один
раз: `formats.py` отвечает на вопрос «что индексируется», `documents.py` — «как
достать текст», `docread` — «как получить структуру». Расхождение между тремя
списками было бы третьим источником правды, поэтому новый формат добавляется
правкой этого словаря, а не ветвлением в индексаторе.

Про отсутствие инструментов. Docling, pandas, BeautifulSoup и trafilatura в
окружении этой машины не установлены (замер вызовом 28.10.2026). Поэтому
базовые читатели — собственные: текст и HTML разбираются здесь, офисные
форматы в фазе B опираются на уже существующие `documents.py`/`officebinary.py`.
Недоступный инструмент понижает статус до `degraded` с причиной, а не тянется
из сети.
"""

from __future__ import annotations

from pathlib import Path

from .. import textdec
from ..docnode import DocumentNode, ReadResult, path_of, split_locator
from . import archive as archive_reader
from . import data as data_reader
from . import epub as epub_reader
from . import html as html_reader
from . import legacy as legacy_reader
from . import mail as mail_reader
from . import ocr as ocr_reader
from . import ooxml as ooxml_reader
from . import pdf as pdf_reader
from . import text as text_reader

#: Версия набора читателей. Пишется в провенанс каждого узла: расхождение
#: «текст из индекса» и «текст с диска» объясняется версией, а не догадкой.
READER_VERSION = "docread/1"

#: Расширение → имя читателя. Форматы, которых здесь нет, документным слоем не
#: разбираются: для них работает прежний плоский путь `_text_chunks`.
READERS: dict[str, str] = {
    ".txt": "text",
    ".text": "text",
    ".log": "text",
    ".md": "text",
    ".markdown": "text",
    ".rst": "text",
    ".adoc": "text",
    ".asciidoc": "text",
    ".html": "html",
    ".htm": "html",
    ".xhtml": "html",
    # Фаза B: офисные форматы и PDF. Разбор свой — библиотека используется,
    # если она есть, и это проверяется вызовом, а не ожиданием.
    ".docx": "ooxml", ".docm": "ooxml", ".dotx": "ooxml", ".dotm": "ooxml",
    ".xlsx": "ooxml", ".xlsm": "ooxml", ".xltx": "ooxml", ".xltm": "ooxml",
    ".pptx": "ooxml", ".pptm": "ooxml", ".ppsx": "ooxml", ".potx": "ooxml",
    ".pdf": "pdf",
    # Устаревшие и «текстовые» офисные: текст извлекается, структура — нет.
    ".doc": "legacy", ".dot": "legacy", ".xls": "legacy", ".xlt": "legacy",
    ".ppt": "legacy", ".pps": "legacy", ".pot": "legacy",
    ".rtf": "legacy", ".odt": "legacy", ".ods": "legacy", ".odp": "legacy",
    # Фаза C: данные, почта, EPUB.
    ".csv": "data", ".tsv": "data",
    ".json": "data", ".jsonl": "data", ".ndjson": "data",
    ".xml": "data", ".xsd": "data", ".xsl": "data", ".svg": "data",
    ".rss": "data", ".atom": "data",
    ".yaml": "data", ".yml": "data", ".toml": "data",
    ".eml": "mail", ".msg": "mail",
    ".epub": "epub",
    # Фаза D: архивы берутся только при включённой настройке (`handled_in_index`),
    # изображения проходят через OCR-читатель, который честно отвечает причиной.
    ".zip": "archive",
    ".png": "ocr", ".jpg": "ocr", ".jpeg": "ocr", ".tif": "ocr",
    ".tiff": "ocr", ".bmp": "ocr", ".webp": "ocr", ".gif": "ocr",
}

#: Расширения, которые документный слой ЗНАЕТ, но разбирает не эта фаза.
#: Причина называется, чтобы `doc_status` не выдавал «нет файла» за «не умеем».
PLANNED: dict[str, str] = {
    ".zip": "архивы: только по явной настройке (фаза D)",
}

#: Расширения, которые документный слой умеет, но НЕ забирает у разбора
#: деревом при индексации. Причина не в лени: разбор HTML деревом питает
#: `class_usage` и реестр маршрутов (`routes`), и замена этого пути отняла бы у
#: кодового индекса связи, которые уже работают. Спецификация прямо запрещает
#: такую регрессию, поэтому HTML-документ входит в слой по явному переключателю
#: группы форматов (фаза D), а до тех пор доступен через `doc_get` по адресу.
INDEXING_EXCLUDE: frozenset[str] = frozenset({".html", ".htm", ".xhtml"})


_READER_FUNCS = {
    "text": text_reader.read,
    "html": html_reader.read,
    "ooxml": ooxml_reader.read,
    "pdf": pdf_reader.read,
    "legacy": legacy_reader.read,
    "data": data_reader.read,
    "mail": mail_reader.read,
    "epub": epub_reader.read,
    "archive": archive_reader.read,
    "ocr": ocr_reader.read_image,
}

#: Форматы, у которых адрес обязан быть НЕ строкой файла: у них нет строк в
#: текстовом смысле, и `path:12` для них — тот самый ложный локатор.
BINARY_READERS = frozenset({"ooxml", "pdf", "legacy"})


def suffix_of(rel: str) -> str:
    return Path(str(rel)).suffix.lower()


def supported(rel: str) -> bool:
    return suffix_of(rel) in READERS


def handled_in_index(rel: str, html_mode: str = "code") -> bool:
    """Забирает ли документный слой этот файл при индексации.

    Отличается от `supported` намеренно: `supported` отвечает «умеем
    разобрать», `handled_in_index` — «берём этот файл у старого пути». Один
    признак на два вопроса давал бы либо мёртвый читатель, либо отнятый у
    кодового индекса HTML.

    Три особых случая, и все три — про разрешение, а не про умение:
      * HTML забирается ТОЛЬКО при `documents_html_mode = document`;
      * архив забирается только при включённой настройке архивов;
      * изображения забирает OCR-читатель: он вернёт причину, а не текст,
        но файл не исчезнет из отчёта.
    """
    ext = suffix_of(rel)
    if ext in INDEXING_EXCLUDE:
        return str(html_mode) == "document"
    if ext == ".zip":
        return archive_reader.enabled()
    return ext in READERS


def planned_reason(rel: str) -> str:
    """Почему формат известен, но не разбирается. Пусто — формат не наш вовсе.

    Источник причины один — `documents.NO_PARSER` (там же, где причины для
    форматов без разборщика вообще), плюс собственный список `PLANNED` для
    форматов, чьи читатели ещё не написаны. Без этого `.pages` отвечал бы
    `failed` — то есть «ошибка», хотя никакой ошибки нет: разборщика не бывает.
    """
    ext = suffix_of(rel)
    reason = PLANNED.get(ext)
    if reason:
        return reason
    try:
        from ..documents import NO_PARSER
    except Exception:  # noqa: BLE001 — нет таблицы причин, значит нет и причины
        return ""
    return str(NO_PARSER.get(ext) or "")


#: Признак указателя Git LFS: файл на диске есть, содержимого в нём нет.
LFS_POINTER = b"version https://git-lfs.github.com/spec/v1"


def _predispatch(rel: str, raw: bytes) -> ReadResult | None:
    """Решения по СОДЕРЖИМОМУ, которые не зависят от расширения.

    Возвращает результат, если файл дальше не читается, иначе None.

    Два случая, оба измерены на массовом корпусе:
      * указатель Git LFS (130 байт текста вместо файла) — отказ с причиной
        «содержимого на диске нет». Сообщение «не zip» отправляло бы искать
        дефект в разборе, а не в выгрузке файла;
      * HTML под именем `.docx/.xlsx` — разбирается HTML-читателем: текст
        в файле есть, отказ терял бы покрытие.
    """
    head = raw[:64]
    if head.startswith(LFS_POINTER):
        oid = ""
        for line in raw[:1024].decode("utf-8", errors="replace").splitlines():
            if line.startswith("oid sha256:"):
                oid = line.split(":", 1)[1].strip()
                break
        return ReadResult(
            status="failed",
            reason=("файл — указатель Git LFS, содержимого на диске нет"
                    + (f" (oid sha256:{oid[:16]}…)" if oid else "")
                    + "; нужен `git lfs pull` или сам файл"),
            reader="lfs",
            metadata={"lfs_pointer": True, "bytes": len(raw)},
        )
    probe = raw[:2048].lstrip()
    suffix = suffix_of(rel)
    if probe[:1] == b"<" and suffix not in (".html", ".htm", ".xhtml", ".xml", ".mht"):
        from . import html as html_reader

        result = html_reader.read(rel, raw)
        note = f"содержимое html, расширение {suffix}"
        result.reason = (result.reason + "; " + note).strip("; ") if result.reason else note
        result.metadata = {**(result.metadata or {}), "sniffed": note}
        result.warnings = list(result.warnings) + [note]
        if result.status == "ok":
            # Разобрано, но расширение обещало другое: это деградация, а не успех.
            result.status = "degraded"
        return result
    return None


def read(rel: str, raw: bytes) -> ReadResult:
    """Разобрать документ в узлы. Никогда не бросает: причина в `status`.

    Отказ читателя обязан быть видимым (`failed` + причина), а не превращаться
    в исключение, которое индексатор переживёт молча: спецификация требует
    `extraction_failed` в отчёте, а не «тихий пропуск».
    """
    ext = suffix_of(rel)
    early = _predispatch(rel, raw)
    if early is not None:
        early.metadata.setdefault("ext", ext)
        early.metadata.setdefault("bytes", len(raw))
        return early
    name = READERS.get(ext)
    if name is None:
        reason = planned_reason(rel)
        return ReadResult(
            status="not_indexed_by_edition" if reason else "failed",
            reason=reason or f"формат не поддержан документным слоем: {ext or '—'}",
            reader="",
            metadata={"ext": ext},
        )
    func = _READER_FUNCS[name]
    try:
        result = func(rel, raw)
    except Exception as exc:  # noqa: BLE001 — причина важнее трейсбека
        return ReadResult(
            status="failed",
            reason=f"разбор {name}: {type(exc).__name__}: {exc}",
            reader=name,
            metadata={"ext": ext},
        )
    result.reader = result.reader or name
    for index, node in enumerate(result.nodes):
        node.index = index
        node.provenance.setdefault("reader", result.reader)
        node.provenance.setdefault("reader_version", READER_VERSION)
        if result.converter:
            node.provenance.setdefault("converter", result.converter)
        # Статус ЧИТАТЕЛЯ переносится на узлы: если разбор деградировал (нет
        # библиотеки, структура не восстановлена), узел не может остаться «ok» —
        # иначе `doc_status` показывает успех там, где текст получен слабым путём.
        if result.status != "ok" and node.extraction_status == "ok":
            node.extraction_status = result.status
        node.metadata.setdefault("ext", ext)

        node.metadata.setdefault("size", len(raw))
    result.metadata.setdefault("ext", ext)
    result.metadata.setdefault("bytes", len(raw))
    return result


def declines(rel: str) -> str:
    """Формат наш, но сейчас не берём: причина или пустая строка.

    Нужен, чтобы отказ был ВИДЕН, но не стоил чтения файла: архив на 4 ГБ при
    выключенной настройке не должен читаться целиком ради фразы «выключено».
    """
    ext = suffix_of(rel)
    if ext == ".zip" and not archive_reader.enabled():
        return ("архивы не индексируются: настройка `documents_archives_enabled`"
                " выключена")
    return ""


def resolve(rel: str, raw: bytes, locator: str) -> tuple[str, dict]:
    """Текст по адресу и как он получен.

    Разрешение идёт по ТОЙ ЖЕ грамматике, что и построение адреса
    (`docnode.split_locator`): второй разбор «на глаз» дал бы адрес, который
    формат поддерживает, а разрешение — нет.

    Для формы `line` текст берётся прямо из строк файла (после той же
    нормализации кодировки, что и при индексации). Для остальных форм документ
    разбирается заново и ищется узел с совпадающим адресом; расхождение
    называется, а не замалчивается.
    """
    form, path, fields = split_locator(locator)
    if not form:
        return ("", {"error": f"локатор не разобран: {locator[:120]!r}", "form": ""})
    if form == "inner":
        # Настройка уважается и здесь: если архивы выключены, элемент не достаётся.
        # Без этой проверки адрес разрешался при выключенной настройке — то есть
        # `doc_get` отдавал текст из того, что индекс не читал (измерено на
        # песочнице: `b.zip!docs/readme.md:1` возвращал `# MARK` при выключенных
        # архивах). Отказ с причиной честнее: он называет, почему адреса нет.
        refused = declines(rel)
        if refused:
            return ("", {"error": refused, "form": form})
        # Адрес элемента архива: `архив!путь/внутри.md:12`. Байты элемента
        # достаются из архива, дальше работает тот же разрешатель, что и для
        # обычного файла, — иначе внутренние адреса пришлось бы разбирать
        # второй, отдельной логикой, и они разошлись бы.
        inner = str(fields.get("inner") or "")
        if not inner:
            return ("", {"error": "в адресе архива нет внутреннего пути", "form": form})
        try:
            member_raw, member = archive_reader.resolve_member(raw, inner)
        except Exception as exc:  # noqa: BLE001 — причина важнее трейсбека
            return ("", {"error": (f"элемент архива недоступен: "
                                   f"{type(exc).__name__}: {exc}"), "form": form})
        return resolve(member, member_raw, inner)
    if form == "line" and suffix_of(rel) in READERS \
            and READERS[suffix_of(rel)] in BINARY_READERS:
        # Строки файла у офисного формата не существует: обращение по такому
        # адресу вернуло бы мусор из байтов вместо честного отказа.
        return ("", {"error": (f"адрес-строки недопустим для этого формата: "
                               f"{suffix_of(rel)}; адресуйте абзацем, страницей, "
                               f"листом или слайдом"), "form": form})
    if form == "line":
        text = textdec.decode(raw, rel)[0]
        lines = text.splitlines()
        start = int(fields.get("start") or 1)
        end = int(fields.get("end") or start)
        if start > len(lines):
            return ("", {
                "error": f"строка {start} за пределами файла: строк всего {len(lines)}",
                "form": form,
                "total_lines": len(lines),
            })
        return ("\n".join(lines[start - 1:end]), {
            "form": form, "start": start, "end": min(end, len(lines)),
            "total_lines": len(lines),
        })
    result = read(rel, raw)
    if result.status != "ok":
        return ("", {"error": result.reason, "form": form, "status": result.status})
    for node in result.nodes:
        if node.locator == locator:
            return (node.text, {"form": form, "node_kind": node.kind,
                                "status": node.extraction_status})
    return ("", {
        "error": f"в документе нет узла с адресом {locator}",
        "form": form, "nodes": len(result.nodes),
    })


def nodes_of(rel: str, raw: bytes) -> list[DocumentNode]:
    """Узлы документа — короткая обёртка для проверок и `doc_status`."""
    return read(rel, raw).nodes


__all__ = [
    "READERS", "PLANNED", "READER_VERSION", "INDEXING_EXCLUDE",
    "read", "resolve", "supported", "handled_in_index", "nodes_of",
    "suffix_of", "planned_reason", "path_of",
]
