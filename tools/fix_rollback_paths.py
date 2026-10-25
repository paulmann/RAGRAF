"""Исправление: откат возвращает пути в двух разных формах.

НАЙДЕННЫЙ ДЕФЕКТ (`core/updater_apply.py::rollback`, строки 501-525).

Список `restored` собирается ДВУМЯ способами, и на Windows они дают разные
разделители:

    строка 507:  file_rel = os.path.relpath(...)     # даёт 'd\\1.txt'
    строка 510:  restored.append(file_rel)           # обратные слеши
    ...
    строка 514:  for file_rel, existed in meta.items()   # ключи 'd/2.txt'
    строка 525:  restored.append(file_rel)               # прямые слеши

ЗАМЕР (воспроизводится тестом):
    ожидалось: {'d/1.txt', 'd/2.txt'}
    получено:  {'d/2.txt', 'd\\1.txt'}

ПОЧЕМУ ЭТО ДЕФЕКТ, А НЕ ПРИДИРКА К ФОРМАТУ. Пути в этом модуле везде
POSIX: их проверяет `_verify_rel` (возвращает POSIX), на них построены
ключи `meta`, `state["entries"]`, `pending.json`. Если `restored` содержит
`d\\1.txt`, то:

    строка 527: state["entries"].pop(file_rel, None)

НЕ НАХОДИТ запись в состоянии и не удаляет её. То есть после отката файла
`d/1.txt` в `state.json` остаётся ложная запись «этот файл применён» —
хотя он восстановлен. Следующее обновление тогда решит, что файл уже
актуален, и пропустит его.

Это уже не косметика: состояние расходится с диском.

ИСПРАВЛЕНИЕ: путь приводится к POSIX сразу после `os.path.relpath` —
там, где он и рождается. Тогда обе ветки дают одинаковый формат, и
удаление записи из состояния работает.
"""

from __future__ import annotations

import ast
import pathlib
import shutil

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
TARGET = SAGA / "core" / "updater_apply.py"
text = TARGET.read_text(encoding="utf-8")

OLD = '''    restored = []
    failures = []
    for dirpath, _dirnames, filenames in os.walk(run_path):
        for name in filenames:
            if name == RUN_META_NAME:
                continue
            file_rel = os.path.relpath(os.path.join(dirpath, name), run_path)
            try:'''

NEW = '''    restored = []
    failures = []
    for dirpath, _dirnames, filenames in os.walk(run_path):
        for name in filenames:
            if name == RUN_META_NAME:
                continue
            file_rel = os.path.relpath(os.path.join(dirpath, name), run_path)
            # POSIX-форма обязательна. На Windows `os.path.relpath` даёт
            # обратные слеши ('d\\\\1.txt'), тогда как ключи meta, записи
            # state["entries"] и пути манифеста хранятся в POSIX-форме
            # ('d/1.txt'). Без приведения:
            #   * `restored` содержал бы пути двух разных форматов;
            #   * строка ниже `state["entries"].pop(file_rel)` НЕ находила
            #     бы запись, и в state.json оставалась бы ложная отметка
            #     «файл применён» — следующее обновление пропустило бы файл.
            # Приведение делается там, где путь рождается, чтобы обе ветки
            # сбора `restored` давали одинаковый результат.
            file_rel = PurePosixPath(file_rel).as_posix()
            try:'''

if OLD not in text:
    if "PurePosixPath(file_rel).as_posix()" in text:
        print("  правка уже внесена")
        raise SystemExit(0)
    raise SystemExit("НЕ НАЙДЕН блок сбора restored")

backup = TARGET.with_suffix(".py.bak-rollback")
shutil.copy2(TARGET, backup)
print(f"  бэкап: {backup.name}")

text = text.replace(OLD, NEW, 1)
TARGET.write_text(text, encoding="utf-8", newline="\n")
after = TARGET.read_text(encoding="utf-8")
print("  правка внесена")

try:
    ast.parse(after)
    print("  синтаксис верен")
except SyntaxError as exc:
    print(f"  ПРОВАЛ синтаксиса: строка {exc.lineno}: {exc.msg}")
    raise SystemExit(1)

checks = [
    ("PurePosixPath(file_rel).as_posix()" in after, "путь приводится к POSIX"),
    ("from pathlib import PurePosixPath" in after, "импорт есть"),
]
print()
for ok, label in checks:
    print(f"  [{'OK ' if ok else '--'}] {label}")
