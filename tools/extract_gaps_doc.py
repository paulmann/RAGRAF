"""Извлечь документ GAPS_FOR_LLM.md из результата исследователя.

Исследователь выполнил работу, но исчерпал бюджет вызовов до записи файла:
содержимое документа лежит в его ответе между маркерами ````markdown и ````.
Скрипт извлекает его в docs/04_research/GAPS_FOR_LLM.md.
"""

from __future__ import annotations

import pathlib

src = pathlib.Path(".zwork/runs/f59b71e6/0-研究员.md")
if not src.exists():
    print("нет файла результата")
    raise SystemExit(1)

text = src.read_text(encoding="utf-8")

start_marker = "````markdown"
end_marker = "````"
i = text.find(start_marker)
if i < 0:
    print("не найден маркер начала документа")
    raise SystemExit(1)

j = text.find(end_marker, i + len(start_marker))
if j < 0:
    print("не найден маркер конца документа")
    raise SystemExit(1)

doc = text[i + len(start_marker) : j].strip() + "\n"

out = pathlib.Path("docs/04_research/GAPS_FOR_LLM.md")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(doc, encoding="utf-8", newline="\n")
print(f"записано: {out} ({len(doc)} символов, {len(doc.splitlines())} строк)")
