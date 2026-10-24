"""Проверка промпта после добавления раздела об исключениях.

Раздел утверждает конкретные числа и списки. Проверяется, что они совпадают
с кодом: иначе агент будет действовать по неверным правилам, а расхождение
в списке исключений опаснее всего — ошибка молча выбросит файлы из индекса.

Проверяется также кодировка: файл правился скриптом.
"""

from __future__ import annotations

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
PROMPT = pathlib.Path.home() / ".pi/agent/AGENTS.md"
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import DEFAULT_EXCLUDES, NON_DEFAULT_EXCLUDES  # noqa: E402

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


data = PROMPT.read_bytes()
text = data.decode("utf-8")
section = text[text.find("### 8.17"):] if "### 8.17" in text else ""

print("=" * 78)
print("1. КОДИРОВКА И ЦЕЛОСТНОСТЬ")
print("=" * 78)
print(f"  размер: {len(data)} байт, строк {text.count(chr(10)) + 1}")
check(data[:3] != b"\xef\xbb\xbf", "нет BOM")
check("\r\n" not in text, "переводы строк LF")
sections = sorted(set(re.findall(r"^### 8\.\d+", text, re.MULTILINE)))
print(f"  разделов 8.x: {len(sections)}")
check(len(sections) >= 17, "все 17 разделов 8.x на месте",
      f"найдено {len(sections)}")
check(bool(section), "раздел об исключениях присутствует")

print()
print("=" * 78)
print("2. ЧИСЛА В ПРОМПТЕ СОВПАДАЮТ С КОДОМ")
print("=" * 78)
count = len(DEFAULT_EXCLUDES)
print(f"  шаблонов в DEFAULT_EXCLUDES: {count}")
check(str(count) in section, "число шаблонов в промпте = коду",
      f"искалось {count}")

# Каждый каталог из базового списка должен быть назван в разделе.
def dir_of(pattern: str) -> str | None:
    m = re.fullmatch(r"\*\*/([A-Za-z0-9_.\-]+)/\*\*", pattern.strip())
    return m.group(1) if m else None


base_dirs = {d for d in (dir_of(p) for p in DEFAULT_EXCLUDES) if d}
missing = [d for d in sorted(base_dirs) if d not in section]
print(f"  каталогов в базовом списке: {len(base_dirs)}")
print(f"  не упомянуты в промпте: {missing or 'нет'}")
check(not missing, "все базовые каталоги упомянуты в промпте",
      f"нет: {missing}")

# Каждый «опасный» каталог должен быть назван как НЕ входящий в базовый.
missing_risky = [d for d in NON_DEFAULT_EXCLUDES if d not in section]
print(f"  опасных каталогов: {len(NON_DEFAULT_EXCLUDES)}")
print(f"  не упомянуты: {missing_risky or 'нет'}")
check(not missing_risky, "все опасные каталоги упомянуты как исключаемые вручную",
      f"нет: {missing_risky}")

print()
print("=" * 78)
print("3. ГЛАВНОЕ УТВЕРЖДЕНИЕ: .ragraf ОБЯЗАТЕЛЕН")
print("=" * 78)
in_code = any(".ragraf" in p for p in DEFAULT_EXCLUDES)
print(f"  .ragraf в базовом списке кода: {'ДА' if in_code else 'НЕТ'}")
check(in_code, "в коде .ragraf исключён базово")
check(".ragraf" in section, "в промпте .ragraf назван обязательным")
check("не отключается" in section,
      "промпт говорит, что правило не отключается")

# Проверяется фактическая работа правила.
from ragraf import util  # noqa: E402

CASES = [
    (".ragraf/index/graph/graph.sqlite", True),
    (".ragraf/cpg/rev-0001.cpg.bin", True),
    ("sub/project/.ragraf/data.db", True),
    ("src/ragraf/config.py", False),
]
for path, should_exclude in CASES:
    hit = bool(util.matches_any(path, DEFAULT_EXCLUDES))
    ok = hit == should_exclude
    print(f"  {path:<44} {'исключён' if hit else 'в индексе':<12} "
          f"{'верно' if ok else 'ОШИБКА'}")
    check(ok, f"{path}: правило работает")

print()
print("=" * 78)
print("4. ФАКТЫ ЗАМЕРОВ В ПРОМПТЕ")
print("=" * 78)
FACTS = {
    "24": "каталогов в базовом списке",
    "6": "рабочих PHP-скриптов в bin revoap",
    "168": "сгенерированных .cs в obj RdpMON",
    "406": "файлов сборки в publish RdpMON",
    "15": "файлов кеша в revoap",
    "src/Cache/CacheStatistics.php": "пример спасённого файла",
    "src/Logs/LogsService.php": "второй пример",
    "measure_safe_excludes": "инструмент замера",
}
for probe, what in FACTS.items():
    present = probe in section
    print(f"  {probe:<34} ({what}): {'есть' if present else 'НЕТ'}")
    check(present, f"в промпте есть {probe}", what)

print()
print("=" * 78)
print("5. РЕГИСТРОЗАВИСИМОСТЬ ОПИСАНА")
print("=" * 78)
check("РЕГИСТРОЗАВИСИМО" in section,
      "промпт предупреждает о регистрозависимости")
# Проверка факта: шаблоны действительно регистрозависимы.
lower_hit = bool(util.matches_any("src/cache/X.php", DEFAULT_EXCLUDES))
upper_hit = bool(util.matches_any("src/Cache/X.php", DEFAULT_EXCLUDES))
print(f"  src/cache/X.php: {'исключён' if lower_hit else 'в индексе'}")
print(f"  src/Cache/X.php: {'исключён' if upper_hit else 'в индексе'}")
check(lower_hit == upper_hit,
      "регистр не влияет на базовые правила (Cache не задет)",
      f"cache={lower_hit}, Cache={upper_hit}")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
