"""Проверка документного слоя, фаза C: данные, почта, EPUB.

Свойства (гейт фазы, каждое — отдельная проверка):

  1. CSV: **ни одна** запись не индексируется без имён колонок — и в целом узле,
     и в каждой части разрезанного; разделитель `;` и кодировка cp1251 читаются.
  2. JSON/JSONL/YAML/TOML/XML: у каждого фрагмента есть адрес в структуре
     (`parent_path` либо сам адрес-путь), и адрес разрешается обратно.
  3. Почта: тема/адреса, тело и цитата — разные узлы; вложение — отдельный узел
     со ссылкой на письмо.
  4. EPUB: главы разделены, абзац главы разрешается по адресу.
  5. Валидатор НЕ пустой: подсаженная запись CSV без колонок ловится проверкой 4,
     фрагмент данных без `parent_path` — проверкой 6; после удаления строк
     вердикт снова положительный.
  6. Кодовый индекс не регрессировал.

Фикстуры создаются кодом в песочнице; реестр разработок восстанавливается.
"""

from __future__ import annotations

import atexit
import email.message
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


def make_csv(path: pathlib.Path) -> None:
    """CSV с `;`, cp1251 и длинными строками: узел обязан разрезаться."""
    long_value = "значение-с-длинным-текстом-" * 8
    lines = ["Номер;Наименование;Комментарий"]
    for row in range(1, 121):
        lines.append(f"{row};OFFICE-CSV-ALPHA-{row};{long_value}")
    path.write_bytes("\r\n".join(lines).encode("cp1251"))


def make_tsv(path: pathlib.Path) -> None:
    lines = ["id\tname\tnote"]
    for row in range(1, 6):
        lines.append(f"{row}\tOFFICE-TSV-BETA-{row}\tзаметка")
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8-sig"))


def make_json(path: pathlib.Path) -> None:
    data = {
        "meta": {"count": 2, "kind": "test"},
        "items": [
            {"id": 1, "name": "OFFICE-JSON-ALPHA"},
            {"id": 2, "name": "OFFICE-JSON-BETA"},
        ],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def make_jsonl(path: pathlib.Path) -> None:
    lines = [json.dumps({"id": number, "name": f"OFFICE-JSONL-{number}"},
                        ensure_ascii=False) for number in range(1, 4)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_xml(path: pathlib.Path) -> None:
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<root xmlns:t="urn:test"><item id="1"><name>OFFICE-XML-ALPHA</name></item>'
        '<item id="2"><name>OFFICE-XML-BETA</name></item></root>',
        encoding="utf-8",
    )


def make_yaml(path: pathlib.Path) -> None:
    path.write_text("meta:\n  kind: test\nname: OFFICE-YAML-ALPHA\n", encoding="utf-8")


def make_toml(path: pathlib.Path) -> None:
    path.write_text('[section]\nname = "OFFICE-TOML-ALPHA"\ncount = 3\n', encoding="utf-8")


def make_eml(path: pathlib.Path) -> None:
    message = email.message.EmailMessage()
    message["Subject"] = "OFFICE-EML-SUBJECT"
    message["From"] = "sender@example.test"
    message["To"] = "recipient@example.test"
    message["Date"] = "Thu, 29 Oct 2026 10:00:00 +0300"
    message.set_content(
        "Тело письма OFFICE-EML-BODY.\n\n> Цитата предыдущего письма OFFICE-EML-QUOTE\n"
    )
    message.add_attachment(
        b"attachment-bytes", maintype="application", subtype="octet-stream",
        filename="OFFICE-EML-ATTACHMENT.bin",
    )
    path.write_bytes(bytes(message))


def make_epub(path: pathlib.Path) -> None:
    container = (
        '<?xml version="1.0"?>'
        '<container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    opf = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title>OFFICE-EPUB-TITLE</dc:title></metadata>"
        '<manifest>'
        '<item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="c2" href="ch2.xhtml" media-type="application/xhtml+xml"/>'
        "</manifest>"
        '<spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>'
    )
    chapter1 = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>'
        "<h1>Глава первая</h1><p>Текст OFFICE-EPUB-ALPHA.</p>"
        "<p>Второй абзац главы.</p></body></html>"
    )
    chapter2 = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>'
        "<h1>Глава вторая</h1><p>Текст OFFICE-EPUB-BETA.</p></body></html>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/ch1.xhtml", chapter1)
        archive.writestr("OEBPS/ch2.xhtml", chapter2)


