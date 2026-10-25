"""Разбор дефекта: обновление отвергает нормальные относительные пути.

ПРИЗНАК (из падений тестов):
    applied=[], error="non-normalized path: 'assets/logo.svg'"
    applied=[], error="non-normalized path: 'langs/ru.json'"
    applied=[], error="non-normalized path: 'new_dir/new_file.txt'"

То есть механизм обновления не может применить НИ ОДНОГО файла: пути,
которые заведомо безопасны (обычные относительные пути внутри проекта),
отвергаются как «ненормализованные».

Это не «тест устарел»: тесты проверяют основной сценарий — применение
обновления. Если он не работает, обновления платформы сломаны полностью.

Надо найти функцию проверки и понять, что именно она считает
«ненормализованным».
"""

from __future__ import annotations

import pathlib
import re

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
TARGET = SAGA / "core" / "updater_apply.py"
lines = TARGET.read_text(encoding="utf-8", errors="replace").splitlines()
print(f"  файл: {TARGET.relative_to(SAGA)}, строк {len(lines)}")

print()
print("=" * 78)
print("1. ГДЕ ФОРМИРУЕТСЯ СООБЩЕНИЕ «non-normalized path»")
print("=" * 78)
for i, line in enumerate(lines, 1):
    if "non-normalized" in line or "non_normalized" in line:
        print(f"\n  строка {i}: {line.strip()}")
        # Печатается контекст: функция целиком.
        start = max(0, i - 30)
        end = min(len(lines), i + 8)
        print(f"\n  --- контекст ({start + 1}—{end}) ---")
        for j in range(start, end):
            marker = ">>" if j == i - 1 else "  "
            print(f"  {marker} {j + 1:>5}: {lines[j][:96]}")

print()
print("=" * 78)
print("2. ФУНКЦИИ ПРОВЕРКИ ПУТЕЙ")
print("=" * 78)
for i, line in enumerate(lines, 1):
    if re.match(r"def .*(path|normal|safe|valid)", line, re.I):
        print(f"  строка {i}: {line.strip()}")
