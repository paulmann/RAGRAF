"""Разбор старых и новых документов: Word, Excel, PowerPoint, ODF, EPUB, почта.

Что закрепляется
    * `.doc` (Word 97) и `.xls` (BIFF8) разбираются собственным читателем
      контейнера OLE и дают текст. Проверка идёт на РЕАЛЬНЫХ файлах этой
      машины, если они есть; если файлов нет — честно пропускается, а не
      объявляется успехом.
    * битый контейнер даёт `OfficeError` с причиной, а не пустой текст:
      «документ без текста» и «разбор не удался» — разные состояния.
    * новые варианты OOXML (.docm, .dotx, .xlsm, .pptm) читаются, хотя
      python-docx/openpyxl их не открывают.
    * форматы без разборщика (Pages, OneNote, CHM, DjVu, .xlsb) возвращают
      `unavailable` с причиной, а не притворяются пустым документом.

Почему проверка на реальных файлах важна
    Синтетический .doc создать нечем: Word 97 — это OLE-контейнер с таблицей
    фрагментов. Первый прогон на настоящем файле сразу показал дефект в .xls:
    разбор останавливался на первом EOF (конец глобальной части книги) и
    возвращал «только формулы» на книге с данными. Синтетика этого не показала.

Запуск: python tests/test_office_binary.py
"""

from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile
import zipfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import documents, officebinary  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


