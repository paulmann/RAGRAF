"""Диагностика: почему вызовы не привязываются к символам (Python).

Состояние: вызовы извлекаются (40 806 после фильтрации), но подтверждено
в графе 0, не разрешено 40 806. При этом рёбра `calls` и `calls_heuristic`
в графе ЕСТЬ (4299 и 22 886) — значит их построил разбор деревом, а не CPG.

Причина в сопоставлении имён. RAGRAF для PHP строит имена так:
    owner_method: 'src/Db.php:<global>-><lambda>0'
А Joern для Python отдаёт так:
    srcMethod: 'core/api_errors.py:__init__'
    srcFile:   'core\\api_errors.py'   (обратные слеши!)

В индексе RAGRAF имена такие:
    fqn: 'core/api_errors.py:__init__'  (прямые слеши!)

Здесь видно ДВА расхождения, которые надо проверить точно:
  1. разделитель пути: обратные слеши у Joern против прямых в индексе;
  2. состав полного имени: есть ли класс и как обозначается модуль.

Проверяется сравнением конкретных записей Joern и узлов индекса.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf import store  # noqa: E402
from ragraf import cpg as cpg_mod  # noqa: E402

cfg = load_config(REPO / "config" / "sagaai.yaml")

print("=" * 78)
print("1. ЗАПИСИ JOERN ДЛЯ PYTHON: КАК ВЫГЛЯДЯТ ИМЕНА")
print("=" * 78)
runner = cpg_mod.JoernRunner(cfg)
files = sorted(runner.workdir.glob("rev-*.cpg.bin"),
               key=lambda p: -p.stat().st_size)
rev_id = files[0].stem.replace(".cpg", "")
print(f"  CPG: {files[0].name}")
ok, note, rows = runner.query(rev_id, cpg_mod.QUERY_CALLS, timeout=1800)
print(f"  записей: {len(rows) if rows else 0}")

# Показываются записи только из каталога src — именно они должны
# сопоставиться с индексом.
from_src = [r for r in (rows or [])
            if "src" in str(r.get("srcFile", "")).replace("\\", "/").lower()]
print(f"  из них с 'src' в пути: {len(from_src)}")
print(f"\n  примеры записей Joern (из src):")
for r in from_src[:8]:
    print(f"    srcMethod={str(r.get('srcMethod'))[:80]}")
    print(f"      srcFile={str(r.get('srcFile'))[:60]}")
    print(f"      callee={str(r.get('callee'))[:40]!r} "
          f"line={r.get('line')}")

print()
print("=" * 78)
print("2. УЗЛЫ ИНДЕКСА: КАКИЕ ИМЕНА ЖДЁТ RAGRAF")
print("=" * 78)
conn = store.connect(cfg.graph_db, readonly=True)
rev = conn.execute(
    "SELECT rev_id FROM rev WHERE state = 'ready' ORDER BY rev_id DESC LIMIT 1"
).fetchone()["rev_id"]
print(f"  ревизия: {rev}")

# Берутся узлы из тех же файлов, что есть в записях Joern.
sample_paths = []
for r in from_src[:6]:
    path = str(r.get("srcFile") or "").replace("\\", "/")
    if path and path not in sample_paths:
        sample_paths.append(path)
print(f"\n  файлы-образцы: {sample_paths[:4]}")

for path in sample_paths[:3]:
    nodes = conn.execute(
        "SELECT node_id, kind, name, fqn, start_line FROM node "
        "WHERE rev_id = ? AND path = ? AND kind IN ('function','method') "
        "ORDER BY start_line LIMIT 4",
        (rev, path),
    ).fetchall()
    print(f"\n  {path}: узлов {len(nodes)}")
    for n in nodes:
        print(f"    {n['kind']:<9} name={n['name']!r} строка {n['start_line']}")
        print(f"      fqn={n['fqn']!r}")

print()
print("=" * 78)
print("3. ЕСТЬ ЛИ УЗЛЫ ПО ПУТЯМ ИЗ JOERN (точное совпадение)")
print("=" * 78)
for r in from_src[:6]:
    raw_file = str(r.get("srcFile") or "")
    posix = raw_file.replace("\\", "/")
    for variant, label in ((raw_file, "как у Joern"),
                           (posix, "прямые слеши")):
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM node WHERE rev_id = ? AND path = ?",
            (rev, variant),
        ).fetchone()["n"]
        print(f"    {label:<14} {variant[:50]:<52} узлов {count}")

print()
print("=" * 78)
print("4. КАК RAGRAF СОПОСТАВЛЯЕТ ИМЕНА (правила для PHP)")
print("=" * 78)
import re  # noqa: E402

cpg_src = (REPO / "src" / "ragraf" / "cpg.py").read_text(encoding="utf-8")
idx = cpg_src.find("def parse_calls_export")
block = cpg_src[idx:idx + 3000]
print("  фрагменты правил разбора:")
for line in block.splitlines():
    s = line.strip()
    if any(k in s for k in ("replace", "split", "::", "->", "owner_file",
                            "owner_method", "<global>", "<lambda>")):
        print(f"    {s[:95]}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Сравнение показывает, какие расхождения мешают сопоставлению:

  1. РАЗДЕЛИТЕЛЬ ПУТИ. Joern для Python отдаёт обратные слеши
     (`core\\api_errors.py`), а в индексе пути прямые (`core/api_errors.py`).
     Без нормализации ни одна запись не совпадёт.

  2. ФОРМА ПОЛНОГО ИМЕНИ. Для PHP RAGRAF разбирает имена вида
     `file.php:<global>-><lambda>0` — там свои правила. Для Python имена
     вида `file.py:__init__` — проще, но правила PHP могут не подходить.

  Проверка показывает точное место: сколько узлов находится по пути
  в каждой форме. Если по прямым слешам узлы есть, а по обратным нет —
  достаточно нормализовать путь.
""")
conn.close()
