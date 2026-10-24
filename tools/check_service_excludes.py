"""Проверка: исключены ли служебные каталоги в каждом корпусе.

Задача от владельца: добавить в исключения служебную папку `.ragraf`.

Прежде чем править, надо понять фактическое состояние: где `.ragraf` уже
исключён, а где нет. Иначе правка будет либо дублем существующего правила,
либо не закроет настоящую дыру.

Проверяется четыре места:
  1. общий список DEFAULT_EXCLUDES в config.py;
  2. список каталогов EXCLUDE_DIRS в init_corpus.py (по нему работает init);
  3. список exclude в КАЖДОМ конфиге корпуса — он у каждого свой;
  4. фактически проиндексированные пути: есть ли среди них служебные.

Пункт 4 — решающий: правило может быть записано, но не работать
(например, из-за формы шаблона). Проверяется результат, а не намерение.

Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store  # noqa: E402
from ragraf.config import DEFAULT_EXCLUDES, load as load_config  # noqa: E402
from ragraf.init_corpus import EXCLUDE_DIRS  # noqa: E402

# Служебные каталоги, которые не должны попадать в индекс.
SERVICE = (".ragraf", ".zwork", ".staging", ".git", ".agents", ".pi",
           ".dev_agent", "node_modules", "vendor", "__pycache__", "obj",
           "bin", "publish", ".tools", ".ort_gpu_probe")

CORPORA = [
    ("z-batteryservice.yaml", "z.batteryservice.ru"),
    ("revoap.yaml", "revoap.com"),
    ("md.viewer.yaml", "MD.Viewer"),
    ("rdpmon.yaml", "RdpMON"),
]

print("=" * 78)
print("1. ОБЩИЙ СПИСОК DEFAULT_EXCLUDES (config.py)")
print("=" * 78)
for pattern in DEFAULT_EXCLUDES:
    print(f"  {pattern}")
has_ragraf = any(".ragraf" in p for p in DEFAULT_EXCLUDES)
print(f"\n  .ragraf в общем списке: {'ДА' if has_ragraf else 'НЕТ'}")

print()
print("=" * 78)
print("2. СПИСОК EXCLUDE_DIRS (init_corpus.py — по нему работает init)")
print("=" * 78)
print(f"  всего каталогов: {len(EXCLUDE_DIRS)}")
for name in sorted(EXCLUDE_DIRS):
    print(f"    {name}")
print(f"\n  .ragraf в списке init: {'ДА' if '.ragraf' in EXCLUDE_DIRS else 'НЕТ'}")

print()
print("=" * 78)
print("3. СПИСОК exclude В КАЖДОМ КОНФИГЕ КОРПУСА")
print("=" * 78)
for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    patterns = list(cfg.exclude)
    print(f"\n  {title}  (шаблонов: {len(patterns)})")
    for p in patterns:
        mark = " <-- служебный" if any(s in p for s in SERVICE) else ""
        print(f"    {p}{mark}")
    found = [s for s in SERVICE if any(s in p for p in patterns)]
    missing = [s for s in SERVICE if s not in found]
    print(f"    есть в правилах: {found}")
    if missing:
        print(f"    нет в правилах:  {missing[:8]}")

print()
print("=" * 78)
print("4. ФАКТ: ЧТО РЕАЛЬНО ПРОИНДЕКСИРОВАНО")
print("=" * 78)
# Это решающая проверка: правило может быть записано, но не сработать.
for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    conn = store.connect(cfg.graph_db, readonly=True)
    row = conn.execute(
        "SELECT rev_id FROM rev WHERE state = 'ready' ORDER BY rev_id DESC LIMIT 1"
    ).fetchone()
    if not row:
        conn.close()
        continue
    rev = row["rev_id"]
    files = conn.execute(
        "SELECT path FROM file WHERE rev_id = ?", (rev,)
    ).fetchall()
    conn.close()

    hits: dict[str, list[str]] = {}
    for f in files:
        p = str(f["path"])
        low = f"/{p.lower()}"
        for marker in SERVICE:
            if f"/{marker.lower()}/" in low or low.endswith(f"/{marker.lower()}"):
                hits.setdefault(marker, []).append(p)

    print(f"\n  {title} ({rev}): файлов {len(files)}")
    if not hits:
        print("    служебных путей в индексе НЕТ")
    for marker, paths in sorted(hits.items()):
        print(f"    {marker}: {len(paths)} файлов")
        for p in paths[:5]:
            print(f"      {p}")

print()
print("=" * 78)
print("5. ПРОВЕРКА МЕХАНИЗМА: РАБОТАЕТ ЛИ ШАБЛОН .ragraf")
print("=" * 78)
from ragraf import util  # noqa: E402

CASES = [
    (".ragraf/index/graph/graph.sqlite", "**/.ragraf/**"),
    (".ragraf/cpg/rev-0001.cpg.bin", "**/.ragraf/**"),
    ("sub/project/.ragraf/data.db", "**/.ragraf/**"),
    ("src/modules/ragraf.php", "**/.ragraf/**"),
    (".ragraf", "**/.ragraf/**"),
    (".ragraf/x", "**/.ragraf/**"),
]
print(f"  {'путь':<44} {'шаблон':<20} совпало")
print("  " + "-" * 76)
for test_path, pattern in CASES:
    matched = util.matches_any(test_path, [pattern])
    print(f"  {test_path:<44} {pattern:<20} {'ДА' if matched else 'нет'}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Проверка показывает по каждому слою, закрыт ли служебный каталог:
    * DEFAULT_EXCLUDES — общий список, действует по умолчанию;
    * EXCLUDE_DIRS — по нему init решает, что не обходить;
    * exclude в конфиге — то, что реально применяется к корпусу;
    * индекс — конечный результат.
""")
