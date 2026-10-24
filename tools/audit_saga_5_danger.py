"""Аудит SagaAI шаг 5: проверка механизма защиты от опасных команд.

Найдено интересное: в списке «безопасных» команд (`_SAFE_SUBPROCESS_COMMANDS`)
есть `pip`, `docker`, `kubectl`, `terraform`, `git`. При этом в правилах
отдельно запрещён `pip install`. Надо понять, что происходит: правила
противоречат друг другу или проверяются на разных уровнях.

Это классическая ошибка ослабления защиты: команда попадает в «белый список»
по имени и перестаёт проверяться на опасные аргументы.

Проверяется предметно:
  1. как применяется whitelist (`_SAFE_SUBPROCESS_COMMANDS`);
  2. есть ли проверка аргументов у «безопасной» команды;
  3. что будет с `pip install <вредоносный пакет>` — это разрешено или нет;
  4. какой уровень защиты применяется к списку аргументов (safe_pattern).

Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import re

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
full = SAGA / "core/dangerous.py"
lines = full.read_text(encoding="utf-8", errors="replace").splitlines()


def show(start: int, count: int, label: str = "") -> None:
    print(f"\n  --- dangerous.py:{start}-{start + count - 1} — {label} ---")
    for i in range(max(0, start - 1), min(len(lines), start - 1 + count)):
        print(f"    {i + 1:>5}: {lines[i][:100]}")


print("=" * 78)
print("1. ГДЕ ПРИМЕНЯЕТСЯ СПИСОК БЕЗОПАСНЫХ КОМАНД")
print("=" * 78)
for i, line in enumerate(lines, 1):
    if "_SAFE_SUBPROCESS_COMMANDS" in line:
        print(f"  {i:>5}: {line.strip()[:100]}")

print()
print("=" * 78)
print("2. ГДЕ ПРИМЕНЯЕТСЯ СПИСОК ОПАСНЫХ КОМАНД")
print("=" * 78)
for i, line in enumerate(lines, 1):
    if "_ALWAYS_DANGEROUS_COMMANDS" in line:
        print(f"  {i:>5}: {line.strip()[:100]}")

print()
print("=" * 78)
print("3. ФУНКЦИЯ ПРОВЕРКИ КОМАНДЫ-СПИСКА")
print("=" * 78)
# Ищется функция, разбирающая subprocess.run([...]).
for i, line in enumerate(lines, 1):
    if re.match(r"def .*(?:subprocess|command|shell)", line, re.I):
        print(f"\n  --- функция на строке {i} ---")
        for j in range(i - 1, min(i + 40, len(lines))):
            print(f"    {j + 1:>5}: {lines[j][:100]}")

print()
print("=" * 78)
print("4. ПРОТИВОРЕЧИЕ: `pip` В БЕЛОМ СПИСКЕ ПРОТИВ ПРАВИЛА `pip install`")
print("=" * 78)
print("""
  _SAFE_SUBPROCESS_COMMANDS содержит: pip, pip3, poetry, conda,
  docker, kubectl, helm, terraform, git.

  _SHELL_RULES содержит запрет: `\\bpip\\s+install\\b` — «Установка
  Python-пакетов... может загрузить вредоносный пакет».

  Вопрос: правило `pip install` работает ВСЕГДА или отключается,
  когда команда уже признана «безопасной» по имени?

  Если отключается — у агента есть способ установить что угодно
  из интернета без подтверждения: `subprocess.run(["pip","install","X"])`.
""")

print()
print("=" * 78)
print("5. ПОЛНАЯ ФУНКЦИЯ ПРОВЕРКИ (что именно возвращается)")
print("=" * 78)
# Ищется главная публичная функция проверки.
for i, line in enumerate(lines, 1):
    if re.match(r"def (?:check|scan|analyze|is_dangerous|evaluate)", line):
        print(f"\n  --- {line.strip()[:90]} (строка {i}) ---")
        for j in range(i - 1, min(i + 45, len(lines))):
            print(f"    {j + 1:>5}: {lines[j][:100]}")
        break

print()
print("=" * 78)
print("6. СПИСОК ВСЕХ ФУНКЦИЙ МОДУЛЯ")
print("=" * 78)
for i, line in enumerate(lines, 1):
    if re.match(r"def ", line):
        print(f"  {i:>5}: {line.strip()[:90]}")
