"""Исправление теста task_state: разбор пути завязан на POSIX.

ПАДЕНИЕ:
    base = first["task_dir"].rsplit("/", 1)[0]
    assert Path(base, "task_1").exists()

ПРИЧИНА. `rsplit("/", 1)` отрезает часть пути по прямому слешу. На Windows
`task_dir` приходит с обратными слешами (`C:\\...\\tasks\\task_1`), поэтому
разделение по '/' ничего не отрезает: `base` остаётся полным путём до
`task_1`. Дальше `Path(base, "task_1")` даёт `...\\task_1\\task_1` —
несуществующий путь, и проверка падает.

Код при этом работает верно: каталоги `task_1`, `task_2` создаются.
Дефект в способе разбора пути внутри теста.

ЗАМЕР (из вывода pytest):
    WindowsPath('C:/.../task_1')  — путь корректен
    Path(base, "task_1').exists() -> False — из-за склейки

ПРАВКА: вместо `rsplit("/", 1)` используется `os.path.dirname` — он
понимает системный разделитель на любой платформе. Это не ослабляет
проверку: имя каталога `task_2` по-прежнему сверяется явно строкой выше.
"""

from __future__ import annotations

import ast
import os
import pathlib
import shutil
import subprocess

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
PY = SAGA / ".venv" / "Scripts" / "python.exe"
TARGET = SAGA / "tests" / "test_task_state.py"
text = TARGET.read_text(encoding="utf-8")

print("=" * 78)
print("МЕСТА РАЗБОРА ПУТЕЙ В ТЕСТЕ")
print("=" * 78)
# Ищутся все места, где путь разбирается по '/'.
for i, line in enumerate(text.splitlines(), 1):
    if 'rsplit("/"' in line or "rsplit('/'" in line:
        print(f"  {i:>5}: {line.strip()[:92]}")

OLD = '''    base = first["task_dir"].rsplit("/", 1)[0]'''

NEW = '''    # Use os.path.dirname, not rsplit("/"): task_dir is built with the
    # platform separator, so on Windows an rsplit on "/" strips nothing and
    # base stays inside task_1, making the next assertion look for a
    # nonexistent ...\\task_1\\task_1. dirname understands both separators.
    base = os.path.dirname(os.path.normpath(first["task_dir"]))'''

if OLD in text:
    backup = TARGET.with_suffix(".py.bak-dirname")
    shutil.copy2(TARGET, backup)
    text = text.replace(OLD, NEW, 1)
    # Проверяется, что os импортирован.
    if not any(ln.strip() == "import os" for ln in text.splitlines()):
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("import ") or line.startswith("from "):
                lines.insert(i, "import os")
                break
        text = "\n".join(lines) + "\n"
        print("  добавлен импорт os")
    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print(f"  исправлен: {TARGET.name} (бэкап {backup.name})")
else:
    if "os.path.dirname(os.path.normpath(first" in text:
        print("  правка уже внесена")
    else:
        print("  НЕ НАЙДЕН фрагмент для правки")

try:
    ast.parse(TARGET.read_text(encoding="utf-8"))
    print("  синтаксис верен")
except SyntaxError as exc:
    print(f"  ПРОВАЛ синтаксиса: {exc.lineno}: {exc.msg}")

print()
print("=" * 78)
print("ПРОВЕРКА")
print("=" * 78)
proc = subprocess.run(
    [str(PY), "-m", "pytest", "tests/test_task_state.py", "-q",
     "-p", "no:cacheprovider",
     "--basetemp=.staging\\pytest_tmp"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    cwd=str(SAGA), timeout=600,
)
for line in (proc.stdout or "").splitlines()[-4:]:
    print(f"  {line.strip()}")
