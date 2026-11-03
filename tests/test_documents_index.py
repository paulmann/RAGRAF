"""Сквозная проверка документов в индексе: PDF, XLSX, ODT.

Проверяется не извлекатель сам по себе, а весь путь:
`init` → `index` → `search` → `live_read`.

Свойства:
  1. маркер из PDF, XLSX и ODT находится поиском с путём документа;
  2. `live_read` документа отдаёт тот же извлечённый текст и вид извлечения;
  3. строки документа — это строки извлечённого текста, а не байты файла.
"""

from __future__ import annotations

import atexit
import json
import pathlib
import subprocess
import sys
import tempfile
import zipfile

REPO = pathlib.Path(__file__).resolve().parents[1]
PASS = 0
FAIL = 0

# Реестр разработок восстанавливается после прогона: `index` песочного корпуса
# пишет его в `catalog/catalog.json`, и запись переживает удаление каталога.
# Измерено 28.10.2026: запись `docidx` от такого прогона осталась в реестре и
# перестала соответствовать чему-либо на диске.
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


def run(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "tools/ragraf.py", *args],
        cwd=str(REPO), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


def make_pdf(path: pathlib.Path, label: str) -> None:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 20 100 Td ({label}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})
    })
    with open(path, "wb") as handle:
        writer.write(handle)


def make_xlsx(path: pathlib.Path, label: str) -> None:
    import openpyxl

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Данные"
    sheet.append([label, 42])
    book.save(str(path))


def make_odt(path: pathlib.Path, label: str) -> None:
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
        ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        f'<office:body><office:text><text:p>{label}</text:p></office:text></office:body>'
        '</office:document-content>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        archive.writestr("content.xml", content)


with tempfile.TemporaryDirectory(prefix="ragraf-docidx-") as tmp:
    base = pathlib.Path(tmp)
    for index in range(3):
        (base / f"m{index}.py").write_text(
            f"def f{index}():\n    return {index}\n", encoding="utf-8"
        )
    make_pdf(base / "text.pdf", "HELLO-PDF-IDX")
    make_xlsx(base / "data.xlsx", "HELLO-XLSX-IDX")
    make_odt(base / "notes.odt", "HELLO-ODT-IDX")
    config = base / "corpus.yaml"

    print("=" * 78)
    print("1. INIT И INDEX")
    print("=" * 78)
    init = run(["init", str(base), "--name", "docidx", "--config", str(config)])
    check(init.returncode == 0, "init выполнен", init.stderr[-300:])
    check(config.exists(), "конфиг создан")
    if not config.exists():
        raise SystemExit(1)
    text = config.read_text(encoding="utf-8")
    check("document" in text, "язык document включён в конфиг")

    index = run(["-p", str(config), "index", "--no-vectors"])
    check(index.returncode == 0, "index выполнен", (index.stderr or index.stdout)[-400:])

    print()
    print("=" * 78)
    print("2. ПОИСК МАРКЕРОВ")
    print("=" * 78)
    for label, filename in (("HELLO-PDF-IDX", "text.pdf"),
                            ("HELLO-XLSX-IDX", "data.xlsx"),
                            ("HELLO-ODT-IDX", "notes.odt")):
        # Вид чанка документа — `doc`, а НЕ `file`: так его записывает
        # документный слой (`source_kind="doc"`), и именно по этому признаку
        # `doc_search` отличает документ от кода. Прежняя редакция проверки
        # спрашивала `--kind file` и объявляла найденный текст потерянным —
        # дефект был в проверке, а не в индексе (замер 29.10.2026: тот же
        # маркер находится с `--kind doc`).
        found = run(["--json", "-p", str(config), "query", "search", label,
                     "--kind", "doc", "--limit", "10"])
        try:
            data = json.loads(found.stdout)
        except json.JSONDecodeError:
            check(False, f"{filename}: ответ разобран", found.stdout[:200])
            continue
        paths = [str(item.get("path")) for item in (data.get("result") or [])]
        check(filename in paths, f"{filename}: маркер найден поиском", str(paths[:5]))
        # И обратное свойство: вид `file` эти чанки НЕ отдаёт. Иначе фильтр
        # по виду перестал бы различать документ и код, и разница между
        # `search` и `doc_search` исчезла бы.
        other = run(["--json", "-p", str(config), "query", "search", label,
                     "--kind", "file", "--limit", "10"])
        try:
            other_paths = [str(item.get("path")) for item in
                           (json.loads(other.stdout).get("result") or [])]
        except json.JSONDecodeError:
            other_paths = []
        check(filename not in other_paths,
              f"{filename}: вид file не подменяет документ",
              str(other_paths[:3]))

    print()
    print("=" * 78)
    print("3. LIVE_READ: ТОТ ЖЕ ТЕКСТ И ВИД ИЗВЛЕЧЕНИЯ")
    print("=" * 78)
    live = run(["--json", "-p", str(config), "query", "live_read", "--path", "text.pdf"])
    try:
        data = json.loads(live.stdout)
    except json.JSONDecodeError:
        check(False, "live_read: ответ разобран", live.stdout[:200])
        data = {}
    check("HELLO-PDF-IDX" in str(data.get("result", {}).get("text", "")),
          "live_read вернул текст PDF")
    check(data.get("extract_kind") == "pdf", "вид извлечения назван",
          str(data.get("extract_kind")))
    check("--- page 1 ---" in str(data.get("result", {}).get("text", "")),
          "маркер страницы сохранён в live_read")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
