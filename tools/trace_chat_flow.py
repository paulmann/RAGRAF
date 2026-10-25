"""Точная причина: тест мокает st так, что моя ветка не исполняется.

Установлено:
    _parse_slash_command('go') → None      (верно, не команда)
    _parse_slash_command('/ragraf') → tuple (верно)

Значит при user_input='go' поток должен пойти в `elif`. Но тест падает.

ГИПОТЕЗА: тест подменяет `st.chat_input` на lambda, но моя правка
ДОБАВИЛА зависимость от порядка: я читаю `user_input` внутри
`elif user_input and user_input.strip()`. Если в моке `chat_input`
возвращает значение не строкой или если `st.bottom` не контекстный
менеджер — поток мог измениться.

Проверяется прямым экспериментом: запускается тот же тест, но с печатью
трассировки по моему коду — вызывается ли `_parse_slash_command` и что
возвращает.

Способ: обёртка вокруг `_parse_slash_command`, которая пишет в файл
каждый вызов. Так видно фактический путь исполнения.
"""

from __future__ import annotations

import pathlib
import subprocess

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
PY = SAGA / ".venv" / "Scripts" / "python.exe"
TRACE = pathlib.Path(__file__).resolve().parents[1] / ".staging/trace_parse.txt"

# Тест-обёртка: подменяет _parse_slash_command и запускает тот же сценарий.
PROBE = SAGA / "tests" / "_probe_trace_chat.py"
PROBE.write_text(
    "# -*- coding: utf-8 -*-\n"
    '"""Проверка: доходит ли поток до блока отправки при моей правке."""\n'
    "from __future__ import annotations\n"
    "\n"
    "import sys\n"
    f"sys.path.insert(0, r'{SAGA}')\n"
    "\n"
    "import tests.test_usability_fixes as utf\n"
    "from ui.pages import orchestrator as orch\n"
    "\n"
    "TRACE = r'" + str(TRACE) + "'\n"
    "CALLS = []\n"
    "\n"
    "_original = orch._parse_slash_command\n"
    "\n"
    "def traced(value):\n"
    "    result = _original(value)\n"
    "    CALLS.append((repr(value), repr(result)))\n"
    "    return result\n"
    "\n"
    "orch._parse_slash_command = traced\n"
    "\n"
    "from tests.test_usability_fixes import (\n"
    "    test_orch_send_persists_raw_uploads_and_sets_notice as t\n"
    ")\n"
    "\n"
    "class FakeMonkeypatch:\n"
    "    def setattr(self, obj, name, value):\n"
    "        setattr(obj, name, value)\n"
    "\n"
    "env = None\n"
    "try:\n"
    "    # Фикстура ui_env создаётся вручную: берётся из модуля теста.\n"
    "    import inspect\n"
    "    fixture = utf.ui_env\n"
    "    env = next(fixture.__wrapped__(None))\n"
    "    mp = FakeMonkeypatch()\n"
    "    t(env, mp)\n"
    "    print('ТЕСТ ПРОШЁЛ')\n"
    "except Exception as exc:\n"
    "    print(f'ТЕСТ УПАЛ: {type(exc).__name__}: {exc}')\n"
    "finally:\n"
    "    with open(TRACE, 'w', encoding='utf-8') as fh:\n"
    "        fh.write(f'вызовов _parse_slash_command: {len(CALLS)}\\n')\n"
    "        for value, result in CALLS[:20]:\n"
    "            fh.write(f'  {value} -> {result}\\n')\n",
    encoding="utf-8", newline="\n",
)

print("=" * 78)
print("ТРАССИРОВКА ВЫЗОВОВ В ТЕСТЕ")
print("=" * 78)
proc = subprocess.run([str(PY), "-m", "pytest", str(PROBE), "-q", "-s",
                       "-p", "no:cacheprovider",
                       "--basetemp=.staging\\pytest_tmp"],
                      capture_output=True, text=True, encoding="utf-8",
                      errors="replace", cwd=str(SAGA), timeout=600)
out = (proc.stdout or "") + (proc.stderr or "")
print(f"  код возврата: {proc.returncode}")
for line in out.splitlines():
    if any(k in line for k in ("ТЕСТ", "passed", "failed", "Error", "error",
                               "вызовов")):
        print(f"    {line.strip()[:120]}")

if TRACE.exists():
    print(f"\n  файл трассировки:")
    for line in TRACE.read_text(encoding="utf-8").splitlines():
        print(f"    {line}")

PROBE.unlink(missing_ok=True)
TRACE.unlink(missing_ok=True)
