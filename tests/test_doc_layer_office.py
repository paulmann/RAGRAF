"""Проверка документного слоя, фаза B: офисные форматы и PDF.

Свойства (каждое — отдельная проверка, а не «файл открылся»):

  1. DOCX: заголовки дают цепочку разделов, таблица — отдельный узел с `table_id`,
     сноска — отдельный узел, колонтитул — отдельный узел. Основной текст с ними
     НЕ смешан: у каждого свой адрес.
  2. Адрес разрешается обратно: `doc_get(path#para=N)` возвращает именно тот абзац.
  3. XLSX: лист — отдельная сущность; разрезанный лист повторяет заголовок колонок
     в каждой части (значение без имени колонки не индексируется).
  4. PPTX: слайд и заметки докладчика — разные узлы (`#notes`); таблица на слайде —
     отдельный узел `:slide=N#table=M`.
  5. PDF: адрес — страница (`path:page=N`), текст страницы находится поиском.
  6. Legacy (`.rtf`): текст извлечён, структуры нет — статус `degraded` с причиной,
     адрес `path` разрешается.
  7. Валидатор НЕ пустой: подсаженный узел «заметки внутри слайда» ловится
     проверкой 5, после удаления строки вердикт снова положительный.
  8. Кодовый индекс не регрессировал.

Библиотеки создания фикстур (python-docx, openpyxl, python-pptx, pypdf) — те же,
что у читателя, но проверяется не библиотека, а ПУТЬ: файл → узлы → чанки → адрес.
"""

from __future__ import annotations

import atexit
import json
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import zipfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

PASS = 0
FAIL = 0

#: Реестр разработок возвращается в исходное состояние: `index` песочного корпуса
#: пишет запись в `catalog/catalog.json`, и она переживает удаление каталога.
CATALOG = REPO / "catalog" / "catalog.json"
_CATALOG_BYTES = CATALOG.read_bytes() if CATALOG.exists() else None


def _restore_registry() -> None:
    if _CATALOG_BYTES is None:
        return
    try:
        CATALOG.write_bytes(_CATALOG_BYTES)
    except OSError:
        pass


atexit.register(_restore_registry)


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


def run(args: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "tools/ragraf.py", *args],
        cwd=str(REPO), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


def as_json(result: subprocess.CompletedProcess) -> dict:
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


def make_docx(path: pathlib.Path) -> None:
    import docx

    document = docx.Document()
    document.add_heading("Раздел первый", level=1)
    document.add_paragraph("Абзац основного текста OFFICE-DOCX-ALPHA.")
    document.add_heading("Подраздел", level=2)
    document.add_paragraph("Второй абзац OFFICE-DOCX-BETA.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Колонка A"
    table.cell(0, 1).text = "Колонка B"
    table.cell(1, 0).text = "OFFICE-DOCX-TABLE"
    table.cell(1, 1).text = "значение"
    document.sections[0].header.paragraphs[0].text = "КОЛОНТИТУЛ-ВЕРХ"
    document.sections[0].footer.paragraphs[0].text = "КОЛОНТИТУЛ-НИЗ"
    document.save(str(path))

    # python-docx не умеет добавлять сноски, поэтому часть пакета дописывается
    # вручную. Читатель берёт сноски из `word/footnotes.xml`, а не через
    # библиотеку, поэтому такой фикстуры достаточно.
    footnotes = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:t>sep</w:t></w:r></w:p></w:footnote>'
        '<w:footnote w:id="3"><w:p><w:r><w:t>Сноска OFFICE-DOCX-FOOTNOTE</w:t></w:r></w:p></w:footnote>'
        "</w:footnotes>"
    ).encode("utf-8")
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("word/footnotes.xml", footnotes)


def make_xlsx(path: pathlib.Path) -> None:
    import openpyxl

    book = openpyxl.Workbook()
    first = book.active
    first.title = "Данные"
    first.append(["Колонка 1", "Колонка 2"])
    for row in range(1, 901):
        first.append([f"OFFICE-XLSX-ALPHA-{row}", row * 2])
    second = book.create_sheet("Итоги")
    second.append(["Итог", "OFFICE-XLSX-BETA"])
    book.save(str(path))


def make_pptx(path: pathlib.Path) -> None:
    from pptx import Presentation
    from pptx.util import Inches

    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = "Заголовок слайда"
    slide.placeholders[1].text = "Содержимое OFFICE-PPTX-ALPHA"
    slide.notes_slide.notes_text_frame.text = "Заметки докладчика OFFICE-PPTX-NOTES"
    second = deck.slides.add_slide(deck.slide_layouts[5])
    second.shapes.title.text = "Слайд с таблицей"
    shape = second.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(4), Inches(1))
    shape.table.cell(0, 0).text = "A"
    shape.table.cell(0, 1).text = "B"
    shape.table.cell(1, 0).text = "OFFICE-PPTX-TABLE"
    shape.table.cell(1, 1).text = "42"
    deck.save(str(path))


