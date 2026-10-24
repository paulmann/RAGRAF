"""Проверка конфига корпуса RAGRAF: исключения работают точно.

Прошлый замер показал «catalog: ПРОТЕКЛО 5» — но это ложная тревога моей
проверки: она искала подстроку `catalog` в пути, а нашла файлы
`src/ragraf/catalog.py` и `tests/test_sagaai_catalog.py` — это КОД проекта,
его исключать нельзя.

Здесь проверяется точно: по КАТАЛОГАМ верхнего уровня, а не по подстроке.

Заодно сверяется, что весь настоящий код RAGRAF попал в индекс.
"""

from __future__ import annotations

import pathlib
import sys
from collections import Counter

REPO = pathlib.Path(r"C:\AutoClaw\RAGRAF")
sys.path.insert(0, str(REPO / "src"))

from ragraf import corpus  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

cfg = load_config(REPO / "config" / "ragraf.yaml")
result = corpus.scan(cfg)
print(f"  файлов к индексации: {len(result.files)}")

# Проверка по каталогам ВЕРХНЕГО УРОВНЯ, а не по подстроке.
tops = Counter()
for f in result.files:
    parts = pathlib.PurePath(str(f.path)).parts
    tops[parts[0] if len(parts) > 1 else "(корень)"] += 1

print(f"\n  состав по каталогам верхнего уровня:")
for name, n in tops.most_common(20):
    print(f"    {name:<26} {n}")

print()
print("=" * 78)
print("ПРОВЕРКА ИСКЛЮЧЕНИЙ (по каталогу верхнего уровня)")
print("=" * 78)
MUST_BE_ABSENT = [
    (".staging", "временные файлы сессий"),
    (".tools", "JDK, Joern, GPU-библиотеки"),
    (".ort_gpu_probe", "скопированные зависимости"),
    (".zwork", "журналы фоновых задач"),
    ("workspace", "рабочие файлы Joern"),
    ("catalog", "реестр и настройки"),
    (".ragraf", "индекс RAGRAF"),
    ("__pycache__", "кеш Python"),
]
ok = True
for name, why in MUST_BE_ABSENT:
    present = tops.get(name, 0)
    status = "исключён" if present == 0 else f"ПРОТЕКЛО: {present} файлов"
    if present:
        ok = False
    print(f"  {name:<20} {status:<24} — {why}")

print()
print("=" * 78)
print("КЛЮЧЕВОЙ КОД ПРОЕКТА НА МЕСТЕ")
print("=" * 78)
MUST_BE_PRESENT = {
    "src": "исходники RAGRAF",
    "tests": "проверки",
    "tools": "инструменты",
    "config": "конфигурации корпусов",
    "docs": "документация",
}
for name, why in MUST_BE_PRESENT.items():
    n = tops.get(name, 0)
    status = f"{n} файлов" if n else "ПРОПАЛО"
    if not n:
        ok = False
    print(f"  {name:<10} {status:<12} — {why}")

print()
print("=" * 78)
print("ТОЧЕЧНАЯ ПРОВЕРКА ВАЖНЫХ ФАЙЛОВ")
print("=" * 78)
paths = {str(f.path) for f in result.files}
IMPORTANT = [
    "src/ragraf/telemetry.py",
    "src/ragraf/facade.py",
    "src/ragraf/mcp_server.py",
    "src/ragraf/cpg.py",
    "src/ragraf/indexer.py",
    "src/ragraf/catalog.py",
    "tools/ragraf.py",
    "tools/ragraf-admin.py",
]
for rel in IMPORTANT:
    present = rel in paths
    if not present:
        ok = False
    print(f"  {rel:<32} {'есть' if present else 'ПРОПАЛ'}")

# Файл с секретами не должен попадать.
secrets = [p for p in result.files if ".local." in str(p.path)]
print(f"\n  файлов с '.local.' (секреты): {len(secrets)}")
if secrets:
    ok = False
    for s in secrets[:5]:
        print(f"    ПРОТЕКЛО: {s.path}")

print()
print("=" * 78)
print(f"ИТОГ: {'настройка верна' if ok else 'ТРЕБУЕТСЯ ВНИМАНИЕ'}")
print("=" * 78)
print(f"""
  Было при `init`: 2638 файлов, 894 МБ — в индекс попадали JDK, Joern,
  скопированные зависимости и временные файлы.
  Стало: {len(result.files)} файлов — только код проекта.
""")
raise SystemExit(0 if ok else 1)
