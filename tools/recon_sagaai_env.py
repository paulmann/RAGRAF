"""Разведка окружения SagaAI: что установлено фактически.

Задача владельца — обновить streamlit и библиотеки. Прежде чем что-то
менять, надо знать исходное состояние: без снимка «до» невозможно ни
доказать успех, ни откатить неудачу.

Проверяется:
  1. полный список пакетов в окружении проекта;
  2. наличие файла зависимостей (по нему обновляется проект);
  3. версия Python и совместимость с целевыми версиями пакетов.
"""

from __future__ import annotations

import json
import pathlib
import subprocess

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
PY = SAGA / ".venv" / "Scripts" / "python.exe"

print("=" * 78)
print("1. ПОЛНЫЙ СПИСОК ПАКЕТОВ")
print("=" * 78)
proc = subprocess.run(
    [str(PY), "-m", "pip", "list", "--format=json"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    timeout=300,
)
print(f"  код возврата: {proc.returncode}")
if proc.stderr:
    print(f"  stderr: {proc.stderr.strip()[:400]}")

packages = []
try:
    packages = json.loads(proc.stdout or "[]")
except json.JSONDecodeError as exc:
    print(f"  вывод не разобран: {exc}")
    print(f"  первые 500 символов: {(proc.stdout or '')[:500]}")

print(f"  пакетов установлено: {len(packages)}")
for item in sorted(packages, key=lambda p: p.get("name", "").lower()):
    print(f"    {item.get('name'):<32} {item.get('version')}")

print()
print("=" * 78)
print("2. ФАЙЛЫ ЗАВИСИМОСТЕЙ")
print("=" * 78)
CANDIDATES = [
    "requirements.txt", "requirements-dev.txt", "requirements-prod.txt",
    "pyproject.toml", "Pipfile", "setup.py", "constraints.txt",
]
found = []
for name in CANDIDATES:
    path = SAGA / name
    if path.exists():
        found.append(path)
        print(f"  есть: {name} ({path.stat().st_size} б)")
if not found:
    print("  файлов зависимостей не найдено в корне")

# Ищутся вложенные файлы требований.
nested = [p for p in SAGA.rglob("requirements*.txt")
          if ".venv" not in p.parts and ".git" not in p.parts]
if nested:
    print(f"\n  вложенные файлы требований ({len(nested)}):")
    for p in nested[:12]:
        print(f"    {p.relative_to(SAGA)} ({p.stat().st_size} б)")

print()
print("=" * 78)
print("3. ЧТО ЗАВИСИТ ОТ STREAMLIT")
print("=" * 78)
# Ищутся импорты streamlit в коде проекта — важно для оценки риска
# обновления.
import re

uses = []
for p in SAGA.rglob("*.py"):
    if {".venv", ".git", ".dev_agent", "__pycache__"} & set(p.parts):
        continue
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    count = len(re.findall(r"^\s*import streamlit|^\s*from streamlit", text,
                           re.MULTILINE))
    if count:
        uses.append((str(p.relative_to(SAGA)).replace("\\", "/"), count))

uses.sort(key=lambda x: -x[1])
print(f"  файлов, использующих streamlit: {len(uses)}")
for rel, count in uses[:14]:
    print(f"    {rel:<46} {count} импортов")

print()
print("=" * 78)
print("4. ВЕРСИЯ PYTHON И ДОСТУПНЫЕ ОБНОВЛЕНИЯ")
print("=" * 78)
proc = subprocess.run([str(PY), "--version"], capture_output=True, text=True,
                      encoding="utf-8", errors="replace", timeout=120)
version = (proc.stdout or "").strip()
print(f"  интерпретатор: {version}")
print(f"  путь: {PY}")
print("""
  ВАЖНО: обновление пакетов под Python 3.14 ограничено теми версиями,
  у которых есть готовые сборки (wheels) под эту версию. Если нужной
  сборки нет, pip попробует собрать пакет из исходников — а это требует
  компилятора и может закончиться неудачей.
""")
