"""Точная проверка: где именно дублируется правило в конфигах.

Расхождение двух измерений:
  * тест `test_exclude_configs.py` считал дубли по ВСЕМ строкам вида `- "..."`
    и нашёл 6 штук в конфиге z;
  * скрипт `clean_duplicate_rules.py` считал по СЕКЦИЯМ (exclude,
    secrets_exclude, include) и не нашёл ни одного.

Значит, дубли находятся в РАЗНЫХ секциях: одно правило стоит, например,
и в `exclude`, и в `secrets_exclude`. Это не безобидная косметика —
правило в `secrets_exclude` означает «файл содержит секреты», а в `exclude`
просто «не индексировать». Смысл разный, и совпадение стоит увидеть.

Проверяется по каждому конфигу: какие правила в каких секциях.
"""

from __future__ import annotations

import pathlib
import re
import sys
from collections import Counter, defaultdict

REPO = pathlib.Path(__file__).resolve().parents[1]

CONFIGS = [
    "z-batteryservice.yaml",
    "revoap.yaml",
    "md.viewer.yaml",
    "rdpmon.yaml",
    "clean_bom_senior.yaml",
    "fixture.yaml",
]

# Секции, в которых бывают списки шаблонов.
SECTIONS = ("include", "exclude", "secrets_exclude")
RULE_RE = re.compile(r'^\s*-\s*"([^"]+)"')

print("=" * 78)
print("РАСПРЕДЕЛЕНИЕ ПРАВИЛ ПО СЕКЦИЯМ")
print("=" * 78)

for name in CONFIGS:
    path = REPO / "config" / name
    if not path.exists():
        continue
    lines = path.read_text(encoding="utf-8").split("\n")

    current = ""
    by_section: dict[str, list[str]] = defaultdict(list)
    for line in lines:
        if line and not line.startswith((" ", "\t")) and line.rstrip().endswith(":"):
            current = line.strip().rstrip(":")
        match = RULE_RE.match(line)
        if match and current in SECTIONS:
            by_section[current].append(match.group(1))

    print(f"\n  {name}")
    total = 0
    for section in SECTIONS:
        rules = by_section.get(section, [])
        total += len(rules)
        print(f"    {section:<16} правил {len(rules)}")
    print(f"    всего в файле:   {total}")

    # Дубли ВНУТРИ секции.
    for section in SECTIONS:
        rules = by_section.get(section, [])
        dupes = {r: n for r, n in Counter(rules).items() if n > 1}
        if dupes:
            print(f"    ВНУТРИ {section}: дубли {dupes}")

    # Совпадение между секциями: правило в двух местах.
    all_rules = []
    for section in SECTIONS:
        all_rules.extend(by_section.get(section, []))
    cross = {r: n for r, n in Counter(all_rules).items() if n > 1}
    if cross:
        print(f"    МЕЖДУ секциями: {len(cross)} совпадений")
        for rule, n in list(cross.items())[:8]:
            where = [s for s in SECTIONS if rule in by_section.get(s, [])]
            print(f"      {rule} × {n} в секциях {where}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Дубли ВНУТРИ секции — это мусор, который стоит убрать.
  Совпадение МЕЖДУ секциями — не всегда ошибка:
    * правило в `exclude` и в `secrets_exclude` безвредно, но означает
      разное: «не индексировать» против «содержит секреты»;
    * правило одновременно в `include` и `exclude` бессмысленно:
      include разрешает, exclude запрещает, и побеждает exclude.

  Что именно найдено — видно выше. Правка делается только там, где дубль
  явно лишний, и с резервной копией.
""")
