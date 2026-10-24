"""Настоящая причина: бэкап создаётся с ОБРАТНЫМ СЛЕШЕМ В ИМЕНИ ФАЙЛА.

ЗАМЕР (воспроизведено):
    файл бэкапа: 'd\\1.txt'      <- в имени файла обратный слеш
    meta:        {'d/1.txt': True, 'd/2.txt': False}

То есть в каталоге бэкапа лежит один файл с именем `d\\1.txt` (обратный
слеш — часть ИМЕНИ, а не разделитель каталогов). Из-за этого обход каталога
возвращает такой путь, и `PurePosixPath` его не исправляет: слеш уже внутри
имени, а не является разделителем.

ЗНАЧИТ правка в `rollback` лечила следствие, а причина — в создании бэкапа.
Именно там путь попадает в имя файла без нормализации.

Проверяется, как `apply_pending` создаёт бэкап: скорее всего используется
`os.path.join(backup_dir, run_id, rel)`, где `rel` содержит прямые слеши.
На Windows `join` их НЕ преобразует, поэтому создаётся файл с именем
`d/1.txt`... но замер показывает `d\\1.txt`.

Дополнительно проверяется: не преобразует ли путь сам Python при открытии
файла на запись.

Скрипт НЕ правит код: сначала надо установить точное место.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
sys.path.insert(0, str(SAGA))

from core import updater_apply as ua  # noqa: E402

print("=" * 78)
print("1. ГДЕ СОЗДАЁТСЯ БЭКАП (поиск кода)")
print("=" * 78)
src = (SAGA / "core" / "updater_apply.py").read_text(encoding="utf-8")
lines = src.splitlines()
for i, line in enumerate(lines, 1):
    if "backup" in line.lower() and ("join" in line or "_atomic_copy" in line
                                     or "makedirs" in line):
        print(f"  {i:>5}: {line.strip()[:96]}")

print()
print("=" * 78)
print("2. ФУНКЦИЯ _atomic_copy")
print("=" * 78)
start = next((i for i, ln in enumerate(lines, 1)
              if ln.startswith("def _atomic_copy")), None)
if start:
    end = next((i for i in range(start + 1, len(lines) + 1)
                if i <= len(lines) and lines[i - 1].startswith("def ")),
               len(lines) + 1)
    for j in range(start - 1, end - 1):
        print(f"  {j + 1:>5}: {lines[j][:94]}")

print()
print("=" * 78)
print("3. ПРОВЕРКА ПОВЕДЕНИЯ os.path.join НА WINDOWS")
print("=" * 78)
rel = "d/1.txt"
for base in ("C:/tmp/backup", "C:\\tmp\\backup"):
    joined = os.path.join(base, rel)
    print(f"  join({base!r}, {rel!r})")
    print(f"    = {joined!r}")
    # Проверяется, что видит файловая система.
    parent = os.path.dirname(joined)
    name = os.path.basename(joined)
    print(f"    dirname:  {parent!r}")
    print(f"    basename: {name!r}")

print()
print("=" * 78)
print("4. ЖИВОЙ ЭКСПЕРИМЕНТ: КАК СОЗДАЁТСЯ ФАЙЛ")
print("=" * 78)
tmp = pathlib.Path(tempfile.mkdtemp(prefix="copy_probe_"))
dest = str(tmp / "backup" / "run1")
os.makedirs(dest, exist_ok=True)
src_file = tmp / "source.txt"
src_file.write_text("data", encoding="utf-8")

# Способ А: как делает save-копирование с os.path.join.
target_a = os.path.join(dest, "d/1.txt")
os.makedirs(os.path.dirname(target_a), exist_ok=True)
shutil.copy2(str(src_file), target_a)
print(f"  способ А (os.path.join + makedirs):")
print(f"    target: {target_a!r}")
for dirpath, _dn, fns in os.walk(dest):
    for name in fns:
        full = os.path.join(dirpath, name)
        rel_path = os.path.relpath(full, dest)
        print(f"      создан файл: {rel_path!r}")

# Способ Б: то же, но путь нормализован через POSIX и разделён.
dest_b = str(tmp / "backup" / "run2")
os.makedirs(dest_b, exist_ok=True)
target_b = os.path.join(dest_b, *("d/1.txt".split("/")))
os.makedirs(os.path.dirname(target_b), exist_ok=True)
shutil.copy2(str(src_file), target_b)
print(f"\n  способ Б (разделение пути на части):")
print(f"    target: {target_b!r}")
for dirpath, _dn, fns in os.walk(dest_b):
    for name in fns:
        full = os.path.join(dirpath, name)
        rel_path = os.path.relpath(full, dest_b)
        print(f"      создан файл: {rel_path!r}")

shutil.rmtree(tmp, ignore_errors=True)

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Если файл бэкапа получает имя с обратным слешем — значит путь собирается
  через os.path.join с последующим преобразованием, либо нормализуется не
  там, где нужно.

  Правильное место правки — создание бэкапа (в `apply_pending`), чтобы
  файловая структура бэкапа повторяла структуру проекта. Тогда и откат
  вернёт правильные пути без дополнительных приведений.
""")