def skip(label: str, why: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [--] {label}: {why}")


print("=" * 78)
print("1. КОНТЕЙНЕР OLE: ОТКАЗ ВМЕСТО ПУСТОГО ТЕКСТА")
print("=" * 78)

for label, reader in (("Word", officebinary.word_text),
                      ("Excel", officebinary.excel_text),
                      ("PowerPoint", officebinary.powerpoint_text)):
    try:
        reader(b"<html>not a document at all</html>" * 40)
        check(False, f"{label}: мусор отвергнут", "исключения не было")
    except officebinary.OfficeError as exc:
        check("OLE" in str(exc), f"{label}: мусор отвергнут с причиной", str(exc))
    except Exception as exc:  # noqa: BLE001
        check(False, f"{label}: мусор отвергнут", f"чужое исключение {type(exc).__name__}")

print()
print("=" * 78)
print("2. РЕАЛЬНЫЕ ФАЙЛЫ: WORD 97 И BIFF8")
print("=" * 78)

real_doc = pathlib.Path(__file__).resolve().parents[1].parent / "1/BCT-M 600_600.doc"
real_xls = pathlib.Path(__file__).resolve().parents[1].parent / "a.batteryservice.ru/backup/mail/import/Letters.xls"

if real_doc.exists():
    got = documents.extract(real_doc)
    check(got.status == "ok", "Word 97: документ разобран", got.reason)
    check(len(got.text) > 1000, "Word 97: текст непустой",
          f"символов {len(got.text)}")
    check("BCT" in got.text, "Word 97: заголовок документа найден")
    check("таблица фрагментов" in got.reason,
          "Word 97: использована таблица фрагментов, а не запасной путь",
          got.reason)
    check(got.kind == "doc", "Word 97: вид определён как doc", got.kind)
else:
    skip("Word 97", f"нет файла {real_doc}")

if real_xls.exists():
    got = documents.extract(real_xls)
    check(got.status == "ok", "Excel BIFF8: книга разобрана", got.reason)
    check("непустых строк" in got.reason, "Excel BIFF8: строки посчитаны", got.reason)
    rows = int(got.reason.split("непустых строк")[1].split(";")[0].strip() or 0)
    check(rows > 5, "Excel BIFF8: прочитано больше пяти строк",
          f"получено {rows}")
    check("Client" in got.text, "Excel BIFF8: заголовок столбца найден")
else:
    skip("Excel BIFF8", f"нет файла {real_xls}")

print()
print("=" * 78)
print("2a. РЕАЛЬНАЯ ПРЕЗЕНТАЦИЯ POWERPOINT 97 (.ppt)")
print("=" * 78)

# Путь вне репозитория: файл принадлежит владельцу, а не проекту. Если его нет —
# раздел пропускается, а не объявляется успехом.
real_ppt = pathlib.Path(
    str(pathlib.Path.home() / "OneDrive/Документы/Консалтинг.ppt")
)
if real_ppt.exists():
    got = documents.extract(real_ppt)
    check(got.status == "ok", "PowerPoint 97: презентация разобрана", got.reason)
    lines = [line for line in got.text.splitlines() if line.strip()]
    check(len(lines) > 50, "PowerPoint 97: текста больше пятидесяти строк",
          f"получено {len(lines)}")
    cyrillic = [line for line in lines if any("а" <= ch.lower() <= "я" for ch in line)]
    check(len(cyrillic) > 50, "PowerPoint 97: текст на языке документа",
          f"строк с кириллицей {len(cyrillic)}")
    check("--- slide 1 ---" in got.text, "PowerPoint 97: маркер слайда есть")
    check(got.text.count("--- slide ") >= 10,
          "PowerPoint 97: слайдов не меньше десяти",
          f"маркеров {got.text.count('--- slide ')}")
    # Заглушки шаблона — служебный текст образца слайда, а не содержимое.
    placeholders = ("Click to edit Master", "Второй уровень", "Пятый уровень",
                    "Щелкните, чтобы изменить")
    check(not any(word in got.text for word in placeholders),
          "PowerPoint 97: заглушки шаблона отброшены")
    # Порядок: маркер слайда стоит ПЕРЕД его текстом, а не отдельным списком.
    first_marker = got.text.index("--- slide 1 ---")
    second_marker = got.text.index("--- slide 2 ---")
    check(first_marker < second_marker, "PowerPoint 97: маркеры идут по порядку")
    between = got.text[first_marker:second_marker]
    check(len(between.splitlines()) > 2,
          "PowerPoint 97: текст слайда стоит после его маркера",
          f"между маркерами строк {len(between.splitlines())}")
    check("текстовых атомов не найдено" not in got.reason,
          "PowerPoint 97: атомы текста найдены", got.reason)
else:
    skip("PowerPoint 97", f"нет файла {real_ppt}")

print()
print("=" * 78)
print("3. ВАРИАНТЫ OOXML, ODF И EPUB")
print("=" * 78)

tmp = pathlib.Path(tempfile.mkdtemp(prefix="ragraf_docs_"))
try:
    try:
        import docx

        d = docx.Document()
        d.add_paragraph("HELLO-DOCX")
        d.save(str(tmp / "a.docx"))
        check("HELLO-DOCX" in documents.extract(tmp / "a.docx").text,
              ".docx: текст извлечён")
        shutil.copy(tmp / "a.docx", tmp / "a.docm")
        got = documents.extract(tmp / "a.docm")
        check("HELLO-DOCX" in got.text and got.status == "ok",
              ".docm: текст извлечён собственным разбором OOXML", got.reason)
        shutil.copy(tmp / "a.docx", tmp / "a.dotx")
        check("HELLO-DOCX" in documents.extract(tmp / "a.dotx").text,
              ".dotx: текст извлечён")
    except ImportError:
        skip("Word OOXML", "python-docx не установлен")

    try:
        import openpyxl

        book = openpyxl.Workbook()
        book.active.append(["HELLO-XLSX", 42])
        book.save(str(tmp / "a.xlsx"))
        check("HELLO-XLSX" in documents.extract(tmp / "a.xlsx").text,
              ".xlsx: значения извлечены")
        shutil.copy(tmp / "a.xlsx", tmp / "a.xlsm")
        check("HELLO-XLSX" in documents.extract(tmp / "a.xlsm").text,
              ".xlsm: значения извлечены")
    except ImportError:
        skip("Excel OOXML", "openpyxl не установлен")

    try:
        from pptx import Presentation
        from pptx.util import Inches

        deck = Presentation()
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(1))
        box.text_frame.text = "HELLO-PPTX"
        deck.save(str(tmp / "a.pptx"))
        check("HELLO-PPTX" in documents.extract(tmp / "a.pptx").text,
              ".pptx: текст слайда извлечён")
        shutil.copy(tmp / "a.pptx", tmp / "a.ppsx")
        check("HELLO-PPTX" in documents.extract(tmp / "a.ppsx").text,
              ".ppsx: текст извлечён")
    except ImportError:
        skip("PowerPoint OOXML", "python-pptx не установлен")

    # ODF: контейнер собирается вручную — так проверяется именно наш разбор.
    for suffix, inner, marker in (
        (".odt", "<text:p>HELLO-ODT</text:p>", "HELLO-ODT"),
        (".ods", "<table:table><table:table-row><table:table-cell><text:p>"
                 "HELLO-ODS</text:p></table:table-cell></table:table-row>"
                 "</table:table>", "HELLO-ODS"),
        (".odp", "<text:p>HELLO-ODP</text:p>", "HELLO-ODP"),
        (".odg", "<text:p>HELLO-ODG</text:p>", "HELLO-ODG"),
    ):
        target = tmp / f"a{suffix}"
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr(
                "content.xml",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<office:document-content '
                'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
                'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">'
                f"<office:body><office:text>{inner}</office:text></office:body>"
                "</office:document-content>",
            )
        got = documents.extract(target)
        check(marker in got.text, f"{suffix}: текст извлечён", got.reason)

    epub = tmp / "a.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf"/>'
            "</rootfiles></container>",
        )
        archive.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
            'version="2.0"><manifest>'
            '<item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
            "</manifest><spine><itemref idref=\"c1\"/></spine></package>",
        )
        archive.writestr("OEBPS/ch1.xhtml",
                         "<html><body><p>HELLO-EPUB</p></body></html>")
    check("HELLO-EPUB" in documents.extract(epub).text, ".epub: текст извлечён")

    (tmp / "a.eml").write_bytes(
        b"Subject: HELLO-EML\r\nFrom: a@b.c\r\nMIME-Version: 1.0\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\nbody HELLO-EML\r\n"
    )
    check("HELLO-EML" in documents.extract(tmp / "a.eml").text,
          ".eml: письмо разобрано")

    (tmp / "a.rtf").write_bytes(
        b"{\\rtf1\\ansi{\\fonttbl{\\f0 Arial;}}\\f0 HELLO-RTF\\par }"
    )
    check("HELLO-RTF" in documents.extract(tmp / "a.rtf").text, ".rtf: текст извлечён")

    print()
    print("=" * 78)
    print("4. ФОРМАТЫ БЕЗ РАЗБОРЩИКА: ПРИЧИНА, А НЕ ПУСТОТА")
    print("=" * 78)

    for suffix in (".pages", ".numbers", ".key", ".one", ".chm", ".djvu", ".xlsb"):
        target = tmp / f"a{suffix}"
        target.write_bytes(b"0" * 128)
        got = documents.extract(target)
        check(got.status == "unavailable" and bool(got.reason),
              f"{suffix}: состояние unavailable с причиной",
              f"{got.status} / {got.reason}")

    print()
    print("=" * 78)
    print("5. ДОСТУПНОСТЬ ПУТЕЙ")
    print("=" * 78)

    available = documents.available()
    for key in ("ooxml", "odf", "epub", "msg", "legacy_doc", "legacy_xls",
                "legacy_ppt"):
        check(available.get(key) is True,
              f"путь «{key}» не зависит от установленных пакетов")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print(f"ИТОГ: {PASS} пройдено, {FAIL} провалено, {SKIP} пропущено")
sys.exit(1 if FAIL else 0)
