"""Аудит SagaAI шаг 2: аутентификация, распаковка архивов, загрузка модулей.

Проверяются три места, где ошибка даёт реальный ущерб:

  1. АУТЕНТИФИКАЦИЯ (страница «Доступы»). Признак проблемы: сравнение
     пароля оператором `==` вместо сравнения с постоянным временем —
     оно позволяет подбирать пароль по времени ответа.
     Замер: `hmac.compare_digest` в проекте НЕ НАЙДЕН.

  2. РАСПАКОВКА АРХИВОВ НАВЫКОВ (`core/skills_library.py`). Признак
     проблемы: имена файлов из архива не проверяются, и запись может
     выйти за пределы каталога («Zip Slip»: `../../secret`).

  3. ЗАГРУЗКА КОДА ИЗ ФАЙЛА (`core/orchestrator_folders.py`). Кастомные
     функции оркестратора исполняются через importlib — надо понять,
     какие проверки есть до исполнения.

Смотрятся ТОЛЬКО те строки, где принимается решение. Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import re

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")


def show(path: str, start: int, count: int, label: str = "") -> None:
    """Печатает фрагмент файла с номерами строк."""
    full = SAGA / path
    if not full.exists():
        print(f"  ФАЙЛА НЕТ: {path}")
        return
    lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
    print(f"\n  --- {path}:{start}-{start + count - 1} {label} ---")
    for i in range(max(0, start - 1), min(len(lines), start - 1 + count)):
        print(f"    {i + 1:>5}: {lines[i][:105]}")


print("=" * 78)
print("1. АУТЕНТИФИКАЦИЯ: ГДЕ СРАВНИВАЕТСЯ ПАРОЛЬ")
print("=" * 78)
# Ищутся файлы, где упоминается пароль и сравнение.
auth_files = []
for p in SAGA.rglob("*.py"):
    if set(p.parts) & {".venv", ".git", ".dev_agent", "tests"}:
        continue
    text = p.read_text(encoding="utf-8", errors="replace")
    if re.search(r"(SAGAAI_AUTH_PASSWORD|auth_password|check_password|"
                 r"verify_password)", text):
        auth_files.append(p.relative_to(SAGA))

print(f"\n  файлов с логикой пароля: {len(auth_files)}")
for rel in auth_files:
    print(f"    {str(rel).replace(chr(92), '/')}")

for rel in auth_files:
    full = SAGA / rel
    lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
    print(f"\n  === {str(rel).replace(chr(92), '/')} ===")
    for i, line in enumerate(lines, 1):
        if re.search(r"(password|passwd|pwd)\w*\s*(==|!=)", line, re.I):
            print(f"    {i:>5}: {line.strip()[:100]}   <-- СРАВНЕНИЕ")
        elif re.search(r"(compare_digest|secrets\.|hashlib)", line):
            print(f"    {i:>5}: {line.strip()[:100]}   <-- защищённое сравнение")
        elif re.search(r"SAGAAI_AUTH_PASSWORD", line):
            print(f"    {i:>5}: {line.strip()[:100]}")

print()
print("=" * 78)
print("2. РАСПАКОВКА АРХИВОВ НАВЫКОВ (проверка Zip Slip)")
print("=" * 78)
show("core/skills_library.py", 395, 60, "импорт навыка из ZIP")

print()
print("=" * 78)
print("3. ЗАГРУЗКА КОДА КАСТОМНОЙ ФУНКЦИИ")
print("=" * 78)
show("core/orchestrator_folders.py", 291, 40, "load_orchestrator_function_module")

print()
print("=" * 78)
print("4. ИСПОЛНЕНИЕ КОДА АГЕНТОМ (где прогоняется сгенерированный код)")
print("=" * 78)
# Ищется, как исполняется код из инструмента run_code.
for probe in ("dev_agent/tool_executor.py", "dev_agent/universal_agent.py"):
    full = SAGA / probe
    if not full.exists():
        continue
    lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
    print(f"\n  === {probe} ===")
    for i, line in enumerate(lines, 1):
        if re.search(r"\b(exec|eval|subprocess\.run|Popen|compile)\s*\(", line):
            print(f"    {i:>5}: {line.strip()[:100]}")

print()
print("=" * 78)
print("5. ДИНАМИЧЕСКИЙ ИМПОРТ В core/files.py")
print("=" * 78)
show("core/files.py", 218, 22, "__import__ по имени модуля")
