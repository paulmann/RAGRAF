"""Кто виноват в несовпадении sha256: тест или код обновления?

УСТАНОВЛЕНО. Хеш не сходится из-за переводов строк:
    тест пишет:  open(p, "w", encoding="utf-8")   -> на Windows '\\r\\n'
    хеш в манифесте считается от строки с '\\n'
    результат:   staged-файл имеет хеш 'a61666d8...', манифест ждёт 'd3923337...'

Ключевой вопрос: как пишет staged-файлы РЕАЛЬНЫЙ механизм подготовки
обновления (`core/updater.py`)? Если он тоже пишет в текстовом режиме —
значит дефект в КОДЕ (обновление само себя не сможет применить).
Если пишет байтами — дефект только в ТЕСТЕ, и править надо тест.

Это принципиально: правка не того файла скроет настоящую причину.
"""

from __future__ import annotations

import pathlib
import re
import sys

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
sys.path.insert(0, str(SAGA))

print("=" * 78)
print("1. КАК core/updater.py ЗАПИСЫВАЕТ STAGED-ФАЙЛЫ")
print("=" * 78)
updater = SAGA / "core" / "updater.py"
lines = updater.read_text(encoding="utf-8", errors="replace").splitlines()
print(f"  файл: core/updater.py, строк {len(lines)}")

# Ищутся все места записи файлов.
print(f"\n  все операции записи файлов:")
for i, line in enumerate(lines, 1):
    if re.search(r"open\([^)]*[\"']w", line) or ".write_bytes(" in line \
            or ".write_text(" in line:
        print(f"    {i:>5}: {line.strip()[:94]}")

# Ищутся места, где пишется в pending/ (хранилище подготовленных файлов).
print(f"\n  контекст вокруг записи в pending:")
for i, line in enumerate(lines, 1):
    if "PENDING_DIR" in line or "pending_dir" in line:
        if "def " in line:
            continue
        start = max(0, i - 8)
        print(f"\n  --- строка {i} ---")
        for j in range(start, min(len(lines), i + 8)):
            marker = ">>" if j == i - 1 else "  "
            print(f"  {marker} {j + 1:>5}: {lines[j][:90]}")
        break

print()
print("=" * 78)
print("2. КАК СЧИТАЕТСЯ ХЕШ В МАНИФЕСТЕ (реальный механизм)")
print("=" * 78)
for i, line in enumerate(lines, 1):
    if "sha256" in line.lower() and "def " not in line:
        print(f"  {i:>5}: {line.strip()[:94]}")

# Ищется функция вычисления хеша.
for i, line in enumerate(lines, 1):
    if re.match(r"def .*sha", line, re.I):
        print(f"\n  --- {line.strip()} (строка {i}) ---")
        for j in range(i - 1, min(len(lines), i + 12)):
            print(f"    {j + 1:>5}: {lines[j][:90]}")

print()
print("=" * 78)
print("3. ПРОВЕРКА: ЧТО ДЕЛАЕТ РЕАЛЬНЫЙ МЕХАНИЗМ")
print("=" * 78)
# Проверяется фактическое поведение: вычисляется хеш строки и хеш файла,
# записанного текстовым режимом.
import hashlib  # noqa: E402
import tempfile  # noqa: E402

CONTENT = '{"new": true}\n'
h_from_string = hashlib.sha256(CONTENT.encode("utf-8")).hexdigest()
print(f"  хеш от строки (как в манифесте): {h_from_string}")

tmp = pathlib.Path(tempfile.mkdtemp(prefix="probe_"))
p_text = tmp / "text.bin"
with open(p_text, "w", encoding="utf-8") as fh:
    fh.write(CONTENT)
p_bin = tmp / "bin.bin"
with open(p_bin, "wb") as fh:
    fh.write(CONTENT.encode("utf-8"))

h_text = hashlib.sha256(p_text.read_bytes()).hexdigest()
h_bin = hashlib.sha256(p_bin.read_bytes()).hexdigest()
print(f"  хеш файла (текстовый режим):     {h_text}")
print(f"  хеш файла (двоичный режим):      {h_bin}")
print(f"\n  текстовый режим совпадает с манифестом: {h_text == h_from_string}")
print(f"  двоичный режим совпадает с манифестом:  {h_bin == h_from_string}")

# Проверка: при какой настройке текстовый режим сохраняет LF.
p_nl = tmp / "nl.bin"
with open(p_nl, "w", encoding="utf-8", newline="") as fh:
    fh.write(CONTENT)
h_nl = hashlib.sha256(p_nl.read_bytes()).hexdigest()
print(f"\n  текстовый режим с newline='':    {h_nl}")
print(f"  совпадает с манифестом:          {h_nl == h_from_string}")

import shutil  # noqa: E402

shutil.rmtree(tmp, ignore_errors=True)

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Хеш манифеста считается от СТРОКИ с символами '\\n'.
  Текстовый режим без newline='' на Windows портит байты ('\\r\\n').
  Двоичный режим их сохраняет.

  Значит правильное поведение — писать staged-файлы так, чтобы байты
  совпадали с тем, от чего считался хеш.

  Если реальный механизм (core/updater.py) пишет двоично — дефект только
  в тесте, и правка теста верна.
  Если текстово — дефект в коде обновления: он сам себя не применит.
""")
