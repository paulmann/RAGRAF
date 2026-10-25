"""Проверка механизма: почему `**/cache/**` не поймал `src/cache/*.php`.

Это важно для решения об исключениях. Если шаблон не ловит вложенный
каталог, то правило «исключить cache» не сработает там, где нужно, — и
наоборот, может сработать там, где не нужно.

Проверяются:
  1. функция сопоставления `util.matches_any` на наборе путей;
  2. есть ли `src/cache/*.php` в скане корпуса revoap;
  3. какой шаблон их ловит, а какой нет.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import corpus, util  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

print("=" * 78)
print("1. СОПОСТАВЛЕНИЕ ШАБЛОНОВ (util.matches_any)")
print("=" * 78)
PATTERNS = ["**/cache/**", "**/logs/**", "**/.ragraf/**"]
PATHS = [
    "cache/bothub/x.json",
    "src/cache/CacheStatistics.php",
    "src/logs/LogsService.php",
    "logs/access.log",
    "src/storage/ResponseRepository.php",
    ".ragraf/cpg/x.json",
    "sub/dir/cache/y.txt",
]
print(f"\n  {'путь':<42} " + "  ".join(f"{p[:16]:<16}" for p in PATTERNS))
print("  " + "-" * 110)
for path in PATHS:
    marks = []
    for pattern in PATTERNS:
        marks.append("ДА" if util.matches_any(path, [pattern]) else "нет")
    print(f"  {path:<42} " + "  ".join(f"{m:<16}" for m in marks))

print()
print("=" * 78)
print("2. ЕСТЬ ЛИ src/cache/*.php В СКАНЕ КОРПУСА")
print("=" * 78)
cfg = load_config(REPO / "config" / "revoap.yaml")
result = corpus.scan(cfg)
targets = [f.path for f in result.files if "/cache/" in f.path
           or "/logs/" in f.path or "/storage/" in f.path]
print(f"\n  файлов с cache/logs/storage в пути: {len(targets)}")
for p in targets[:15]:
    print(f"    {p}")

# Проверка физического наличия файлов.
root = pathlib.Path(str(cfg.root))
for probe in ("src/cache/CacheStatistics.php", "src/logs/LogsService.php",
              "src/storage/ResponseRepository.php"):
    abs_path = root / probe
    print(f"\n  {probe}")
    print(f"    файл существует: {abs_path.exists()}")
    if abs_path.exists():
        # Проверяется, попадает ли он под правила корпуса.
        excluded = util.matches_any(probe, cfg.exclude)
        included = util.matches_any(probe, cfg.include)
        lang = cfg.language_of(probe)
        print(f"    исключён правилами: {excluded}")
        print(f"    проходит include:   {included}")
        print(f"    язык определён:     {lang}")
        print(f"    в языке корпуса:    {lang in set(cfg.languages)}")

print()
print("=" * 78)
print("3. СРАВНЕНИЕ: ЧТО ЛОВИТ КАЖДЫЙ ШАБЛОН")
print("=" * 78)
CASES = {
    "**/cache/**": ["cache/x.json", "src/cache/X.php", "a/b/cache/y.txt",
                    "cache", "src/cache"],
    "**/logs/**": ["logs/a.log", "src/logs/L.php", "a/logs/b.txt"],
}
for pattern, paths in CASES.items():
    print(f"\n  шаблон {pattern}:")
    for p in paths:
        print(f"    {p:<34} {'ДА' if util.matches_any(p, [pattern]) else 'нет'}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Проверка показывает, ловит ли шаблон вложенные каталоги с тем же именем.
  От этого зависит, безопасно ли вносить имя каталога в БАЗОВЫЙ список:
  базовый список действует на все корпуса, и ошибка в нём выбросит рабочий
  код из индекса без единого сообщения.
""")
