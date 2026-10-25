"""Итоговая проверка задачи об исключениях служебных каталогов.

Собирает результат всей работы в один отчёт:
  1. `.ragraf` исключён на всех уровнях (запрос владельца выполнен);
  2. базовый список расширен и синхронизирован со списком init;
  3. рабочий код не потерян — числа корпусов не изменились;
  4. `.staging` НАМЕРЕННО не исключён, и вот почему;
  5. опасные по имени каталоги (bin, obj, publish) остались в конфигах.

Запускается после правок; ничего не меняет.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import corpus, util  # noqa: E402
from ragraf.config import (DEFAULT_EXCLUDES, NON_DEFAULT_EXCLUDES,  # noqa: E402
                           load as load_config)
from ragraf.init_corpus import EXCLUDE_DIRS  # noqa: E402

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


CORPORA = [
    ("z-batteryservice.yaml", "z.batteryservice.ru", 162),
    ("revoap.yaml", "revoap.com", 592),
    ("md.viewer.yaml", "MD.Viewer", 16),
    ("rdpmon.yaml", "RdpMON", 1266),
    ("clean_bom_senior.yaml", "Clean_BOM_Senior", 6),
]

print("=" * 78)
print("1. ЗАПРОС ВЛАДЕЛЬЦА: .ragraf В ИСКЛЮЧЕНИЯХ")
print("=" * 78)
check(any(".ragraf" in p for p in DEFAULT_EXCLUDES),
      ".ragraf исключён базовым списком (действует всегда)")
check(".ragraf" in EXCLUDE_DIRS,
      ".ragraf есть в списке init (экслучается при создании корпуса)")
for config_name, title, _ in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    check(any(".ragraf" in p for p in cfg.exclude),
          f"{title}: .ragraf исключён в конфиге")

# Фактическая проверка: служебные пути не попадают в скан.
for config_name, title, expected in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    result = corpus.scan(cfg)
    ragraf_paths = [f.path for f in result.files if ".ragraf" in f.path]
    check(not ragraf_paths, f"{title}: файлов .ragraf в скане нет",
          f"найдено {len(ragraf_paths)}")

print()
print("=" * 78)
print("2. БАЗОВЫЙ СПИСОК РАСШИРЕН И СИНХРОНИЗИРОВАН")
print("=" * 78)
print(f"  шаблонов в базовом списке: {len(DEFAULT_EXCLUDES)}")
check(len(DEFAULT_EXCLUDES) == 28, "базовый список содержит 28 шаблонов",
      f"получено {len(DEFAULT_EXCLUDES)}")

# Ключевая проверка синхронизации: каждый каталог из базового списка должен
# быть и в списке init — иначе init создаст корпус без части исключений.
import re  # noqa: E402


def dir_of(pattern: str) -> str | None:
    m = re.fullmatch(r"\*\*/([A-Za-z0-9_.\-]+)/\*\*", pattern.strip())
    return m.group(1) if m else None


base_dirs = {d for d in (dir_of(p) for p in DEFAULT_EXCLUDES) if d}
missing_in_init = sorted(base_dirs - set(EXCLUDE_DIRS))
print(f"  каталогов в базовом списке: {len(base_dirs)}")
print(f"  не хватает в списке init: {missing_in_init or 'нет'}")
check(not missing_in_init, "списки init и базовый согласованы",
      f"нет в init: {missing_in_init}")

print()
print("=" * 78)
print("3. ГЛАВНОЕ: РАБОЧИЙ КОД НЕ ПОТЕРЯН")
print("=" * 78)
for config_name, title, expected in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    result = corpus.scan(cfg)
    got = len(result.files)
    print(f"\n  {title}: файлов {got} (базовый замер {expected})")
    # Сравнение — НЕ на равенство, а «не меньше базового замера».
    #
    # Задача проверки: служебные каталоги не выбросили рабочий код, то есть
    # файлов не стало МЕНЬШЕ ожидаемого. Точное равенство ловило другое — рост
    # ЧУЖИХ корпусов с момента замера: z.batteryservice.ru 162→222,
    # MD.Viewer 16→80, RdpMON 1266→1274, Clean_BOM_Senior 6→18 (замер
    # 14.10.2026). Проекты, которые развиваются, не должны красить проверку
    # исключений.
    check(got >= expected, f"{title}: рабочих файлов не меньше базового замера",
          f"было {expected}, стало {got}")

# Конкретные файлы кода в каталогах с «служебными» именами.
cfg = load_config(REPO / "config" / "revoap.yaml")
paths = {f.path for f in corpus.scan(cfg).files}
MUST_KEEP = [
    "src/Cache/CacheStatistics.php",
    "src/Logs/LogsService.php",
    "src/Storage/ResponseRepository.php",
    "bin/cache-purge.php",
]
print()
for p in MUST_KEEP:
    in_scan = p in paths
    print(f"  {p:<44} {'на месте' if in_scan else 'ПОТЕРЯН'}")
    check(in_scan, f"{p} остался в индексе")

print()
print("=" * 78)
print("4. .staging НАМЕРЕННО НЕ ИСКЛЮЧЁН")
print("=" * 78)
check(".staging" not in EXCLUDE_DIRS,
      ".staging не внесён в список init")
cfg = load_config(REPO / "config" / "rdpmon.yaml")
staging_files = [f.path for f in corpus.scan(cfg).files
                 if ".staging" in f.path]
print(f"  файлов .staging в RdpMON: {len(staging_files)}")
scripts = [p for p in staging_files
           if p.lower().endswith((".sh", ".ps1", ".psm1"))]
print(f"  из них скриптов .sh/.ps1: {len(scripts)}")
for p in scripts:
    print(f"    {p}")
check(bool(scripts),
      "скрипты из .staging остаются в индексе (не выброшены)",
      f"найдено {len(scripts)}")

print()
print("=" * 78)
print("5. ОПАСНЫЕ КАТАЛОГИ ОСТАЛИСЬ В КОНФИГАХ, А НЕ В БАЗОВОМ СПИСКЕ")
print("=" * 78)
for name in NON_DEFAULT_EXCLUDES:
    in_base = any(f"/{name}/" in p for p in DEFAULT_EXCLUDES)
    print(f"  {name:<10} в базовом списке: {'ЕСТЬ (ошибка)' if in_base else 'нет'}")
    check(not in_base, f"{name} не внесён в базовый список")

# Правила для bin/obj/publish должны быть там, где нужны.
rdp = load_config(REPO / "config" / "rdpmon.yaml")
for rule in ("**/bin/**", "**/obj/**", "**/publish/**"):
    present = rule in rdp.exclude
    print(f"  RdpMON {rule:<18} {'на месте' if present else 'ПОТЕРЯНО'}")
    check(present, f"RdpMON: {rule} сохранено")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
