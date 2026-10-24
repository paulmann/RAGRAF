"""Что оказалось в Python-графе после исправления исключений.

Наблюдение: после правки исключений записей стало ВДВОЕ больше
(172 184 против 72 566), но записей с «src» в пути — ноль. То есть
исключения сработали не так, как ожидалось: вместо исходников в граф
попали другие каталоги.

Надо увидеть фактический состав путей в записях Joern: какие каталоги
попали в граф и какие нет.

Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import sys
from collections import Counter

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf import cpg as cpg_mod  # noqa: E402

cfg = load_config(REPO / "config" / "sagaai.yaml")
runner = cpg_mod.JoernRunner(cfg)

print("=" * 78)
print("СОСТАВ ПУТЕЙ В ЗАПИСЯХ JOERN")
print("=" * 78)
files = sorted(runner.workdir.glob("rev-*.cpg.bin"),
               key=lambda p: -p.stat().st_size)
if not files:
    print("  CPG нет")
    raise SystemExit(1)
rev_id = files[0].stem.replace(".cpg", "")
print(f"  CPG: {files[0].name} ({files[0].stat().st_size / 1024 / 1024:.1f} МБ)")

ok, note, rows = runner.query(rev_id, cpg_mod.QUERY_CALLS, timeout=1800)
print(f"  записей вызовов: {len(rows) if rows else 0}")
if not rows:
    print(f"  ошибка запроса: {note}")
    raise SystemExit(1)

# Считается, из каких каталогов верхнего уровня записи.
tops: Counter = Counter()
src_files: set[str] = set()
for r in rows:
    raw = str(r.get("srcFile") or "")
    posix = raw.replace("\\", "/").lstrip("./")
    top = posix.split("/", 1)[0] if "/" in posix else "(корень)"
    tops[top] += 1
    if top == "src":
        src_files.add(posix)

print(f"\n  записи по каталогам верхнего уровня:")
for top, n in tops.most_common(14):
    print(f"    {top:<28} {n}")

print(f"\n  файлов из src: {len(src_files)}")
for f in sorted(src_files)[:10]:
    print(f"    {f}")

print()
print("=" * 78)
print("СРАВНЕНИЕ С ФИЗИЧЕСКИМ СОСТАВОМ ПРОЕКТА")
print("=" * 78)
root = pathlib.Path(str(cfg.root))
disk_top: Counter = Counter()
for p in root.rglob("*.py"):
    rel = p.relative_to(root)
    if any(part in (".venv", "__pycache__", ".git") for part in rel.parts):
        continue
    top = rel.parts[0] if len(rel.parts) > 1 else "(корень)"
    disk_top[top] += 1
print(f"  .py на диске по каталогам:")
for top, n in disk_top.most_common(14):
    print(f"    {top:<28} {n}")

print()
print("=" * 78)
print("ЧТО ИСКЛЮЧАЕТСЯ: СПИСОК КАТАЛОГОВ")
print("=" * 78)
dirs = runner._exclude_dirs()
print(f"  {len(dirs)} каталогов:")
for d in dirs:
    present = (root / d).is_dir()
    files_count = 0
    if present:
        try:
            files_count = len([f for f in (root / d).rglob('*.py')])
        except OSError:
            pass
    mark = f"есть, {files_count} .py" if present else "нет в проекте"
    print(f"    {d:<20} {mark}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Сравнение показывает, совпадает ли состав графа с составом проекта.
  Если в графе нет исходников из `src`, надо понять почему: либо каталог
  попал в исключения по ошибке, либо разбор идёт не от того корня.
""")
