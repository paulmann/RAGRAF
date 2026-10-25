"""Диагностика: почему Joern не извлёк вызовы для Python-проекта.

Симптом: индексация sagaai дала `capability=degraded`, в логе:
    cpg 20.145s items=0  CPG построена, но вызовы не извлечены
                          (формат экспорта изменился)
    · сервер вернул пустой список вызовов
    · граф свойств кода не построен и переносить нечего

При этом рёбра `calls` есть (4322) — они выведены разбором дерева, а не
из графа свойств кода. То есть `callers` работает по частичным данным,
а `slice` недоступен вовсе.

Надо понять причину:
  1. Joern 4.0.99 умеет python2cpg — но, возможно, нужен другой вызов;
  2. экспорт из сервера мог вернуть пустой результат;
  3. мог сломаться разбор формата экспорта.

Проверяется по факту: запускается сборка CPG для небольшого python-файла
и печатается, что вернул Joern. Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402

cfg = load_config(REPO / "config" / "sagaai.yaml")
print(f"  проект: {cfg.project}")
print(f"  корень: {cfg.root}")

print()
print("=" * 78)
print("1. ЧТО JOERN УМЕЕТ ДЛЯ PYTHON")
print("=" * 78)
joern_dir = REPO / ".tools" / "joern-cli"
print(f"  каталог Joern: {joern_dir} (есть: {joern_dir.exists()})")
if joern_dir.exists():
    # Ищутся парсеры: их имена говорят, какие языки поддержаны.
    parsers = sorted(p.name for p in joern_dir.iterdir()
                     if p.is_file() and "parse" in p.name.lower())
    print(f"  файлы-парсеры: {parsers}")
    # Ищется python-парсер в библиотеках Joern.
    py_parsers = list(joern_dir.rglob("*2cpg*.jar"))
    print(f"  jar-файлы парсеров: {len(py_parsers)}")
    for p in py_parsers[:12]:
        print(f"    {p.name}")

print()
print("=" * 78)
print("2. КАК RAGRAF ВЫЗЫВАЕТ JOERN ДЛЯ PYTHON")
print("=" * 78)
cpg_src = (REPO / "src" / "ragraf" / "cpg.py").read_text(encoding="utf-8")
import re  # noqa: E402

# Ищутся упоминания языков и парсеров.
for i, line in enumerate(cpg_src.splitlines(), 1):
    if any(k in line for k in ("python", "php2cpg", "c2cpg", "lang",
                               "frontend", "LANG", "language")):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        print(f"  {i:>4}: {stripped[:88]}")

print()
print("=" * 78)
print("3. ЕСТЬ ЛИ PYTHON-ПАРСЕР В УСТАНОВЛЕННОМ JOERN")
print("=" * 78)
if joern_dir.exists():
    # Ищется всё, что относится к python.
    py_items = [p for p in joern_dir.rglob("*") if "python" in p.name.lower()]
    print(f"  найдено объектов с 'python' в имени: {len(py_items)}")
    for p in py_items[:15]:
        print(f"    {p.relative_to(joern_dir)}")

print()
print("=" * 78)
print("4. ПРОВЕРКА: РАЗБИРАЕТ ЛИ JOERN PYTHON-ФАЙЛ")
print("=" * 78)
# Берётся небольшой файл проекта и подаётся парсеру напрямую.
root = pathlib.Path(str(cfg.root))
sample = None
for p in sorted(root.rglob("*.py")):
    if ".venv" in p.parts or "__pycache__" in p.parts:
        continue
    if p.stat().st_size > 2000:
        sample = p
        break
if sample is None:
    print("  python-файлов не найдено")
else:
    print(f"  пробный файл: {sample.relative_to(root)} "
          f"({sample.stat().st_size} б)")
    # Ищется python-парсер Joern и пробуется его справка.
    candidates = list(joern_dir.glob("*python*")) + \
        list(joern_dir.glob("*2cpg*"))
    print(f"  кандидаты парсеров: {[c.name for c in candidates[:8]]}")
    # Проверяется наличие скрипта python2cpg.
    py2cpg = joern_dir / "python2cpg.bat"
    print(f"  python2cpg.bat существует: {py2cpg.exists()}")
    if not py2cpg.exists():
        print("  ВЫВОД: отдельного скрипта python2cpg нет — разбор Python")
        print("         идёт через общий joern-parse с автоопределением языка")
    if py2cpg.exists():
        out = root / ".ragraf" / "python_probe"
        proc = subprocess.run(
            [str(py2cpg), str(sample), "-o", str(out)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=600, cwd=str(joern_dir),
        )
        print(f"  код возврата: {proc.returncode}")
        print(f"  stderr: {(proc.stderr or '')[:400]}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Проверка показывает, есть ли в установленном Joern поддержка Python.
  Если парсера нет — граф вызовов по Python не построить, и это надо
  честно записать как ограничение: RAGRAF будет работать по данным
  разбора дерева (calls/heuristic), а `slice` останется недоступным.
""")
