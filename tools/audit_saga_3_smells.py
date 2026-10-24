"""Аудит SagaAI шаг 3: проглатывание ошибок, заглушки, мёртвый код.

Ищутся дефекты, которые не видны при обычной работе, но проявляются
в аварийных ситуациях:

  1. `except: pass` и `except Exception: pass` — ошибка исчезает молча,
     и потом невозможно понять, почему что-то не сработало;
  2. `except` с широким перехватом без логирования;
  3. TODO/FIXME/HACK/XXX/заглушки — незаконченные места;
  4. мёртвый код: функция объявлена, но её никто не вызывает
     (проверяется через RAGRAF references);
  5. дубли: одинаковые имена функций в разных файлах.

Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import re
from collections import Counter, defaultdict

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
SKIP = {".venv", ".git", ".dev_agent", "__pycache__", "node_modules",
        "history", "storage"}


def scan() -> list[tuple[str, list[str]]]:
    """Возвращает (путь, строки) по всем исходникам проекта."""
    out = []
    for p in sorted(SAGA.rglob("*.py")):
        if set(p.parts) & SKIP:
            continue
        rel = str(p.relative_to(SAGA)).replace("\\", "/")
        if rel.startswith("tests/"):
            continue
        try:
            out.append((rel, p.read_text(encoding="utf-8",
                                         errors="replace").splitlines()))
        except OSError:
            continue
    return out


files = scan()
print(f"  файлов исходников: {len(files)}")

print()
print("=" * 78)
print("1. ПРОГЛАЧИВАНИЕ ОШИБОК (except ... pass)")
print("=" * 78)
# Ищется: строка `except`, и в следующих двух строках — `pass`.
swallowed: list[tuple[str, int, str, str]] = []
for rel, lines in files:
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not re.match(r"except\b", stripped):
            continue
        # Просматриваются следующие 2 строки: там может быть pass.
        for j in range(i + 1, min(i + 3, len(lines))):
            body = lines[j].strip()
            if not body or body.startswith("#"):
                continue
            if body == "pass":
                swallowed.append((rel, i + 1, stripped[:60],
                                  f"тело: pass (строка {j + 1})"))
            break

print(f"\n  найдено мест: {len(swallowed)}")
by_file = Counter(s[0] for s in swallowed)
for rel, n in by_file.most_common():
    print(f"\n  {rel}: {n}")
    for s in swallowed:
        if s[0] == rel:
            print(f"    {s[1]:>5}: {s[2]}")

print()
print("=" * 78)
print("2. ШИРОКИЙ ПЕРЕХВАТ БЕЗ ЛОГИРОВАНИЯ")
print("=" * 78)
# Ищется `except Exception` без логирования в теле (следующие 6 строк).
broad: list[tuple[str, int, str]] = []
for rel, lines in files:
    for i, line in enumerate(lines):
        if not re.search(r"except\s+(Exception|BaseException)", line):
            continue
        body = " ".join(lines[i + 1:i + 7])
        if not re.search(r"(log|logger|print|raise|warn|error)", body, re.I):
            broad.append((rel, i + 1, line.strip()[:80]))

print(f"\n  найдено мест: {len(broad)}")
by_file = Counter(b[0] for b in broad)
for rel, n in by_file.most_common(12):
    print(f"    {rel}: {n}")

print()
print("=" * 78)
print("3. НЕЗАКОНЧЕННЫЕ МЕСТА (TODO/FIXME/HACK/XXX)")
print("=" * 78)
MARKERS = r"\b(TODO|FIXME|HACK|XXX|не реализовано|not implemented|заглушка|"
MARKERS += r"временно|костыль)\b"
markers: list[tuple[str, int, str]] = []
for rel, lines in files:
    for i, line in enumerate(lines, 1):
        if re.search(MARKERS, line, re.I):
            markers.append((rel, i, line.strip()[:100]))

print(f"\n  найдено мест: {len(markers)}")
by_file = Counter(m[0] for m in markers)
for rel, n in by_file.most_common(15):
    print(f"\n  {rel}: {n}")
    for m in markers:
        if m[0] == rel:
            print(f"    {m[1]:>5}: {m[2]}")

print()
print("=" * 78)
print("4. ДУБЛИ ИМЁН ФУНКЦИЙ")
print("=" * 78)
# Собираются имена функций верхнего уровня по файлам.
funcs: dict[str, list[tuple[str, int]]] = defaultdict(list)
for rel, lines in files:
    for i, line in enumerate(lines, 1):
        m = re.match(r"def\s+([a-zA-Z_]\w*)\s*\(", line)
        if m:
            funcs[m.group(1)].append((rel, i))

dupes = {name: places for name, places in funcs.items()
         if len({p[0] for p in places}) > 1
         and not name.startswith("_")}
print(f"\n  имён функций всего: {len(funcs)}")
print(f"  имён, встречающихся в разных файлах: {len(dupes)}")
for name, places in sorted(dupes.items(), key=lambda kv: -len(kv[1]))[:12]:
    print(f"\n  {name}: {len(places)} определений")
    for rel, line in places[:5]:
        print(f"    {rel}:{line}")

print()
print("=" * 78)
print("5. САМЫЕ БОЛЬШИЕ ФАЙЛЫ (риск сложности)")
print("=" * 78)
sizes = sorted(((len(lines), rel) for rel, lines in files), reverse=True)
for count, rel in sizes[:12]:
    kb = (SAGA / rel).stat().st_size / 1024
    print(f"  {rel:<44} {count:>6} строк  {kb:>7.0f} КБ")
