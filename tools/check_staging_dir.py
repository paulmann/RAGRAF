"""Проверка .staging: исключать ли его из индекса.

Вопрос возник из запроса владельца об исключении служебных папок. `.staging` —
служебный каталог подготовки правок, и по имени его хочется исключить.

Но замер прошлой сессии показал обратное: в `C:\\1st_RdpMON\\Service\\.staging`
лежит ЕДИНСТВЕННЫЙ файл `.sh` проекта и портированные скрипты `.ps1`, ради
которых и делалась задача «индексировать ps1, sh, bat». Исключение каталога
означало бы прямое уничтожение результата той работы.

Проверяется по факту:
  1. что лежит в `.staging` каждого корпуса;
  2. попадают ли эти файлы в индекс;
  3. что будет, если добавить `.staging` в базовый список исключений.

Отдельно проверяется `.staging` самого RAGRAF: он не корпус, но если им
станет, 100+ файлов временных инструментов попадут в поиск.

Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import corpus, util  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

CORPORA = [
    ("z-batteryservice.yaml", "z.batteryservice.ru"),
    ("revoap.yaml", "revoap.com"),
    ("md.viewer.yaml", "MD.Viewer"),
    ("rdpmon.yaml", "RdpMON"),
    ("clean_bom_senior.yaml", "Clean_BOM_Senior"),
]

print("=" * 78)
print("1. ЧТО ЛЕЖИТ В .staging КАЖДОГО КОРПУСА")
print("=" * 78)
for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    root = pathlib.Path(str(cfg.root))
    staging = root / ".staging"
    if not staging.exists():
        continue
    files = [f for f in staging.rglob("*") if f.is_file()]
    print(f"\n  {title}: файлов {len(files)}")
    for f in files[:12]:
        rel = f.relative_to(root)
        size = f.stat().st_size
        print(f"    {str(rel).replace(chr(92), '/'):<50} {size:>8} б")
    if len(files) > 12:
        print(f"    … и ещё {len(files) - 12}")

print()
print("=" * 78)
print("2. ПОПАДАЮТ ЛИ ОНИ В ИНДЕКС")
print("=" * 78)
for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    result = corpus.scan(cfg)
    in_scan = [f.path for f in result.files if ".staging" in f.path]
    print(f"\n  {title}: файлов .staging в скане {len(in_scan)}")
    for p in in_scan:
        print(f"    {p}")
    # Отдельно: файлы .sh и .ps1 — они ради задачи и индексируются ли.
    scripts = [f.path for f in result.files
               if f.path.lower().endswith((".sh", ".ps1", ".psm1"))]
    print(f"    всего скриптов .sh/.ps1 в корпусе: {len(scripts)}")
    for p in scripts[:6]:
        print(f"      {p}")

print()
print("=" * 78)
print("3. ЧТО БУДЕТ ПРИ ИСКЛЮЧЕНИИ .staging")
print("=" * 78)
# Симулируется: применяется шаблон и смотрится, сколько файлов выпадет.
PATTERN = "**/.staging/**"
total_lost = 0
lost_scripts: list[str] = []
for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    result = corpus.scan(cfg)
    dropped = [f.path for f in result.files
               if util.matches_any(f.path, [PATTERN])]
    if not dropped:
        continue
    total_lost += len(dropped)
    print(f"\n  {title}: выпало бы {len(dropped)}")
    for p in dropped[:10]:
        is_script = p.lower().endswith((".sh", ".ps1", ".psm1"))
        mark = "  <-- СКРИПТ" if is_script else ""
        print(f"    {p}{mark}")
        if is_script:
            lost_scripts.append(p)

print(f"\n  всего выпало бы: {total_lost}")
print(f"  из них скриптов .sh/.ps1: {len(lost_scripts)}")
for p in lost_scripts:
    print(f"    {p}")

print()
print("=" * 78)
print("4. .staging САМОГО RAGRAF (не корпус, но проверяется)")
print("=" * 78)
staging = REPO / ".staging"
if staging.exists():
    files = [f for f in staging.rglob("*") if f.is_file()]
    exts: dict[str, int] = {}
    for f in files:
        exts[f.suffix.lower()] = exts.get(f.suffix.lower(), 0) + 1
    print(f"  файлов: {len(files)}")
    print(f"  расширения: {dict(sorted(exts.items(), key=lambda kv: -kv[1])[:8])}")
    # Подключён ли RAGRAF как корпус.
    from ragraf.catalog import Catalog  # noqa: E402

    try:
        cat = Catalog()
        entries = cat.entries() if hasattr(cat, "entries") else {}
        names = list(entries) if isinstance(entries, dict) else []
    except Exception:
        names = []
    print(f"  корпуса в реестре: {names}")
    print(f"  RAGRAF среди них: "
          f"{'да' if any('ragraf' in str(n).lower() for n in names) else 'нет'}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print(f"""
  Выводов два, и они разные для разных каталогов:

  * В КОРПУСАХ `.staging` НЕЛЬЗЯ исключать базово: там лежат рабочие скрипты,
    в том числе единственный `.sh` проекта RdpMON. Выпало бы {len(lost_scripts)}
    скриптов — именно тех, ради индексации которых делалась задача.

  * В самом RAGRAF `.staging` — рабочий каталог проекта с временными
    инструментами. Он не корпус, и в индекс не попадает. Если RAGRAF
    когда-нибудь подключат как корпус, `.staging` стоит исключить
    В ЕГО КОНФИГЕ, а не в базовом списке.
""")
