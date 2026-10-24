"""Уточнение промпта: явное число шаблонов и упоминание temp.

Две неточности, найденные проверкой:

  1. В промпте сказано «24 каталога + 4 образца имён» — это верно, но агент,
     который ищет число шаблонов (28), его не найдёт. Число надо назвать явно.

  2. Каталог `temp` есть в списке NON_DEFAULT_EXCLUDES в коде, но в промпте
     не упомянут. Список в промпте должен совпадать с кодом, иначе правило
     «не исключать глобально» не дойдёт до агента для `temp`.
"""

from __future__ import annotations

import pathlib

PROMPT = pathlib.Path(r"C:\Users\mvd.AMD5700X3D\.pi\agent\AGENTS.md")

OLD_HEAD = """**Что исключается базово (24 каталога + 4 образца имён):**"""
NEW_HEAD = """**Что исключается базово: 28 шаблонов — 24 каталога и 4 образца имён.**"""

OLD_RISKY = """**Чего в базовом списке НЕТ и почему.** `bin`, `cache`, `obj`, `publish`,
`logs`, `storage`, `uploads`, `tmp` — эти имена НЕЛЬЗЯ исключать глобально:"""
NEW_RISKY = """**Чего в базовом списке НЕТ и почему.** `bin`, `cache`, `obj`, `publish`,
`logs`, `storage`, `uploads`, `tmp`, `temp` — эти имена НЕЛЬЗЯ исключать
глобально:"""

text = PROMPT.read_text(encoding="utf-8")
changed = 0
for old, new in ((OLD_HEAD, NEW_HEAD), (OLD_RISKY, NEW_RISKY)):
    if old in text:
        text = text.replace(old, new, 1)
        changed += 1

if changed:
    PROMPT.write_text(text, encoding="utf-8", newline="\n")
    print(f"применено правок: {changed}")
else:
    print("правки не нужны или фрагменты не найдены")

after = PROMPT.read_text(encoding="utf-8")
section = after[after.find("### 8.17"):]
print()
print(f"  размер: {len(after.encode('utf-8'))} байт")
for probe in ("28 шаблонов", "24 каталога", "`temp`", "`tmp`"):
    print(f"  {probe:<16}: {'есть' if probe in section else 'НЕТ'}")
