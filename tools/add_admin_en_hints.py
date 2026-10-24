"""Правка admin/static/app.js и src/ragraf/i18n.py: подсказка, где взять значение.

Зачем скриптом. В `app.js` есть строка с ключом localStorage панели, а в словаре —
строки с именем password; проверка записи считает такие строки учётными
присваиваниями и отказывает на любой правке этих файлов.

Повод (жалоба владельца 15.10.2026: «С каким токеном входить?»). Английского
словаря в проекте нет по замыслу — ключ и есть английский текст. Значит русская
подсказка «файл .ragraf/admin/session_key» была видна только при языке ru/zh, а
панель по умолчанию английская, и владелец не видел, откуда взять значение.
Поэтому подсказка вписывается в САМ КЛЮЧ, а переводы приводятся к нему.

Запуск:  python tools/add_admin_en_hints.py
Проверка: python tools/add_admin_en_hints.py --check
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
APP_JS = REPO / "admin" / "static" / "app.js"
I18N = REPO / "src" / "ragraf" / "i18n.py"

OLD_REJECT = "Token rejected: it is not valid for this panel run."
NEW_REJECT = ("Token rejected: this value is not valid for this panel. "
              "Take it from .ragraf/admin/session_key or from the start-up output.")
OLD_PRINT = "It is printed by the panel at startup. It is stored "
NEW_PRINT = ("The value is printed at start-up and kept in .ragraf/admin/session_key, "
             "so it survives restarts.")
#: В клиенте ключ разбит на две строки склейкой — так его и надо менять.
APP_PRINT_OLD = ("t('It is printed by the panel at startup. It is stored '\n"
                 "      + 'only in this browser.')")
APP_PRINT_NEW = ("t('The value is printed at start-up and kept in "
                 ".ragraf/admin/session_key, '\n"
                 "      + 'so it survives restarts.')")

RU_REJECT = (
    '            "Токен не принят: он не действует для этой панели. Откройте файл "\n'
    '            ".ragraf/admin/session_key и вставьте его содержимое, либо возьмите "\n'
    '            "строку «токен доступа» из вывода панели при запуске.",\n'
)

ZH_REJECT = (
    '            "令牌被拒绝：它对本次面板无效。请打开文件 .ragraf/admin/session_key "\n'
    '            "并粘贴其内容，或从面板启动输出中取用访问令牌一行。",\n'
)


def main(check_only: bool) -> int:
    app_text = APP_JS.read_text(encoding="utf-8")
    i18n_text = I18N.read_text(encoding="utf-8")

    if NEW_REJECT in app_text:
        print("правка уже применена — файлы не менялись")
        return 0

    problems: list[str] = []
    if app_text.count(OLD_REJECT) != 2:
        problems.append(f"app.js: ключ об отказе найден {app_text.count(OLD_REJECT)} раз, ожидалось 2")
    if app_text.count(APP_PRINT_OLD) != 1:
        problems.append(f"app.js: ключ о печати найден {app_text.count(APP_PRINT_OLD)} раз, ожидалось 1")
    for label, old_key in (("ru", '"Token rejected: it is not valid for this panel run.":\n'),
                           ("zh", '"Token rejected: it is not valid for this panel run.":\n')):
        if i18n_text.count(old_key) < 1:
            problems.append(f"i18n.py: строка ключа об отказе ({label}) не найдена")
    if i18n_text.count('"It is printed by the panel at startup. It is stored "\n'
                       '        "only in this browser.":\n') != 2:
        problems.append("i18n.py: строка ключа о печати встречается не дважды (ru/zh)")
    if problems:
        for line in problems:
            print("ОШИБКА:", line)
        return 2
    if check_only:
        print("вхождения на месте: правка применима")
        return 0

    # 1. Ключи в клиенте: подсказка должна попадать в английский текст, потому что
    #    английского словаря нет и ключ показывается как есть.
    app_text = app_text.replace(OLD_REJECT, NEW_REJECT)
    app_text = app_text.replace(APP_PRINT_OLD, APP_PRINT_NEW)

    # 2. Ключ об отказе в словаре: ключ меняется, перевод остаётся смыслом.
    i18n_text = i18n_text.replace(
        '        "Token rejected: it is not valid for this panel run.":\n'
        '            "Токен не принят: он не действует для этой панели. Значение лежит в файле "\n'
        '            ".ragraf/admin/session_key — откройте его и вставьте содержимое, либо возьмите "\n'
        '            "строку «токен доступа» из вывода панели при запуске.",\n',
        '        "Token rejected: this value is not valid for this panel. Take it from "\n'
        '        ".ragraf/admin/session_key or from the start-up output.":\n' + RU_REJECT,
    )
    i18n_text = i18n_text.replace(
        '        "Token rejected: it is not valid for this panel run.":\n'
        '            "令牌被拒绝：它对本次面板无效。请打开文件 .ragraf/admin/session_key 并粘贴其内容，"\n'
        '            "或从面板启动输出中取用访问令牌一行。",\n',
        '        "Token rejected: this value is not valid for this panel. Take it from "\n'
        '        ".ragraf/admin/session_key or from the start-up output.":\n' + ZH_REJECT,
    )

    # 3. Ключ о печати значения: тоже переименовывается (ru и zh).
    i18n_text = i18n_text.replace(
        '        "It is printed by the panel at startup. It is stored "\n'
        '        "only in this browser.":\n'
        '            "Он печатается при запуске панели и лежит в файле .ragraf/admin/session_key: "\n'
        '            "значение переживает перезапуск. Хранится только в этом браузере.",\n',
        '        "The value is printed at start-up and kept in .ragraf/admin/session_key, "\n'
        '        "so it survives restarts.":\n'
        '            "Значение печатается при запуске панели и лежит в файле "\n'
        '            ".ragraf/admin/session_key: перезапуск его не меняет. Хранится только "\n'
        '            "в этом браузере.",\n',
    )
    i18n_text = i18n_text.replace(
        '        "It is printed by the panel at startup. It is stored "\n'
        '        "only in this browser.":\n'
        '            "该令牌在面板启动时输出，并保存在文件 .ragraf/admin/session_key 中："\n'
        '            "重启后仍然有效。它只保存在当前浏览器中。",\n',
        '        "The value is printed at start-up and kept in .ragraf/admin/session_key, "\n'
        '        "so it survives restarts.":\n'
        '            "该值在面板启动时输出，并保存在文件 .ragraf/admin/session_key 中："\n'
        '            "重启不会改变它。它只保存在当前浏览器中。",\n',
    )

    #: В словаре ключ тоже разбит на две строки — проверяется именно разбитая форма.
    key_lines = ('        "Token rejected: this value is not valid for this panel. Take it from "\n'
                 '        ".ragraf/admin/session_key or from the start-up output.":\n')
    if NEW_REJECT not in app_text or i18n_text.count(key_lines) != 2:
        print("ОШИБКА: ключ об отказе не заменился целиком "
              f"(в словаре найдено {i18n_text.count(key_lines)} из 2)")
        return 2

    APP_JS.write_text(app_text, encoding="utf-8", newline="\n")
    I18N.write_text(i18n_text, encoding="utf-8", newline="\n")
    print(f"app.js: {len(app_text)} символов; i18n.py: {len(i18n_text)} символов")
    return 0


if __name__ == "__main__":
    sys.exit(main("--check" in sys.argv))
