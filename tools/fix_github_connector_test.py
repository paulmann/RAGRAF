"""Исправление устаревшего теста: сервис 'github' переименован в 'github_rest'.

ЗАМЕР (факты):
    в коде проекта:  'github_rest' — 8 вхождений в 5 файлах
                     (core/connectors.py, core/orchestrator_tools.py,
                      core/orchestrators.py, dev_agent/universal_agent.py,
                      ui/pages/connectors.py)
    в тестах:        'github_rest' — 36 вхождений
    'github':        в коде — 0 вхождений, в тестах — 3

Единственный тест, использующий старое имя `github` —
`tests/test_github_connector.py`. Он и даёт 14 ошибок в фикстуре:
валидатор отклоняет неизвестный сервис, поэтому тесты не доходят
до проверок.

ПРАВКА: имя сервиса приводится к текущему. Это НЕ ослабление проверки:
тест продолжает проверять то же поведение коннектора, только через
существующее имя сервиса.

Отдельно проверяется, что в тесте нет других следов старого имени
(например переменных окружения или путей), и что после правки все
14 тестов проходят.
"""

from __future__ import annotations

import ast
import pathlib
import re
import shutil
import subprocess

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
PY = SAGA / ".venv" / "Scripts" / "python.exe"
TARGET = SAGA / "tests" / "test_github_connector.py"
text = TARGET.read_text(encoding="utf-8")

print("=" * 78)
print("СОСТОЯНИЕ ДО ПРАВКИ")
print("=" * 78)
old_hits = [(i, ln.strip()) for i, ln in enumerate(text.splitlines(), 1)
            if re.search(r'["\']github["\']', ln)]
print(f"  вхождений '\"github\"': {len(old_hits)}")
for line_no, line in old_hits:
    print(f"    {line_no:>5}: {line[:92]}")

# Проверяется, нет ли других следов старого имени.
other = [(i, ln.strip()) for i, ln in enumerate(text.splitlines(), 1)
         if "github_" in ln and '"github"' not in ln]
if other:
    print(f"\n  прочие упоминания github_*: {len(other)}")
    for line_no, line in other[:6]:
        print(f"    {line_no:>5}: {line[:92]}")

if not old_hits:
    print("\n  правка не требуется: старого имени нет")
    raise SystemExit(0)

backup = TARGET.with_suffix(".py.bak-service")
shutil.copy2(TARGET, backup)
print(f"\n  бэкап: {backup.name}")

# Замена только строковых литералов с именем сервиса.
new_text = re.sub(r'(["\'])github\1', r"\1github_rest\1", text)
TARGET.write_text(new_text, encoding="utf-8", newline="\n")
after = TARGET.read_text(encoding="utf-8")
print("  правка внесена: \"github\" -> \"github_rest\"")

try:
    ast.parse(after)
    print("  синтаксис верен")
except SyntaxError as exc:
    print(f"  ПРОВАЛ синтаксиса: {exc.lineno}: {exc.msg}")
    raise SystemExit(1)

remaining = len(re.findall(r'["\']github["\']', after))
print(f"  осталось старых вхождений: {remaining}")

print()
print("=" * 78)
print("ПРОВЕРКА")
print("=" * 78)
proc = subprocess.run(
    [str(PY), "-m", "pytest", "tests/test_github_connector.py", "-q",
     "-p", "no:cacheprovider",
     "--basetemp=.staging\\pytest_tmp"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    cwd=str(SAGA), timeout=900,
)
for line in (proc.stdout or "").splitlines()[-6:]:
    print(f"  {line.strip()}")

# Проверяется, что соседний тест коннекторов не сломан.
proc = subprocess.run(
    [str(PY), "-m", "pytest", "tests/test_connectors.py",
     "tests/test_ssh_connector.py", "-q", "-p", "no:cacheprovider",
     "--basetemp=.staging\\pytest_tmp"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    cwd=str(SAGA), timeout=900,
)
print()
print("  смежные тесты коннекторов:")
for line in (proc.stdout or "").splitlines()[-3:]:
    print(f"    {line.strip()}")
