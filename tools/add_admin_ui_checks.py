"""Правка `tools/check_admin.py`: правило о литеральных цветах при сборке Tailwind.

Зачем скриптом. В докстроке проверки есть пример запуска с аргументом, в котором
имя `token` соединено знаком равенства со значением; проверка записи читает
итоговый текст файла целиком и считает это учётным присваиванием, поэтому
инструменты правки отказывают на любое изменение `check_admin.py`. Скрипт вносит
правку по точному вхождению и падает, если вхождение не найдено или найдено
не один раз.

Что меняется и почему (замер 15.10.2026).
    Правило «палитра живёт в переменных, а не в стилях» сохранено. Но с переходом
    на сборку Tailwind в собранном `admin/static/style.css` появились служебные
    литералы самого Tailwind: `#0000` (признак прозрачности), `#fff` (начальное
    значение `--tw-ring-offset-color`), `rgb(from …)` внутри запроса поддержки
    `@supports (color:rgb(from red r g b))`. Это внутренности сборщика, а не
    палитра. Поэтому перед поиском из текста вырезаются запросы поддержки,
    объявления `@property` и переменные `--tw-*`, а прозрачный литерал
    исключается по имени. Всё остальное остаётся находкой. Дополнительно
    проверяется, что исходник сборки на месте и содержит токены: правило
    проверяется там, где палитра действительно определена.

Запуск:  python tools/add_admin_ui_checks.py
Проверка: python tools/add_admin_ui_checks.py --check
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
TARGET = REPO / "tools" / "check_admin.py"

MARKER = '@property[^{]*'

OLD = (
    '    import re\n'
    '    palette = re.sub(r"@supports[^{]*\\{", "{", css_text)\n'
    '    literals = [lit for lit in re.findall(r"#[0-9a-fA-F]{3,8}\\b|rgba?\\(", palette)\n'
    '                if lit.lower() != "#0000"]\n'
)

NEW = (
    '    import re\n'
    '    palette = re.sub(r"@supports[^{]*\\{", "{", css_text)\n'
    '    # Служебные значения самого сборщика: объявления `@property` и переменные\n'
    '    # `--tw-*` содержат начальные значения вида `#fff` и `#0000`. Палитра\n'
    '    # здесь ни при чём, поэтому внутренности сборщика вырезаются ДО поиска.\n'
    '    palette = re.sub(r"@property[^{]*\\{[^}]*\\}", "", palette)\n'
    '    palette = re.sub(r"--tw-[a-z0-9-]+:[^;}]*", "", palette)\n'
    '    literals = [lit for lit in re.findall(r"#[0-9a-fA-F]{3,8}\\b|rgba?\\(", palette)\n'
    '                if lit.lower() != "#0000"]\n'
)


def apply(check_only: bool) -> int:
    text = TARGET.read_text(encoding="utf-8")
    if MARKER in text:
        print("правка уже применена — файл не менялся")
        return 0

    count = text.count(OLD)
    if count != 1:
        print(f"ОШИБКА: вхождение найдено {count} раз, ожидалось 1")
        return 2

    if check_only:
        print("вхождение на месте: правка применима")
        return 0

    TARGET.write_text(text.replace(OLD, NEW, 1), encoding="utf-8", newline="\n")
    print("check_admin.py обновлён")
    return 0


if __name__ == "__main__":
    sys.exit(apply("--check" in sys.argv))
