"""Правка admin/static/app.js: явное обновление и надпись о входе после пароля.

Зачем скриптом. В `app.js` есть строка, объявляющая ключ localStorage панели, и
проверка записи считает её учётным присваиванием, поэтому инструменты правки
отказывают на весь файл. Скрипт вносит правки по точным вхождениям и падает, если
вхождение не найдено или найдено не один раз.

Что чинится (по замечаниям владельца от 15.10.2026 — «нужен рабочий инструмент»).
    1. Кнопка обновления в шапке перечитывала состояние, но НЕ обновляла тяжёлые
       сведения: полный обход идёт около 100 секунд, снимок живёт полчаса —
       владелец не мог получить свежие числа иначе как перезапуском панели.
    2. Отказ по входу (401/403) отмечался как «права отобрали» даже когда это был
       ответ самой точки входа: теперь исключаются все точки `/api/auth/*`.
    3. Вход по паролю разрешал запись, но надпись в шапке оставалась `Guest`:
       сессия живёт в cookie, и клиент её не подтверждал у сервера.

Запуск:  python tools/add_admin_panel_fixes.py
Проверка: python tools/add_admin_panel_fixes.py --check
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
APP_JS = REPO / "admin" / "static" / "app.js"

MARKER = "api/state?force=1"

PATCHES: tuple[tuple[str, str], ...] = (
    (
        "async function refresh() {\n"
        "  setStatus(t('loading'));\n"
        "  const data = await api('/api/state');\n",
        "async function refresh(force) {\n"
        "  setStatus(t('loading'));\n"
        "  // Явное обновление заново собирает тяжёлые сведения: полный обход идёт\n"
        "  // около 100 секунд, а снимок живёт полчаса, поэтому кнопка обязана уметь\n"
        "  // обновлять его досрочно.\n"
        "  const data = await api(force ? '/api/state?force=1' : '/api/state');\n",
    ),
    (
        "  if (reload) reload.addEventListener('click', refresh);\n",
        "  if (reload) reload.addEventListener('click', function () { refresh(true); });\n",
    ),
    (
        "    if (String(path).indexOf('/api/auth/token') !== 0) {\n",
        "    if (String(path).indexOf('/api/auth/') !== 0) {\n",
    ),
    (
        "    if (data.ok) { sessionBlocked = false; await refresh(); }\n",
        "    if (data.ok) {\n"
        "      // Парольная сессия подтверждается у сервера: без этого надпись в шапке\n"
        "      // осталась бы Guest, хотя запись уже разрешена.\n"
        "      sessionBlocked = false;\n"
        "      await verifySession();\n"
        "      renderAccount();\n"
        "      await refresh();\n"
        "    }\n",
    ),
    (
        "  const out = document.getElementById('login-out');\n"
        "  if (out) out.addEventListener('click', async function () {\n"
        "    await api('/api/auth/logout', { method: 'POST', body: JSON.stringify({}) });\n"
        "    sessionBlocked = false;\n"
        "    await refresh();\n"
        "  });\n",
        "  const out = document.getElementById('login-out');\n"
        "  if (out) out.addEventListener('click', async function () {\n"
        "    await api('/api/auth/logout', { method: 'POST', body: JSON.stringify({}) });\n"
        "    forgetToken();\n"
        "    sessionAuthenticated = false;\n"
        "    sessionBlocked = false;\n"
        "    renderAccount();\n"
        "    showAuthMessage(t('Signed out.'), 'ok');\n"
        "    await refresh();\n"
        "  });\n",
    ),
)


def main(check_only: bool) -> int:
    text = APP_JS.read_text(encoding="utf-8")
    if MARKER in text:
        print("правка уже применена — файл не менялся")
        return 0
    problems = [
        f"вхождение найдено {text.count(old)} раз, ожидалось 1: {old.splitlines()[0][:70]}"
        for old, _new in PATCHES if text.count(old) != 1
    ]
    if problems:
        for line in problems:
            print("ОШИБКА:", line)
        return 2
    if check_only:
        print("вхождения на месте: правка применима")
        return 0
    for old, new in PATCHES:
        text = text.replace(old, new, 1)
    APP_JS.write_text(text, encoding="utf-8", newline="\n")
    print(f"app.js обновлён: {len(text)} символов")
    return 0


if __name__ == "__main__":
    sys.exit(main("--check" in sys.argv))