def make_pdf(path: pathlib.Path) -> None:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    def add_page(writer: PdfWriter, text: str) -> None:
        page = writer.add_blank_page(width=300, height=300)
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 20 200 Td ({text}) Tj ET".encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)})
        })

    writer = PdfWriter()
    add_page(writer, "OFFICE-PDF-FIRST")
    add_page(writer, "OFFICE-PDF-SECOND")
    with open(path, "wb") as handle:
        writer.write(handle)


def make_rtf(path: pathlib.Path) -> None:
    path.write_bytes(
        b"{\\rtf1\\ansi\\deff0 {\\fonttbl {\\f0 Arial;}}"
        b"\\f0\\fs20 RTF text OFFICE-RTF-GAMMA\\par}"
    )


with tempfile.TemporaryDirectory(prefix="ragraf-office-",
                                 ignore_cleanup_errors=True) as tmp:
    base = pathlib.Path(tmp)
    make_docx(base / "doc.docx")
    make_xlsx(base / "book.xlsx")
    make_pptx(base / "deck.pptx")
    make_pdf(base / "report.pdf")
    make_rtf(base / "note.rtf")
    (base / "mod.py").write_text("def office_marker():\n    return 'OFFICE-CODE'\n",
                                 encoding="utf-8")
    config = base / "corpus.yaml"

    print("=" * 78)
    print("1. INIT И INDEX")
    print("=" * 78)
    init = run(["init", str(base), "--name", "officeb", "--config", str(config)])
    check(init.returncode == 0, "init выполнен", init.stderr[-300:])
    if not config.exists():
        raise SystemExit(1)
    index = run(["-p", str(config), "index", "--no-vectors"])
    check(index.returncode == 0, "index выполнен",
          (index.stderr or index.stdout)[-600:])

    db = base / ".ragraf" / "index" / "lex" / "lexvec.sqlite"
    rev = ""
    nodes: list[dict] = []
    chunks: list[dict] = []
    if db.exists():
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        status = as_json(run(["--json", "-p", str(config), "query", "doc_status"]))
        rev = str(status.get("index_rev") or "")
        nodes = [dict(row) for row in conn.execute(
            "SELECT doc_id, kind, locator, text, structure_json, extraction_status"
            " FROM document_node WHERE rev_id = ?", (rev,)).fetchall()]
        chunks = [dict(row) for row in conn.execute(
            "SELECT chunk_id, path, locator, kind, text, tokens, part_of,"
            " structure_json, extraction_status FROM document_chunk"
            " WHERE rev_id = ?", (rev,)).fetchall()]
        conn.close()
    check(db.exists() and bool(rev), "хранилище и ревизия найдены", str(rev))

    def kinds_of(path_fragment: str) -> set[str]:
        return {node["kind"] for node in nodes
                if path_fragment in str(node["locator"])}

    def locators_of(path_fragment: str) -> list[str]:
        return [str(node["locator"]) for node in nodes
                if path_fragment in str(node["locator"])]

    print()
    print("=" * 78)
    print("2. DOCX: РАЗДЕЛЫ, ТАБЛИЦА, СНОСКА, КОЛОНТИТУЛ — РАЗНЫЕ УЗЛЫ")
    print("=" * 78)
    docx_kinds = kinds_of("doc.docx")
    check({"section", "paragraph", "table"} <= docx_kinds,
          "основной текст, заголовки и таблица — разные виды узлов", str(docx_kinds))
    docx_locators = locators_of("doc.docx")
    check(any((":table=" in loc) for loc in docx_locators),
          "таблица имеет собственный адрес", str(docx_locators[:6]))
    check(any("#footnote=" in loc for loc in docx_locators),
          "сноска имеет собственный адрес", str([l for l in docx_locators if "foot" in l]))
    check(any("#header=" in loc for loc in docx_locators),
          "верхний колонтитул — отдельный узел", str([l for l in docx_locators if "header" in l]))
    check(any("#footer=" in loc for loc in docx_locators),
          "нижний колонтитул — отдельный узел", str([l for l in docx_locators if "footer" in l]))
    footnote_text = " ".join(str(node["text"]) for node in nodes
                             if "#footnote=" in str(node["locator"]))
    check("OFFICE-DOCX-FOOTNOTE" in footnote_text, "текст сноски прочитан",
          footnote_text[:80])
    body_text = " ".join(str(node["text"]) for node in nodes
                         if str(node["locator"]).endswith("#para=1"))
    check("OFFICE-DOCX-ALPHA" in body_text and "Сноска" not in body_text,
          "абзац не смешан со сноской", body_text[:80])
    header_text = " ".join(str(node["text"]) for node in nodes
                           if "#header=" in str(node["locator"]))
    check("КОЛОНТИТУЛ-ВЕРХ" in header_text and "OFFICE-DOCX-BETA" not in header_text,
          "колонтитул не смешан с текстом", header_text[:80])
    table_chunk = next((chunk for chunk in chunks
                        if str(chunk["locator"]).endswith(":table=1")), None)
    check(table_chunk is not None, "таблица попала в чанки")
    if table_chunk is not None:
        structure = json.loads(table_chunk["structure_json"] or "{}")
        check(bool(structure.get("table_id")), "у таблицы есть table_id",
              str(structure.get("table_id")))

    print()
    print("=" * 78)
    print("3. АДРЕС РАЗРЕШАЕТСЯ: doc_get ПО path#para=N")
    print("=" * 78)
    para_locator = next((loc for loc in docx_locators
                         if loc.endswith("#para=1")), "")
    got = as_json(run(["--json", "-p", str(config), "query", "doc_get",
                       "--locator", para_locator]))
    text = str((got.get("result") or {}).get("text") or "")
    check(got.get("found") is True and "OFFICE-DOCX-ALPHA" in text,
          "абзац разрешён по адресу", text[:100])
    check(str((got.get("result") or {}).get("provenance", {}).get("reader")) == "docx",
          "провенанс называет читателя",
          str((got.get("result") or {}).get("provenance")))
    bad = as_json(run(["--json", "-p", str(config), "query", "doc_get",
                       "--locator", "doc.docx:12"]))
    check(bad.get("found") is False,
          "адрес-строки для DOCX отвергнут, а не разобран в мусор",
          str(bad.get("error"))[:120])

    print()
    print("=" * 78)
    print("4. XLSX: ЛИСТ — СУЩНОСТЬ, ЗАГОЛОВОК КОЛОНОК ПОВТОРЯЕТСЯ")
    print("=" * 78)
    sheet_node = next((node for node in nodes
                       if ":sheet=" in str(node["locator"])), None)
    check(sheet_node is not None, "лист — отдельный узел",
          str([l for l in locators_of("book.xlsx")][:3]))
    sheet_chunks = [chunk for chunk in chunks if ":sheet=" in str(chunk["locator"])]
    check(len(sheet_chunks) >= 2, "большой лист разрезан",
          f"частей {len(sheet_chunks)}")
    # Сравнение — ВНУТРИ ОДНОГО листа: у второго листа свои колонки, и требовать
    # от него «Колонка 1» значило бы проверять чужой лист.
    data_sheet = [chunk for chunk in sheet_chunks
                  if str(chunk["locator"]) == "book.xlsx:sheet=Данные"]
    repeated = [chunk for chunk in data_sheet if "Колонка 1" in str(chunk["text"])]
    check(bool(data_sheet) and len(repeated) == len(data_sheet),
          "заголовок колонок есть в каждой части листа",
          f"{len(repeated)} из {len(data_sheet)}")
    saved = [chunk for chunk in sheet_chunks if str(chunk["part_of"])]
    check(bool(saved), "у частей записана связь part_of", str(len(saved)))
    if saved:
        structure = json.loads(saved[0]["structure_json"] or "{}")
        check(bool(structure.get("table_id")), "у части листа есть table_id",
              str(structure.get("table_id")))
    sheet_locator = str((sheet_node or {}).get("locator") or "")
    sheet_get = as_json(run(["--json", "-p", str(config), "query", "doc_get",
                             "--locator", sheet_locator]))
    sheet_text = str((sheet_get.get("result") or {}).get("text") or "")
    check(sheet_get.get("found") is True and "OFFICE-XLSX-ALPHA-1" in sheet_text,
          "лист разрешён по адресу", sheet_text[:80])

    print()
    print("=" * 78)
    print("5. PPTX: СЛАЙД И ЗАМЕТКИ РАЗДЕЛЕНЫ, ТАБЛИЦА ОТДЕЛЬНО")
    print("=" * 78)
    slide_locators = locators_of("deck.pptx")
    check("deck.pptx:slide=1" in slide_locators, "адрес слайда по номеру",
          str(slide_locators[:5]))
    check(any(loc.endswith("slide=1#notes") for loc in slide_locators),
          "заметки — отдельный узел с адресом #notes", str(slide_locators[:6]))
    slide_chunk = next((chunk for chunk in chunks
                        if str(chunk["locator"]) == "deck.pptx:slide=1"), None)
    check(slide_chunk is not None and "OFFICE-PPTX-NOTES" not in str(slide_chunk["text"]),
          "текст заметок не попал в чанк слайда",
          str((slide_chunk or {}).get("text"))[:100])
    check(any("slide=2#table=1" in loc for loc in slide_locators),
          "таблица на слайде — отдельный узел",
          str([l for l in slide_locators if "table" in l]))

    print()
    print("=" * 78)
    print("6. PDF: СТРАНИЦА КАК АДРЕС")
    print("=" * 78)
    pdf_locators = locators_of("report.pdf")
    check("report.pdf:page=1" in pdf_locators and "report.pdf:page=2" in pdf_locators,
          "страницы PDF адресованы номером", str(pdf_locators))
    pdf_get = as_json(run(["--json", "-p", str(config), "query", "doc_get",
                           "--locator", "report.pdf:page=2"]))
    pdf_text = str((pdf_get.get("result") or {}).get("text") or "")
    check("OFFICE-PDF-SECOND" in pdf_text, "страница 2 разрешена по адресу",
          pdf_text[:80])

    print()
    print("=" * 78)
    print("7. LEGACY: ТЕКСТ ЕСТЬ, СТРУКТУРЫ НЕТ — И ЭТО НАЗВАНО")
    print("=" * 78)
    rtf_nodes = [node for node in nodes if "note.rtf" in str(node["locator"])]
    check(bool(rtf_nodes), "RTF разобран", str(len(rtf_nodes)))
    if rtf_nodes:
        check(str(rtf_nodes[0]["locator"]) == "note.rtf",
              "адрес legacy-документа — файл целиком", str(rtf_nodes[0]["locator"]))
        check(str(rtf_nodes[0]["extraction_status"]) == "degraded",
              "статус degraded, а не ok", str(rtf_nodes[0]["extraction_status"]))
    rtf_text = " ".join(str(node["text"]) for node in rtf_nodes)
    check("OFFICE-RTF-GAMMA" in rtf_text, "текст RTF извлечён", rtf_text[:80])

    print()
    print("=" * 78)
    print("8. ВАЛИДАТОР: ПРОВЕРКА 5 НЕ ПУСТАЯ")
    print("=" * 78)
    verdict = as_json(run(["--json", "-p", str(config), "query", "doc_validate"]))
    data = verdict.get("result") or {}
    check(data.get("ok") is True, "валидатор прошёл на честном индексе",
          str((data.get("problems") or [])[:2]))
    if db.exists() and rev:
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT OR REPLACE INTO document_chunk(rev_id, chunk_id, doc_id, path,"
            " locator, kind, text, tokens, part_of, structure_json,"
            " provenance_json, extraction_status, vector_chars)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rev, "sandbox-notes-in-slide", "sandbox", "deck.pptx",
             "deck.pptx:slide=1", "slide", "заметки внутри слайда", 9, "",
             json.dumps({"from_notes": True}, ensure_ascii=False), "{}", "ok", 9),
        )
        conn.commit()
        conn.close()
        broken = as_json(run(["--json", "-p", str(config), "query", "doc_validate"]))
        problems = (broken.get("result") or {}).get("problems") or []
        check(any(int(item.get("check", 0)) == 5 for item in problems),
              "заметки внутри слайда пойманы проверкой 5", str(problems[:1]))
        conn = sqlite3.connect(str(db))
        conn.execute("DELETE FROM document_chunk WHERE chunk_id = 'sandbox-notes-in-slide'")
        conn.commit()
        conn.close()
        clean = as_json(run(["--json", "-p", str(config), "query", "doc_validate"]))
        check((clean.get("result") or {}).get("ok") is True,
              "после удаления строки вердикт снова положительный",
              str((clean.get("result") or {}).get("problems")))

    print()
    print("=" * 78)
    print("9. КОДОВЫЙ ИНДЕКС НЕ РЕГРЕССИРОВАЛ")
    print("=" * 78)
    code_hit = as_json(run(["--json", "-p", str(config), "query", "search",
                            "office_marker", "--limit", "5"]))
    hits = code_hit.get("result") or []
    check(any("mod.py" in str(item.get("path")) for item in hits),
          "поиск по коду находит символ", str([h.get("path") for h in hits][:3]))

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
