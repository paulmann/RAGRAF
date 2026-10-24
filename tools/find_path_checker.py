"""Поиск функции проверки пути и проверка её поведения вызовом.

Сообщение «non-normalized path» формируется где-то в модуле. Надо найти
эту функцию и проверить её на нормальных путях — не по чтению кода,
а вызовом: только так видно фактическое поведение.

Проверяется:
  1. какая функция отвергает нормальные пути;
  2. что именно она считает «ненормализованным»;
  3. ломается ли основной сценарий обновления на этом.
"""

from __future__ import annotations

import pathlib
import re
import sys

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
sys.path.insert(0, str(SAGA))

TARGET = SAGA / "core" / "updater_apply.py"
text = TARGET.read_text(encoding="utf-8", errors="replace")
lines = text.splitlines()

print("=" * 78)
print("1. ПОИСК ФУНКЦИИ ПРОВЕРКИ ПУТИ")
print("=" * 78)
# Ищутся все функции модуля.
functions = []
for i, line in enumerate(lines, 1):
    m = re.match(r"def (\w+)\(", line)
    if m:
        functions.append((m.group(1), i))
print(f"  функций в модуле: {len(functions)}")
for name, line_no in functions:
    print(f"    {name:<32} строка {line_no}")

print()
print("=" * 78)
print("2. ФУНКЦИИ С 'path' ИЛИ 'normal' В ИМЕНИ")
print("=" * 78)
candidates = [(n, l) for n, l in functions
              if "path" in n.lower() or "normal" in n.lower()]
for name, line_no in candidates:
    print(f"\n  === {name} (строка {line_no}) ===")
    # Печатается тело функции.
    end = next((l for n, l in functions if l > line_no), len(lines))
    for j in range(line_no - 1, min(end, line_no + 45)):
        print(f"    {j + 1:>5}: {lines[j][:96]}")

print()
print("=" * 78)
print("3. ПРОВЕРКА ВЫЗОВОМ: НОРМАЛЬНЫЕ ПУТИ")
print("=" * 78)
# Импортируется модуль и проверяются его функции на нормальных путях.
import core.updater_apply as ua  # noqa: E402

NORMAL_PATHS = [
    "assets/logo.svg",
    "langs/ru.json",
    "new_dir/new_file.txt",
    "k/1.txt",
    "app.py",
    "core/updater.py",
    "ui/pages/chat.py",
]
BAD_PATHS = [
    "../escape.txt",
    "..\\escape.txt",
    "/etc/passwd",
    "C:\\Windows\\system32\\x.dll",
    "a/../../b.txt",
    "back\\slash.txt",
]

# Ищется функция, возвращающая bool по пути.
checker = None
for name, _ in functions:
    fn = getattr(ua, name, None)
    if fn is None or not callable(fn):
        continue
    try:
        result = fn("assets/logo.svg")
    except TypeError:
        continue
    except Exception:
        continue
    if isinstance(result, bool):
        checker = (name, fn)
        print(f"  найдена функция-проверка: {name}()")
        break

if checker:
    name, fn = checker
    print(f"\n  нормальные пути (ожидается True):")
    for path in NORMAL_PATHS:
        try:
            result = fn(path)
        except Exception as exc:
            result = f"ошибка {type(exc).__name__}"
        mark = "верно" if result is True else "ОТВЕРГНУТ"
        print(f"    {path:<28} {result}   {mark}")

    print(f"\n  опасные пути (ожидается False):")
    for path in BAD_PATHS:
        try:
            result = fn(path)
        except Exception as exc:
            result = f"ошибка {type(exc).__name__}"
        mark = "верно" if result is False else "ПРОПУЩЕН"
        print(f"    {path:<28} {result}   {mark}")
else:
    print("  функция-проверка с bool-результатом не найдена по имени")
    # Ищется по поведению: перебираются все функции модуля.
    for name in dir(ua):
        if name.startswith("__"):
            continue
        obj = getattr(ua, name)
        if not callable(obj):
            continue
        try:
            res = obj("assets/logo.svg")
        except Exception:
            continue
        print(f"    {name}(...) → {res!r}")

print()
print("=" * 78)
print("4. ЧТО ИМЕННО СЧИТАЕТСЯ «НЕНОРМАЛИЗОВАННЫМ»")
print("=" * 78)
# Ищется регулярное выражение или условие нормализации.
for i, line in enumerate(lines, 1):
    if re.search(r"normali[sz]", line, re.I) and "def " not in line:
        print(f"  {i:>5}: {line.strip()[:100]}")
