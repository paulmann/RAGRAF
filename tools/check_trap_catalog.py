"""Счёт ловушек: шапка, оглавление и указатель обязаны называть одно число.

Дефект, из которого это выросло (22.11.2026, блок 11): шапка `AGENTS-TRAPS.md`
говорила «57 ловушек», оглавление уже шло 1–60, а `AGENTS.md` повторял «Список 57».
Счёт не сверяла ни одна проверка — числа расходились молча, и новая ловушка
добавлялась в тело без строки в оглавлении.

Проверяются три места, и каждое считается наблюдением:
  1. шапка файла называет число ловушек;
  2. оглавление идёт сплошно 1..N и его длина равна этому числу;
  3. `AGENTS.md` называет в указателе то же число.

Запуск: python tools/check_trap_catalog.py [корень]
Коды: 0 — счёт сходится, 1 — расхождение (строки FAIL называют места).
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
TRAPS = ROOT / "AGENTS-TRAPS.md"
AGENTS = ROOT / "AGENTS.md"

problems: list[str] = []
examined = 0

if not TRAPS.is_file():
    print(f"FAIL: нет {TRAPS.name}: считать нечего")
    print("осмотрено: 0")
    raise SystemExit(1)

text = TRAPS.read_text(encoding="utf-8")

declared: int | None = None
head = re.search(r"доказательная часть:\s*(\d+)\s+ловуш", text)
if head:
    declared = int(head.group(1))
    examined += 1
else:
    problems.append("AGENTS-TRAPS.md: шапка не называет число ловушек")

numbers: list[int] = []
start = text.find("## Оглавление")
if start < 0:
    problems.append("AGENTS-TRAPS.md: нет раздела «Оглавление»")
else:
    rest = text[start + len("## Оглавление"):]
    end = rest.find("\n## ")
    block = rest if end < 0 else rest[:end]
    numbers = [int(m) for m in re.findall(r"(?m)^\|\s*(\d+)\s*\|", block)]
    examined += 1
    if not numbers:
        problems.append("оглавление: ни одной строки с номером")
    else:
        if numbers != list(range(1, len(numbers) + 1)):
            problems.append(
                f"оглавление: нумерация не сплошная — {len(numbers)} строк, "
                f"последняя {numbers[-1]}")
        if declared is not None and declared != len(numbers):
            problems.append(
                f"шапка называет {declared}, а в оглавлении {len(numbers)} строк")

if AGENTS.is_file():
    listed = re.search(r"Список\s+(\d+)\s*—", AGENTS.read_text(encoding="utf-8"))
    if not listed:
        problems.append("AGENTS.md: нет строки «Список N —»")
    elif declared is not None:
        examined += 1
        if int(listed.group(1)) != declared:
            problems.append(
                f"AGENTS.md называет «Список {listed.group(1)}», а шапка — {declared}")
else:
    problems.append("AGENTS.md: файла нет, указатель не сверен")

for item in problems:
    print(f"FAIL: {item}")
print(f"осмотрено: {examined}")
raise SystemExit(0 if not problems else 1)
