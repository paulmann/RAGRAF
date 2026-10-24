"""Точная причина: почему diff одного файла не применяется.

Diff выглядит корректным, но `git apply --check` его отвергает,
причём без сообщения. Надо найти фактическое расхождение между
контекстом в diff и содержимым файла-копии.

Проверяется по строкам: берётся каждый hunk, и его контекстные строки
сравниваются с соответствующими строками файла «до правки».
"""

from __future__ import annotations

import pathlib
import re

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
OUT = pathlib.Path(__file__).resolve().parents[1].parent / "docs/SagaAI/Fixes"
REL = "tests/test_task_state.py"

origin = SAGA / "tests" / "test_task_state.py.bak-dirname"
current = OUT / "изменённые" / REL
diff_file = current.with_name(current.name + ".diff")

old_lines = origin.read_text(encoding="utf-8", errors="replace").splitlines()
new_lines = current.read_text(encoding="utf-8", errors="replace").splitlines()
diff_lines = diff_file.read_text(encoding="utf-8").splitlines()

print("=" * 78)
print("РАЗМЕРЫ И КОНЦЫ ФАЙЛОВ")
print("=" * 78)
old_bytes = origin.read_bytes()
new_bytes = current.read_bytes()
print(f"  до правки:    строк {len(old_lines)}, байт {len(old_bytes)}")
print(f"    конец: {old_bytes[-20:]!r}")
print(f"  после правки: строк {len(new_lines)}, байт {len(new_bytes)}")
print(f"    конец: {new_bytes[-20:]!r}")

print()
print("=" * 78)
print("РАЗБОР HUNK-ОВ: СВЕРКА КОНТЕКСТА С ФАЙЛОМ")
print("=" * 78)
# Разбираются заголовки hunk.
hunks = []
i = 0
while i < len(diff_lines):
    line = diff_lines[i]
    m = re.match(r"@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@", line)
    if m:
        old_start = int(m.group(1))
        old_count = int(m.group(2) or 1)
        hunks.append((old_start, old_count, i))
    i += 1

print(f"  найдено hunk-ов: {len(hunks)}")
for old_start, old_count, idx in hunks:
    print(f"\n  === hunk: строки {old_start}..{old_start + old_count - 1} "
          f"(файла до правки) ===")
    # Печатаются контекстные строки hunk и соответствующие строки файла.
    j = idx + 1
    file_no = old_start
    mismatch = 0
    while j < len(diff_lines) and not diff_lines[j].startswith("@@"):
        entry = diff_lines[j]
        if entry.startswith(" ") or entry.startswith("-"):
            text_in_diff = entry[1:]
            text_in_file = (old_lines[file_no - 1]
                            if 0 < file_no <= len(old_lines) else "<нет строки>")
            same = text_in_diff == text_in_file
            if not same:
                mismatch += 1
                print(f"    НЕ СОВПАЛО на строке файла {file_no}:")
                print(f"      в diff: {text_in_diff[:80]!r}")
                print(f"      в файле: {text_in_file[:80]!r}")
            if entry.startswith(" "):
                file_no += 1
            elif entry.startswith("-"):
                file_no += 1
        elif entry.startswith("+"):
            pass
        j += 1
    if mismatch == 0:
        print("    контекст совпадает полностью")

print()
print("=" * 78)
print("ПОПЫТКА ПРИМЕНЕНИЯ БЕЗ -p1 (пути в diff относительные)")
print("=" * 78)
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402

tmp = pathlib.Path(tempfile.mkdtemp(prefix="applyprobe_"))
work = tmp / REL
work.parent.mkdir(parents=True, exist_ok=True)
work.write_bytes(origin.read_bytes())

for args in (["git", "apply", "-p1", str(diff_file)],
             ["git", "apply", "-p1", "--whitespace=nowarn", str(diff_file)],
             ["git", "apply", "-p1", "--ignore-whitespace", str(diff_file)]):
    result = subprocess.run(args, capture_output=True, text=True,
                            encoding="utf-8", errors="replace",
                            cwd=str(tmp), timeout=120)
    out = ((result.stdout or "") + (result.stderr or "")).strip()
    print(f"  {' '.join(args[1:])}: код {result.returncode}"
          + (f" — {out[:100]}" if out else ""))
    # После успешного применения файл меняется: восстанавливается.
    work.write_bytes(origin.read_bytes())

shutil.rmtree(tmp, ignore_errors=True)

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Разбор hunk-ов показывает, совпадает ли контекст diff с файлом.
  Если контекст совпадает, а применение падает — дело в формате патча
  (например git ожидает другой префикс путей или маркер конца файла).
""")
