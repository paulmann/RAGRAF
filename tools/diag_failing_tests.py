"""Разбор падающих тестов SagaAI: код сломан или тест устарел?

Прежде чем «чинить все найденные ошибки», надо понять природу каждой.
Есть три разных случая, и лечатся они по-разному:

  A. КОД СЛОМАН — тест прав, поведение неверное. Править код.
  B. ТЕСТ УСТАРЕЛ — код работает верно, тест ждёт старый результат.
     Править тест (и это надо явно назвать, чтобы не приняли за поломку).
  C. ОКРУЖЕНИЕ — падение из-за прав доступа или отсутствия ресурса.
     Кода не касается.

Собираются точные причины по каждому падающему тесту.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
PY = SAGA / ".venv" / "Scripts" / "python.exe"
BASETEMP = r"C:\AutoClaw\RAGRAF\.staging\pytest_tmp"

FAILED = [
    "tests/test_task_state.py::test_next_task_gets_next_number_and_moves_current_marker",
    "tests/test_threads_devagent_files.py::test_list_thread_files_sorted_with_text_probe",
    "tests/test_updater.py::test_apply_applies_staged_files",
    "tests/test_updater_apply.py::test_apply_single_file_happy_path",
    "tests/test_updater_apply.py::test_apply_rejects_unsafe_path",
    "tests/test_updater_apply.py::test_apply_rollback_on_failed_second_file",
    "tests/test_updater_apply.py::test_apply_new_file_success",
    "tests/test_updater_apply.py::test_apply_removes_only_applied",
    "tests/test_updater_apply.py::test_apply_updates_state_with_version",
    "tests/test_updater_apply.py::test_rollback_whole_run",
    "tests/test_usability_fixes.py::test_orch_send_persists_raw_uploads_and_sets_notice",
]

print("=" * 78)
print("ПРИЧИНЫ ПАДЕНИЙ")
print("=" * 78)

for test_id in FAILED:
    proc = subprocess.run(
        [str(PY), "-m", "pytest", test_id, "-q", "-p", "no:cacheprovider",
         f"--basetemp={BASETEMP}", "--tb=short", "--no-header"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(SAGA), timeout=600,
    )
    out = (proc.stdout or "") + (proc.stderr or "")

    # Извлекается строка с assert или ошибкой.
    reason = ""
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("E ") and len(s) > 3:
            reason = s[2:].strip()[:150]
            break
    if not reason:
        for line in out.splitlines():
            if "Error" in line or "assert" in line.lower():
                reason = line.strip()[:150]
                break

    # Определяется природа падения.
    nature = "неясно"
    low = out.lower()
    if "permissionerror" in low or "winerror 5" in low:
        nature = "C. ОКРУЖЕНИЕ (права доступа)"
    elif 'assert "' in reason or "assert '" in reason:
        nature = "B. ТЕСТ ждёт другой текст"
    elif "assert" in reason:
        nature = "A/B. расхождение значений"

    short = test_id.split("::")[-1]
    file_name = test_id.split("::")[0].replace("tests/", "")
    print(f"\n  {file_name}")
    print(f"    {short}")
    print(f"    природа: {nature}")
    print(f"    причина: {reason}")

print()
print("=" * 78)
print("СВОДКА")
print("=" * 78)
print(f"""
  Всего падающих тестов: {len(FAILED)}

  Для передачи разработчику важно разделить:
    * правки КОДА — это исправление ошибок;
    * правки ТЕСТОВ — это приведение проверок в соответствие с кодом.

  Падения типа C (права доступа) к коду отношения не имеют.
""")
