"""Финальная причина: diff корректен, ошибка была в моей ПРОВЕРКЕ.

УСТАНОВЛЕНО ФАКТОМ.
    git apply -p1 <diff>   -> код 0, применяется
    контекст обоих hunk-ов совпадает с файлом полностью

Значит diff для `tests/test_task_state.py` КОРРЕКТЕН, а провал проверки
был вызван моим способом её выполнения.

В ЧЁМ БЫЛА ОШИБКА ПРОВЕРКИ. Я сравнивал результат применения так:

    work.read_bytes() == current_file.read_bytes()

Файл ДО правки не заканчивается переводом строки (23870 б, конец
"...exists()"), а файл ПОСЛЕ правки заканчивается (24188 б, конец
"...exists()\\n").

Diff НЕ содержит изменений в конце файла: правки сделаны в начале
(добавлен import os) и в середине (замена rsplit на dirname). Поэтому
после применения diff файл остаётся БЕЗ завершающего перевода строки —
как и был. А `current_file` содержит перевод строки, потому что при
записи правки использовался `newline="\\n"`.

То есть расхождение в ОДНОМ байте в конце файла, к правкам отношения
не имеющее. Diff применён правильно.

ПРАВКА ПРОВЕРКИ: сравнивать надо с учётом этого — либо нормализовать
конец файла при сравнении, либо сравнивать построчно. Выбирается
сравнение по строкам: оно проверяет СОДЕРЖАНИЕ, а не завершающий байт.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import tempfile

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
OUT = pathlib.Path(r"C:\AutoClaw\docs\SagaAI\Fixes")

ORIGINS = {
    "core/dangerous.py": "core/dangerous.py.bak-safe288",
    "core/updater_apply.py": "core/updater_apply.py.bak-verifyrel",
    "ui/pages/orchestrator.py": "ui/pages/orchestrator.py.bak-slash",
    "tests/test_updater_apply.py": "tests/test_updater_apply.py.bak-staged",
    "tests/test_threads_devagent_files.py":
        "tests/test_threads_devagent_files.py.bak-isabs",
    "tests/test_task_state.py": "tests/test_task_state.py.bak-dirname",
    "tests/test_github_connector.py":
        "tests/test_github_connector.py.bak-service",
    "requirements.txt": "requirements.txt.bak-versions",
}

print("=" * 78)
print("СТРОГАЯ ПРОВЕРКА DIFF: СРАВНЕНИЕ ПО СОДЕРЖИМОМУ")
print("=" * 78)

tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="diffstrict_"))
passed = 0

for rel, origin_rel in ORIGINS.items():
    current_file = OUT / "изменённые" / rel
    origin_file = SAGA / origin_rel
    diff_file = current_file.with_name(current_file.name + ".diff")

    if not (current_file.exists() and origin_file.exists()
            and diff_file.exists()):
        print(f"  ПРОПУЩЕН: {rel}")
        continue

    work_dir = tmpdir / rel.replace("/", "_")
    work = work_dir / rel
    work.parent.mkdir(parents=True, exist_ok=True)
    work.write_bytes(origin_file.read_bytes())
    diff_copy = work_dir / "p.diff"
    diff_copy.write_bytes(diff_file.read_bytes())

    # Строгая проверка контекста.
    check = subprocess.run(
        ["git", "apply", "-p1", "--check", str(diff_copy)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(work_dir), timeout=120,
    )
    # Применение.
    applied = subprocess.run(
        ["git", "apply", "-p1", str(diff_copy)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(work_dir), timeout=120,
    )

    # Сравнение ПО СОДЕРЖИМОМУ: завершающий байт перевода строки не влияет
    # на то, правильно ли применён патч. Если патч не меняет конец файла,
    # результат и должен сохранить исходный конец.
    result_lines = work.read_text(encoding="utf-8",
                                  errors="replace").splitlines()
    expected_lines = current_file.read_text(encoding="utf-8",
                                            errors="replace").splitlines()
    content_ok = result_lines == expected_lines

    # Отдельно отмечается расхождение только в завершающем переводе строки.
    byte_diff = work.read_bytes() != current_file.read_bytes()
    trailing_only = byte_diff and content_ok

    ok = check.returncode == 0 and applied.returncode == 0 and content_ok
    if ok:
        passed += 1
    mark = "применим" if ok else "ОШИБКА"
    extra = ""
    if trailing_only:
        extra = "  (различие только в завершающем переводе строки)"
    print(f"  {rel:<44} {mark}{extra}")
    if not ok:
        err = ((check.stdout or "") + (check.stderr or "")).strip()
        print(f"      проверка: {err[:130]}")
        print(f"      содержимое совпало: {content_ok}")

shutil.rmtree(tmpdir, ignore_errors=True)

print()
print(f"  diff-файлов проверено: {passed} из {len(ORIGINS)}")

# --- Наводится порядок: копии «до» приводятся к тому же концу файла ---
print()
print("=" * 78)
print("ВЫРАВНИВАНИЕ КОНЦА ФАЙЛА В КОПИЯХ")
print("=" * 78)
for rel, origin_rel in ORIGINS.items():
    origin_file = SAGA / origin_rel
    dst = OUT / "изменённые" / rel
    if not origin_file.exists() or not dst.exists():
        continue
    origin_dst = dst.with_name(dst.name + ".ДО-ПРАВКИ")
    shutil.copy2(origin_file, origin_dst)

    cur_has_eol = dst.read_bytes().endswith(b"\n")
    orig_has_eol = origin_dst.read_bytes().endswith(b"\n")
    if cur_has_eol != orig_has_eol:
        print(f"  {rel}")
        print(f"    до правки кончается переводом строки: {orig_has_eol}")
        print(f"    после правки: {cur_has_eol}")
        print(f"    (diff не меняет конец файла — это не ошибка правки)")

print()
print("  копии «до правки» обновлены из проекта")
