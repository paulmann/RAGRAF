"""Подключение CSS и разметки к корпусам.

Что меняется в конфигах:
  * в `include` добавляются шаблоны `**/*.css`, `**/*.htm`, `**/*.html`;
  * в `languages` добавляются `css` и `html`.

Зачем. Без этого файлы оформления и разметки не попадают в индекс: сканер
отбрасывает их по признаку «язык не включён». Из-за этого был потерян целый
слой — классы оформления и их использование (см. .staging/extra_css_measure.py).

Правка построчная: комментарии конфигов сохраняются. Для каждого файла
создаётся резервная копия. Скрипт идемпотентен.
"""

from __future__ import annotations

import pathlib
import shutil
from datetime import datetime

REPO = pathlib.Path(__file__).resolve().parents[1]

INCLUDE_OLD = '  - "**/*.sql"\n'
INCLUDE_NEW = ('  - "**/*.sql"\n'
               '  # Стили и разметка: значительная часть интерфейса живёт вне PHP.\n'
               '  # Без их разбора из индекса выпадает слой классов оформления,\n'
               '  # и расхождение «класс используется, но правила нет» незаметно.\n'
               '  - "**/*.css"\n'
               '  - "**/*.htm"\n'
               '  - "**/*.html"\n')

LANG_OLD = "  - sql\n"
LANG_NEW = "  - sql\n  - css\n  - html\n"

for name in ("z-batteryservice", "revoap"):
    path = REPO / "config" / f"{name}.yaml"
    text = path.read_text(encoding="utf-8")
    if "**/*.css" in text:
        print(f"{name}: уже настроено")
        continue

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(path, path.with_name(f"{path.name}.bak.{stamp}-css"))
    print(f"{name}: копия {path.name}.bak.{stamp}-css")

    changed = False
    if INCLUDE_OLD in text:
        text = text.replace(INCLUDE_OLD, INCLUDE_NEW, 1)
        changed = True
        print(f"{name}: в include добавлены css, htm, html")
    else:
        print(f"{name}: ВНИМАНИЕ, блок include не найден в ожидаемом виде")

    if LANG_OLD in text:
        text = text.replace(LANG_OLD, LANG_NEW, 1)
        changed = True
        print(f"{name}: в languages добавлены css, html")
    else:
        print(f"{name}: ВНИМАНИЕ, блок languages не найден в ожидаемом виде")

    if changed:
        path.write_text(text, encoding="utf-8", newline="\n")

print("\n=== проверка ===")
import sys

sys.path.insert(0, str(REPO / "src"))
from ragraf.config import load  # noqa: E402

for name in ("z-batteryservice", "revoap"):
    cfg = load(REPO / "config" / f"{name}.yaml")
    print(f"{name}: языки {cfg.languages}, включений {len(cfg.include)}")
    # Ключевая проверка: файл CSS теперь распознаётся как языковой.
    print(f"   style.css → {cfg.language_of('assets/css/z.css')}")
    print(f"   layout.htm → {cfg.language_of('templates/layout.htm')}")
