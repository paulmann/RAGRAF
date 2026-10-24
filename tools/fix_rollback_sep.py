"""Исправление: PurePosixPath НЕ преобразует обратные слеши.

МОЯ ОШИБКА, найденная фактом.

В прошлой правке я написал:
    file_rel = PurePosixPath(file_rel).as_posix()

и предположил, что это приведёт `d\\1.txt` к `d/1.txt`. Проверка вызовом:

    PurePosixPath('d\\\\1.txt').as_posix()  →  'd\\\\1.txt'

Результат НЕ изменился. Причина: в POSIX обратный слеш — обычный символ
имени файла, а не разделитель. `PurePosixPath` не знает, что строка пришла
из Windows, и оставляет её как есть.

ЧТО ЭТО ЗНАЧИТ ПРАКТИЧЕСКИ. Путь `d\\1.txt` приходит из `os.path.relpath`
на Windows: `relpath` всегда использует системный разделитель. Значит
приведение к POSIX надо делать ЗАМЕНОЙ разделителя, а не сменой класса пути.

ПРАВКА:
    file_rel = file_rel.replace(os.sep, "/")
    if os.altsep:
        file_rel = file_rel.replace(os.altsep, "/")

`os.sep` — системный разделитель ('\\\\' на Windows, '/' на POSIX),
`os.altsep` — альтернативный ('/' на Windows, None на POSIX). Такая замена
работает в обе стороны: на Linux ничего не меняет, на Windows приводит
к POSIX.

ПОЧЕМУ ЭТО ВАЖНО ДЛЯ РАБОТЫ, А НЕ КОСМЕТИКА. Ниже по коду выполняется:

    for file_rel in restored:
        state["entries"].pop(file_rel, None)

Если `restored` содержит `d\\1.txt`, а ключи состояния — `d/1.txt`,
то запись НЕ удаляется. В `state.json` остаётся ложная отметка «файл
применён», и следующее обновление пропустит этот файл как актуальный.
Состояние расходится с диском.
"""

from __future__ import annotations

import ast
import pathlib

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
TARGET = SAGA / "core" / "updater_apply.py"
text = TARGET.read_text(encoding="utf-8")

OLD = '''            # POSIX-форма обязательна. На Windows `os.path.relpath` даёт
            # обратные слеши ('d\\\\1.txt'), тогда как ключи meta, записи
            # state["entries"] и пути манифеста хранятся в POSIX-форме
            # ('d/1.txt'). Без приведения:
            #   * `restored` содержал бы пути двух разных форматов;
            #   * строка ниже `state["entries"].pop(file_rel)` НЕ находила
            #     бы запись, и в state.json оставалась бы ложная отметка
            #     «файл применён» — следующее обновление пропустило бы файл.
            # Приведение делается там, где путь рождается, чтобы обе ветки
            # сбора `restored` давали одинаковый результат.
            file_rel = PurePosixPath(file_rel).as_posix()'''

NEW = '''            # POSIX-форма обязательна. `os.path.relpath` на Windows
            # возвращает путь с ОБРАТНЫМИ слешами ('d\\\\1.txt'), тогда как
            # ключи meta, записи state["entries"] и пути манифеста хранятся
            # в POSIX-форме ('d/1.txt').
            #
            # ВАЖНО: `PurePosixPath(rel).as_posix()` здесь НЕ помогает —
            # проверено вызовом: для POSIX обратный слеш является обычным
            # символом имени, а не разделителем, поэтому строка остаётся
            # без изменений. Нужна ЗАМЕНА системного разделителя.
            #
            # Без приведения строки ниже `state["entries"].pop(file_rel)`
            # не находят запись, и в state.json остаётся ложная отметка
            # «файл применён»: следующее обновление пропустило бы файл.
            file_rel = file_rel.replace(os.sep, "/")
            if os.altsep:
                file_rel = file_rel.replace(os.altsep, "/")'''

if OLD not in text:
    if 'file_rel.replace(os.sep, "/")' in text:
        print("  правка уже внесена")
        raise SystemExit(0)
    raise SystemExit("НЕ НАЙДЕН блок приведения пути")

text = text.replace(OLD, NEW, 1)
TARGET.write_text(text, encoding="utf-8", newline="\n")
after = TARGET.read_text(encoding="utf-8")
print("  правка внесена: замена разделителя вместо PurePosixPath")

try:
    ast.parse(after)
    print("  синтаксис верен")
except SyntaxError as exc:
    print(f"  ПРОВАЛ синтаксиса: строка {exc.lineno}: {exc.msg}")
    raise SystemExit(1)

# Проверка: PurePosixPath больше не используется для этого пути.
import re  # noqa: E402

checks = [
    ('file_rel.replace(os.sep, "/")' in after, "замена os.sep есть"),
    ("os.altsep" in after, "учтён альтернативный разделитель"),
]
print()
for ok, label in checks:
    print(f"  [{'OK ' if ok else '--'}] {label}")

# Проверка поведения на обоих разделителях.
import os  # noqa: E402

for probe in ("d/1.txt", "d\\1.txt", "a/b/c.txt"):
    fixed = probe.replace(os.sep, "/")
    if os.altsep:
        fixed = fixed.replace(os.altsep, "/")
    mark = "верно" if "\\" not in fixed else "ПРОБЛЕМА"
    print(f"  {probe!r:<16} -> {fixed!r:<16} {mark}")
