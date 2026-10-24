"""Где применяется проверка опасных команд и что происходит при срабатывании.

Нужно установить серьёзность уязвимости: если `assess_code` стоит на пути
исполнения кода агентом, обход защиты означает реальный риск.

Проверяется:
  1. точный номер строки дефекта (последний `continue`);
  2. кто вызывает проверку и как используется результат;
  3. что происходит при `dangerous=True` — блокировка или запрос
     подтверждения;
  4. можно ли подтверждение обойти.
"""

from __future__ import annotations

import pathlib
import re

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")


def show(path: str, start: int, count: int, label: str = "") -> None:
    full = SAGA / path
    if not full.exists():
        print(f"  ФАЙЛА НЕТ: {path}")
        return
    lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
    print(f"\n  --- {path}:{start}-{start + count - 1} — {label} ---")
    for i in range(max(0, start - 1), min(len(lines), start - 1 + count)):
        print(f"    {i + 1:>5}: {lines[i][:100]}")


print("=" * 78)
print("1. ТОЧНЫЙ НОМЕР СТРОКИ ДЕФЕКТА")
print("=" * 78)
lines = (SAGA / "core/dangerous.py").read_text(
    encoding="utf-8", errors="replace").splitlines()
for i in range(276, 290):
    text = lines[i]
    marks = []
    if "safe_compiled" in text and "search" in text:
        marks.append("сработал safe_pattern")
    if re.match(r"\s+continue\s*$", text):
        marks.append("ПОДАВЛЕНИЕ ПРАВИЛА")
    print(f"  {i + 1:>5}: {text[:88]}")
    for m in marks:
        print(f"         ^ {m}")

print()
print("=" * 78)
print("2. КТО ВЫЗЫВАЕТ ПРОВЕРКУ")
print("=" * 78)
for p in sorted(SAGA.rglob("*.py")):
    if set(p.parts) & {".venv", ".git", ".dev_agent", "tests"}:
        continue
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    if not re.search(r"(assess_code|assess_tool_code|dangerous)", text):
        continue
    rel = str(p.relative_to(SAGA)).replace("\\", "/")
    for i, line in enumerate(text.splitlines(), 1):
        if re.search(r"\b(assess_code|assess_tool_code)\s*\(", line):
            print(f"  {rel}:{i}: {line.strip()[:90]}")

print()
print("=" * 78)
print("3. ЧТО ПРОИСХОДИТ ПРИ ОБНАРУЖЕНИИ ОПАСНОСТИ")
print("=" * 78)
# Ищется место, где решение проверки влияет на исполнение.
for probe, label in (("dev_agent/tool_executor.py", "исполнитель инструментов"),
                     ("dev_agent/agent_loop.py", "цикл агента")):
    full = SAGA / probe
    if not full.exists():
        continue
    lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
    print(f"\n  === {probe} — {label} ===")
    for i, line in enumerate(lines, 1):
        if re.search(r"(dangerous|assess)", line, re.I):
            # Печатается с контекстом.
            start = max(0, i - 3)
            for j in range(start, min(i + 4, len(lines))):
                prefix = ">>" if j + 1 == i else "  "
                print(f"  {prefix} {j + 1:>5}: {lines[j][:92]}")
            print()

print()
print("=" * 78)
print("4. РЕЖИМЫ ПРОВЕРКИ ПО ИНСТРУМЕНТАМ (документация в коде)")
print("=" * 78)
# Читается docstring функции проверки: там описано, что проверяется.
full = SAGA / "core/dangerous.py"
lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
start = next((i for i, l in enumerate(lines, 1)
              if "def assess_tool_code" in l), None)
if start:
    show("core/dangerous.py", start - 2, 30, "правила по инструментам")
else:
    # Ищется описание режимов.
    for i, line in enumerate(lines, 1):
        if "run_code" in line or "run_test" in line:
            print(f"  {i:>5}: {line.strip()[:95]}")
