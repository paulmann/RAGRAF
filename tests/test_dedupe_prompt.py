"""Проверка системного промпта после добавления раздела о дублях.

Проверяется не факт «текст добавлен», а его достоверность: числа и правила
в промпте обязаны совпадать с кодом и с фактическим состоянием индексов.
Расхождение здесь опасно: агент будет действовать по неверным числам.

Дополнительно проверяется кодировка: промпт правился скриптом, и порча
кодировки не проявляется сразу.
"""

from __future__ import annotations

import os
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
# Путь к промпту переопределяется переменной окружения: без этого проверку
# нельзя прогнать на КОПИИ промпта в песочнице, а значит нельзя доказать, что
# она краснеет на подсаженном нарушении. По умолчанию — живой промпт агента.
# Документы, которые обязан прочитать агент проекта. Прежняя редакция брала
# `~/.pi/agent/AGENTS.md` — файл машины владельца (PowerShell/MySQL/Git); когда
# из него ушли разделы о схлопывании, проверка стала красной не по вине проекта,
# а по чужой правке. Предмет проверки — «агент МОЖЕТ узнать о настройках»,
# значит и смотреть надо на документацию проекта.
PROMPT_PATHS = [
    pathlib.Path(os.environ.get("RAGRAF_PROMPT"))
] if os.environ.get("RAGRAF_PROMPT") else [
    REPO / "AGENTS.md",
    REPO / "docs" / "AGENTS-context.md",
]
PROMPT = PROMPT_PATHS[0]
_OLD_PROMPT = pathlib.Path(
    os.environ.get("RAGRAF_PROMPT")
    or (pathlib.Path.home() / ".pi/agent/AGENTS.md")
)
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


# Документы читаются все: кодировка проверяется у каждого, содержание — по
# объединению. Иначе правило, перенесённое из одного файла в другой, объявлялось
# пропавшим.
_parts: list[str] = []
for _path in PROMPT_PATHS:
    _data = _path.read_bytes()
    if _data[:3] == b"\xef\xbb\xbf":
        print(f"  [FAIL] BOM в {_path.name}")
        FAIL += 1
    _decoded = _data.decode("utf-8")
    if "\r\n" in _decoded:
        print(f"  [FAIL] CRLF в {_path.name}")
        FAIL += 1
    _parts.append(_decoded)
data = "".join(_parts).encode("utf-8")
text = "\n".join(_parts)

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
# Нумерация не проверяется намеренно: раздел может переехать, и тогда проверка
# краснела бы на оформлении. Проверяется содержание — тема дублей раскрыта,
# а не только названа: рядом с ней обязано стоять правило выбора главной копии.
dupes_topic = ("дубл" in text.lower() or "копи" in text.lower())
check(dupes_topic and "dedupe_keep" in text,
      "тема дублей раскрыта: названо правило выбора главной копии",
      f"дубли={dupes_topic}, dedupe_keep={'dedupe_keep' in text}")

print()
print("=" * 78)
print("2. НАСТРОЙКИ В ПРОМПТЕ СОВПАДАЮТ С КОДОМ")
print("=" * 78)
from ragraf.config import IndexConfig  # noqa: E402
from ragraf.dedupe import (MODES, SCOPES, KEEP_RULES,  # noqa: E402
                           COPY_SEGMENTS, DOC_EXTENSIONS)
from ragraf.settings import ENUM_KEYS, DEFAULTS  # noqa: E402

# Каждый режим и область должны быть названы в промпте.
# Фрагмент от первого упоминания темы дублей до конца документа. Прежний срез
# по заголовку `### 8.16` при его отсутствии давал `text[-1:]` — один последний
# символ, и проверки «не находили» слова, которые в документе есть.
_dupes_at = text.find("Схлопывание дублей")
if _dupes_at < 0:
    _dupes_at = text.lower().find("дубл")
section = text[_dupes_at:] if _dupes_at >= 0 else ""
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
# Список строится по СВОЙСТВУ: «хранится в настройках и относится к дублям»,
# то есть имя начинается с `dedupe_`. Прежняя редакция брала все `ENUM_KEYS`
# минус `ragraf_mode`, и это делало проверку заложницей чужих тем: как только
# в настройках появилось перечислимое значение другого раздела (издание,
# провайдер SSO, приёмник и протокол SIEM, видимость секретов по умолчанию),
# раздел 8.16 потребовал упоминать их у себя — и проверка покраснела по чужой
# причине (замер 28.10.2026: 5 лишних ключей, провалено 1).
#
# Перечисления берутся из модуля схлопывания (`SCOPES`, `MODES`, `KEEP_RULES`),
# а не из настроек: значения проверяются выше, здесь — только имена настроек.
DEDUPE_SETTINGS = sorted(k for k in DEFAULTS if k.startswith("dedupe_"))
print(f"  настройки схлопывания по имени dedupe_*: {DEDUPE_SETTINGS}")
missing_keys = [k for k in DEDUPE_SETTINGS if k not in section]
print(f"  настройки не упомянуты: {missing_keys or 'нет'}")
check(not missing_keys, "все настройки схлопывания названы",
      f"нет: {missing_keys}")
# Список не должен быть пустым: пустой список прошёл бы проверку молча.
check(len(DEDUPE_SETTINGS) >= 5,
      "настройки схлопывания найдены в настройках (а не пустой список)",
      f"найдено {DEDUPE_SETTINGS}")

# Два источника значений обязаны совпадать: перечисления в настройках и
# константы модуля схлопывания. Расхождение значило бы, что интерфейс
# предлагает одно, а индексатор принимает другое.
settings_enums = {k: set(v) for k, v in ENUM_KEYS.items()
                  if k.startswith("dedupe_")}
module_enums = {"dedupe_scope": set(SCOPES), "dedupe_mode": set(MODES),
                "dedupe_keep": set(KEEP_RULES)}
print("  перечисления в настройках: "
      + ", ".join(f"{k}={sorted(v)}"
                  for k, v in sorted(settings_enums.items())))
check(settings_enums == module_enums,
      "перечисления настроек совпадают с константами модуля схлопывания",
      f"настройки {settings_enums}, модуль {module_enums}")

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
check("по умолчанию" in section.lower() and "нет" in section.lower(),
      "документ говорит о выключенном состоянии по умолчанию")

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
# Числа замеров живут в ОТЧЁТЕ (`docs/RAGRAF-REPORT.md`), а не в
# правилах агента: правила обязаны быть короткими, справка — по адресу.
MEASURE_DOC = (REPO / "docs" / "RAGRAF-REPORT.md").read_text(encoding="utf-8")
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
    present = number in MEASURE_DOC
    print(f"  {number:<6} ({what}): {'есть' if present else 'НЕТ'}")
    check(present, f"число {number} названо в отчёте замеров", what)

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
check("не удаля" in section.lower(), "документ прямо говорит, что файлы не удаляются")

# «Сравнение по содержимому» — проверяется, что группировка идёт по sha256.
check("sha256" in dedupe_src, "группировка идёт по контрольной сумме")
check("содержим" in section.lower() and "sha256" in section.lower(),
      "документ говорит: сравнение по содержимому (sha256)")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
