"""Поиск конфигов корпусов, где .ragraf НЕ исключён.

Прошлая проверка показала: в четырёх рабочих корпусах `.ragraf` исключён,
и в индексах служебных путей нет. Значит, задача «добавить .ragraf
в исключения» уже выполнена для них — но, возможно, не везде.

Проверяются ВСЕ конфиги в каталоге config/, включая служебные и те, что
созданы вручную (не командой init): именно там правило могло не появиться.

Кроме того, проверяется механизм: применяются ли базовые исключения
(DEFAULT_EXCLUDES) автоматически, или конфиг обязан перечислять их сам.
Это важно: если базовые не применяются, то КАЖДЫЙ новый корпус, созданный
без них, будет индексировать `.ragraf`.

Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import util  # noqa: E402
from ragraf.config import DEFAULT_EXCLUDES, load as load_config  # noqa: E402

CONFIG_DIR = REPO / "config"

print("=" * 78)
print("1. ВСЕ КОНФИГИ В config/: ЕСТЬ ЛИ ПРАВИЛО ДЛЯ .ragraf")
print("=" * 78)
print(f"\n  {'конфиг':<32} {'корпус':<20} {'шаблонов':>9}  есть .ragraf")
print("  " + "-" * 76)

bad: list[tuple[str, str]] = []
for path in sorted(CONFIG_DIR.glob("*.yaml")):
    try:
        cfg = load_config(path)
    except Exception as exc:
        print(f"  {path.name:<32} ОШИБКА ЧТЕНИЯ: {type(exc).__name__}")
        continue
    patterns = list(cfg.exclude)
    has = any(".ragraf" in p for p in patterns)
    mark = "да" if has else "НЕТ"
    print(f"  {path.name:<32} {cfg.project:<20} {len(patterns):>9}  {mark}")
    if not has:
        bad.append((path.name, cfg.project))

print()
if bad:
    print("  КОНФИГИ БЕЗ ПРАВИЛА ДЛЯ .ragraf:")
    for name, project in bad:
        print(f"    {name} (корпус {project})")
else:
    print("  во всех конфигах правило для .ragraf есть")

print()
print("=" * 78)
print("2. ПРИМЕНЯЮТСЯ ЛИ БАЗОВЫЕ ИСКЛЮЧЕНИЯ АВТОМАТИЧЕСКИ")
print("=" * 78)
# Ключевой вопрос: если конфиг НЕ перечисляет .ragraf, попадёт ли он в индекс?
# Проверяется по коду: как формируется итоговый список exclude при загрузке.
import inspect  # noqa: E402

import ragraf.config as config_mod  # noqa: E402

src = inspect.getsource(config_mod)
# Ищется место, где exclude из файла соединяется с DEFAULT_EXCLUDES.
lines = src.split("\n")
print("  строки, где упоминается DEFAULT_EXCLUDES в config.py:")
for i, line in enumerate(lines, 1):
    if "DEFAULT_EXCLUDES" in line:
        print(f"    {i:>4}: {line.strip()}")

print()
print("  строки, где формируется поле exclude:")
for i, line in enumerate(lines, 1):
    stripped = line.strip()
    if stripped.startswith("exclude=") or "exclude =" in stripped:
        print(f"    {i:>4}: {stripped}")

print()
print("=" * 78)
print("3. ПРОВЕРКА НА КОНФИГЕ БЕЗ ПРАВИЛА")
print("=" * 78)
# Проверяется: если убрать правило для .ragraf из конфига, попадёт ли
# служебный каталог в скан. Это и покажет, нужна ли правка.
cfg = load_config(REPO / "config" / "fixture.yaml") \
    if (REPO / "config" / "fixture.yaml").exists() \
    else load_config(REPO / "config" / "md.viewer.yaml")

print(f"  взят конфиг корпуса: {cfg.project}")
patterns_with = list(cfg.exclude)
patterns_without = [p for p in patterns_with if ".ragraf" not in p]

TEST_PATHS = [
    ".ragraf/index/graph/graph.sqlite",
    ".ragraf/cpg/rev-0001.cpg.bin",
    ".ragraf/drift/archive/x.md",
    "sub/.ragraf/data.db",
]
print(f"\n  {'путь':<42} {'с правилом':<12} без правила")
print("  " + "-" * 70)
for p in TEST_PATHS:
    with_rule = util.matches_any(p, patterns_with)
    without_rule = util.matches_any(p, patterns_without)
    print(f"  {p:<42} {'исключён' if with_rule else 'попадёт':<12} "
          f"{'исключён' if without_rule else 'ПОПАДЁТ'}")

print()
print("=" * 78)
print("4. ЕСТЬ ЛИ КАТАЛОГИ .ragraf ВНУТРИ КОРПУСОВ ФИЗИЧЕСКИ")
print("=" * 78)
for path in sorted(CONFIG_DIR.glob("*.yaml")):
    try:
        cfg = load_config(path)
    except Exception:
        continue
    root = pathlib.Path(str(cfg.root))
    if not root.exists():
        continue
    found = [d for d in root.rglob(".ragraf") if d.is_dir()]
    print(f"\n  {cfg.project} ({root}): каталогов .ragraf найдено {len(found)}")
    for d in found[:6]:
        try:
            n = len([f for f in d.rglob("*") if f.is_file()])
        except OSError:
            n = -1
        print(f"    {d.relative_to(root)}: файлов {n}")
    if len(found) > 6:
        print(f"    … и ещё {len(found) - 6}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Проверка отвечает на два разных вопроса:
    * «записано ли правило» — по конфигам;
    * «применяется ли оно» — по механизму и по индексу.

  Если во всех конфигах правило есть и базовые исключения применяются
  автоматически, то задача уже выполнена, и добавлять нечего. Тогда
  остаётся проверить только те конфиги, где правила нет.
""")
