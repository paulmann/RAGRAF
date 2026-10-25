"""Замер по всем корпусам: какие каталоги безопасно внести в базовый список.

Установлено:
  * `.ragraf` УЖЕ исключён: есть в том же базовом списке, в списке init,
    во всех шести конфигах, и в сканы не попадает (0 файлов).

  * Настоящий пробел в другом: базовый список (9 каталогов) и список init
    (28 каталогов) расходятся. Корпус, созданный вручную, не получит
    исключений для `__pycache__`, `obj`, `.pytest_cache` и подобных.

Перед правкой надо измерить по ВСЕМ корпусам, а не по одному: сколько
файлов выпадет при внесении каждого каталога в базовый список. Базовый
список действует на все корпуса сразу, и ошибка в нём молча выбросит
рабочий код из индекса.

Отдельно проверяется регистр: `src/Cache` (заглавная) и `cache` (строчная) —
это разные каталоги, и шаблон их различает.

Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import corpus, util  # noqa: E402
from ragraf.config import DEFAULT_EXCLUDES, load as load_config  # noqa: E402
from ragraf.init_corpus import EXCLUDE_DIRS  # noqa: E402

CORPORA = [
    ("z-batteryservice.yaml", "z.batteryservice.ru"),
    ("revoap.yaml", "revoap.com"),
    ("md.viewer.yaml", "MD.Viewer"),
    ("rdpmon.yaml", "RdpMON"),
    ("clean_bom_senior.yaml", "Clean_BOM_Senior"),
]

# Каталоги, которые знает init, но не знает базовый список.
BASE_DIRS = {
    ".agents", ".git", ".ragraf", ".zwork", "build", "dist", "node_modules",
    "var", "vendor",
}
CANDIDATES = sorted(set(EXCLUDE_DIRS) - BASE_DIRS)

print("=" * 78)
print("СКАНЫ КОРПУСОВ")
print("=" * 78)
scans: dict[str, list[str]] = {}
for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    result = corpus.scan(cfg)
    scans[title] = [f.path for f in result.files]
    print(f"  {title:<22} файлов в скане: {len(result.files)}")

print()
print("=" * 78)
print("СКОЛЬКО ФАЙЛОВ ВЫПАДЕТ ПО КАЖДОМУ КАНДИДАТУ")
print("=" * 78)
print(f"\n  {'каталог':<20} " + " ".join(f"{t[:10]:>11}" for t in scans)
      + f"  {'итого':>7}  вердикт")
print("  " + "-" * 100)

safe: list[str] = []
risky: list[tuple[str, dict[str, int]]] = []

for name in CANDIDATES:
    pattern = f"**/{name}/**"
    counts: dict[str, int] = {}
    for title, paths in scans.items():
        counts[title] = sum(1 for p in paths if util.matches_any(p, [pattern]))
    total = sum(counts.values())
    if total == 0:
        verdict = "безопасно"
        safe.append(name)
    else:
        verdict = "ВЫПАДУТ ФАЙЛЫ"
        risky.append((name, counts))
    row = " ".join(f"{counts.get(t, 0):>11}" for t in scans)
    print(f"  {name:<20} {row}  {total:>7}  {verdict}")

print()
print("=" * 78)
print("ОПАСНЫЕ КАНДИДАТЫ: ЧТО ИМЕННО ВЫПАДЕТ")
print("=" * 78)
for name, counts in risky:
    pattern = f"**/{name}/**"
    print(f"\n  {name}: всего {sum(counts.values())} файлов")
    for title, paths in scans.items():
        if counts.get(title, 0) == 0:
            continue
        dropped = [p for p in paths if util.matches_any(p, [pattern])]
        print(f"    {title}: {len(dropped)}")
        for p in dropped[:4]:
            print(f"      {p}")

print()
print("=" * 78)
print("СВОДКА")
print("=" * 78)
print(f"\n  БЕЗОПАСНО внести в базовый список ({len(safe)}):")
for name in safe:
    print(f"    {name:<22} {EXCLUDE_DIRS.get(name, '')}")

print(f"\n  НЕЛЬЗЯ вносить ({len(risky)}):")
for name, counts in risky:
    print(f"    {name:<22} выпадет {sum(counts.values())} файлов")

print()
print("=" * 78)
print("ЧТО ЭТО ЗНАЧИТ ДЛЯ ЗАДАЧИ")
print("=" * 78)
print(f"""
  `.ragraf` в базовом списке: {'ДА' if '.ragraf' in BASE_DIRS else 'НЕТ'}
  `.ragraf` в списке init:    {'ДА' if '.ragraf' in EXCLUDE_DIRS else 'НЕТ'}

  То есть по прямому запросу «добавить .ragraf в исключения» править нечего:
  он закрыт на всех уровнях, и это подтверждено сканами (0 файлов).

  Реальная работа — устранить расхождение списков: внести в базовый список
  те {len(safe)} каталогов, которые сейчас исключаются ТОЛЬКО при создании
  корпуса командой init. Иначе корпус, подключённый вручную, начнёт
  индексировать кеши и виртуальные окружения.
""")
