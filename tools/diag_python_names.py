"""Диагностика: почему вызовы извлечены, но не привязаны к символам.

Результат после обхода:
    вызовы взяты запросами к файлу CPG: 18778 после фильтрации из 72566,
    подтверждено в графе 0 (по полному имени 0, по имени и файлу 0),
    не разрешено 18778

То есть вызовы ЕСТЬ, но ни один не связался с символом в индексе. Причина
может быть в формате имён: RAGRAF строит полные имена (fqn) по своему
правилу, а Joern для Python отдаёт их иначе, чем для PHP.

Например, для PHP запись выглядит так:
    srcMethod: '_diag.php:<global>-><lambda>0'
А для Python:
    srcMethod: '.dev_agent\\updates\\pending\\core\\version.py:<module>'

Здесь видно расхождение: разделители путей (обратные слеши) и другое
обозначение модуля (`<module>` вместо `<global>`).

Проверяется: какие имена отдаёт Joern и какие имена есть в индексе RAGRAF.
По сравнению видно, что именно надо сопоставить.

Ничего не меняет.
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
print("1. ИМЕНА В ИНДЕКСЕ RAGRAF (Python-корпус)")
print("=" * 78)
conn = store.connect(cfg.graph_db, readonly=True)
rev = conn.execute(
    "SELECT rev_id FROM rev WHERE state = 'ready' ORDER BY rev_id DESC LIMIT 1"
).fetchone()["rev_id"]
print(f"  ревизия: {rev}")

print(f"\n  примеры узлов-функций и методов:")
rows = conn.execute(
    "SELECT kind, name, fqn, path, start_line FROM node "
    "WHERE rev_id = ? AND kind IN ('function','method') "
    "ORDER BY path, start_line LIMIT 12",
    (rev,),
).fetchall()
for r in rows:
    print(f"    {r['kind']:<9} name={r['name']!r}")
    print(f"      fqn={r['fqn']!r}")
    print(f"      path={r['path']!r} строка {r['start_line']}")

total_fns = conn.execute(
    "SELECT COUNT(*) AS n FROM node WHERE rev_id = ? "
    "AND kind IN ('function','method')",
    (rev,),
).fetchone()["n"]
print(f"\n  всего функций и методов в индексе: {total_fns}")

# Проверяется, есть ли рёбра вызовов после обхода.
edge_kinds = conn.execute(
    "SELECT kind, COUNT(*) AS n FROM edge WHERE rev_id = ? "
    "GROUP BY kind ORDER BY n DESC",
    (rev,),
).fetchall()
print(f"\n  рёбра по видам:")
for r in edge_kinds:
    print(f"    {r['kind']:<20} {r['n']}")
conn.close()

print()
print("=" * 78)
print("2. ИМЕНА, КОТОРЫЕ ОТДАЁТ JOERN ДЛЯ PYTHON")
print("=" * 78)
runner = cpg_mod.JoernRunner(cfg)
files = sorted(runner.workdir.glob("rev-*.cpg.bin"),
               key=lambda p: -p.stat().st_size)
if files:
    rev_id = files[0].stem.replace(".cpg", "")
    print(f"  CPG: {files[0].name}, ревизия {rev_id}")
    ok, note, rows = runner.query(rev_id, cpg_mod.QUERY_CALLS, timeout=1800)
    print(f"  запрос вызовов: ok={ok}, записей {len(rows) if rows else 0}")
    if rows:
        print(f"\n  первые записи (что отдаёт Joern):")
        for r in rows[:6]:
            print(f"    srcMethod={str(r.get('srcMethod'))[:70]!r}")
            print(f"      srcFile={str(r.get('srcFile'))[:60]!r} "
                  f"callee={str(r.get('callee'))[:40]!r}")

    # Запрос методов: там полные имена, которые RAGRAF использует для узлов.
    ok_m, note_m, methods = runner.query(rev_id, cpg_mod.QUERY_METHODS,
                                         timeout=1800)
    print(f"\n  запрос методов: ok={ok_m}, записей "
          f"{len(methods) if methods else 0}")
    if methods:
        print(f"  первые записи:")
        for m in methods[:6]:
            print(f"    fullName={str(m.get('fullName'))[:75]!r}")
            print(f"      name={str(m.get('name'))[:40]!r} "
                  f"file={str(m.get('filename'))[:50]!r} "
                  f"line={m.get('lineNumber')}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Сравнение показывает расхождение форматов имён:
    * индекс RAGRAF: fqn вида «путь/к/файлу.py:имя»;
    * Joern для Python: srcMethod вида «путь\\к\\файлу.py:<module>».

  Различия, которые надо устранить в сопоставлении:
    1. разделитель пути: обратные слеши против прямых;
    2. имя модуля: `<module>` против реального имени символа;
    3. возможно, отсутствие имени класса в полном имени.

  Эти же правила для PHP работают — значит, разбор имён надо расширить,
  а не переписывать: для PHP-корпусов поведение должно остаться прежним.
""")
