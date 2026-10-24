"""Устранение настоящего изъяна: файл без перевода строки в конце.

ЧТО ОБНАРУЖЕНО. При сборке пакета выяснилось:
    tests/test_task_state.py.bak-dirname (до правки): НЕ кончается переводом
    tests/test_task_state.py (после правки):        кончается переводом

То есть мой скрипт правки добавил завершающий перевод строки, которого
в исходном файле не было. Сам diff от этого стал содержать лишний байт
изменения в конце — при том, что содержательных правок там две (import os
и замена rsplit на dirname).

ПОЧЕМУ ЭТО ИЗЪЯН, А НЕ МЕЛОЧЬ:
  * diff описывает изменение, которого автор правки не замышлял;
  * при применении diff к исходному файлу результат отличается от
    исправленного файла на один байт — разработчик получит вопрос
    «почему файл не совпадает»;
  * в проекте 610 файлов, и если часть из них без завершающего перевода,
    это признак принятого стиля — менять его попутной правкой нельзя.

ПРАВКА: файл приводится к исходному виду ПО КОНЦУ — перевод строки
в конце убирается. Содержательные правки сохраняются.

ПОСЛЕ ПРАВКИ: diff применяется к копии «до», и результат БАЙТ В БАЙТ
совпадает с исправленным файлом.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import tempfile

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
OUT = pathlib.Path(__file__).resolve().parents[1].parent / "docs/SagaAI/Fixes"
REL = "tests/test_task_state.py"
ORIGIN_REL = "tests/test_task_state.py.bak-dirname"

live = SAGA / REL
origin = SAGA / ORIGIN_REL
packed = OUT / "изменённые" / REL

print("=" * 78)
print("СОСТОЯНИЕ ДО ПРАВКИ")
print("=" * 78)
for label, path in (("до правки (бэкап)", origin), ("в проекте", live)):
    data = path.read_bytes()
    print(f"  {label}:")
    print(f"    размер: {len(data)} б")
    print(f"    кончается переводом строки: {data.endswith(b'\\n')}")

print()
print("=" * 78)
print("ВЫРАВНИВАНИЕ КОНЦА ФАЙЛА В ПРОЕКТЕ")
print("=" * 78)
origin_bytes = origin.read_bytes()
live_bytes = live.read_bytes()

if live_bytes.endswith(b"\n") and not origin_bytes.endswith(b"\n"):
    # Убирается ОДИН завершающий перевод строки: только он лишний.
    fixed = live_bytes[:-1]
    live.write_bytes(fixed)
    print(f"  убран завершающий перевод строки")
    print(f"  размер: {len(live_bytes)} -> {len(fixed)} б")
    print(f"  кончается переводом строки: {fixed.endswith(b'\\n')}")
elif live_bytes.endswith(b"\n") == origin_bytes.endswith(b"\n"):
    print("  выравнивание не требуется: концы совпадают по типу")
else:
    print("  внимание: состояние не совпадает с ожидаемым, правка не делается")

print()
print("=" * 78)
print("ПРОВЕРКА: СОДЕРЖАТЕЛЬНЫЕ ПРАВКИ СОХРАНЕНЫ")
print("=" * 78)
after = live.read_text(encoding="utf-8", errors="replace")
checks = [
    ("import os" in after, "добавлен импорт os"),
    ("os.path.dirname(os.path.normpath(first" in after,
     "замена rsplit на dirname"),
    ('rsplit("/", 1)[0]' not in after, "старый разбор пути убран"),
]
for ok, label in checks:
    print(f"  [{'OK ' if ok else '--'}] {label}")

print()
print("=" * 78)
print("ПЕРЕСБОРКА ПАКЕТА ДЛЯ ЭТОГО ФАЙЛА")
print("=" * 78)
shutil.copy2(live, packed)
shutil.copy2(origin, packed.with_name(packed.name + ".ДО-ПРАВКИ"))

# Diff строится заново, без добавления перевода строки.
import difflib  # noqa: E402

old_text = origin.read_text(encoding="utf-8", errors="replace")
new_text = live.read_text(encoding="utf-8", errors="replace")
diff_text = "".join(difflib.unified_diff(
    old_text.splitlines(keepends=True),
    new_text.splitlines(keepends=True),
    fromfile=f"a/{REL}", tofile=f"b/{REL}", n=3,
))
diff_file = packed.with_name(packed.name + ".diff")
diff_file.write_text(diff_text, encoding="utf-8", newline="\n")
print(f"  diff пересобран: {len(diff_text.splitlines())} строк")

# Строгая проверка: результат применения БАЙТ В БАЙТ равен файлу в пакете.
tmp = pathlib.Path(tempfile.mkdtemp(prefix="strictcheck_"))
work = tmp / REL
work.parent.mkdir(parents=True, exist_ok=True)
work.write_bytes(origin.read_bytes())
diff_copy = tmp / "p.diff"
diff_copy.write_bytes(diff_file.read_bytes())

check = subprocess.run(["git", "apply", "-p1", "--check", str(diff_copy)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(tmp), timeout=120)
applied = subprocess.run(["git", "apply", "-p1", str(diff_copy)],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", cwd=str(tmp), timeout=120)

byte_equal = work.read_bytes() == packed.read_bytes()
print(f"  проверка контекста: код {check.returncode}")
print(f"  применение:         код {applied.returncode}")
print(f"  результат БАЙТ В БАЙТ совпадает: {byte_equal}")
if not byte_equal:
    print(f"    размер результата: {work.stat().st_size}")
    print(f"    размер файла:      {packed.stat().st_size}")

shutil.rmtree(tmp, ignore_errors=True)

print()
print("=" * 78)
print("ПРОГОН ТЕСТА ПОСЛЕ ПРАВКИ КОНЦА ФАЙЛА")
print("=" * 78)
proc = subprocess.run(
    [str(SAGA / ".venv" / "Scripts" / "python.exe"), "-m", "pytest",
     "tests/test_task_state.py", "-q", "-p", "no:cacheprovider",
     "--basetemp=.staging\\pytest_tmp"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    cwd=str(SAGA), timeout=900,
)
for line in (proc.stdout or "").splitlines()[-3:]:
    print(f"  {line.strip()}")
