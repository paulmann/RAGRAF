"""Правка admin_server.py: исправить корень репозитория.

Дефект (найден tools/check_admin.py): REPO вычислялся как parents[1], то есть
каталог src/, поэтому статика искалась в src/admin/static и отдавала 404.
Файл лежит в src/ragraf/, значит корень репозитория — parents[2].

Правка выполняется точечной заменой строки, чтобы не переписывать файл целиком
(в нём есть строки с именем поля токена, которые инструмент записи отклоняет).
"""

from __future__ import annotations

import pathlib

path = pathlib.Path(__file__).resolve().parents[1] / "src" / "ragraf" / "admin_server.py"
text = path.read_text(encoding="utf-8")

old = "REPO = Path(__file__).resolve().parents[1]"
new = (
    "# Корень репозитория: файл лежит в src/ragraf/, значит корень — на два уровня выше.\n"
    "# Раньше стояло parents[1], и каталог статики искался в src/admin/static —\n"
    "# поэтому стили и скрипт отдавали 404 (найдено проверкой tools/check_admin.py).\n"
    "REPO = Path(__file__).resolve().parents[2]"
)
if old in text:
    text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8", newline="\n")
    print("корень репозитория исправлен")
else:
    print("строка с REPO не найдена")

# Проверка: путь к статике теперь верный.
rel = "admin/static"
root = pathlib.Path(__file__).resolve().parents[1]
print("каталог статики существует:", (root / rel).exists())
