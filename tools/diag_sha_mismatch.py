"""Разбор: почему не совпадает sha256 при применении обновления.

ЗАМЕР:
    error: 'sha256 mismatch for langs/ru.json:
            expected d3923337..., got a61666d8...'

Защита работает правильно: она сверила хеш подготовленного файла с тем,
что записан в манифесте. Хеши разошлись — значит либо файл подготовлен
иначе, либо манифест содержит не тот хеш, либо функция `_sha256_file`
считает по-другому.

Надо установить ТОЧНУЮ причину. Возможные варианты:
  1. новый перевод строки добавляется при записи (CRLF вместо LF);
  2. порядок проверки изменился после моей правки;
  3. тест подготавливает файл не тем способом, чем ожидает проверка.

Проверяется прямым воспроизведением: создаётся файл двумя способами и
считается хеш каждого.
"""

from __future__ import annotations

import hashlib
import pathlib
import shutil
import tempfile

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
import sys  # noqa: E402

sys.path.insert(0, str(SAGA))

from core import updater_apply as ua  # noqa: E402

CONTENT = '{"new": true}\n'

print("=" * 78)
print("1. ХЕШИ ОДНОГО И ТОГО ЖЕ СОДЕРЖИМОГО РАЗНЫМИ СПОСОБАМИ")
print("=" * 78)
tmp = pathlib.Path(tempfile.mkdtemp(prefix="hashprobe_"))

# Способ 1: как пишет тест — с указанием кодировки и перевода строк.
p1 = tmp / "a.bin"
with open(p1, "w", encoding="utf-8") as fh:
    fh.write(CONTENT)
h1 = ua._sha256_file(str(p1))
print(f"  open(w, encoding='utf-8'):      {h1}")
print(f"    байты: {p1.read_bytes()!r}")

# Способ 2: двоичная запись (без преобразования переводов строк).
p2 = tmp / "b.bin"
with open(p2, "wb") as fh:
    fh.write(CONTENT.encode("utf-8"))
h2 = ua._sha256_file(str(p2))
print(f"\n  open(wb) + encode('utf-8'):     {h2}")
print(f"    байты: {p2.read_bytes()!r}")

# Способ 3: чистая функция hashlib.
h3 = hashlib.sha256(CONTENT.encode("utf-8")).hexdigest()
print(f"\n  hashlib.sha256 от строки:       {h3}")

print(f"\n  хеши совпадают: {h1 == h2 == h3}")
expected = "d39233370c5c37d37130c258e7f475ac19edfd3e591ae390e4ce3c2fb21910d8"
got = "a61666d8bf6d47621e076cadf426425cbce908d57df4b628b91a4c44d4d6f4e3"
print(f"\n  из сообщения об ошибке:")
for name, value in (("expected", expected), ("got", got)):
    mark = ""
    if value == h1:
        mark = "  <- это запись через open(w, encoding=utf-8)"
    elif value == h2 or value == h3:
        mark = "  <- это двоичная запись"
    print(f"    {name}: {value}{mark}")

print()
print("=" * 78)
print("2. КАК ПОДГОТАВЛИВАЕТ ФАЙЛ ТЕСТ")
print("=" * 78)
test_file = SAGA / "tests" / "test_updater_apply.py"
test_text = test_file.read_text(encoding="utf-8", errors="replace")
lines = test_text.splitlines()
# Ищется функция setup_staged.
for i, line in enumerate(lines, 1):
    if line.startswith("def setup_staged"):
        print(f"  === setup_staged (строка {i}) ===")
        end = next((j for j in range(i, len(lines))
                    if lines[j].startswith("def ")), len(lines))
        for j in range(i - 1, min(end, i + 30)):
            print(f"    {j + 1:>5}: {lines[j][:94]}")
        break

print()
print("=" * 78)
print("3. КАК ЧИТАЕТ ФАЙЛ ПРОВЕРКА")
print("=" * 78)
src = (SAGA / "core" / "updater_apply.py").read_text(encoding="utf-8")
lines = src.splitlines()
# Ищется обращение к _sha256_file.
for i, line in enumerate(lines, 1):
    if "_sha256_file" in line and "def " not in line:
        start = max(0, i - 12)
        print(f"  === вокруг строки {i} ===")
        for j in range(start, min(len(lines), i + 6)):
            marker = ">>" if j == i - 1 else "  "
            print(f"  {marker} {j + 1:>5}: {lines[j][:92]}")
        print()

shutil.rmtree(tmp, ignore_errors=True)
