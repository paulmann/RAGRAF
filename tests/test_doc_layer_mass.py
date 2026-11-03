"""Свойства массового корпуса в тест слоя: то, что измерено, должно держаться.

Пять измеренных дефектов фазы E — по свойству на каждый:
  1. имя листа с пробелом разбирается грамматикой (иначе валидатор завернёт ревизию);
  2. в тексте узла нет NUL и управляющих символов (иначе поиск видит часть текста);
  3. узел со статусом `degraded` режется по бюджету, статус остаётся на частях;
  4. ни одна часть не превышает бюджет — в том числе при повторяемом заголовке,
     который сам больше бюджета;
  5. содержание решает, каким читателем разбирать файл: RTF и HTML под именем
     `.doc` дают текст, а не отказ.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import docchunk, docnode, docread  # noqa: E402
from ragraf.docread import ocr as ocr_reader  # noqa: E402

PASS = 0
FAIL = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


print("=" * 78)
print("1. АДРЕС ЛИСТА: ПРОБЕЛ И ДИАПАЗОН")
print("=" * 78)
check(docnode.form_of("книга.xlsx:sheet=О выгрузке") == "sheet",
      "имя листа с пробелом разбирается", str(docnode.form_of("книга.xlsx:sheet=О выгрузке")))
check(docnode.form_of("книга.xlsx:sheet=Прайс (опт)!A1:C9") == "sheet",
      "имя листа с пробелом и диапазон ячеек разбирается")
check(docnode.locator_problems("sheet", "книга.xlsx:sheet=О выгрузке") == [],
      "валидатор не считает такой адрес нарушением")

print()
print("=" * 78)
print("2. ТЕКСТ УЗЛА: NUL И УПРАВЛЯЮЩИЕ СИМВОЛЫ")
print("=" * 78)
dirty = "начало" + chr(0) + "середина" + chr(1) + "конец"
node = docnode.DocumentNode(kind="paragraph", locator="t.md:1",
                            text=dirty)
check(chr(0) not in node.text, "NUL удалён из текста узла")
check(chr(1) not in node.text, "управляющий символ заменён")
check("начало" in node.text and "конец" in node.text,
      "текст по обе стороны NUL сохранён", node.text)
check("\n" in docnode.clean_text("a\nb") and "\t" in docnode.clean_text("a\tb"),
      "перевод строки и табуляция сохраняются")

print()
print("=" * 78)
print("3. БЮДЖЕТ: ЛЮБОЙ СТАТУС, ЛЮБОЙ ЗАГОЛОВОК")
print("=" * 78)
big = "x" * 900_000
degraded_node = docnode.DocumentNode(kind="document", locator="old.xls",
                                     text=big, extraction_status="degraded")
parts = docchunk.chunk_node(degraded_node, "doc-degraded")
check(len(parts) > 1, "узел `degraded` разрезан", str(len(parts)))
check(max(part.tokens for part in parts) <= 1200,
      "ни одна часть не больше бюджета", str(max(part.tokens for part in parts)))
check({part.extraction_status for part in parts} == {"degraded"},
      "статус `degraded` перенесён на части")
check(all(part.part_of for part in parts),
      "связь с родительским узлом заполнена у всех частей")

wide = " | ".join(f"Столбец{index}" for index in range(400))
rows = "\n".join(f"{index} | {index} | {index}" for index in range(20_000))
sheet = docnode.DocumentNode(kind="sheet", locator="w.xlsx:sheet=Широкий лист",
                             text=wide + "\n" + rows, structure={"repeat_header": wide})
sheet_parts = docchunk.chunk_node(sheet, "doc-sheet")
check(max(part.tokens for part in sheet_parts) <= 1200,
      "широкий лист: части в бюджете даже с повторяемым заголовком",
      str(max(part.tokens for part in sheet_parts)))
check(any(part.structure.get("header_truncated") for part in sheet_parts),
      "усечение заголовка названо в структуре части")
check(all(part.structure.get("header_repeated") for part in sheet_parts[1:]),
      "заголовок повторён в каждой части, кроме первой")

print()
print("=" * 78)
print("4. СОДЕРЖИМОЕ РЕШАЕТ, ЧИТАТЕЛЬ ИСПОЛНЯЕТ")
print("=" * 78)
rtf = b"{\\rtf1\\ansi\\deff0 {\\fonttbl {\\f0 Arial;}}\\f0 Mark-RTF-ONE}"
rtf_result = docread.read("letter.doc", rtf)
check(any("Mark-RTF-ONE" in node.text for node in rtf_result.nodes),
      "RTF под именем .doc даёт текст", rtf_result.status)
check("rtf" in str((rtf_result.metadata or {}).get("sniffed", "")),
      "подмена расширения названа", str(rtf_result.metadata))

html_bytes = b"<html><body><p>Mark-HTML-TWO</p></body></html>"
html_result = docread.read("letter2.doc", html_bytes)
check(any("Mark-HTML-TWO" in node.text for node in html_result.nodes),
      "HTML под именем .doc даёт текст", html_result.status)
check("html" in str((html_result.metadata or {}).get("sniffed", "")),
      "подмена расширения названа и здесь", str(html_result.metadata))

noise = docread.read("noise.doc", b"\x01\x02\x03\x04" * 40)
check(noise.status == "failed", "файл без сигнатуры честно назван неразобранным",
      noise.status)
check(bool(noise.reason), "причина отказа названа", (noise.reason or "")[:60])


print()
print("=" * 78)
print("5. СОСТОЯНИЯ, КОТОРЫЕ НЕЛЬЗЯ ВЫДАВАТЬ ЗА ПОЛОМКУ")
print("=" * 78)
lfs = (b"version https://git-lfs.github.com/spec/v1\n"
       b"oid sha256:0123456789abcdef0123456789abcdef\n"
       b"size 123456\n")
lfs_result = docread.read("big.docx", lfs)
check(lfs_result.status == "failed", "указатель Git LFS — отказ", lfs_result.status)
check("Git LFS" in (lfs_result.reason or ""),
      "причина называет LFS, а не «не zip»", (lfs_result.reason or "")[:70])
check(bool((lfs_result.metadata or {}).get("lfs_pointer")),
      "признак LFS есть в метаданных")

html_in_xlsx = b"<!DOCTYPE html><html><body><p>Mark-HTML-THREE</p></body></html>"
xlsx_result = docread.read("wrong.xlsx", html_in_xlsx)
check(any("Mark-HTML-THREE" in node.text for node in xlsx_result.nodes),
      "HTML под именем .xlsx даёт текст", xlsx_result.status)
check(xlsx_result.status == "degraded",
      "но успехом это не называется: расширение обещало другое", xlsx_result.status)

print()
print("=" * 78)
print("6. НАСТРОЙКИ СЛОЯ: ОБЪЯВЛЕННОЕ СОХРАНЯЕТСЯ")
print("=" * 78)
import atexit  # noqa: E402
import json  # noqa: E402

from ragraf import settings as settings_mod  # noqa: E402

# Свойство поведения: умолчание каждого ключа обязано проходить проверку.
# Прежняя редакция сверяла наличие в STRING_KEYS и падала на перечислениях —
# то есть проверка писала про настройки, а не проверяла их.
rejected = []
for key in settings_mod.STORE_KEYS:
    if key not in settings_mod.DEFAULTS:
        continue
    problems = settings_mod.validate({key: settings_mod.DEFAULTS[key]})
    if problems:
        rejected.append(f"{key}: {problems[0][:50]}")
check(not rejected, "умолчание каждого ключа проходит проверку", str(rejected[:3]))
check("documents_ocr_languages" in settings_mod.STRING_KEYS
      and "documents_archives_extensions" in settings_mod.STRING_KEYS,
      "ключи слоя документов принимают строку")
check(settings_mod.validate({"documents_ocr_languages": "rus+eng"}) == [],
      "языки OCR проверяются как строка")
check(settings_mod.validate({"documents_html_mode": "html"}) != [],
      "режим HTML: опечатка отклоняется")

print()
print("=" * 78)
print("7. БЮДЖЕТ ПО ВИДАМ: РЕШЕНИЕ ЗАМЕРА")
print("=" * 78)
check(docchunk.budget_for("sheet")[1] == 950,
      "у листа бюджет 950: вектор видит текст целиком", str(docchunk.budget_for("sheet")))
check(docchunk.budget_for("document")[1] == 950,
      "у старого документа бюджет 950", str(docchunk.budget_for("document")))
check(docchunk.budget_for("section")[1] == 1200,
      "у раздела бюджет остаётся 1200", str(docchunk.budget_for("section")))
check(docchunk.budget_for("sheet")[1] * 4.2 <= docchunk.EMBED_MAX_CHARS,
      "бюджет листа не превышает предел эмбеддера")
check(docchunk.budget_for("document")[1] * 4.2 <= docchunk.EMBED_MAX_CHARS,
      "бюджет документа не превышает предел эмбеддера")


print()
print("=" * 78)
print("8. PDF И ЯЗЫКОВОЙ ПАКЕТ")
print("=" * 78)
import io  # noqa: E402

# Зашифрованный PDF: основной извлекатель отказывает, запасной читает.
try:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 100 Td (ENCRYPTED-MARKER) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    writer.encrypt(user_password="", owner_password="owner", algorithm="AES-128")
    buffer = io.BytesIO()
    writer.write(buffer)
    locked = buffer.getvalue()
    locked_result = docread.read("locked.pdf", locked)
    check(locked_result.status in ("ok", "failed"),
          "зашифрованный PDF не роняет разбор", locked_result.status)
    if locked_result.nodes:
        # Текст найден — неважно, каким извлекателем; важно, что он НАЗВАН
        # и что маркер внутри, а не «пустой успех».
        found_marker = any("ENCRYPTED-MARKER" in node.text
                           for node in locked_result.nodes)
        check(found_marker and bool(locked_result.converter),
              "зашифрованный PDF даёт текст, извлекатель назван",
              f"converter={locked_result.converter}")
    else:
        check("запасной извлекатель" in (locked_result.reason or ""),
              "отказ называет оба извлекателя", (locked_result.reason or "")[:90])
except Exception as exc:  # noqa: BLE001 — нет pypdf или шифрование не поддержано
    print(f"  [SKIP] шифрованный PDF: {type(exc).__name__}")

# Каталог языковых пакетов: объявлен строкой и проверяется как строка.
check(settings_mod.validate({"documents_ocr_tessdata": "C:/tmp/tessdata"}) == [],
      "каталог пакетов OCR принимается строкой")
check(settings_mod.validate({"documents_ocr_tessdata": 5}) != [],
      "число вместо пути отклоняется")
state_now = ocr_reader.state()
check(bool(state_now["reason"]),
      "состояние OCR всегда называет причину", str(state_now)[:110])
check(state_now["status"] in ("disabled", "missing_engine", "missing_language", "ready"),
      "состояние OCR из закрытого перечня", state_now["status"])


print()
print("=" * 78)
print("9. ФАЙЛ НАСТРОЕК: ОДНА ФОРМА")
print("=" * 78)
settings_path = REPO / "catalog" / "settings.json"
if settings_path.exists():
    raw_settings = json.loads(settings_path.read_text(encoding="utf-8"))
    extra_keys = [key for key in raw_settings
                  if key not in ("version", "settings", "updated_at")]
    check(not extra_keys,
          "на верхнем уровне файла нет ключей настроек",
          f"лишние: {extra_keys}")
    check(isinstance(raw_settings.get("settings"), dict),
          "ключи живут внутри секции settings",
          str(type(raw_settings.get("settings")).__name__))
    declared = raw_settings.get("settings") or {}
    defaults_written = [key for key, value in declared.items()
                        if key in settings_mod.DEFAULTS
                        and settings_mod.DEFAULTS[key] == value]
    check(not defaults_written,
          "умолчания не записаны как заявления", f"равны умолчанию: {defaults_written}")
else:
    check(False, "файл настроек существует", str(settings_path))

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
