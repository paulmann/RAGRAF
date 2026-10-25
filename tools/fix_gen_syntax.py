"""Исправить генератор: вернуть валидный Python-синтаксис для статусов TD.

При прошлой правке в Python-код попал PHP-синтаксис `[...]` с `=>`, из-за чего
генератор перестал запускаться. Здесь те же данные записываются словарями
Python — генератор сам преобразует их в PHP.
"""

from __future__ import annotations

import pathlib
import re

p = pathlib.Path("tools/gen_journal_configs.py")
t = p.read_text(encoding="utf-8")

# 1. status_titles для TD: PHP-список превращаем в словарь Python.
start = t.find("        # Белый список статусов задач отличается")
end = t.find("        ],", start)
if start >= 0 and end >= 0:
    block = '''        # Белый список статусов задач отличается от остальных журналов:
        # в нём нет on_approval и rework, зато есть new и in_progress
        # (проверено вызовом z_agr_statuses('TD') и схемой колонки Status).
        status_titles={
            "new": "Новая",
            "in_work": "В работе",
            "in_progress": "Выполняется",
            "on_review": "На проверке",
            "done": "Выполнена",
            "rejected": "Отклонена",
            "cancelled": "Аннулирована",
            "archived": "В архиве",
            "draft": "Черновик",
            "approved": "Принята",
        },'''
    t = t[:start] + block + t[end + len("        ],"):]
    print("status_titles TD исправлен на словарь Python")
else:
    print("блок status_titles TD не найден")

# 2. transitions для TD: PHP-список превращаем в словарь Python.
start = t.find("        # Переходы задач описаны по фактическим состояниям")
end = t.find("        ],", start)
if start >= 0 and end >= 0:
    block = '''        # Переходы задач описаны по фактическим состояниям журнала:
        # задача не проходит общий маршрут согласования, её принимает постановщик.
        transitions={
            "new": ["update", "cancel", "copy"],
            "draft": ["submit", "update", "delete", "copy", "cancel"],
            "in_work": ["submit", "update", "cancel"],
            "in_progress": ["submit", "update", "cancel"],
            "on_review": ["approve", "reject", "withdraw"],
            "done": ["archive", "copy"],
            "approved": ["archive", "copy", "cancel"],
            "rejected": ["submit", "update", "cancel", "copy"],
            "cancelled": ["copy"],
            "archived": ["copy"],
        },'''
    t = t[:start] + block + t[end + len("        ],"):]
    print("transitions TD исправлен на словарь Python")
else:
    print("блок transitions TD не найден")

p.write_text(t, encoding="utf-8", newline="\n")

# 3. Проверка синтаксиса компиляцией.
try:
    compile(t, str(p), "exec")
    print("синтаксис генератора корректен")
except SyntaxError as exc:
    print(f"СИНТАКСИЧЕСКАЯ ОШИБКА: строка {exc.lineno}: {exc.text}")
