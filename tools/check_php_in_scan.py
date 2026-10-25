"""Точная проверка: входит ли src/cache/*.php в скан корпуса revoap.

Противоречие, которое надо снять:
  * замер по каталогам сказал: `cache` содержит 20 файлов и ОБХОДИТСЯ;
  * фильтр по скану сказал: файлов с «/cache/» в пути — 0.

Одно из двух: либо файлы не проходят include/языки, либо ошибка в моём
фильтре. От ответа зависит вывод: если рабочие PHP-файлы из `src/cache`
в индекс не попадают, то исключение `cache` безопасно; если попадают —
исключение их выбросит.

Проверяется прямым перечислением всех .php в скане и всех .php на диске
под src/, с точным указанием, какие из них отсеяны и почему.

Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import corpus, util  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

cfg = load_config(REPO / "config" / "revoap.yaml")
root = pathlib.Path(str(cfg.root))
print(f"корпус: {cfg.project}, корень: {root}")

print()
print("=" * 78)
print("1. ФАЙЛЫ .php В СКАНЕ: ЕСТЬ ЛИ СРЕДИ НИХ src/cache и src/logs")
print("=" * 78)
result = corpus.scan(cfg)
php_in_scan = sorted(f.path for f in result.files if f.path.endswith(".php"))
print(f"  всего .php в скане: {len(php_in_scan)}")
interesting = [p for p in php_in_scan
               if "cache" in p.lower() or "logs" in p.lower()
               or "storage" in p.lower() or "bin" in p.lower()]
print(f"  из них с cache/logs/storage/bin в пути: {len(interesting)}")
for p in interesting:
    print(f"    {p}")

print()
print("=" * 78)
print("2. ФАЙЛЫ .php НА ДИСКЕ ПОД src/: СКОЛЬКО ИХ")
print("=" * 78)
src = root / "src"
if src.exists():
    disk_php = sorted(
        p.relative_to(root) for p in src.rglob("*.php") if p.is_file()
    )
    print(f"  .php на диске под src/: {len(disk_php)}")
    disk_with_marker = [
        p for p in disk_php
        if any(m in str(p).lower().replace("\\", "/")
               for m in ("/cache/", "/logs/", "/storage/", "/bin/"))
    ]
    print(f"  из них с cache/logs/storage/bin: {len(disk_with_marker)}")
    for p in disk_with_marker:
        print(f"    {str(p).replace(chr(92), '/')}")
else:
    print("  каталога src нет")

print()
print("=" * 78)
print("3. РАЗБОР: ПОЧЕМУ КОНКРЕТНЫЙ ФАЙЛ ПОПАЛ ИЛИ НЕ ПОПАЛ")
print("=" * 78)
PROBES = [
    "src/cache/CacheStatistics.php",
    "src/cache/ModelResponseCache.php",
    "src/logs/LogsService.php",
    "src/storage/ResponseRepository.php",
    "bin/cache-purge.php",
]
for probe in PROBES:
    abs_path = root / probe
    in_scan = probe in php_in_scan
    print(f"\n  {probe}")
    print(f"    существует на диске: {abs_path.exists()}")
    print(f"    есть в скане:        {in_scan}")
    # Пошагово: что говорит каждый фильтр.
    hit_secret = util.matches_any(probe, list(cfg.secrets_exclude))
    hit_exc = util.matches_any(probe, list(cfg.exclude))
    hit_inc = util.matches_any(probe, list(cfg.include))
    lang = cfg.language_of(probe)
    print(f"    секретный шаблон:    {hit_secret}")
    print(f"    шаблон exclude:      {hit_exc}")
    print(f"    шаблон include:      {hit_inc}")
    print(f"    язык:                {lang}")
    print(f"    язык в корпусе:      {lang in set(cfg.languages) if lang else '—'}")

print()
print("=" * 78)
print("4. СКОЛЬКО ВСЕГО ФАЙЛОВ НА ДИСКЕ ПОД src/ И СКОЛЬКО В СКАНЕ")
print("=" * 78)
if src.exists():
    all_disk = [p for p in src.rglob("*") if p.is_file()]
    in_scan_src = [p for p in result.files if p.path.startswith("src/")]
    print(f"  на диске под src/:      {len(all_disk)}")
    print(f"  в скане с префиксом src/: {len(in_scan_src)}")
    # Какие расширения отсеиваются.
    from collections import Counter

    disk_ext = Counter(p.suffix.lower() for p in all_disk)
    scan_ext = Counter(pathlib.Path(p.path).suffix.lower() for p in in_scan_src)
    print(f"\n  {'расширение':<12} {'на диске':>9} {'в скане':>8}  отсеяно")
    print("  " + "-" * 48)
    for ext in sorted(disk_ext, key=lambda e: -disk_ext[e])[:14]:
        d, s = disk_ext[ext], scan_ext.get(ext, 0)
        print(f"  {ext:<12} {d:>9} {s:>8}  {d - s}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Здесь видно окончательно: попадают ли рабочие PHP-файлы из подкаталогов
  с «служебными» именами в индекс. Это и определяет, можно ли вносить
  такие имена в базовый список исключений.
""")
