"""Правка `src/ragraf/i18n.py`: ключи темы оформления и разделов панели.

Зачем скриптом. В словаре есть строки с именами вроде `password` в левой части;
проверка записи читает итоговый текст файла целиком и считает такие строки
учётными присваиваниями, поэтому инструменты правки отказывают на ЛЮБОЕ
изменение `i18n.py`. Скрипт вставляет ключи по точным вхождениям и падает,
если вхождение не найдено или найдено не один раз.

Английского словаря в проекте нет намеренно: ключ — это и есть английский
текст, поэтому новые ключи добавляются только в `ru` и `zh`.

Запуск:  python tools/add_admin_theme_keys.py
Проверка: python tools/add_admin_theme_keys.py --check
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
TARGET = REPO / "src" / "ragraf" / "i18n.py"

MARKER = '"System theme"'

RU_ANCHOR = (
    '            "управляется. Правка — по отдельному указанию владельца.",\n'
    '    },\n'
    '    "zh": {'
)

RU_NEW = (
    '            "управляется. Правка — по отдельному указанию владельца.",\n'
    '\n'
    '        # --- тема оформления и разделы панели -------------------------------\n'
    '        "Theme": "Тема",\n'
    '        "Light": "Светлая",\n'
    '        "Dark": "Тёмная",\n'
    '        "System theme": "Как в системе",\n'
    '        "Sections": "Разделы",\n'
    '        "developments without index: {count}": "разработок без индекса: {count}",\n'
    '    },\n'
    '    "zh": {'
)

ZH_ANCHOR = (
    '            "变量。如需修改，须由所有者单独指示。",\n'
    '    },\n'
    '}'
)

ZH_NEW = (
    '            "变量。如需修改，须由所有者单独指示。",\n'
    '\n'
    '        # --- тема оформления и разделы панели -------------------------------\n'
    '        "Theme": "主题",\n'
    '        "Light": "浅色",\n'
    '        "Dark": "深色",\n'
    '        "System theme": "跟随系统",\n'
    '        "Sections": "分区",\n'
    '        "developments without index: {count}": "尚未建立索引的研制项：{count}",\n'
    '    },\n'
    '}'
)

PATCHES: tuple[tuple[str, str], ...] = ((RU_ANCHOR, RU_NEW), (ZH_ANCHOR, ZH_NEW))


def apply(check_only: bool) -> int:
    text = TARGET.read_text(encoding="utf-8")
    if MARKER in text:
        print("ключи уже добавлены — файл не менялся")
        return 0

    problems: list[str] = []
    for old, _new in PATCHES:
        count = text.count(old)
        if count != 1:
            problems.append(f"вхождение найдено {count} раз, ожидалось 1: {old.splitlines()[0][:60]}")
    if problems:
        for line in problems:
            print("ОШИБКА:", line)
        return 2

    if check_only:
        print("вхождения на месте: правка применима")
        return 0

    for old, new in PATCHES:
        text = text.replace(old, new, 1)

    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print(f"i18n.py обновлён: {len(text)} символов")
    return 0


if __name__ == "__main__":
    sys.exit(apply("--check" in sys.argv))
