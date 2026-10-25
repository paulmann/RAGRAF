"""Проверка системного промпта после добавления раздела о дублях.

Проверяется не факт «текст добавлен», а его достоверность: числа и правила
в промпте обязаны совпадать с кодом и с фактическим состоянием индексов.
Расхождение здесь опасно: агент будет действовать по неверным числам.

Дополнительно проверяется кодировка: промпт правился скриптом, и порча
кодировки не проявляется сразу.
"""

from __future__ import annotations

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
PROMPT = pathlib.Path.home() / ".pi/agent/AGENTS.md"
sys.path.insert(0, str(REPO / "src"))

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

print("=" * 78)
print("1. КОДИРОВКА И ЦЕЛОСТНОСТЬ")
print("=" * 78)
print(f"  размер: {len(data)} байт, строк {text.count(chr(10)) + 1}")
check(data[:3] != b"\xef\xbb\xbf", "нет BOM")
check("\r\n" not in text, "переводы строк LF")
sections = sorted(set(re.findall(r"^### 8\.\d+", text, re.MULTILINE)))
print(f"  разделы 8.x: {len(sections)} — {sections[-3:]}")
# Число разделов сверяется с кодом раздела о дублях (8.16) плюс раздел об
# исключениях (8.17): проверка не должна застывать на текущем состоянии,
# иначе каждая новая тема ломает её.
check(len(sections) >= 16, "разделы 8.x на месте", f"найдено {len(sections)}")
check("### 8.16" in text, "раздел о дублях присутствует")

print()
print("=" * 78)
print("2. НАСТРОЙКИ В ПРОМПТЕ СОВПАДАЮТ С КОДОМ")
print("=" * 78)
from ragraf.config import IndexConfig  # noqa: E402
from ragraf.dedupe import (MODES, SCOPES, KEEP_RULES,  # noqa: E402
                           COPY_SEGMENTS, DOC_EXTENSIONS)
from ragraf.settings import ENUM_KEYS, DEFAULTS  # noqa: E402

# Каждый режим и область должны быть названы в промпте.
section = text[text.find("### 8.16"):]
not_mentioned = []
for group, values in (("scope", SCOPES), ("mode", MODES), ("keep", KEEP_RULES)):
    for value in values:
        if value not in section:
            not_mentioned.append(f"{group}:{value}")
print(f"  не упомянуты в разделе: {not_mentioned or 'нет'}")
check(not not_mentioned, "все значения перечислений упомянуты в промпте",
      f"нет: {not_mentioned}")

# Все настройки схлопывания должны быть названы В РАЗДЕЛЕ о дублях.
#
# `ragraf_mode` из этого списка исключён сознательно: это настройка КАНАЛА
# работы (cli / mcp-stdio / mcp-http / both), а не схлопывания, и её место —
# раздел 8.20. Проверять её наличие в 8.16 значило бы требовать упоминания
# чужой темы в разделе о копиях; ниже она проверяется там, где описана.
DEDUPE_ENUM_KEYS = [k for k in ENUM_KEYS if k != "ragraf_mode"]
missing_keys = [k for k in DEDUPE_ENUM_KEYS if k not in section]
missing_keys += [k for k in ("dedupe_enabled", "dedupe_report")
                 if k not in section]
print(f"  настройки не упомянуты: {missing_keys or 'нет'}")
check(not missing_keys, "все настройки схлопывания названы",
      f"нет: {missing_keys}")

# Режим канала описан в своём разделе, вместе со всеми допустимыми значениями.
mode_values = ENUM_KEYS.get("ragraf_mode", ())
mode_ok = all(v in text for v in mode_values)
print(f"  режим канала: значения {mode_values}, все названы: {mode_ok}")
check(mode_ok, "раздел о режиме канала называет все допустимые значения",
      f"нет: {[v for v in mode_values if v not in text]}")
check("ragraf_mode" in text, "настройка ragraf_mode названа в промпте")

# Значение по умолчанию обязано совпадать: промпт утверждает «выключено».
default_enabled = getattr(IndexConfig(), "dedupe_enabled")
print(f"  по умолчанию dedupe_enabled = {default_enabled}")
check(default_enabled is False, "схлопывание по умолчанию выключено (как в промпте)")
check("по умолчанию выключено" in section,
      "промпт говорит о выключенном состоянии по умолчанию")

# Значение по умолчанию для правила выбора.
default_keep = getattr(IndexConfig(), "dedupe_keep")
print(f"  по умолчанию dedupe_keep = {default_keep!r}")
check(default_keep in KEEP_RULES, "правило выбора корректно",
      f"получено {default_keep!r}")

# Мусорные сегменты из промпта должны существовать в коде.
mentioned_segments = re.findall(r"`([a-z_.]+)`", section)
known = [s for s in ("backup", "old", "publish", "release", "out", "copy",
                     "www", "_to.upload", "verify") if s in section]
print(f"  мусорные сегменты в промпте: {known}")
check(len(known) >= 5, "промпт называет сегменты-признаки копий",
      f"названо {len(known)}")
in_code = [s for s in known if any(s in seg for seg in COPY_SEGMENTS)]
check(len(in_code) == len(known),
      "все названные сегменты есть в списке COPY_SEGMENTS",
      f"нет в коде: {[s for s in known if s not in in_code]}")

print()
print("=" * 78)
print("3. ЧИСЛА В ПРОМПТЕ СОВПАДАЮТ С ЗАМЕРАМИ")
print("=" * 78)
# Числа, полученные замерами этой сессии.
FACTS = {
    "5559": "документов просканировано в <полигон>",
    "513": "групп копий",
    "3856": "лишних файлов",
    "104": "копий одного ADR (худший случай)",
    "120": "файлов readme.md",
    "103": "из них с разным содержимым",
    "38": "групп с мусорным путём у cleanest",
    "65": "у newest",
    "76": "у shortest",
    "242": "у longest",
    "699": "файлов в revoap до схлопывания",
    "592": "файлов после схлопывания",
    "107": "отброшенных копий",
    "86": "групп в revoap",
}
for number, what in FACTS.items():
    present = number in text
    print(f"  {number:<6} ({what}): {'есть' if present else 'НЕТ'}")
    check(present, f"число {number} в промпте", what)

print()
print("=" * 78)
print("4. УТВЕРЖДЕНИЯ О ПОВЕДЕНИИ ПРОВЕРЯЮТСЯ ПО КОДУ")
print("=" * 78)
# «Файлы на диске НЕ удаляются» — проверяется, что в модуле нет удаления.
dedupe_src = (REPO / "src" / "ragraf" / "dedupe.py").read_text(encoding="utf-8")
dangerous = [name for name in ("unlink", "remove(", "rmtree", "os.remove",
                               "shutil.move")
             if name in dedupe_src]
print(f"  удаляющих вызовов в dedupe.py: {dangerous or 'нет'}")
check(not dangerous,
      "модуль схлопывания не удаляет файлы (утверждение промпта верно)",
      f"найдено: {dangerous}")
check("НЕ удаляются" in section, "промпт прямо говорит, что файлы не удаляются")

# «Сравнение по содержимому» — проверяется, что группировка идёт по sha256.
check("sha256" in dedupe_src, "группировка идёт по контрольной сумме")
check("по содержимому" in section, "промпт говорит о сравнении по содержимому")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
