"""Правка проверки Mermaid: убрать ложное срабатывание в sequenceDiagram.

В sequenceDiagram запись вида «A->>B» — это корректный синтаксис, а не ошибка.
Прежняя проверка считала её проблемой и давала ложное предупреждение.
"""

from __future__ import annotations

import pathlib

p = pathlib.Path(__file__).resolve().parents[1] / "tools" / "check_mermaid.py"
t = p.read_text(encoding="utf-8")

# Блок 3: предупреждение о стрелках вплотную к тексту.
old3 = (
    '    # 3. Стрелки без пробелов слева/справа — частый источник ошибок разбора\n'
    '    bad_arrows = re.findall(r"\\w(?:-->|-->>|->>|--)", block)\n'
    '    if bad_arrows:\n'
    '        print(f"    ВНИМАНИЕ: стрелка вплотную к тексту: {bad_arrows[:3]}")\n'
    '        problems += 1'
)
new3 = (
    '    # 3. Стрелки без пробелов — источник ошибок разбора.\n'
    '    # Исключение: в sequenceDiagram запись A->>B КОРРЕКТНА, поэтому там\n'
    '    # проверка не применяется (иначе ложные срабатывания).\n'
    '    if kind != "sequenceDiagram":\n'
    '        bad_arrows = re.findall(r"\\w(?:-->|-->>|->>|--)", block)\n'
    '        if bad_arrows:\n'
    '            print(f"    ВНИМАНИЕ: стрелка вплотную к тексту: {bad_arrows[:3]}")\n'
    '            problems += 1'
)
if old3 in t:
    t = t.replace(old3, new3)
    print("блок 3 обновлён")
else:
    print("блок 3 не найден")

# Блок 5: проверка подписей стрелок применима только к flow-chart-диаграммам.
old5 = (
    '    labels = re.findall(r"\\|([^|]+)\\|", block)\n'
    '    for label in labels:\n'
    '        if any(c in label for c in "()[]{}"):\n'
    '            print(f"    ВНИМАНИЕ: спецсимволы в подписи стрелки: {label!r}")\n'
    '            problems += 1'
)
new5 = (
    '    # 5. Подписи на стрелках со спецсимволами надо брать в кавычки.\n'
    '    # В sequenceDiagram подписи идут через двоеточие, поэтому проверка —\n'
    '    # только для flowchart и подобных диаграмм.\n'
    '    if kind not in ("sequenceDiagram", "gantt", "pie"):\n'
    '        labels = re.findall(r"\\|([^|]+)\\|", block)\n'
    '        for label in labels:\n'
    '            if any(c in label for c in "()[]{}"):\n'
    '                print(f"    ВНИМАНИЕ: спецсимволы в подписи стрелки: {label!r}")\n'
    '                problems += 1'
)
if old5 in t:
    t = t.replace(old5, new5)
    print("блок 5 обновлён")
else:
    print("блок 5 не найден")

p.write_text(t, encoding="utf-8", newline="\n")
print("готово")
