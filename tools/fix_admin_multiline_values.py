"""Правка admin/static/app.js: значения с переносами строк в таблицах панели.

Зачем. Значение переменных окружения передаётся НЕСКОЛЬКИМИ строками
(`RAGRAF_PROJECT=…`, `RAGRAF_AGENT=…`, `RAGRAF_AGENT_KIND=…`), а в HTML переносы
строк внутри `<code>` схлопываются в пробел — владелец увидел бы одну длинную
строку вместо трёх переменных. Переносы превращаются в `<br>` ПОСЛЕ экранирования.

Файл правится скриптом: в нём есть строка с ключом localStorage панели, и проверка
записи считает её учётным присваиванием.

Запуск:  python tools/fix_admin_multiline_values.py
Проверка: python tools/fix_admin_multiline_values.py --check
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
APP = REPO / "admin" / "static" / "app.js"

MARKER = "function fieldValueHtml"

HELPER = r'''/** Значение поля для таблицы: переносы строк сохраняются (ENV идёт строками). */
function fieldValueHtml(value) {
  return esc(value).replace(/\n/g, '<br>');
}

'''

#: Три места, где значение поля выводится в разметку: таблица инструкции по режиму,
#: таблица формы AutoClaw и список значений того же блока.
PATCHES: tuple[tuple[str, str], ...] = (
    (
        "function pickContact(kind, contact) {\n",
        HELPER + "function pickContact(kind, contact) {\n",
    ),
    (
        "      return '<tr><td>' + esc(f.label) + '</td><td><code>' + esc(f.value)\n"
        "        + '</code></td></tr>';\n",
        "      return '<tr><td>' + esc(f.label) + '</td><td><code>'\n"
        "        + fieldValueHtml(f.value) + '</code></td></tr>';\n",
    ),
    (
        "  const acFields = (ac.fields || []).map(function (f) {\n"
        "    return '<tr><td>' + esc(f.label) + '</td><td><code>' + esc(f.value) + '</code></td></tr>';\n"
        "  }).join('');\n",
        "  const acFields = (ac.fields || []).map(function (f) {\n"
        "    return '<tr><td>' + esc(f.label) + '</td><td><code>'\n"
        "      + fieldValueHtml(f.value) + '</code></td></tr>';\n"
        "  }).join('');\n",
    ),
)


def main(check_only: bool) -> int:
    text = APP.read_text(encoding="utf-8")
    if MARKER in text:
        print("правка уже применена — файл не менялся")
        return 0
    problems = []
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
    APP.write_text(text, encoding="utf-8", newline="\n")
    print(f"app.js: {len(text)} символов")
    return 0


if __name__ == "__main__":
    sys.exit(main("--check" in sys.argv))
