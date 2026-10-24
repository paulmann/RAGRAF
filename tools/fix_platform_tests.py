"""Исправление двух тестов, написанных под Linux.

Оба падения — дефекты ТЕСТОВ, а не кода. Разбор:

--- 1. tests/test_threads_devagent_files.py:101 ---

    assert all(r["path"].startswith(os.sep) for r in records)

Проверка требует, чтобы путь начинался с системного разделителя.
На POSIX (Linux) `os.sep` = '/', и любой абсолютный путь начинается с '/',
поэтому там тест проходит. На Windows `os.sep` = '\\', а абсолютный путь
начинается с буквы диска: `C:\\...\\a.txt`. Условие ложное не потому,
что путь неверный, а потому что проверка написана под одну платформу.

ЗАМЕР (вызовом): `path='C:\\...\\a.txt'`, `os.path.isabs(path)` → True.
Путь корректный; проверка нет.

ПРАВКА: вместо проверки конкретного символа проверяется абсолютность —
именно это тест имел в виду. `os.path.isabs` работает на обеих платформах.

--- 2. tests/test_task_state.py ---

Надо посмотреть причину отдельно: причина в выводе скрипта обрезалась.
Здесь проверяется то, что уже установлено, и правится только первый тест,
если второй окажется другого рода.

ВАЖНО: правки НЕ ослабляют проверки. `isabs` строже, чем `startswith(os.sep)`:
он отвергает и относительные пути, и путь из одного имени файла.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
PY = SAGA / ".venv" / "Scripts" / "python.exe"

# --- 1. Правка теста путей ---
TARGET1 = SAGA / "tests" / "test_threads_devagent_files.py"
text1 = TARGET1.read_text(encoding="utf-8")

OLD1 = '''    assert all(r["path"].startswith(os.sep) for r in records)'''

NEW1 = '''    # The path must be ABSOLUTE. An earlier version checked
    # `path.startswith(os.sep)`, which only holds on POSIX: there os.sep is
    # '/', while on Windows an absolute path starts with a drive letter
    # ('C:\\\\...'), so the check failed for correct paths. `os.path.isabs`
    # states the real requirement and works on both platforms - it is also
    # stricter, since it rejects relative paths outright.
    assert all(os.path.isabs(r["path"]) for r in records)'''

if OLD1 in text1:
    backup1 = TARGET1.with_suffix(".py.bak-isabs")
    import shutil  # noqa: E402

    shutil.copy2(TARGET1, backup1)
    text1 = text1.replace(OLD1, NEW1, 1)
    TARGET1.write_text(text1, encoding="utf-8", newline="\n")
    print(f"  исправлен: {TARGET1.name} (бэкап {backup1.name})")
else:
    print(f"  {TARGET1.name}: правка не требуется или уже внесена")

# --- 2. Разбор второго падения ---
print()
print("=" * 78)
print("РАЗБОР ВТОРОГО ПАДЕНИЯ: test_task_state")
print("=" * 78)
proc = subprocess.run(
    [str(PY), "-m", "pytest",
     "tests/test_task_state.py::test_next_task_gets_next_number_and_moves_current_marker",
     "-q", "-p", "no:cacheprovider",
     "--basetemp=C:\\AutoClaw\\RAGRAF\\.staging\\pytest_tmp", "--tb=long"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    cwd=str(SAGA), timeout=600,
)
out = (proc.stdout or "") + (proc.stderr or "")
# Печатается тело теста и место падения.
lines = out.splitlines()
printing = False
for line in lines:
    if "def test_next_task" in line:
        printing = True
    if printing:
        print(f"  {line[:100]}")
    if printing and len([1 for ln in lines if ln.startswith("  ")]) > 60:
        break

print()
print("=" * 78)
print("ПРОВЕРКА ПЕРВОЙ ПРАВКИ")
print("=" * 78)
proc = subprocess.run(
    [str(PY), "-m", "pytest", "tests/test_threads_devagent_files.py",
     "-q", "-p", "no:cacheprovider",
     "--basetemp=C:\\AutoClaw\\RAGRAF\\.staging\\pytest_tmp"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    cwd=str(SAGA), timeout=600,
)
for line in (proc.stdout or "").splitlines()[-3:]:
    print(f"  {line.strip()}")

try:
    ast.parse(TARGET1.read_text(encoding="utf-8"))
    print("  синтаксис верен")
except SyntaxError as exc:
    print(f"  ПРОВАЛ синтаксиса: {exc.lineno}: {exc.msg}")
