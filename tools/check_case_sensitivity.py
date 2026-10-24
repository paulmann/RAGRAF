"""Проверка: ловит ли шаблон каталоги с большой буквы (src/Cache).

Решающая проверка перед правкой исключений. В корпусе revoap рабочий код
лежит в каталогах с ЗАГЛАВНОЙ буквы:

    src/Cache/CacheStatistics.php
    src/Logs/LogsService.php
    src/Storage/ResponseRepository.php

Если шаблон `**/cache/**` ловит и `Cache` (без учёта регистра), то внесение
имени `cache` в базовый список исключений выбросит эти файлы из индекса.

Проверяется фактически: и функцией сопоставления, и через реальный скан
(фильтрацией по исключающему шаблону).

Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import corpus, util  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

print("=" * 78)
print("1. СОПОСТАВЛЕНИЕ: РЕГИСТР ИМЕЕТ ЗНАЧЕНИЕ?")
print("=" * 78)
CASES = [
    ("src/cache/X.php", "**/cache/**"),
    ("src/Cache/X.php", "**/cache/**"),
    ("src/CACHE/X.php", "**/cache/**"),
    ("src/Cache/X.php", "**/Cache/**"),
    ("src/logs/L.php", "**/logs/**"),
    ("src/Logs/L.php", "**/logs/**"),
    ("src/Storage/S.php", "**/storage/**"),
    ("src/Storage/S.php", "**/Storage/**"),
]
print(f"\n  {'путь':<26} {'шаблон':<18} совпало")
print("  " + "-" * 56)
for path, pattern in CASES:
    hit = util.matches_any(path, [pattern])
    print(f"  {path:<26} {pattern:<18} {'ДА' if hit else 'нет'}")

print()
print("=" * 78)
print("2. ЕСТЬ ЛИ ЭТИ ФАЙЛЫ В СКАНЕ (точная фильтрация)")
print("=" * 78)
cfg = load_config(REPO / "config" / "revoap.yaml")
result = corpus.scan(cfg)
print(f"  корпус {cfg.project}: файлов {len(result.files)}")

# Точный поиск без учёта регистра: именно он даёт верную картину.
TARGETS = [
    "src/Cache/CacheStatistics.php",
    "src/Cache/ModelResponseCache.php",
    "src/Logs/LogsService.php",
    "src/Storage/ResponseRepository.php",
    "src/Storage/VectorStore.php",
]
paths = {f.path for f in result.files}
for t in TARGETS:
    print(f"  {t:<44} в скане: {'ДА' if t in paths else 'нет'}")

print()
print("=" * 78)
print("3. ЧТО БУДЕТ, ЕСЛИ ДОБАВИТЬ ШАБЛОНЫ В БАЗОВЫЙ СПИСОК")
print("=" * 78)
# Симулируется: применяются шаблоны к файлам скана.
DANGEROUS = ["**/cache/**", "**/logs/**", "**/bin/**", "**/storage/**"]
print(f"\n  {'шаблон':<20} {'выпадет файлов':>15}  примеры")
print("  " + "-" * 72)
for pattern in DANGEROUS:
    dropped = [f.path for f in result.files if util.matches_any(f.path, [pattern])]
    examples = ", ".join(dropped[:3])
    print(f"  {pattern:<20} {len(dropped):>15}  {examples[:60]}")

print()
print("=" * 78)
print("4. СРАВНЕНИЕ С БЕЗОПАСНЫМИ ШАБЛОНАМИ")
print("=" * 78)
SAFE = ["**/__pycache__/**", "**/.pytest_cache/**", "**/.mypy_cache/**",
        "**/.venv/**", "**/venv/**", "**/.idea/**", "**/.vscode/**",
        "**/coverage/**", "**/TestResults/**", "**/node_modules/**",
        "**/vendor/**", "**/bower_components/**", "**/.nuget/**",
        "**/target/**", "**/.ragraf/**"]
print(f"\n  {'шаблон':<26} {'выпадет файлов':>15}")
print("  " + "-" * 44)
for pattern in SAFE:
    dropped = [f.path for f in result.files if util.matches_any(f.path, [pattern])]
    print(f"  {pattern:<26} {len(dropped):>15}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Если шаблон ловит каталоги независимо от регистра (а это ожидаемо),
  то безопасных исключений по имени мало: `cache`, `logs`, `bin`, `storage`
  задевают рабочий код проекта.

  Вносить в базовый список можно только те, содержимое которых однозначно
  служебное — и это подтверждено нулём выпавших файлов проекта.
""")
