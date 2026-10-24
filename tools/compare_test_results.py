"""Проверка: падения тестов вызваны моей правкой или были раньше?

ЗАМЕР: 1713 тестов проходят, 11 падают, 14 ошибок окружения.
Надо установить причину падений — это может быть как мой недочёт, так
и существующие дефекты проекта (например ошибки в самих тестах).

СПОСОБ. Полный набор тестов прогоняется ДВАЖДЫ:
  1. на ИСХОДНОЙ версии `dangerous.py` (из бэкапа);
  2. на ИСПРАВЛЕННОЙ версии.

Сравниваются списки падений. Если они совпадают — падения не мои.
Если появились новые — правку надо доработать.

Так исключается догадка: «наверное, из-за правки» или «наверное, нет».
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
TARGET = SAGA / "core" / "dangerous.py"
BACKUP = TARGET.with_suffix(".py.bak-safe288")
SAGA_PY = str(SAGA / ".venv" / "Scripts" / "python.exe")
BASETEMP = r"C:\AutoClaw\RAGRAF\.staging\pytest_tmp"


def run_tests(label: str) -> tuple[set[str], str]:
    """Прогоняет тесты и возвращает множество упавших тестов."""
    print(f"\n  === {label} ===", flush=True)
    proc = subprocess.run(
        [SAGA_PY, "-m", "pytest", "tests/", "-q", "-p", "no:cacheprovider",
         f"--basetemp={BASETEMP}", "--ignore=tests/scenarios",
         "--tb=no", "-rf"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(SAGA), timeout=3000,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    failed = set(re.findall(r"^FAILED (tests/[\w/]+\.py::[\w\[\]-]+)", out,
                            re.MULTILINE))
    errored = set(re.findall(r"^ERROR (tests/[\w/]+\.py::[\w\[\]-]+)", out,
                             re.MULTILINE))
    summary = ""
    for line in out.splitlines():
        if "passed" in line or "failed" in line or "error" in line:
            if "=" in line or re.search(r"\d+ (passed|failed|error)", line):
                summary = line.strip()
    print(f"  итог: {summary}")
    print(f"  упавших тестов: {len(failed)}")
    return failed | errored, summary


print("=" * 78)
print("СРАВНЕНИЕ: ИСХОДНАЯ И ИСПРАВЛЕННАЯ ВЕРСИИ")
print("=" * 78)

# 1. Текущая (исправленная).
fixed_text = TARGET.read_text(encoding="utf-8")
fixed_failures, fixed_summary = run_tests("ИСПРАВЛЕННАЯ (сейчас)")

# 2. Исходная.
if BACKUP.exists():
    shutil.copy2(BACKUP, TARGET)
    original_failures, original_summary = run_tests("ИСХОДНАЯ (до правки)")
    TARGET.write_text(fixed_text, encoding="utf-8", newline="\n")
    print("\n  исправленная версия возвращена на место")
else:
    print(f"\n  бэкап не найден: {BACKUP}")
    original_failures, original_summary = set(), "?"

print()
print("=" * 78)
print("СРАВНЕНИЕ РЕЗУЛЬТАТОВ")
print("=" * 78)
print(f"  на исходной версии:      {original_summary}")
print(f"  на исправленной версии:  {fixed_summary}")

new_failures = fixed_failures - original_failures
fixed_by_change = original_failures - fixed_failures

print(f"\n  падений было:   {len(original_failures)}")
print(f"  падений стало:  {len(fixed_failures)}")
print(f"\n  НОВЫХ падений из-за правки: {len(new_failures)}")
for name in sorted(new_failures):
    print(f"    + {name}")

print(f"\n  падений, исчезнувших после правки: {len(fixed_by_change)}")
for name in sorted(fixed_by_change):
    print(f"    - {name}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
if not new_failures:
    print(f"""
  Правка НЕ вызвала ни одного нового падения.

  Все {len(fixed_failures)} падений существовали до неё и связаны с самим
  проектом или окружением (например PermissionError при сканировании
  каталога временных файлов).
""")
else:
    print(f"""
  ВНИМАНИЕ: правка вызвала {len(new_failures)} новых падений.
  Требуется доработка правки, а не откат: сами падения надо разобрать
  и либо исправить, либо подтвердить, что они ожидаемы.
""")
