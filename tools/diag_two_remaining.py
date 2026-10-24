"""Разбор двух оставшихся падений: код сломан или тест устарел?

ПАДЕНИЕ 1: `test_threads_devagent_files.py:101`
    assert all(r["path"].startswith(os.sep) for r in records)
    -> False

Тест ожидает, что поле `path` начинается с системного разделителя
(на Windows — '\\\\', на POSIX — '/'). Значит он написан под POSIX:
там `os.sep` = '/', и путь `/abs/path` подходит. На Windows `os.sep` = '\\\\',
и путь `C:\\abs\\path` с него не начинается.

ПАДЕНИЕ 2: `test_task_state.py`
    assert False — надо посмотреть, что именно проверяется.

Проверяется фактическое значение поля `path` и логика теста:
  * если `path` — корректный абсолютный путь, а тест требует именно
    POSIX-разделитель, это дефект ТЕСТА (проверка платформозависимая);
  * если `path` не абсолютный, это дефект КОДА.
"""

from __future__ import annotations

import os
import pathlib
import sys

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
sys.path.insert(0, str(SAGA))

print("=" * 78)
print("1. ПАДЕНИЕ В test_threads_devagent_files")
print("=" * 78)
test_file = SAGA / "tests" / "test_threads_devagent_files.py"
lines = test_file.read_text(encoding="utf-8", errors="replace").splitlines()
# Печатается тест целиком.
start = next(i for i, ln in enumerate(lines, 1)
             if ln.startswith("def test_list_thread_files_sorted_with_text_probe"))
end = next((i for i in range(start + 1, len(lines) + 1)
            if i <= len(lines) and lines[i - 1].startswith("def ")),
           len(lines) + 1)
for j in range(start - 1, end - 1):
    print(f"  {j + 1:>5}: {lines[j][:96]}")

print()
print("  os.sep на этой машине:", repr(os.sep))
print("  os.altsep:", repr(os.altsep))
print("""
  Проверка `path.startswith(os.sep)` работает только на POSIX:
  там os.sep = '/', и любой абсолютный путь начинается с '/'.
  На Windows os.sep = '\\\\', а абсолютный путь начинается с 'C:'.
  Значит тест написан под Linux.
""")

print()
print("=" * 78)
print("2. ЧТО РЕАЛЬНО В ПОЛЕ path")
print("=" * 78)
probe = SAGA / "_probe_path_field.py"
probe.write_text(
    "import sys, os\n"
    f"sys.path.insert(0, r'{SAGA}')\n"
    "import tempfile, pathlib\n"
    "from core import threads_devagent as td\n"
    "tmp = tempfile.mkdtemp(prefix='pathprobe_')\n"
    "try:\n"
    "    # Создаётся тред и файл, как в тесте.\n"
    "    tid = td.create_devagent_thread(title='probe')\n"
    "    td.save_thread_file_data(tid, 'a.txt', b'hello text\\n')\n"
    "    td.save_thread_file_data(tid, 'b.zip', b'PK\\x03\\x04raw')\n"
    "    records = td.list_thread_files(tid)\n"
    "    print(f'записей: {len(records)}')\n"
    "    for r in records:\n"
    "        p = r.get('path', '')\n"
    "        print(f\"name={r.get('name')!r} path={p!r} \"\n"
    "              f\"isabs={os.path.isabs(p)} \"\n"
    "              f\"startswith_sep={p.startswith(os.sep)}\")\n"
    "except Exception as exc:\n"
    "    print(f'ОШИБКА: {type(exc).__name__}: {exc}')\n"
    "finally:\n"
    "    import shutil\n"
    "    shutil.rmtree(tmp, ignore_errors=True)\n",
    encoding="utf-8", newline="\n",
)
import subprocess  # noqa: E402

proc = subprocess.run([str(SAGA / ".venv" / "Scripts" / "python.exe"),
                       str(probe)],
                      capture_output=True, text=True, encoding="utf-8",
                      errors="replace", cwd=str(SAGA), timeout=300)
for line in (proc.stdout or "").splitlines():
    print(f"  {line}")
if proc.stderr.strip():
    print(f"  stderr: {proc.stderr.strip()[:240]}")
probe.unlink(missing_ok=True)

print()
print("=" * 78)
print("3. ПАДЕНИЕ В test_task_state")
print("=" * 78)
probe2 = SAGA / "_probe_task_state.py"
probe2.write_text(
    "import sys\n"
    f"sys.path.insert(0, r'{SAGA}')\n"
    "from dev_agent import task_state as ts\n"
    "print(f'функции: {[n for n in dir(ts) if not n.startswith(\"_\")][:14]}')\n"
    "for name in ('load_tasks', 'list_tasks', 'get_tasks'):\n"
    "    fn = getattr(ts, name, None)\n"
    "    if callable(fn):\n"
    "        try:\n"
    "            result = fn()\n"
    "            print(f'{name}() -> {type(result).__name__}: '\n"
    "                  f'{str(result)[:160]}')\n"
    "        except Exception as exc:\n"
    "            print(f'{name}() -> ошибка {type(exc).__name__}: {exc}')\n",
    encoding="utf-8", newline="\n",
)
proc = subprocess.run([str(SAGA / ".venv" / "Scripts" / "python.exe"),
                       str(probe2)],
                      capture_output=True, text=True, encoding="utf-8",
                      errors="replace", cwd=str(SAGA), timeout=300)
for line in (proc.stdout or "").splitlines():
    print(f"  {line}")
if proc.stderr.strip():
    print(f"  stderr: {proc.stderr.strip()[:240]}")
probe2.unlink(missing_ok=True)
