"""Состав проекта RAGRAF — основа для его собственного конфига корпуса.

Решение владельца: `.staging` исключать в конфиге самого RAGRAF, а не в общем
списке. Замер это подтверждает: в RdpMON `.staging` содержит рабочие
фикстуры (15 файлов), и общее исключение их потеряет.

Здесь замеряется, что вообще лежит в проекте RAGRAF и что надо исключить,
чтобы в индекс попал ТОЛЬКО код, а не инструменты и артефакты.

Ничего не меняет.
"""

from __future__ import annotations

import pathlib
from collections import Counter

REPO = pathlib.Path(__file__).resolve().parents[1]

# Каталоги верхнего уровня: сколько файлов и какой объём.
print("=" * 78)
print("КАТАЛОГИ ВЕРХНЕГО УРОВНЯ")
print("=" * 78)
print(f"\n  {'каталог':<24} {'файлов':>8} {'объём':>10} {'код':>6} "
      f"что это")
print("  " + "-" * 76)

CODE_EXT = {".py", ".ps1", ".psm1", ".sh", ".js", ".sql", ".php", ".css",
            ".html", ".cs", ".md", ".yaml", ".yml", ".json"}

rows = []
for entry in sorted(REPO.iterdir()):
    if not entry.is_dir():
        continue
    files = [p for p in entry.rglob("*") if p.is_file()]
    total_mb = sum(p.stat().st_size for p in files) / 1024 / 1024
    code = sum(1 for p in files if p.suffix.lower() in CODE_EXT)
    rows.append((entry.name, len(files), total_mb, code))

for name, count, mb, code in sorted(rows, key=lambda r: -r[1]):
    print(f"  {name:<24} {count:>8} {mb:>9.1f}М {code:>6}")

print()
print("=" * 78)
print("ФАЙЛЫ В КОРНЕ")
print("=" * 78)
root_files = [p for p in REPO.iterdir() if p.is_file()]
print(f"  всего: {len(root_files)}")
by_ext = Counter(p.suffix.lower() for p in root_files)
for ext, n in by_ext.most_common(10):
    print(f"    {ext or '(без расширения)'}: {n}")

print()
print("=" * 78)
print("ЧТО НАДО ИСКЛЮЧИТЬ В КОНФИГЕ RAGRAF")
print("=" * 78)
# Кандидаты: служебные каталоги проекта.
CANDIDATES = {
    ".staging": "временные файлы сессий (182 файла, 16 МБ)",
    ".tools": "JDK и Joern (внешние инструменты, сотни МБ)",
    ".zwork": "журналы фоновых задач и запусков",
    ".git": "история версий",
    ".ragraf": "сам индекс (исключён в базовом списке)",
    "__pycache__": "кеш Python (исключён в базовом списке)",
    ".venv": "окружение (исключено в базовом списке)",
    "catalog": "реестр и настройки (JSON, не код)",
}
for name, why in CANDIDATES.items():
    path = REPO / name
    if not path.exists():
        print(f"  {name:<16} НЕТ в проекте")
        continue
    files = [p for p in path.rglob("*") if p.is_file()]
    code = sum(1 for p in files if p.suffix.lower() in CODE_EXT)
    mb = sum(p.stat().st_size for p in files) / 1024 / 1024
    print(f"  {name:<16} {len(files):>6} файлов, {mb:>8.1f}М, "
          f"кода {code:<6} — {why}")

print()
print("=" * 78)
print("ЧТО ПОПАДЁТ В ИНДЕКС, ЕСЛИ ИСКЛЮЧИТЬ СЛУЖЕБНОЕ")
print("=" * 78)
EXCLUDE = {".staging", ".tools", ".zwork", ".git", ".ragraf", "__pycache__",
           ".venv", "catalog", "docs"}
kept = []
for p in REPO.rglob("*"):
    if not p.is_file():
        continue
    rel = p.relative_to(REPO)
    if set(rel.parts) & EXCLUDE:
        continue
    if p.suffix.lower() in CODE_EXT:
        kept.append(rel)
print(f"  файлов кода к индексации: {len(kept)}")
by_ext = Counter(p.suffix.lower() for p in kept)
for ext, n in by_ext.most_common(12):
    print(f"    {ext}: {n}")

# Проверка: не осталось ли внутри чего-то тяжёлого.
total_kb = sum((REPO / p).stat().st_size for p in kept) / 1024
print(f"\n  объём к индексации: {total_kb:.0f} КБ")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Конфиг корпуса RAGRAF должен исключать служебные каталоги проекта.
  `.staging` — первый из них: там временные файлы задач, а не код.

  В ОБЩИЙ список (DEFAULT_EXCLUDES) его добавлять НЕЛЬЗЯ: в корпусе RdpMON
  он содержит рабочие фикстуры, и их потеря исказит индекс (замерено:
  было 1266 файлов, стало 1251).
""")
