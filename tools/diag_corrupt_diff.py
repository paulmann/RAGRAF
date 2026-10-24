"""Разбор: почему diff для test_task_state.py не применяется.

СЕМЬ diff-файлов применяются, один нет. Причина не в переводе строки —
его я уже добавил. Надо найти настоящее отличие.

ГИПОТЕЗЫ:
  1. в файле есть символы, которые git apply трактует как служебные
     (например строки, начинающиеся с '--' или содержащие '\\ No newline');
  2. в diff попал контекст, отличающийся от файла (например из-за
     BOM или разных переводов строк в самом файле);
  3. файл содержит пустую последнюю строку, и diff её не учитывает.

Проверяется сравнение: берётся diff, применяется вручную и печатается
точное сообщение git, а также сравнение байтов.
"""

from __future__ import annotations

import difflib
import pathlib
import subprocess
import tempfile

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
OUT = pathlib.Path(r"C:\AutoClaw\docs\SagaAI\Fixes")
REL = "tests/test_task_state.py"
ORIGIN_REL = "tests/test_task_state.py.bak-dirname"

origin = SAGA / ORIGIN_REL
current = OUT / "изменённые" / REL

print("=" * 78)
print("1. СВОЙСТВА ФАЙЛОВ")
print("=" * 78)
for label, path in (("до правки", origin), ("после правки", current)):
    data = path.read_bytes()
    text = data.decode("utf-8", errors="replace")
    print(f"  {label}: {path.name}")
    print(f"    размер: {len(data)} б")
    print(f"    BOM: {data[:3] == b'\\xef\\xbb\\xbf'}")
    print(f"    CRLF: {'\\r\\n' in text}")
    print(f"    кончается переводом строки: {text.endswith(chr(10))}")
    print(f"    строк: {len(text.splitlines())}")
    print(f"    последние 3 строки:")
    for line in text.splitlines()[-3:]:
        print(f"      {line[:80]!r}")

print()
print("=" * 78)
print("2. ПОИСК ПОДОЗРИТЕЛЬНЫХ СТРОК В DIFF")
print("=" * 78)
diff_file = current.with_name(current.name + ".diff")
diff_text = diff_file.read_text(encoding="utf-8")
print(f"  размер diff: {len(diff_text)} б, строк {len(diff_text.splitlines())}")
# Ищутся строки, которые git apply трактует как служебные.
for i, line in enumerate(diff_text.splitlines(), 1):
    if line.startswith("---") or line.startswith("+++"):
        continue
    if line.strip() in ("",) and not line.startswith((" ", "+", "-", "@")):
        print(f"  строка {i}: ПУСТАЯ без префикса: {line!r}")
    if line.startswith("-") and line[1:].startswith("-"):
        print(f"  строка {i}: начинается с '--': {line[:70]!r}")
    if "No newline" in line:
        print(f"  строка {i}: {line[:70]!r}")

print()
print("=" * 78)
print("3. ПРИМЕНЕНИЕ С ПОЛНЫМ СООБЩЕНИЕМ GIT")
print("=" * 78)
tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="diffdebug_"))
work = tmpdir / REL
work.parent.mkdir(parents=True, exist_ok=True)
work.write_bytes(origin.read_bytes())
diff_copy = tmpdir / "p.diff"
diff_copy.write_bytes(diff_file.read_bytes())

for args in (["git", "apply", "-p1", "--verbose", str(diff_copy)],
             ["git", "apply", "-p1", "--check", str(diff_copy)],
             ["git", "apply", "--recount", str(diff_copy)]):
    result = subprocess.run(args, capture_output=True, text=True,
                            encoding="utf-8", errors="replace",
                            cwd=str(tmpdir), timeout=120)
    print(f"  {' '.join(args[1:])}: код {result.returncode}")
    out = ((result.stdout or "") + (result.stderr or "")).strip()
    for line in out.splitlines()[:6]:
        print(f"    {line[:120]}")

print()
print("=" * 78)
print("4. СРАВНЕНИЕ: ЧТО В ФАЙЛЕ, ЧЕГО НЕТ В DIFF")
print("=" * 78)
# Строится diff заново и сравнивается с сохранённым.
old_text = origin.read_text(encoding="utf-8", errors="replace")
new_text = current.read_text(encoding="utf-8", errors="replace")
if not old_text.endswith("\n"):
    old_text += "\n"
if not new_text.endswith("\n"):
    new_text += "\n"
rebuilt = "".join(difflib.unified_diff(
    old_text.splitlines(keepends=True),
    new_text.splitlines(keepends=True),
    fromfile=f"a/{REL}", tofile=f"b/{REL}", n=3,
))
print(f"  пересобранный diff совпадает с файлом: {rebuilt == diff_text}")
if rebuilt != diff_text:
    print(f"    размер файла:     {len(diff_text)}")
    print(f"    размер пересборки: {len(rebuilt)}")
    # Ищется первое расхождение.
    for i, (a, b) in enumerate(zip(diff_text.splitlines(),
                                   rebuilt.splitlines()), 1):
        if a != b:
            print(f"    строка {i}:")
            print(f"      в файле:  {a[:90]!r}")
            print(f"      сборка:   {b[:90]!r}")
            break

# Применяется пересобранный diff: если он работает, причина в записи файла.
work2 = tmpdir / "rebuilt" / REL
work2.parent.mkdir(parents=True, exist_ok=True)
work2.write_bytes(origin.read_bytes())
diff2 = tmpdir / "p2.diff"
diff2.write_text(rebuilt, encoding="utf-8", newline="\n")
result = subprocess.run(["git", "apply", "-p1", str(diff2)],
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace", cwd=str(tmpdir / "rebuilt"),
                        timeout=120)
ok = work2.read_bytes() == current.read_bytes()
print(f"\n  пересобранный diff применим: {ok} (код {result.returncode})")
if not ok:
    err = ((result.stdout or "") + (result.stderr or "")).strip()
    print(f"    {err[:160]}")

import shutil  # noqa: E402

shutil.rmtree(tmpdir, ignore_errors=True)
