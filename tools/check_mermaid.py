"""Проверка диаграмм Mermaid в статье.

Mermaid может отрисоваться не всегда: ошибка в синтаксисе даёт пустой блок
вместо схемы. Проверка ловит типичные проблемы без запуска рендера:
  * незакрытые блоки ```;
  * узлы с недопустимыми символами в идентификаторах;
  * незакрытые кавычки в подписях;
  * несогласованные стрелки (-->> против -->).
"""

from __future__ import annotations

import pathlib
import re

ARTICLE = pathlib.Path(__file__).resolve().parents[1] / "docs" / "05_article" / "RAGRAF_ARTICLE.md"

text = ARTICLE.read_text(encoding="utf-8")
blocks = re.findall(r"```mermaid\n(.*?)```", text, re.DOTALL)

print(f"блоков mermaid: {len(blocks)}")
print(f"закрывающих ``` всего: {text.count('```')} (должно быть чётным)")

problems = 0
for i, block in enumerate(blocks, 1):
    lines = block.rstrip("\n").splitlines()
    kind = lines[0].strip().split()[0] if lines else "?"
    print(f"\n--- блок {i}: {kind}, строк {len(lines)}")

    # 1. Тип диаграммы
    known = ("flowchart", "graph", "sequenceDiagram", "classDiagram", "erDiagram",
             "stateDiagram", "gantt", "pie", "journey")
    if not any(kind.startswith(k) for k in known):
        print(f"    ПРОБЛЕМА: неизвестный тип {kind}")
        problems += 1

    # 2. Баланс скобок и кавычек
    for ch_open, ch_close, title in (("(", ")", "круглые скобки"),
                                     ("[", "]", "квадратные скобки"),
                                     ('"', '"', "кавычки")):
        opened = block.count(ch_open)
        closed = block.count(ch_close)
        if ch_open == ch_close:
            if opened % 2 != 0:
                print(f"    ПРОБЛЕМА: непарные {title} ({opened})")
                problems += 1
        elif opened != closed:
            print(f"    ПРОБЛЕМА: {title}: {opened} открывающих, {closed} закрывающих")
            problems += 1

    # 3. Стрелки без пробелов — источник ошибок разбора.
    # Исключение: в sequenceDiagram запись A->>B КОРРЕКТНА, поэтому там
    # проверка не применяется (иначе ложные срабатывания).
    if kind != "sequenceDiagram":
        bad_arrows = re.findall(r"\w(?:-->|-->>|->>|--)", block)
        if bad_arrows:
            print(f"    ВНИМАНИЕ: стрелка вплотную к тексту: {bad_arrows[:3]}")
            problems += 1

    # 4. Идентификаторы узлов: латиница, цифры, подчёркивание
    ids = set(re.findall(r"(?:^|\s)([A-Za-z_][A-Za-z0-9_]*)\s*[\[\(\{]", block, re.MULTILINE))
    bad_ids = [i_ for i_ in ids if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", i_)]
    if bad_ids:
        print(f"    ПРОБЛЕМА: недопустимые идентификаторы: {bad_ids[:5]}")
        problems += 1
    print(f"    узлов с текстом: {len(ids)}")

    # 5. Подписи на стрелках должны быть в кавычках, если содержат спецсимволы
    # 5. Подписи на стрелках со спецсимволами надо брать в кавычки.
    # В sequenceDiagram подписи идут через двоеточие, поэтому проверка —
    # только для flowchart и подобных диаграмм.
    if kind not in ("sequenceDiagram", "gantt", "pie"):
        labels = re.findall(r"\|([^|]+)\|", block)
        for label in labels:
            if any(c in label for c in "()[]{}"):
                print(f"    ВНИМАНИЕ: спецсимволы в подписи стрелки: {label!r}")
                problems += 1

print(f"\nвсего проблем: {problems}")

# Дополнительно: диаграммы в тексте статьи должны быть упомянуты словами,
# иначе читатель не поймёт, что на схеме.
for i, block in enumerate(blocks, 1):
    head = block.strip().splitlines()[0]
    before = text[: text.find(block)]
    tail = before[-400:].strip().split("\n")[-1] if before.strip() else ""
    print(f"\nблок {i} ({head}): предыдущая строка текста: {tail[:110]}")
