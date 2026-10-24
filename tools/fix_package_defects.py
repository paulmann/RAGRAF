"""Исправление двух изъянов пакета.

ИЗЪЯН 1: diff для `tests/test_task_state.py` не применяется.
    error: corrupt patch at ...test_task_state.py.diff:29

ПРИЧИНА. Последняя строка файла не заканчивается переводом строки.
`difflib.unified_diff` в этом случае формирует строку без завершающего
`\\n`, и склеивает её со следующей служебной строкой diff:

    -    assert not (base / 'task_1 (current)').exists()+    assert not ...

Видно: строка удаления и строка добавления слились в одну. Такой diff
некорректен.

ПРАВКА. Строки файлов разбираются с сохранением переводов
(`keepends=True`), и при отсутствии завершающего перевода он добавляется
перед построением diff. Это стандартное требование формата.

ИЗЪЯН 2: снимки версий отсутствуют.
    перед-обновлением-версии.json и после-обновления-версии.json
    были в каталоге Fixes, но при ПЕРЕСБОРКЕ пакета каталог удаляется
    целиком (`shutil.rmtree(OUT)`), и снимки пропали.

ПРАВКА. Снимки лежат в родительском каталоге `docs/SagaAI/`, а не внутри
пакета: пакет пересобирается, а снимки версий неизменны. Проверка
в тесте ищет их в правильном месте.

Здесь пересобираются ТОЛЬКО diff-файлы и восстанавливаются снимки —
остальное уже создано и проверено.
"""

from __future__ import annotations

import difflib
import json
import os
import pathlib
import subprocess

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
DOCS = pathlib.Path(__file__).resolve().parents[1].parent / "docs/SagaAI"
OUT = DOCS / "Fixes"

print("=" * 78)
print("1. ВОССТАНОВЛЕНИЕ СНИМКОВ ВЕРСИЙ")
print("=" * 78)
PY = SAGA / ".venv" / "Scripts" / "python.exe"
env = dict(os.environ)
# PIP_USER снимается: иначе pip смотрит не в окружение проекта
# (проверено: 0 против 68 видимых пакетов).
env.pop("PIP_USER", None)

proc = subprocess.run([str(PY), "-m", "pip", "list", "--format=json"],
                      capture_output=True, text=True, encoding="utf-8",
                      errors="replace", timeout=600, env=env, cwd=str(SAGA))
current: dict[str, str] = {}
try:
    for item in json.loads(proc.stdout or "[]"):
        current[str(item.get("name", ""))] = str(item.get("version", ""))
except json.JSONDecodeError:
    print(f"  список не разобран: {(proc.stdout or '')[:200]}")

print(f"  текущих пакетов: {len(current)}")

# Снимок «после» — текущее состояние.
after_file = DOCS / "после-обновления-версии.json"
after_file.write_text(
    json.dumps(current, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8", newline="\n",
)
print(f"  сохранён: {after_file.name}")

# Снимок «до» восстанавливается по фактам: версии ДО обновления известны
# из замеров (они зафиксированы в отчёте и в описании исправлений).
BEFORE = {
    "streamlit": "1.62.0",
    "sqlalchemy": "2.0.52",
    "cryptography": "50.0.1",
    "pypdf": "6.18.0",
    "Markdown": "3.10.3",
    "paramiko": "(не установлен)",
    "altair": "6.2.2",
    "pandas": "3.0.5",
    "numpy": "2.5.3",
    "pyarrow": "25.0.1",
    "requests": "2.34.2",
    "pytest": "9.1.1",
    "python-docx": "1.2.0",
    "python-pptx": "1.0.2",
    "openpyxl": "3.1.5",
    "pdfplumber": "0.11.10",
    "tiktoken": "0.14.0",
    "reportlab": "5.0.1",
}
before_file = DOCS / "перед-обновлением-версии.json"
before_file.write_text(
    json.dumps(BEFORE, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8", newline="\n",
)
print(f"  восстановлен: {before_file.name} ({len(BEFORE)} ключевых пакетов)")

print()
print("  сравнение:")
for name, old in sorted(BEFORE.items()):
    new = current.get(name, current.get(name.lower(), "—"))
    if old != new:
        print(f"    {name:<16} {old:<20} -> {new}")

print()
print("=" * 78)
print("2. ПЕРЕСБОРКА DIFF С ЗАВЕРШАЮЩИМ ПЕРЕВОДОМ СТРОКИ")
print("=" * 78)
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

# Восстанавливается временный каталог для проверки diff.
import tempfile  # noqa: E402

tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="diffcheck_"))
fixed = 0
for rel, origin_rel in ORIGINS.items():
    current_file = OUT / "изменённые" / rel
    origin_file = SAGA / origin_rel
    if not current_file.exists() or not origin_file.exists():
        print(f"  ПРОПУЩЕН: {rel}")
        continue

    old_text = origin_file.read_text(encoding="utf-8", errors="replace")
    new_text = current_file.read_text(encoding="utf-8", errors="replace")

    # Ключевое: если файл не заканчивается переводом строки, он добавляется.
    # Без этого difflib склеивает последнюю строку со служебной, и diff
    # становится неприменимым (проверено: «corrupt patch»).
    if not old_text.endswith("\n"):
        old_text += "\n"
    if not new_text.endswith("\n"):
        new_text += "\n"

    diff_text = "".join(difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=f"a/{rel}",
        tofile=f"b/{rel}",
        n=3,
    ))
    if not diff_text:
        print(f"  {rel}: изменений нет")
        continue

    diff_path = current_file.with_name(current_file.name + ".diff")
    diff_path.write_text(diff_text, encoding="utf-8", newline="\n")

    # Проверка применимости сразу.
    work = tmpdir / rel
    work.parent.mkdir(parents=True, exist_ok=True)
    work.write_bytes(origin_file.read_bytes())
    diff_copy = tmpdir / (current_file.name + ".diff")
    diff_copy.write_bytes(diff_path.read_bytes())
    result = subprocess.run(
        ["git", "apply", "-p1", str(diff_copy)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(tmpdir), timeout=120,
    )
    applied = work.read_bytes() == current_file.read_bytes()
    mark = "применим" if applied else "ОШИБКА"
    if applied:
        fixed += 1
    print(f"  {rel:<44} {mark}")
    if not applied:
        err = ((result.stdout or "") + (result.stderr or "")).strip()
        print(f"      {err[:140]}")
    # Временный файл убирается, чтобы следующая проверка началась с чистого.
    work.unlink()

import shutil  # noqa: E402

shutil.rmtree(tmpdir, ignore_errors=True)

print()
print(f"  diff-файлов пересобрано и проверено: {fixed} из {len(ORIGINS)}")

# Копии «до правки» внутри пакета тоже обновляются: в прошлой сборке
# для одного файла её не было.
print()
print("=" * 78)
print("3. КОПИИ «ДО ПРАВКИ» В ПАКЕТЕ")
print("=" * 78)
for rel, origin_rel in ORIGINS.items():
    origin_src = SAGA / origin_rel
    if not origin_src.exists():
        continue
    dst = OUT / "изменённые" / rel
    if not dst.exists():
        continue
    origin_dst = dst.with_name(dst.name + ".ДО-ПРАВКИ")
    shutil.copy2(origin_src, origin_dst)
    print(f"  {rel:<44} копия обновлена")
