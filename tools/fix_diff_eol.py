"""Исправление diff для файлов БЕЗ завершающего перевода строки.

ТОЧНАЯ ПРИЧИНА (установлена фактом).
    исходный файл:  кончается переводом строки: False
    исправленный:   кончается переводом строки: True

Мой скрипт при построении diff ДОБАВЛЯЛ перевод строки к исходному тексту
(`if not old_text.endswith("\\n"): old_text += "\\n"`). Из-за этого diff
описывал файл, которого не существует: реальный файл без перевода строки.
Проверка это и показала:

    git apply --verbose  -> "Applied patch ... cleanly"  (ложный успех)
    git apply --check    -> "patch does not apply"

Разница между этими двумя вызовами как раз в проверке контекста: без неё
git применяет патч по номерам строк, не сверяя содержимое.

ПРАВИЛЬНЫЙ СПОСОБ. Стандартный формат diff поддерживает отсутствие
перевода строки маркером:

    \\ No newline at end of file

Маркер ставится ПОСЛЕ той строки, у которой нет завершающего перевода.
Реализуется разбором текста на строки с сохранением этой информации.

ПРОВЕРКА: после правки каждый diff применяется с `--check` (строгая
проверка контекста) к копии «до» и даёт файл, побайтово равный
исправленному.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import tempfile

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
OUT = pathlib.Path(__file__).resolve().parents[1].parent / "docs/SagaAI/Fixes"

ORIGINS = {
    "core/dangerous.py": "core/dangerous.py.bak-safe288",
    "core/updater_apply.py": "core/updater_apply.py.bak-verifyrel",
    "ui/pages/orchestrator.py": "ui/pages/orchestrator.py.bak-slash",
    "tests/test_updater_apply.py": "tests/test_updater_apply.py.bak-staged",
    "tests/test_threads_devagent_files.py":
        "tests/test_threads_devagent_files.py.bak-isabs",
    "tests/test_task_state.py": "tests/test_task_state.py.bak-dirname",
    "tests/test_github_connector.py":
        "tests/test_github_connector.py.bak-staged"
        if (SAGA / "tests/test_github_connector.py.bak-staged").exists()
        else "tests/test_github_connector.py.bak-service",
    "requirements.txt": "requirements.txt.bak-versions",
}

NO_NEWLINE = "\\ No newline at end of file"


def lines_with_eol(text: str) -> tuple[list[str], bool]:
    """Разбить текст на строки, сохранив переводы.

    Возвращает (строки с переводами, был_завершающий_перевод).
    """
    lines = text.splitlines(keepends=True)
    ends_with_eol = text.endswith("\n") or text.endswith("\r")
    if not ends_with_eol and lines and not lines[-1].endswith(("\n", "\r")):
        # Последняя строка без перевода: информация сохраняется отдельно.
        return lines, False
    return lines, True


def make_diff(old_text: str, new_text: str, rel: str) -> str:
    """Построить unified diff с корректным маркером конца файла.

    Реализовано вручную, потому что `difflib.unified_diff` не ставит
    маркер «\\ No newline at end of file»: при отсутствии перевода строки
    в исходном или новом файле он портит патч.
    """
    import difflib

    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)

    # difflib работает со строками; служебные строки формируются отдельно.
    diff = list(difflib.unified_diff(
        [ln.rstrip("\r\n") for ln in old_lines],
        [ln.rstrip("\r\n") for ln in new_lines],
        fromfile=f"a/{rel}", tofile=f"b/{rel}", n=3, lineterm="",
    ))
    if not diff:
        return ""

    out: list[str] = []
    old_had_eol = old_text.endswith(("\n", "\r"))
    new_had_eol = new_text.endswith(("\n", "\r"))

    for line in diff:
        out.append(line)
        # Маркер ставится после последней строки контекста/удаления,
        # если в ИСХОДНОМ файле не было перевода строки.
        if line.startswith("-") and not line.startswith("---"):
            # Проверяется, последняя ли это строка старого файла.
            if old_lines and line[1:] == old_lines[-1].rstrip("\r\n") \
                    and not old_had_eol:
                out.append(NO_NEWLINE)
        elif line.startswith("+") and not line.startswith("+++"):
            if new_lines and line[1:] == new_lines[-1].rstrip("\r\n") \
                    and not new_had_eol:
                out.append(NO_NEWLINE)

    return "\n".join(out) + "\n"


print("=" * 78)
print("ПЕРЕСБОРКА DIFF С КОРРЕКТНЫМ КОНЦОМ ФАЙЛА")
print("=" * 78)
tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="diffverify_"))
ok_count = 0

for rel, origin_rel in ORIGINS.items():
    current_file = OUT / "изменённые" / rel
    origin_file = SAGA / origin_rel
    if not current_file.exists() or not origin_file.exists():
        print(f"  ПРОПУЩЕН: {rel} (нет {origin_rel})")
        continue

    old_text = origin_file.read_text(encoding="utf-8", errors="replace")
    new_text = current_file.read_text(encoding="utf-8", errors="replace")

    diff_text = make_diff(old_text, new_text, rel)
    if not diff_text:
        print(f"  {rel}: изменений нет")
        continue

    diff_path = current_file.with_name(current_file.name + ".diff")
    diff_path.write_text(diff_text, encoding="utf-8", newline="\n")

    # Строгая проверка: --check сверяет контекст, а не только номера строк.
    work_dir = tmpdir / rel.replace("/", "_")
    work = work_dir / rel
    work.parent.mkdir(parents=True, exist_ok=True)
    work.write_bytes(origin_file.read_bytes())
    diff_copy = work_dir / "p.diff"
    diff_copy.write_bytes(diff_path.read_bytes())

    check = subprocess.run(
        ["git", "apply", "-p1", "--check", str(diff_copy)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(work_dir), timeout=120,
    )
    apply_result = subprocess.run(
        ["git", "apply", "-p1", str(diff_copy)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(work_dir), timeout=120,
    )
    equal = work.read_bytes() == current_file.read_bytes()
    mark = "применим" if (check.returncode == 0 and equal) else "ОШИБКА"
    if mark == "применим":
        ok_count += 1
    print(f"  {rel:<44} {mark}")
    if mark == "ОШИБКА":
        err = ((check.stdout or "") + (check.stderr or "")).strip()
        print(f"      проверка: {err[:140]}")
        print(f"      результат совпал: {equal}")

shutil.rmtree(tmpdir, ignore_errors=True)

print()
print(f"  diff-файлов проверено строго: {ok_count} из {len(ORIGINS)}")

# Обновляются копии «до правки»: они должны соответствовать реальным файлам.
print()
print("=" * 78)
print("КОПИИ «ДО ПРАВКИ»")
print("=" * 78)
for rel, origin_rel in ORIGINS.items():
    origin_src = SAGA / origin_rel
    dst = OUT / "изменённые" / rel
    if not origin_src.exists() or not dst.exists():
        continue
    origin_dst = dst.with_name(dst.name + ".ДО-ПРАВКИ")
    shutil.copy2(origin_src, origin_dst)
print(f"  обновлено копий: {len(ORIGINS)}")
