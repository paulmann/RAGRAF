"""Почему правка не помогла: путь приходит из meta с обратными слешами.

ЗАМЕР ПОСЛЕ ПРАВКИ: результат не изменился — `{'d/2.txt', 'd\\1.txt'}`.

Значит `d\\1.txt` приходит НЕ из `os.path.relpath` (я его уже привёл
к POSIX), а из ключей `meta`. Проверяется, кто и как пишет `meta`.

ЦЕПОЧКА:
  1. `apply_pending` записывает `meta` — «какие файлы существовали до запуска»;
  2. `rollback` читает `meta` и обходит её ключи (строка 514);
  3. если ключи записаны через `os.path.join`, они уже с обратными слешами.

Надо найти, где формируется `meta`, и привести путь к POSIX там же —
в месте создания, а не в месте чтения. Правка в одном месте чтения
проблему не решает: у неё несколько источников.
"""

from __future__ import annotations

import pathlib
import re

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
TARGET = SAGA / "core" / "updater_apply.py"
lines = TARGET.read_text(encoding="utf-8", errors="replace").splitlines()

print("=" * 78)
print("1. ГДЕ ФОРМИРУЕТСЯ META (какие файлы существовали)")
print("=" * 78)
for i, line in enumerate(lines, 1):
    if re.search(r"\bmeta\b", line) and "def " not in line:
        print(f"  {i:>5}: {line.strip()[:94]}")

print()
print("=" * 78)
print("2. БЛОК ЗАПИСИ META В APPLY_PENDING")
print("=" * 78)
start = next((i for i, ln in enumerate(lines, 1)
              if re.match(r"def apply_pending\(", ln)), None)
if start:
    end = next((i for i in range(start + 1, len(lines) + 1)
                if i <= len(lines) and lines[i - 1].startswith("def ")),
               len(lines) + 1)
    # Печатается часть, где пишется meta.
    for j in range(start - 1, end - 1):
        ln = lines[j]
        if re.search(r"meta|RUN_META|_write_run_meta|os\.path\.join\(root, rel\)",
                     ln):
            print(f"  {j + 1:>5}: {ln.strip()[:94]}")

print()
print("=" * 78)
print("3. ФУНКЦИИ РАБОТЫ С META")
print("=" * 78)
for i, line in enumerate(lines, 1):
    if re.match(r"def .*meta", line, re.I):
        print(f"  {i:>5}: {line.strip()[:94]}")

print()
print("=" * 78)
print("4. ПРОВЕРКА: КАК os.path.join ВЛИЯЕТ НА КЛЮЧ")
print("=" * 78)
import os  # noqa: E402

rel = "d/1.txt"
joined = os.path.join("root", rel)
print(f"  исходный rel:            {rel!r}")
print(f"  os.path.join('root', rel): {joined!r}")
back = os.path.relpath(joined, "root")
print(f"  os.path.relpath обратно:  {back!r}")
print(f"\n  вывод: os.path.relpath возвращает '\\\\' на Windows")
print(f"  значит любой путь, прошедший через join+relpath, теряет POSIX-форму")