with tempfile.TemporaryDirectory(prefix="ragraf-data-",
                                 ignore_cleanup_errors=True) as tmp:
    base = pathlib.Path(tmp)
    make_csv(base / "table.csv")
    make_tsv(base / "small.tsv")
    make_json(base / "data.json")
    make_jsonl(base / "lines.jsonl")
    make_xml(base / "data.xml")
    make_yaml(base / "conf.yaml")
    make_toml(base / "conf.toml")
    make_eml(base / "letter.eml")
    make_epub(base / "book.epub")
    (base / "mod.py").write_text("def data_marker():\n    return 'OFFICE-DATA-CODE'\n",
                                 encoding="utf-8")
    config = base / "corpus.yaml"

    print("=" * 78)
    print("1. INIT И INDEX")
    print("=" * 78)
    init = run(["init", str(base), "--name", "datac", "--config", str(config)])
    check(init.returncode == 0, "init выполнен", init.stderr[-300:])
    if not config.exists():
        raise SystemExit(1)
    index = run(["-p", str(config), "index", "--no-vectors"])
    check(index.returncode == 0, "index выполнен",
          (index.stderr or index.stdout)[-800:])

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
            "SELECT kind, locator, text, structure_json, extraction_status"
            " FROM document_node WHERE rev_id = ?", (rev,)).fetchall()]
        chunks = [dict(row) for row in conn.execute(
            "SELECT chunk_id, path, locator, kind, text, part_of, structure_json,"
            " extraction_status FROM document_chunk WHERE rev_id = ?",
            (rev,)).fetchall()]
        conn.close()
    check(db.exists() and bool(rev), "хранилище и ревизия найдены", str(rev))

    by_path = lambda fragment: [chunk for chunk in chunks  # noqa: E731
                                if fragment in str(chunk["path"])]

    print()
    print("=" * 78)
    print("2. CSV: КОЛОНКИ В КАЖДОЙ ЗАПИСИ, РАЗДЕЛИТЕЛЬ `;`, КОДИРОВКА CP1251")
    print("=" * 78)
    csv_chunks = by_path("table.csv")
    check(bool(csv_chunks), "CSV попал в чанки", str(len(csv_chunks)))
    with_columns = [chunk for chunk in csv_chunks if "Наименование" in str(chunk["text"])]
    check(len(with_columns) == len(csv_chunks),
          "имя колонки есть в КАЖДОЙ записи",
          f"{len(with_columns)} из {len(csv_chunks)}")
    structures = [json.loads(chunk["structure_json"] or "{}") for chunk in csv_chunks]
    check(all(str(item.get("columns") or "").strip() for item in structures),
          "у каждой записи в структуре есть columns",
          str(structures[0].get("columns"))[:60])
    check(any(str(item.get("header_repeated")) == "True" for item in structures),
          "разрезанный узел повторяет заголовок в частях",
          str([item.get("header_repeated") for item in structures]))
    check("OFFICE-CSV-ALPHA-1" in " ".join(str(chunk["text"]) for chunk in csv_chunks),
          "текст CSV прочитан (cp1251 разобран)")
    tsv_chunks = by_path("small.tsv")
    check(bool(tsv_chunks) and "name" in str(tsv_chunks[0]["text"]),
          "TSV с BOM разобран", str((tsv_chunks or [{}])[0].get("text"))[:60])

    print()
    print("=" * 78)
    print("3. СТРУКТУРЫ: АДРЕС В ДАННЫХ И ЕГО РАЗРЕШЕНИЕ")
    print("=" * 78)
    for fragment, marker, check_no in (("data.json", "OFFICE-JSON-ALPHA", 6),
                                       ("data.xml", "OFFICE-XML-ALPHA", 6),
                                       ("conf.yaml", "OFFICE-YAML-ALPHA", 6),
                                       ("conf.toml", "OFFICE-TOML-ALPHA", 6),
                                       ("lines.jsonl", "OFFICE-JSONL-1", 6)):
        items = by_path(fragment)
        found = [chunk for chunk in items if marker in str(chunk["text"])]
        check(bool(found), f"{fragment}: маркер найден в узле", str(len(items)))
        if found:
            structure = json.loads(found[0]["structure_json"] or "{}")
            parent = str(structure.get("parent_path") or "")
            parseable = False
            try:
                json.loads(str(found[0]["text"]))
                parseable = True
            except (ValueError, TypeError):
                parseable = False
            check(bool(parent) or parseable or "#/" in str(found[0]["locator"]),
                  f"{fragment}: у фрагмента есть адрес в структуре",
                  f"parent={parent!r} locator={found[0]['locator']}")
            got = as_json(run(["--json", "-p", str(config), "query", "doc_get",
                               "--locator", str(found[0]["locator"])]))
            check(got.get("found") is True and marker in str(
                (got.get("result") or {}).get("text") or ""),
                  f"{fragment}: адрес разрешается обратно",
                  str(found[0]["locator"])[:60])

    print()
    print("=" * 78)
    print("4. ПОЧТА: ТЕМА, ТЕЛО, ЦИТАТА, ВЛОЖЕНИЕ — РАЗНЫЕ УЗЛЫ")
    print("=" * 78)
    mail_nodes = [node for node in nodes if "letter.eml" in str(node["locator"])]
    locators = [str(node["locator"]) for node in mail_nodes]
    check(any(loc.endswith("#message") for loc in locators),
          "тема и адреса — отдельный узел", str(locators))
    body = " ".join(str(node["text"]) for node in mail_nodes
                    if "#quote" not in str(node["locator"])
                    and not str(node["locator"]).endswith("#message"))
    check("OFFICE-EML-BODY" in body, "тело письма прочитано", body[:80])
    quotes = " ".join(str(node["text"]) for node in mail_nodes
                      if "#quote=" in str(node["locator"]))
    check("OFFICE-EML-QUOTE" in quotes, "цитата — отдельный узел", quotes[:80])
    check("OFFICE-EML-QUOTE" not in body, "цитата не попала в тело")
    attachments = [node for node in mail_nodes
                   if "OFFICE-EML-ATTACHMENT" in str(node["text"])]
    check(bool(attachments), "вложение названо отдельным узлом",
          str([n["text"][:40] for n in attachments]))
    if attachments:
        structure = json.loads(attachments[0]["structure_json"] or "{}")
        check(str(structure.get("parent_document")) == "letter.eml",
              "у вложения записана ссылка на письмо", str(structure.get("parent_document")))

    print()
    print("=" * 78)
    print("5. EPUB: ГЛАВЫ И АБЗАЦЫ")
    print("=" * 78)
    epub_nodes = [node for node in nodes if "book.epub" in str(node["locator"])]
    epub_locators = [str(node["locator"]) for node in epub_nodes]
    check("book.epub#chapter=1" in epub_locators and "book.epub#chapter=2" in epub_locators,
          "главы разделены", str(epub_locators[:4]))
    para = next((loc for loc in epub_locators if "#chapter=1#para=1" in loc), "")
    check(bool(para), "абзац главы адресован", str(epub_locators[:5]))
    if para:
        got = as_json(run(["--json", "-p", str(config), "query", "doc_get",
                           "--locator", para]))
        check("OFFICE-EPUB-ALPHA" in str((got.get("result") or {}).get("text") or ""),
              "абзац главы разрешается по адресу", para)

    print()
    print("=" * 78)
    print("6. ВАЛИДАТОР: ПРОВЕРКИ 4 И 6 НЕ ПУСТЫЕ")
    print("=" * 78)
    verdict = as_json(run(["--json", "-p", str(config), "query", "doc_validate"]))
    data = verdict.get("result") or {}
    check(data.get("ok") is True, "валидатор прошёл на честном индексе",
          str((data.get("problems") or [])[:2]))
    if db.exists() and rev:
        conn = sqlite3.connect(str(db))
        for chunk_id, path, locator, kind, text, structure in (
            ("sandbox-csv-no-columns", "table.csv", "table.csv:rows=2-41", "record",
             "1 | 2 | 3", {"parent_path": "/"}),
            # Адрес без пути в структуре и без xpath: именно такой фрагмент
            # проверка 6 обязана назвать неполным.
            ("sandbox-fragment", "data.json", "data.json:row=5", "record",
             "просто текст", {}),
        ):
            conn.execute(
                "INSERT OR REPLACE INTO document_chunk(rev_id, chunk_id, doc_id, path,"
                " locator, kind, text, tokens, part_of, structure_json,"
                " provenance_json, extraction_status, vector_chars)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rev, chunk_id, "sandbox", path, locator, kind, text, 6, "",
                 json.dumps(structure, ensure_ascii=False), "{}", "ok", 6),
            )
        conn.commit()
        conn.close()
        broken = as_json(run(["--json", "-p", str(config), "query", "doc_validate"]))
        problems = (broken.get("result") or {}).get("problems") or []
        check(any(int(item.get("check", 0)) == 4 for item in problems),
              "запись CSV без колонок поймана проверкой 4", str(problems[:1]))
        check(any(int(item.get("check", 0)) == 6 for item in problems),
              "фрагмент без parent_path пойман проверкой 6", str(problems[:2]))
        conn = sqlite3.connect(str(db))
        conn.execute("DELETE FROM document_chunk WHERE chunk_id LIKE 'sandbox-%'")
        conn.commit()
        conn.close()
        clean = as_json(run(["--json", "-p", str(config), "query", "doc_validate"]))
        check((clean.get("result") or {}).get("ok") is True,
              "после удаления строк вердикт снова положительный",
              str((clean.get("result") or {}).get("problems")))

    print()
    print("=" * 78)
    print("7. КОДОВЫЙ ИНДЕКС НЕ РЕГРЕССИРОВАЛ")
    print("=" * 78)
    code_hit = as_json(run(["--json", "-p", str(config), "query", "search",
                            "data_marker", "--limit", "5"]))
    hits = code_hit.get("result") or []
    check(any("mod.py" in str(item.get("path")) for item in hits),
          "поиск по коду находит символ", str([h.get("path") for h in hits][:3]))

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
