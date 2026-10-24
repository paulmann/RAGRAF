"""Проверка последних созданных файлов: BOM, CRLF, непустота.

Запускается отдельно, потому что добавление в общий список очистки делалось
inline-кодом и тот упал на экранировании PowerShell. Вместо починки команды
проще проверить состояние файлов напрямую — это и есть цель.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
BASH = r"C:\Program Files\Git\bin\bash.exe"
TOOL = str(pathlib.Path(__file__).resolve().parents[1].parent / "Clean_BOM_Senior/clean-bom-senior.sh")

# Файлы, созданные после последнего прогона очистки.
FILES = [
    "tools/check_environment.py",
    "tools/final_cleanup.py",
    "tools/final_verification.py",
    "tools/fix_class_usage_test.py",
    "tools/fix_css_tests.py",
    "tools/probe_class_usage.py",
    "tools/diag_css_regression.py",
    "tools/update_report_revisions.py",
    "tools/add_doc_role.py",
    "tests/test_css_layer.py",
    "tests/test_class_usage.py",
    "tests/test_report_facts.py",
    "docs/RAGRAF-REPORT.md",
]

paths = [REPO / rel for rel in FILES]
existing = [p for p in paths if p.exists()]
missing = [rel for rel, p in zip(FILES, paths) if not p.exists()]

print("=" * 78)
print("ПРОВЕРКА ПОСЛЕДНИХ ФАЙЛОВ")
print("=" * 78)
print(f"  в списке: {len(FILES)}, существует: {len(existing)}")
for rel in missing:
    print(f"    НЕТ: {rel}")


def state(p: pathlib.Path) -> tuple[bool, bool, int]:
    data = p.read_bytes()
    return data[:3] == b"\xef\xbb\xbf", b"\r\n" in data, len(data)


dirty = [p for p in existing if any(state(p)[:2])]
print(f"\n  требуют очистки: {len(dirty)}")
for p in dirty:
    bom, crlf, size = state(p)
    flags = ", ".join(f for f, on in (("BOM", bom), ("CRLF", crlf)) if on)
    print(f"    {p.relative_to(REPO)}: {flags} ({size} б)")

if dirty:
    tool_arg = TOOL.replace("\\", "/")
    args = " ".join(f"'{p}'" for p in dirty)
    result = subprocess.run([BASH, "-c", f"bash '{tool_arg}' {args}"],
                            capture_output=True, text=True)
    print(f"  очистка: код {result.returncode}")
    if result.returncode:
        print(f"    {(result.stderr or result.stdout)[:200]}")

print()
print("=" * 78)
print("ИТОГ")
print("=" * 78)
bad = 0
for p in existing:
    bom, crlf, size = state(p)
    empty = size == 0
    if bom or crlf or empty:
        bad += 1
        print(f"  {p.relative_to(REPO)}: BOM={bom} CRLF={crlf} пусто={empty}")
print(f"  проверено: {len(existing)}")
print(f"  проблем:   {bad}")

# Компиляция: файлы Python не должны быть испорчены.
check = subprocess.run(
    [sys.executable, "-m", "compileall", "-q", str(REPO / "tools"),
     str(REPO / "tests")],
    capture_output=True, text=True,
)
print(f"  компиляция tools и tests: код {check.returncode}")
if check.returncode:
    print(f"    {(check.stdout or '')[:300]}")

print()
print(f"  РЕЗУЛЬТАТ: {'всё чисто' if bad == 0 else 'ЕСТЬ ПРОБЛЕМЫ'}")
raise SystemExit(0 if bad == 0 else 1)
