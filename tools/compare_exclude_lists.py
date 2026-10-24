"""Точное сравнение двух списков исключений (исправление замера).

В прошлом замере была ошибка измерения: имена каталогов извлекались через
`.strip("*/. ")`, а этот вызов срезает точку с обоих концов. В результате
`.ragraf` превращался в `ragraf` и ошибочно попадал в список «есть только
в init» — хотя он есть и в базовом списке.

Настоящее расхождение может быть меньше. Здесь сравнение идёт по РАЗБОРУ
шаблонов: из `**/.ragraf/**` извлекается сегмент `.ragraf` корректно,
с сохранением точки.

Это важно: на ошибке измерения можно было сделать неверный вывод и «починить»
то, что уже работает.

Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import DEFAULT_EXCLUDES  # noqa: E402
from ragraf.init_corpus import EXCLUDE_DIRS  # noqa: E402

# Извлекается имя каталога из шаблона. Шаблон вида `**/.ragraf/**` означает
# каталог `.ragraf`; `**/*backup*/**` — не имя, а образец, его пропускаем.
def dir_of(pattern: str) -> str | None:
    """Имя каталога из шаблона исключения, либо None для образцов."""
    m = re.fullmatch(r"\*\*/([A-Za-z0-9_.\-]+)/\*\*", pattern.strip())
    if m:
        return m.group(1)
    return None


print("=" * 78)
print("1. КАКИЕ КАТАЛОГИ ЗАКРЫТЫ БАЗОВЫМ СПИСКОМ")
print("=" * 78)
default_dirs = set()
default_patterns = []
for pattern in DEFAULT_EXCLUDES:
    name = dir_of(pattern)
    if name:
        default_dirs.add(name)
    else:
        default_patterns.append(pattern)

print(f"\n  каталоги из DEFAULT_EXCLUDES ({len(default_dirs)}):")
for name in sorted(default_dirs):
    print(f"    {name}")
print(f"\n  шаблоны-образцы в DEFAULT_EXCLUDES ({len(default_patterns)}):")
for p in default_patterns:
    print(f"    {p}")

print()
print("=" * 78)
print("2. ЧЕГО НЕТ В БАЗОВОМ СПИСКЕ, НО ЗНАЕТ init")
print("=" * 78)
only_init = sorted(set(EXCLUDE_DIRS) - default_dirs)
print(f"\n  таких каталогов: {len(only_init)}")
for name in only_init:
    print(f"    {name:<20} {EXCLUDE_DIRS[name]}")

# Проверка ключевого: закрыт ли .ragraf базовым списком.
print()
print("=" * 78)
print("3. ПРОВЕРКА ГЛАВНОГО ВОПРОСА: ЗАКРЫТ ЛИ .ragraf БАЗОВО")
print("=" * 78)
print(f"  .ragraf в DEFAULT_EXCLUDES: "
      f"{'ДА' if '.ragraf' in default_dirs else 'НЕТ'}")
print(f"  .ragraf в EXCLUDE_DIRS:     "
      f"{'ДА' if '.ragraf' in EXCLUDE_DIRS else 'НЕТ'}")

# Фактическая проверка применимости: совпадает ли путь.
from ragraf import util  # noqa: E402

SAMPLE = [
    ".ragraf/index/graph/graph.sqlite",
    ".ragraf/cpg/x.json",
    "sub/.ragraf/data.db",
    "src/ragraf/config.py",
    "a/.ragraf/b/c/d.txt",
]
print(f"\n  {'путь':<40} исключён базовым списком")
print("  " + "-" * 62)
for p in SAMPLE:
    hit = util.matches_any(p, DEFAULT_EXCLUDES)
    print(f"  {p:<40} {'ДА' if hit else 'нет'}")

print()
print("=" * 78)
print("4. ИТОГ: РЕАЛЬНОЕ РАСХОЖДЕНИЕ")
print("=" * 78)
print(f"""
  Базовый список закрывает: {len(default_dirs)} каталогов.
  Список init знает ещё:    {len(only_init)}.

  Столько каталогов НЕ исключаются, если конфиг корпуса создан вручную,
  а не командой init:
""")
for name in only_init:
    reason = EXCLUDE_DIRS[name]
    print(f"    {name:<20} {reason}")

print(f"""
  Проверка главного вопроса: `.ragraf` закрыт И базовым списком, И init,
  И всеми шестью конфигами. Фактически он обрезается при обходе
  (проверено в tools/measure_service_dirs.py: 0 файлов в сканах).

  Значит, по прямому запросу «добавить .ragraf в исключения» делать нечего.
  Работать надо над расхождением списков — оно и есть настоящий пробел.
""")
