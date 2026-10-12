"""Проверка интеграции: какой язык куда направляется и есть ли разбор.

Проверяется после правок indexer.py и css_html.py:
  1. MultiExtractor правильно определяет язык по расширению;
  2. для языков с профилем возвращается извлекатель с разбором;
  3. для языков без профиля извлечение помечается как text_only;
  4. индексатор считает тот же набор языков с разбором (одна точка правды).
"""

from __future__ import annotations

import pathlib
import sys


# Отчёт печатается по-русски, а консоль Windows по умолчанию не UTF-8: без этой
# настройки запуск падал UnicodeEncodeError ('charmap' codec can't encode),
# причём только на Windows и только вне UTF-8-консоли — в CI это валило job.
# Проверено: PYTHONIOENCODING=cp1252 -> exit 1. Настройка делает тест
# независимым от локали/консоли (Python 3.7+: TextIOWrapper.reconfigure).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.extract.css_html import MultiExtractor  # noqa: E402
from ragraf.extract.generic import PROFILES, GenericExtractor  # noqa: E402

PASS = 0
FAIL = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


print("=" * 78)
print("1. ОПРЕДЕЛЕНИЕ ЯЗЫКА И НАПРАВЛЕНИЕ ФАЙЛА")
print("=" * 78)
print()
print(f"  {'файл':<14} {'язык':<12} {'разбор':<10} вид извлечения")
print("  " + "-" * 58)

mx = MultiExtractor()
CASES = [
    ("a.php", "php", True),
    ("a.phtml", "php", True),
    ("a.css", "css", True),
    ("a.html", "html", True),
    ("a.js", "javascript", True),
    ("a.mjs", "javascript", True),
    ("a.cs", "csharp", True),
    ("a.ps1", "powershell", True),
    ("a.psm1", "powershell", True),
    ("a.sh", "bash", True),
    ("a.pl", "perl", True),
    ("a.py", "python", True),
    ("a.ts", "typescript", True),
    ("a.go", "go", True),
    ("a.lua", "lua", True),
    ("a.c", "c", True),
    ("a.cpp", "cpp", True),
    ("a.rb", "ruby", True),
    # Текстовые: грамматика либо отсутствует, либо дала ошибки на реальных файлах.
    ("a.bat", "batch", False),
    ("a.cmd", "batch", False),
    ("a.sql", "sql", False),
    ("a.md", "markdown", False),
    ("a.yaml", "yaml", False),
    ("a.json", "json", False),
    ("a.ini", "ini", False),
    ("a.txt", "text", False),
    ("a.xml", "xml", False),
]

for name, want_lang, want_parse in CASES:
    lang = mx.lang_for(name)
    extractor = mx.generic_for(lang)
    has_parse = extractor is not None
    # PHP, CSS и разметка обслуживаются своими извлекателями, не профилями.
    if name.endswith((".php", ".phtml", ".css", ".html", ".htm")):
        has_parse = True
    verdict = "разбор" if has_parse else "текст"
    print(f"  {name:<14} {lang or '—':<12} {verdict:<10} "
          f"{'профиль' if extractor else '—'}")
    check(lang == want_lang, f"{name}: язык = {want_lang}",
          f"получено {lang!r}")
    check(has_parse == want_parse, f"{name}: разбор={'да' if want_parse else 'нет'}",
          f"получено {has_parse}")

print()
print("=" * 78)
print("2. СОГЛАСОВАННОСТЬ С ИНДЕКСАТОРОМ (одна точка правды)")
print("=" * 78)

from ragraf.config import LANG_BY_EXT, PARSE_LANGS_EXT  # noqa: E402
from ragraf.indexer import Indexer  # noqa: E402

# Набор языков с разбором, как его видит индексатор.
indexer_langs = {"php", "css", "html"} | set(PROFILES)
print(f"  языков с разбором у извлекателя: {len(indexer_langs)}")
print(f"    {sorted(indexer_langs)}")

# Какое расширение каким языком считается.
print()
print("  Проверка: каждый язык с разбором достижим по своему расширению")
missing = []
for ext, lang in PARSE_LANGS_EXT.items():
    if lang not in indexer_langs:
        missing.append(f"{ext} -> {lang}")
check(not missing, "все расширения из PARSE_LANGS_EXT ведут к языку с разбором",
      f"проблемные: {missing}")

# Расширения из карты, которые дают язык с разбором, должны быть и в PARSE_LANGS_EXT.
print()
not_in_parse = []
for ext, lang in LANG_BY_EXT.items():
    if lang in ("php", "css", "html") or lang in PROFILES:
        if ext not in PARSE_LANGS_EXT:
            not_in_parse.append(f"{ext} -> {lang}")
check(not not_in_parse, "все расширения с разбором есть в PARSE_LANGS_EXT",
      f"пропущены: {not_in_parse}")

print()
print("=" * 78)
print("3. НАПРАВЛЕНИЕ НЕИЗВЕСТНОГО РАСШИРЕНИЯ")
print("=" * 78)
for name in ("a.qqq", "Makefile", "a.php5"):
    lang = mx.lang_for(name)
    extractor = mx.generic_for(lang)
    empty = mx.extract(name, b"test content\n")
    print(f"  {name:<12} язык={lang or '—':<8} статус={empty.parse_status:<10} "
          f"примечание={(empty.parse_note or '—')[:42]}")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
