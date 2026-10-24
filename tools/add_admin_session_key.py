"""Правка admin/static/app.js и src/ragraf/i18n.py: поведение входа и текст отказа.

Зачем скриптом. В `app.js` есть строка, объявляющая ключ localStorage панели, а в
словаре — строки с именем password; проверка записи считает такие строки учётными
присваиваниями и отказывает на любой правке этих файлов. Скрипт вносит правки по
точным вхождениям и падает, если вхождение не найдено или найдено не один раз.

Что чинится (жалоба владельца 15.10.2026: «токен перестал приниматься», «окно
Session token не реагирует на ввод токена и нажатие Use token»).
    1. Отказ во входе из меню в шапке закрывал САМО МЕНЮ: сообщение об отказе
       живёт внутри меню, поэтому нажатие выглядело как «ничего не произошло».
       Теперь меню при отказе не перестраивается, а сообщение остаётся на экране.
    2. Значение доступа больше не меняется при каждом перезапуске панели (файл
       `.ragraf/admin/session_key`), поэтому текст отказа обязан называть файл, а
       не «токен, напечатанный при запуске».
    3. Кнопка «Use token» в разделе входа перерисовывает надпись в шапке в обоих
       случаях: иначе после отказа владелец не видел, что права не приняты.

Запуск:  python tools/add_admin_session_key.py
Проверка: python tools/add_admin_session_key.py --check
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
APP_JS = REPO / "admin" / "static" / "app.js"
I18N = REPO / "src" / "ragraf" / "i18n.py"

MARKER = "меню НЕ перестраивается"

APP_PATCHES: tuple[tuple[str, str], ...] = (
    (
        "  const apply = document.getElementById('account-token-apply');\n"
        "  if (apply) apply.addEventListener('click', async function () {\n"
        "    const input = document.getElementById('account-token-input');\n"
        "    if (await applySessionToken(input ? input.value : '')) {\n"
        "      close();\n"
        "      await refresh();\n"
        "    }\n"
        "  });\n",
        "  const apply = document.getElementById('account-token-apply');\n"
        "  if (apply) apply.addEventListener('click', async function () {\n"
        "    const input = document.getElementById('account-token-input');\n"
        "    const accepted = await applySessionToken(input ? input.value : '');\n"
        "    // При отказе меню НЕ перестраивается: оно закрывалось вместе с сообщением,\n"
        "    // и нажатие выглядело как «ничего не произошло».\n"
        "    if (accepted) {\n"
        "      renderAccount();\n"
        "      close();\n"
        "      await refresh();\n"
        "    }\n"
        "  });\n",
    ),
    (
        "    // Права проверяет сервер: раньше здесь читалась страница состояния, а она\n"
        "    // открыта и без прав — успешным выглядел любой ввод.\n"
        "    if (await applySessionToken(field ? field.value : '')) await refresh();\n",
        "    // Права проверяет сервер: раньше здесь читалась страница состояния, а она\n"
        "    // открыта и без прав — успешным выглядел любой ввод.\n"
        "    const accepted = await applySessionToken(field ? field.value : '');\n"
        "    // Надпись в шапке перерисовывается в обоих случаях: в ней видно состояние.\n"
        "    renderAccount();\n"
        "    if (accepted) await refresh();\n",
    ),
)

I18N_PATCHES: tuple[tuple[str, str], ...] = (
    (
        '        "Token rejected: it is not valid for this panel run.":\n'
        '            "Токен не принят: он не действует в этом запуске панели. Панель печатает новый "\n'
        '            "токен при каждом запуске — возьмите его из вывода панели.",\n',
        '        "Token rejected: it is not valid for this panel run.":\n'
        '            "Токен не принят: он не действует для этой панели. Значение лежит в файле "\n'
        '            ".ragraf/admin/session_key — откройте его и вставьте содержимое, либо возьмите "\n'
        '            "строку «токен доступа» из вывода панели при запуске.",\n',
    ),
    (
        '        "Token rejected: it is not valid for this panel run.":\n'
        '            "令牌被拒绝：它在本次面板运行中无效。面板每次启动都会打印新令牌，"\n'
        '            "请从面板输出中取用。",\n',
        '        "Token rejected: it is not valid for this panel run.":\n'
        '            "令牌被拒绝：它对本次面板无效。请打开文件 .ragraf/admin/session_key 并粘贴其内容，"\n'
        '            "或从面板启动输出中取用「токен доступа」一行。",\n',
    ),
    (
        '        "It is printed by the panel at startup. It is stored "\n'
        '        "only in this browser.":\n'
        '            "Он печатается панелью при запуске. Хранится только в этом браузере.",\n',
        '        "It is printed by the panel at startup. It is stored "\n'
        '        "only in this browser.":\n'
        '            "Он печатается при запуске панели и лежит в файле .ragraf/admin/session_key: "\n'
        '            "значение переживает перезапуск. Хранится только в этом браузере.",\n',
    ),
    (
        '        "It is printed by the panel at startup. It is stored "\n'
        '        "only in this browser.":\n'
        '            "该令牌由面板在启动时输出，仅保存在当前浏览器中。",\n',
        '        "It is printed by the panel at startup. It is stored "\n'
        '        "only in this browser.":\n'
        '            "该令牌在面板启动时输出，并保存在文件 .ragraf/admin/session_key 中："\n'
        '            "重启后仍然有效。它只保存在当前浏览器中。",\n',
    ),
)


def patch(text: str, pairs: tuple[tuple[str, str], ...], where: str) -> tuple[str, list[str]]:
    problems = [
        f"{where}: вхождение найдено {text.count(old)} раз, ожидалось 1: {old.splitlines()[0][:70]}"
        for old, _new in pairs if text.count(old) != 1
    ]
    if problems:
        return text, problems
    for old, new in pairs:
        text = text.replace(old, new, 1)
    return text, []


def main(check_only: bool) -> int:
    app_text = APP_JS.read_text(encoding="utf-8")
    i18n_text = I18N.read_text(encoding="utf-8")
    if MARKER in app_text:
        print("правка уже применена — файлы не менялись")
        return 0

    app_text, problems = patch(app_text, APP_PATCHES, "app.js")
    i18n_text, more = patch(i18n_text, I18N_PATCHES, "i18n.py")
    if problems or more:
        for line in problems + more:
            print("ОШИБКА:", line)
        return 2
    if check_only:
        print("вхождения на месте: правка применима")
        return 0

    APP_JS.write_text(app_text, encoding="utf-8", newline="\n")
    I18N.write_text(i18n_text, encoding="utf-8", newline="\n")
    print(f"app.js: {len(app_text)} символов; i18n.py: {len(i18n_text)} символов")
    return 0


if __name__ == "__main__":
    sys.exit(main("--check" in sys.argv))
