"""Финальный аудит SagaAI: слабые места, которые надо включить в отчёт.

Собираются конкретные факты по темам, которые ещё не проверялись:
  1. запуск внешних процессов агентом и проверка аргументов;
  2. отключение защиты (`_safety_enabled`) — где задаётся и чем управляется;
  3. глобальное изменяемое состояние в модулях (риск гонок);
  4. запись в базу без транзакций;
  5. размеры и сложность ключевых файлов.

Каждый пункт — с путём и строкой.
"""

from __future__ import annotations

import pathlib
import re

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
SKIP = {".venv", ".git", ".dev_agent", "__pycache__", "node_modules",
        "history", "storage"}


def files() -> list[tuple[str, list[str]]]:
    out = []
    for p in sorted(SAGA.rglob("*.py")):
        if set(p.parts) & SKIP:
            continue
        rel = str(p.relative_to(SAGA)).replace("\\", "/")
        try:
            out.append((rel, p.read_text(encoding="utf-8",
                                         errors="replace").splitlines()))
        except OSError:
            continue
    return out


ALL = files()
SRC = [(r, l) for r, l in ALL if not r.startswith("tests/")]
print(f"  файлов: {len(ALL)} (исходники {len(SRC)})")

print()
print("=" * 78)
print("1. ОТКЛЮЧЕНИЕ ЗАЩИТЫ ОПАСНЫХ КОМАНД")
print("=" * 78)
# Ищется, кто ставит флаг безопасности.
for rel, lines in ALL:
    for i, line in enumerate(lines, 1):
        if re.search(r"_safety_enabled", line):
            print(f"  {rel}:{i}")
            print(f"    {line.strip()[:95]}")

print()
print("=" * 78)
print("2. ЗАПУСК ВНЕШНИХ ПРОЦЕССОВ АГЕНТОМ")
print("=" * 78)
for rel, lines in SRC:
    for i, line in enumerate(lines, 1):
        if re.search(r"subprocess\.(run|Popen|call|check_output)\s*\(", line):
            print(f"  {rel}:{i}")
            print(f"    {line.strip()[:95]}")

print()
print("=" * 78)
print("3. ГЛОБАЛЬНОЕ ИЗМЕНЯЕМОЕ СОСТОЯНИЕ (РИСК ГОНОК)")
print("=" * 78)
# Ищутся присваивания атрибутам модулей верхнего уровня.
count = 0
for rel, lines in SRC:
    for i, line in enumerate(lines, 1):
        if re.match(r"\s*config\.\w+\s*=", line) or \
                re.match(r"\s*[A-Z_]{3,}\s*=\s*(?!None)", line.strip()) and \
                "import" not in line:
            if re.match(r"\s*(config|state|settings)\.\w+\s*=", line):
                print(f"  {rel}:{i}: {line.strip()[:88]}")
                count += 1
print(f"\n  всего присваиваний модульному состоянию: {count}")

print()
print("=" * 78)
print("4. ЗАПИСЬ В БАЗУ: ТРАНЗАКЦИИ И COMMIT")
print("=" * 78)
# Ищутся места записи без явной транзакции.
commits = 0
writes = 0
for rel, lines in SRC:
    for i, line in enumerate(lines, 1):
        if re.search(r"\.commit\(\)", line):
            commits += 1
        if re.search(r"\.execute\(\s*[\"'](?:INSERT|UPDATE|DELETE)", line, re.I):
            writes += 1
print(f"  вызовов commit(): {commits}")
print(f"  инструкций INSERT/UPDATE/DELETE в коде: {writes}")

# Ищется использование контекстных менеджеров (правильный способ).
with_blocks = 0
for rel, lines in SRC:
    for line in lines:
        if re.search(r"with\s+\w+\.(begin|transaction|connect)", line):
            with_blocks += 1
print(f"  блоков with ...begin/transaction: {with_blocks}")

print()
print("=" * 78)
print("5. РАЗМЕР И СЛОЖНОСТЬ КЛЮЧЕВЫХ ФАЙЛОВ")
print("=" * 78)
sizes = sorted(((len(l), r) for r, l in SRC), reverse=True)
for count, rel in sizes[:14]:
    kb = (SAGA / rel).stat().st_size / 1024
    print(f"  {rel:<44} {count:>6} строк {kb:>8.0f} КБ")

print()
print("=" * 78)
print("6. ДЛИННЫЕ ФУНКЦИИ (риск ошибок)")
print("=" * 78)
# Ищутся функции длиннее 120 строк.
long_funcs: list[tuple[str, str, int, int]] = []
for rel, lines in SRC:
    current: tuple[str, int] | None = None
    for i, line in enumerate(lines, 1):
        m = re.match(r"def\s+(\w+)\s*\(", line)
        if m:
            if current:
                name, start = current
                length = i - start
                if length > 120:
                    long_funcs.append((rel, name, start, length))
            current = (m.group(1), i)
        elif line.strip() and not line.startswith((" ", "\t", "#")) \
                and current:
            name, start = current
            length = i - start
            if length > 120:
                long_funcs.append((rel, name, start, length))
            current = None
    if current:
        name, start = current
        length = len(lines) - start
        if length > 120:
            long_funcs.append((rel, name, start, length))

long_funcs.sort(key=lambda x: -x[3])
print(f"  функций длиннее 120 строк: {len(long_funcs)}")
for rel, name, start, length in long_funcs[:14]:
    print(f"  {rel}:{start}")
    print(f"    {name}() — {length} строк")

print()
print("=" * 78)
print("7. СЕКРЕТЫ В КОДЕ (по именам переменных, без значений)")
print("=" * 78)
SECRET_PATTERNS = (
    r"(?:api_key|apikey|token|secret|password|passwd)\s*=\s*[\"'][^\"']{8,}",
)
found = 0
for rel, lines in SRC:
    for i, line in enumerate(lines, 1):
        for pattern in SECRET_PATTERNS:
            if re.search(pattern, line, re.I):
                # Значение не печатается, только имя и путь.
                name = re.search(r"(\w+)\s*=", line)
                print(f"  {rel}:{i}: похоже на литерал секрета "
                      f"в переменной {name.group(1) if name else '?'}")
                found += 1
print(f"\n  найдено мест: {found}")
