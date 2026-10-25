"""Разбор: откат возвращает пути с обратными слешами.

ЗАМЕР:
    ожидалось: {'d/1.txt', 'd/2.txt'}
    получено:  {'d/2.txt', 'd\\1.txt'}

Один путь — `d/2.txt` (прямые слеши), другой — `d\\1.txt` (обратные).
Значит в списке `restored` пути собираются РАЗНЫМИ способами:
  * новый файл (его не было) — путь сохраняется как был;
  * существующий файл (заменён) — путь получается через `os.path.join`,
    который на Windows ставит обратные слеши.

Это тот же класс дефекта, что и в `_verify_rel`: смешение разделителей
на Windows. Тут он не ломает работу, но ломает согласованность: потребитель
списка (интерфейс, манифест состояния) сравнивает строки и не находит файл.

Надо найти место сборки `restored` и понять, дефект в коде или в тесте:
  * если пути в отчётах обязаны быть POSIX (как в манифестах) — дефект
    в коде, и это надо править;
  * если формат не задан — тест требует того, чего код не обещает.
"""

from __future__ import annotations

import pathlib
import re

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
TARGET = SAGA / "core" / "updater_apply.py"
lines = TARGET.read_text(encoding="utf-8", errors="replace").splitlines()

print("=" * 78)
print("1. ГДЕ СОБИРАЕТСЯ СПИСОК restored")
print("=" * 78)
for i, line in enumerate(lines, 1):
    if "restored" in line:
        print(f"  {i:>5}: {line.strip()[:96]}")

print()
print("=" * 78)
print("2. ФУНКЦИЯ ROLLBACK ЦЕЛИКОМ")
print("=" * 78)
start = next((i for i, ln in enumerate(lines, 1)
              if re.match(r"def rollback\(", ln)), None)
if start:
    end = next((i for i in range(start + 1, len(lines) + 1)
                if i <= len(lines) and lines[i - 1].startswith("def ")),
               len(lines) + 1)
    for j in range(start - 1, min(end - 1, start + 60)):
        print(f"  {j + 1:>5}: {lines[j][:94]}")

print()
print("=" * 78)
print("3. ГДЕ ФОРМИРУЕТСЯ СПИСОК applied (для сравнения)")
print("=" * 78)
# Сравнивается, как формируются два списка: если applied делается
# нормализованно, а restored — нет, это несогласованность.
applied_start = next((i for i, ln in enumerate(lines, 1)
                      if re.match(r"def apply_pending\(", ln)), None)
if applied_start:
    end = next((i for i in range(applied_start + 1, len(lines) + 1)
                if i <= len(lines) and lines[i - 1].startswith("def ")),
               len(lines) + 1)
    for j in range(applied_start - 1, end - 1):
        ln = lines[j]
        if re.search(r"applied\s*[.=]|rel\s*=|os\.path\.join|_atomic_write",
                     ln):
            print(f"  {j + 1:>5}: {ln.strip()[:94]}")

print()
print("=" * 78)
print("4. ЕСТЬ ЛИ НОРМАЛИЗАЦИЯ ПУТЕЙ В ОТЧЁТАХ")
print("=" * 78)
for i, line in enumerate(lines, 1):
    if "_verify_rel" in line and "def " not in line:
        print(f"  {i:>5}: {line.strip()[:94]}")
