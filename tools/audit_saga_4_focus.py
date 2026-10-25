"""Аудит SagaAI шаг 4: проверка самых серьёзных мест предметно.

По результатам шагов 1-3 выбраны конкретные места, которые надо прочитать
и оценить по существу, а не по шаблону:

  1. `core/ssh_connector.py` — 15 проглатываний из 15 перехватов: весь
     модуль построен на `except Exception: pass`. Проверяется, что именно
     теряется.
  2. `core/dangerous.py` — здесь проверяются опасные команды агента.
     Надо понять, покрывает ли защита `eval/exec`, `os.system`, `subprocess`.
  3. `core/files.py:230` — динамический импорт модуля по имени.
  4. `dev_agent/tool_executor.py` — исполнение кода агентом.
  5. Дубли `test_connection`/`read_file`/`upload_file` в трёх коннекторах:
     проверяется, действительно ли это разные реализации или копия.

Каждое место читается целиком в нужном фрагменте.
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
print("1. SSH-КОННЕКТОР: 15 ПРОГЛАЧИВАНИЙ ИЗ 15 ПЕРЕХВАТОВ")
print("=" * 78)
full = SAGA / "core/ssh_connector.py"
lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
print(f"  строк в файле: {len(lines)}")
# Собирается статистика: сколько перехватов и сколько с pass.
excepts = [i for i, line in enumerate(lines, 1)
           if re.match(r"\s*except\b", line)]
passes = []
for i in excepts:
    for j in range(i, min(i + 3, len(lines))):
        body = lines[j].strip()
        if not body or body.startswith("#"):
            continue
        if body == "pass":
            passes.append(i)
        break
print(f"  перехватов исключений: {len(excepts)}")
print(f"  из них с `pass` в теле: {len(passes)}")

# Показываются три примера, где теряется важная информация.
for target in (141, 277, 406):
    show("core/ssh_connector.py", target - 8, 16,
         "потеря ошибки при операции")

print()
print("=" * 78)
print("2. ЗАЩИТА ОТ ОПАСНЫХ КОМАНД АГЕНТА (core/dangerous.py)")
print("=" * 78)
full = SAGA / "core/dangerous.py"
lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
print(f"  строк в файле: {len(lines)}")
# Ищутся правила: какие команды запрещены.
print(f"\n  объявленные правила:")
for i, line in enumerate(lines, 1):
    if re.match(r"\s*(?:DangerRule|_rule|Rule)\s*\(", line.strip()) or \
            "pattern=" in line:
        print(f"    {i:>5}: {line.strip()[:100]}")
# Ищется список опасных шаблонов.
for i, line in enumerate(lines, 1):
    if re.match(r"\s*_SHELL_RULES|_PYTHON_RULES|_RULES\s*[:=]", line):
        print(f"\n  список правил на строке {i}:")
        for j in range(i - 1, min(i + 14, len(lines))):
            print(f"    {j + 1:>5}: {lines[j][:100]}")

print()
print("=" * 78)
print("3. ДИНАМИЧЕСКИЙ ИМПОРТ В core/files.py")
print("=" * 78)
show("core/files.py", 215, 25, "__import__ по имени модуля")

print()
print("=" * 78)
print("4. ИСПОЛНЕНИЕ КОДА АГЕНТОМ (tool_executor)")
print("=" * 78)
full = SAGA / "dev_agent/tool_executor.py"
lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
print(f"  строк в файле: {len(lines)}")
# Ищется, где запускается код: run_code, run_test.
for i, line in enumerate(lines, 1):
    if re.search(r"def (run_code|run_test|_run_code|execute_code)\s*\(", line):
        print(f"\n  --- функция на строке {i} ---")
        for j in range(i - 1, min(i + 30, len(lines))):
            print(f"    {j + 1:>5}: {lines[j][:100]}")

print()
print("=" * 78)
print("5. ДУБЛИ КОННЕКТОРОВ: РАЗНЫЕ РЕАЛИЗАЦИИ ИЛИ КОПИЯ")
print("=" * 78)
# Сравниваются размеры функций test_connection в трёх файлах.
for path, line_no in (("core/api_layer.py", 2041),
                      ("core/github_connector.py", 96),
                      ("core/github_connector_rest.py", 216),
                      ("core/ssh_connector.py", 396)):
    full = SAGA / path
    if not full.exists():
        continue
    lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
    # Определяется конец функции: следующая строка того же уровня.
    start = line_no - 1
    end = start + 1
    while end < len(lines):
        if lines[end].startswith("def ") or lines[end].startswith("class "):
            break
        end += 1
    print(f"\n  {path}:{line_no} — функция {end - start} строк")
    for j in range(start, min(start + 12, end)):
        print(f"    {j + 1:>5}: {lines[j][:100]}")
