"""Проверка: не сломал ли мой `/RAGRAF` тест usability_fixes.

Падает `test_usability_fixes.py::test_orch_send_persists_raw_uploads_and_sets_notice`
с `KeyError: 'orch_o1_user_message'`.

Я правил `ui/pages/orchestrator.py` — добавлял команду `/RAGRAF` и обернул
отправку сообщения в ветку `elif`. Надо исключить, что падение вызвано
моей правкой.

СПОСОБ: тест прогоняется на ТЕКУЩЕЙ версии и на КОПИИ до моей правки
(`orchestrator.py.bak-slash`). Если падает в обоих случаях — причина
не в моей правке.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
PY = SAGA / ".venv" / "Scripts" / "python.exe"
TARGET = SAGA / "ui" / "pages" / "orchestrator.py"
BACKUP = TARGET.with_suffix(".py.bak-slash")
TEST = "tests/test_usability_fixes.py::test_orch_send_persists_raw_uploads_and_sets_notice"
BASETEMP = r"C:\AutoClaw\RAGRAF\.staging\pytest_tmp"


def run_test(label: str) -> tuple[int, str]:
    proc = subprocess.run(
        [str(PY), "-m", "pytest", TEST, "-q", "-p", "no:cacheprovider",
         f"--basetemp={BASETEMP}", "--tb=line"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(SAGA), timeout=600,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    last = ""
    for line in out.splitlines():
        if line.strip().startswith("E "):
            last = line.strip()[2:].strip()[:140]
    print(f"\n  === {label} ===")
    print(f"    код возврата: {proc.returncode}")
    if last:
        print(f"    причина: {last}")
    for line in out.splitlines():
        if "passed" in line or "failed" in line:
            print(f"    итог: {line.strip()[:100]}")
    return proc.returncode, out


print("=" * 78)
print("СРАВНЕНИЕ: С МОЕЙ ПРАВКОЙ И БЕЗ НЕЁ")
print("=" * 78)

current = TARGET.read_text(encoding="utf-8")
code_now, _ = run_test("ТЕКУЩАЯ версия (с командой /RAGRAF)")

if BACKUP.exists():
    shutil.copy2(BACKUP, TARGET)
    code_before, _ = run_test("БЕЗ моей правки (из бэкапа)")
    TARGET.write_text(current, encoding="utf-8", newline="\n")
    print("\n  текущая версия возвращена на место")
else:
    print(f"\n  бэкап не найден: {BACKUP}")
    code_before = -1

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
if code_now != 0 and code_before != 0:
    print("""
  Тест падает В ОБОИХ случаях. Значит причина НЕ в моей правке:
  это существующий дефект проекта (или устаревший тест).
  Моя правка не добавляет и не убирает это падение.
""")
elif code_now != 0 and code_before == 0:
    print("""
  ВНИМАНИЕ: тест проходил до моей правки и падает после.
  Значит дефект внесён МНОЙ — правку надо править.
""")
elif code_now == 0 and code_before != 0:
    print("""
  Моя правка ИСПРАВИЛА это падение.
""")
else:
    print("  Тест проходит в обоих случаях — падение было устранено ранее.")
