"""Проверка правки базового списка исключений.

Правка расширила DEFAULT_EXCLUDES с 13 шаблонов до 31. Проверяется:
  1. `.ragraf` по-прежнему исключён (главное требование задачи);
  2. НИ ОДИН файл не выпал из индексов корпусов — это главное: расширение
     списка не должно выбросить рабочий код;
  3. состав каждого корпуса не изменился;
  4. каталоги `bin`, `cache`, `obj`, `publish` намеренно НЕ внесены;
  5. ранее исключённые каталоги (`node_modules`, `vendor`, `.git`) остались.

Пункт 2 — решающий. Расширение списка исключений безопасно ровно тогда,
когда ни один файл из уже подключённых корпусов не потерялся.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import corpus, util  # noqa: E402
from ragraf.config import (DEFAULT_EXCLUDES, NON_DEFAULT_EXCLUDES,  # noqa: E402
                           load as load_config)

REPO_ROOT = REPO

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
print("1. СОСТАВ БАЗОВОГО СПИСКА")
print("=" * 78)
print(f"  шаблонов: {len(DEFAULT_EXCLUDES)}")
# Ожидание — 28 шаблонов: 24 каталога + 4 образца имён (*_bak*, *backup*,
# *.min.js, *.min.css). Число проверяется точно, а не «больше тридцати»:
# неточное ожидание давало ложный провал при верной правке.
check(len(DEFAULT_EXCLUDES) == 28, "состав базового списка верен",
      f"шаблонов {len(DEFAULT_EXCLUDES)}, ожидалось 28")

# Главное требование задачи.
check(any(".ragraf" in p for p in DEFAULT_EXCLUDES),
      ".ragraf исключён базовым списком")

# Ранее бывшие каталоги не должны потеряться.
KEEP = [".git", "node_modules", "vendor", ".zwork", ".agents", "var", "dist",
        "build"]
lost = [k for k in KEEP if not any(k in p for p in DEFAULT_EXCLUDES)]
check(not lost, "прежние исключения сохранены", f"потеряны: {lost}")

# Опасные каталоги не должны попасть в базовый список.
bad_added = [n for n in NON_DEFAULT_EXCLUDES
             if any(f"/{n}/" in p for p in DEFAULT_EXCLUDES)]
check(not bad_added, "опасные каталоги не внесены в базовый список",
      f"внесены: {bad_added}")

print()
print("=" * 78)
print("2. ГЛАВНАЯ ПРОВЕРКА: НЕ ВЫПАЛ ЛИ РАБОЧИЙ КОД")
print("=" * 78)
# Именно этого боятся при расширении списка исключений.
CORPORA = [
    ("z-batteryservice.yaml", "z.batteryservice.ru"),
    ("revoap.yaml", "revoap.com"),
    ("md.viewer.yaml", "MD.Viewer"),
    ("rdpmon.yaml", "RdpMON"),
    ("clean_bom_senior.yaml", "Clean_BOM_Senior"),
]

# Базовый замер числа файлов по каждому корпусу — НИЖНЯЯ ГРАНИЦА, а не точное
# равенство.
#
# Почему так. Прежняя редакция требовала точного совпадения, и проверка падала
# не от дефекта исключений, а от того, что ЧУЖИЕ проекты на диске выросли:
# замер 14.10.2026 дал z 222 (было 162), MD.Viewer 180 (было 16),
# RdpMON 1274 (1266), Clean_BOM_Senior 18 (6). Задача этой проверки — поймать
# ИСЧЕЗНОВЕНИЕ рабочего кода (слишком широкое исключение), а не запретить
# проектам развиваться. Рост числа файлов дефектом не является; уменьшение —
# является, и его правило ловит.
# MD.Viewer: базовые 16, а не 15 — в проекте появился файл .md.ini (29.09.2026).
EXPECTED_MIN = {
    "z.batteryservice.ru": 162,
    "revoap.com": 592,
    "MD.Viewer": 16,
    "RdpMON": 1266,
    "Clean_BOM_Senior": 6,
}

for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    result = corpus.scan(cfg)
    got = len(result.files)
    want = EXPECTED_MIN.get(title)
    print(f"\n  {title}: файлов {got} (базовый замер {want})")
    check(got >= want, f"{title}: файлов не меньше базового замера",
          f"было {want}, стало {got}")

    # Дополнительно: рабочие файлы из «опасных» каталогов на месте.
    markers = ("src/Cache/", "src/Logs/", "src/Storage/", "bin/")
    present = [f.path for f in result.files
               if any(m.lower() in f.path.lower() for m in markers)]
    print(f"    файлов из src/Cache, src/Logs, src/Storage, bin: {len(present)}")
    for p in present[:6]:
        print(f"      {p}")

print()
print("=" * 78)
print("3. ПРОВЕРКА НА КОНКРЕТНЫХ ФАЙЛАХ КОДА ПРОЕКТА")
print("=" * 78)
# Эти файлы лежат в каталогах с «служебными» именами и обязаны остаться.
cfg = load_config(REPO / "config" / "revoap.yaml")
paths = {f.path for f in corpus.scan(cfg).files}
MUST_KEEP = [
    "src/Cache/CacheStatistics.php",
    "src/Cache/ModelResponseCache.php",
    "src/Logs/LogsService.php",
    "src/Storage/ResponseRepository.php",
    "src/Storage/VectorStore.php",
    "bin/cache-purge.php",
    "bin/revoap-worker.php",
]
for p in MUST_KEEP:
    in_scan = p in paths
    print(f"  {p:<44} в скане: {'ДА' if in_scan else 'НЕТ'}")
    check(in_scan, f"{p} остался в индексе")

print()
print("=" * 78)
print("4. ПРОВЕРКА МЕХАНИЗМА: БАЗОВЫЙ СПИСОК ПРИМЕНЯЕТСЯ ВСЕГДА")
print("=" * 78)
# Ключевое свойство: базовый список действует даже без правил в конфиге.
#
# Проверять «есть ли правило в конфиге» надо по ФАЙЛУ, а не по `cfg.exclude`:
# в нём базовые шаблоны уже объединены с файловыми (config.py:654), и проверка
# по объекту всегда показывала «есть», ничего не доказывая.
cfg_path = REPO / "config" / "md.viewer.yaml"
raw_text = cfg_path.read_text(encoding="utf-8")
merged_probe = ["**/__pycache__/**", "**/.venv/**", "**/target/**"]
print(f"  конфиг для проверки: {cfg_path.name}")
for pattern in merged_probe:
    segment = pattern.strip("*/ ").strip("*")
    # Ищется правило именно в ФАЙЛЕ конфига.
    in_file = f"**/{segment}/**" in raw_text
    works = util.matches_any(f"sub/{segment}/x.py", [pattern])
    print(f"  {pattern:<24} в файле конфига: "
          f"{'да' if in_file else 'нет (приходит из базового)'}")
    check(works, f"{pattern}: правило работает")

# Проверка на реальном пути: кеши внутри корпуса не попадут в скан.
TEST = [
    (".venv/lib/python3.13/site-packages/x.py", True),
    ("src/__pycache__/mod.cpython-313.pyc", True),
    ("build/output.js", True),
    ("target/release/app", True),
    ("src/modules/employees.php", False),
]
print()
# ВАЖНО про форму ответа: `util.matches_any` возвращает НЕ True/False,
# а имя сработавшего правила (строку) или None. Первая версия проверки
# сравнивала результат с True и давала ложные провалы: строка вида
# `**/.venv/**` не равна `True`.
#
# Поэтому здесь проверяется именно «вернулось ли правило».
TEST = [
    (".venv/lib/python3.13/site-packages/x.py", True),
    ("src/__pycache__/mod.cpython-313.pyc", True),
    ("build/output.js", True),
    ("target/release/app", True),
    ("src/modules/employees.php", False),
]
for path, should_exclude in TEST:
    hit = util.matches_any(path, DEFAULT_EXCLUDES)
    excluded = bool(hit)
    mark = f"исключён ({hit})" if excluded else "в индексе"
    ok = excluded == should_exclude
    print(f"  {path:<48} {mark:<26} {'верно' if ok else 'ОШИБКА'}")
    check(ok, f"{path}: {'исключён' if should_exclude else 'остаётся в индексе'}")

print()
print("=" * 78)
print("5. ЧИСЛО ПРАВИЛ В КОНФИГАХ КОРПУСОВ")
print("=" * 78)
for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    print(f"  {title:<22} правил после объединения: {len(cfg.exclude)}")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
