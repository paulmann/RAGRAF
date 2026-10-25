"""Проверка системного промпта после правки: целостность и кодировка.

Промпт — рабочий инструмент агента, и его порча не проявляется сразу:
сломанная разметка или потерянная строка выяснятся посреди работы.
Поэтому проверяется:
  1. кодировка (UTF-8 без BOM), переводы строк (LF — файл правится скриптом);
  2. все разделы 8.x на месте: не потерялись при вставке;
  3. новые разделы 8.14 и 8.15 есть и непусты;
  4. ключевые утверждения из новых разделов совпадают с фактами кода
     (набор языков, список ролей) — иначе промпт будет врать агенту.
"""

from __future__ import annotations

import pathlib
import re
import sys

TARGET = pathlib.Path.home() / ".pi/agent/AGENTS.md"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

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


data = TARGET.read_bytes()
text = data.decode("utf-8")
lines = text.split("\n")

print("=" * 78)
print("1. КОДИРОВКА И ПЕРЕВОДЫ СТРОК")
print("=" * 78)
has_bom = data[:3] == b"\xef\xbb\xbf"
has_crlf = "\r\n" in text
print(f"  размер: {len(data)} байт")
print(f"  строк:  {len(lines)}")
print(f"  BOM:    {has_bom}")
print(f"  CRLF:   {has_crlf}")
check(not has_bom, "нет BOM (UTF-8 без BOM)")
check(not has_crlf, "нет CRLF (переводы строк LF)")

print()
print("=" * 78)
print("2. ВСЕ РАЗДЕЛЫ 8.x НА МЕСТЕ")
print("=" * 78)
expected = [f"### 8.{i}" for i in range(1, 16)]
found = {m.group(0) for m in re.finditer(r"^### 8\.\d+", text, re.MULTILINE)}
print(f"  найдено разделов: {len(found)}")
for section in expected:
    mark = "есть" if section in found else "НЕТ"
    print(f"    {section}: {mark}")
missing = [s for s in expected if s not in found]
check(not missing, "все разделы 8.1–8.15 присутствуют", f"нет: {missing}")

print()
print("=" * 78)
print("3. НОВЫЕ РАЗДЕЛЫ НЕПУСТЫ")
print("=" * 78)
for section, min_lines in (("### 8.14", 40), ("### 8.15", 40)):
    start = text.find(section)
    if start < 0:
        check(False, f"{section} найден")
        continue
    # Раздел тянется до следующего «### » уровня 8 или до конца файла.
    rest = text[start + len(section):]
    nxt = re.search(r"\n### 8\.", rest)
    body = rest[: nxt.start()] if nxt else rest
    count = body.count("\n")
    print(f"  {section}: {count} строк")
    check(count >= min_lines, f"{section} содержит не меньше {min_lines} строк",
          f"получено {count}")

print()
print("=" * 78)
print("4. УТВЕРЖДЕНИЯ ПРОМПТА СОВПАДАЮТ С КОДОМ")
print("=" * 78)
from ragraf.config import TREE_LANGS, LANG_BY_EXT  # noqa: E402
from ragraf.docrole import ROLES, ROLE_TITLES  # noqa: E402

print(f"  языков с разбором в коде: {len(TREE_LANGS)}")
print(f"  всего языков в карте:     {len(set(LANG_BY_EXT.values()))}")
print(f"  ролей документов:         {len(ROLES)}")

# Промпт обещает 16 языков с разбором — сверяется с кодом.
promise = re.search(r"их (\d+):", text)
if promise:
    promised = int(promise.group(1))
    print(f"  промпт обещает языков с разбором: {promised}")
    check(promised == len(TREE_LANGS),
          "число языков с разбором в промпте = коду",
          f"промпт {promised}, код {len(TREE_LANGS)}")

# Каждый язык из кода должен быть упомянут в разделе 8.14.
sec = text[text.find("### 8.14"):]
not_mentioned = [lang for lang in sorted(TREE_LANGS) if lang not in sec]
check(not not_mentioned, "все языки с разбором упомянуты в 8.14",
      f"нет: {not_mentioned}")

# Каждая роль должна быть в разделе 8.15.
sec15 = text[text.find("### 8.15"):]
roles_missing = [r for r in ROLES if f"`{r}`" not in sec15]
check(not roles_missing, "все роли документов упомянуты в 8.15",
      f"нет: {roles_missing}")

# Таблица ролей должна содержать правильные названия.
bad_titles = []
for role in ROLES:
    title = ROLE_TITLES[role]
    if title not in sec15:
        bad_titles.append(f"{role} → {title}")
check(not bad_titles, "названия ролей в промпте совпадают с кодом",
      f"расхождения: {bad_titles}")

print()
print("=" * 78)
print("5. ЧИСЛА ИЗ ЗАМЕРОВ НА МЕСТЕ")
print("=" * 78)
FACTS = {
    "1235": "файлов Perl в <полигон>",
    "1337": "файлов Python",
    "1217": "файлов C#",
    "1164": "файлов .cs в RdpMON",
    "96": "файлов PowerShell",
    "53": "файлов .ps1 разобрано полностью",
    "43": "файлов .ps1 с неполным разбором",
    # Числа, появившиеся после реализации схлопывания дублей (раздел 8.16).
    # Прежнее число 669 убрано из промпта осознанно: оно было из первой
    # разведки, а теперь стоит реализованный замер (3856 лишних копий).
    "3856": "лишних копий в <полигон> (реализованный замер)",
    "513": "групп копий в <полигон>",
    "5559": "документов просканировано",
    "699": "файлов в revoap до схлопывания",
    "592": "файлов после схлопывания",
    "1550": "документов после отсева мусора",
    "122": "файлов README",
    "21": "файлов AGENTS.md",
    "16": "языков с разбором деревом",
}
for number, what in FACTS.items():
    present = number in text
    print(f"  {number:<6} ({what}): {'есть' if present else 'НЕТ'}")
    check(present, f"число {number} присутствует", what)

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
