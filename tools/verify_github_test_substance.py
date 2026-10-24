"""Проверка: не подменил ли я СУТЬ теста при правке имени сервиса.

ОПАСЕНИЕ. Тест называется `test_github_connector.py` и импортирует
`core.github_connector` (`gh`), но я заменил сервис коннектора с `github`
на `github_rest`. Если это разные модули, тест теперь проверяет не то,
что проверял: соединение создаётся через один модуль, а мокается другой.

Надо установить фактом:
  1. какие модули стоят за именами `github_connector` и `github_connector_rest`;
  2. какой модуль обслуживает сервис `github_rest`;
  3. работает ли тест ПО СУЩЕСТВУ: вызывается ли замоканный код.

Если тест теперь проходит, но не проверяет ничего (мок не срабатывает),
это хуже падения: он создаёт ложную уверенность. Проверяется, что мок
действительно вызывается.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
PY = SAGA / ".venv" / "Scripts" / "python.exe"
sys.path.insert(0, str(SAGA))

print("=" * 78)
print("1. КАКОЙ МОДУЛЬ ОБСЛУЖИВАЕТ СЕРВИС github_rest")
print("=" * 78)
from core import connectors as c  # noqa: E402

# Ищется, куда ведёт сервис.
src = (SAGA / "core" / "connectors.py").read_text(encoding="utf-8",
                                                 errors="replace")
lines = src.splitlines()
for i, line in enumerate(lines, 1):
    if "github_rest" in line:
        print(f"  {i:>5}: {line.strip()[:94]}")

print()
print("=" * 78)
print("2. КАКИЕ МОДУЛИ СУЩЕСТВУЮТ")
print("=" * 78)
for name in ("github_connector.py", "github_connector_rest.py"):
    path = SAGA / "core" / name
    if path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        first = [ln for ln in text.splitlines()
                 if ln.strip().startswith('"""') or "SPDX" in ln]
        print(f"  {name}: есть ({path.stat().st_size} б)")
        # Ищутся публичные функции.
        funcs = re.findall(r"^def (\w+)", text, re.MULTILINE)
        print(f"    функции: {funcs[:8]}")

print()
print("=" * 78)
print("3. ЧТО МОКАЕТ ТЕСТ И ЧТО ВЫЗЫВАЕТСЯ")
print("=" * 78)
test_text = (SAGA / "tests" / "test_github_connector.py").read_text(
    encoding="utf-8", errors="replace")
print("  патчи (моки) в тесте:")
for i, line in enumerate(test_text.splitlines(), 1):
    if "mock.patch" in line or "@mock.patch" in line:
        print(f"    {i:>5}: {line.strip()[:86]}")
print("\n  импорты в тесте:")
for i, line in enumerate(test_text.splitlines(), 1):
    if line.startswith("from ") or line.startswith("import "):
        print(f"    {i:>5}: {line.strip()[:86]}")

print()
print("=" * 78)
print("4. РЕАЛЬНАЯ ПРОВЕРКА: ВЫЗЫВАЕТСЯ ЛИ МОК")
print("=" * 78)
# Запускается тест с проверкой, что мок сработал: если mock не вызван,
# Mock сам это покажет.
probe = SAGA / "tests" / "_probe_mock_used.py"
probe.write_text(
    "# -*- coding: utf-8 -*-\n"
    '"""Проверка: срабатывает ли мок в тесте github-коннектора."""\n'
    "from __future__ import annotations\n"
    "\n"
    "from unittest import mock\n"
    "\n"
    "from core import connectors\n"
    "\n"
    "\n"
    "def test_mock_is_really_used(tmp_path):\n"
    "    \"\"\"Мок должен быть вызван: иначе тест ничего не проверяет.\"\"\"\n"
    "    with mock.patch(\n"
    "        \"core.github_connector._ensure_github\"\n"
    "    ) as mocked:\n"
    "        mocked.return_value = mock.MagicMock()\n"
    "        try:\n"
    "            connectors.create_connection(\n"
    "                \"github_rest\", \"Probe\", \"ghp-token\"\n"
    "            )\n"
    "        except Exception as exc:\n"
    "            print(f'вызов дал: {type(exc).__name__}: {exc}')\n"
    "        print(f'мок вызван: {mocked.called}')\n"
    "        assert mocked.called, (\n"
    "            'мок НЕ вызван: тест проходит, ничего не проверяя'\n"
    "        )\n",
    encoding="utf-8", newline="\n",
)
proc = subprocess.run(
    [str(PY), "-m", "pytest", str(probe), "-q", "-s", "-p",
     "no:cacheprovider",
     "--basetemp=.staging\\pytest_tmp"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    cwd=str(SAGA), timeout=600,
)
print(f"  код возврата: {proc.returncode}")
for line in ((proc.stdout or "") + (proc.stderr or "")).splitlines():
    if any(k in line for k in ("мок", "вызов", "passed", "failed", "Error")):
        print(f"    {line.strip()[:110]}")
probe.unlink(missing_ok=True)

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Проверка показывает, работает ли тест ПО СУЩЕСТВУ после правки имени
  сервиса. Если мок вызывается — тест проверяет то же поведение, что и
  раньше, просто через существующее имя сервиса.

  Если мок НЕ вызывается — тест стал пустым, и его надо не «починить»,
  а привести к реальному модулю.
""")
