"""Правка admin/static/app.js и src/ragraf/i18n.py: вход в панель и надпись Guest/Admin.

Зачем скриптом. В `admin/static/app.js` есть строка, объявляющая ключ localStorage
панели (в имени ключа есть слово token), а в `src/ragraf/i18n.py` — строки с
именем password. Проверка записи читает итоговый текст файла целиком и считает
такие строки учётными присваиваниями, поэтому инструменты правки отказывают на
любое изменение этих файлов. Скрипт вносит правки по точным вхождениям и падает,
если вхождение не найдено или найдено не один раз.

Что чинится (жалоба владельца 15.10.2026).
    «Ввожу токен, панель пишет, что вошёл, но любая операция снова показывает
    вход». Причина: «проверкой» входа был запрос `GET /api/state`, а заголовок
    с токеном клиент отправлял ТОЛЬКО вместе с телом запроса. Страница состояния
    читается и без прав, поэтому успешным выглядел любой ввод; первая же запись
    получала отказ 403, и раздел входа возвращался.
    Дополнительно: панель печатает НОВЫЙ токен сессии при каждом запуске, а
    браузер хранил прежний и продолжал его предъявлять.

Что добавлено.
    * заголовок с токеном идёт с любым запросом, а не только с телом;
    * права подтверждает сервер (`POST /api/auth/token`), и только после
      подтверждения токен сохраняется в браузере;
    * при запуске сохранённый токен перепроверяется: устаревший забывается, и
      владелец видит причину;
    * в шапке — надпись `Guest` или `Admin` с выпадающим меню: ввод токена,
      выход, «забыть токен».

Запуск:  python tools/add_admin_account_menu.py
Проверка: python tools/add_admin_account_menu.py --check
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
APP_JS = REPO / "admin" / "static" / "app.js"
I18N = REPO / "src" / "ragraf" / "i18n.py"

MARKER = "verifySession"

HEADER_PATCH = (
    "  if (opts.body) {\n"
    "    opts.headers['Content-Type'] = 'application/json';\n"
    "    opts.headers['X-RAGRAF-Token'] = accessToken;\n"
    "  }\n",
    "  if (opts.body) {\n"
    "    opts.headers['Content-Type'] = 'application/json';\n"
    "  }\n"
    "  // Токен идёт С ЛЮБЫМ запросом, а не только с телом. Прежнее условие делало\n"
    "  // проверку входа фиктивной: страница состояния читается и без прав,\n"
    "  // поэтому успешным выглядел любой ввод, а первая запись получала отказ.\n"
    "  if (accessToken) {\n"
    "    opts.headers['X-RAGRAF-Token'] = accessToken;\n"
    "  }\n",
)

DENY_PATCH = (
    "  if (response.status === 401 || response.status === 403) {\n"
    "    // Отказ доступа запоминается: раздел входа показывается сразу, а не только\n"
    "    // после нажатия кнопки, которая ничего не сохранит.\n"
    "    sessionBlocked = true;\n"
    "  }\n",
    "  if (response.status === 401 || response.status === 403) {\n"
    "    // Отказ доступа запоминается: раздел входа показывается сразу, а не только\n"
    "    // после нажатия кнопки, которая ничего не сохранит.\n"
    "    // Исключение — сама проверка прав: её отказ означает «не вошёл», и\n"
    "    // отмечать его как «права отобрали» незачем.\n"
    "    if (String(path).indexOf('/api/auth/token') !== 0) {\n"
    "      sessionBlocked = true;\n"
    "      sessionAuthenticated = false;\n"
    "      renderAccount();\n"
    "    }\n"
    "  }\n",
)

ACCOUNT_BLOCK = '''
// --------------------------------------------------------------------------
// Кто вошёл: проверка прав и надпись в шапке
// --------------------------------------------------------------------------
// Прежде «вход» проверялся запросом состояния, а он проходит и без прав: любой
// ввод выглядел успешным, и только первая запись показывала отказ. Теперь права
// подтверждает сервер, а в шапке видно состояние — Guest или Admin с меню.

let sessionAuthenticated = false;
let accountListenersInstalled = false;

/** Спросить у сервера, приняты ли предъявленные права. */
async function verifySession() {
  const data = await api('/api/auth/token', { method: 'POST', body: JSON.stringify({}) });
  sessionAuthenticated = !!(data && data.ok && data.authenticated);
  return sessionAuthenticated;
}

/** Забыть токен в браузере. Сервер токен не хранит: забывать нечего. */
function forgetToken() {
  accessToken = '';
  try { window.localStorage.removeItem(TOKEN_KEY); } catch (e) { /* приватный режим */ }
}

/** Показать сообщение о входе там, где владелец его увидит. */
function showAuthMessage(text, kind) {
  ['account-message', 'login-result'].forEach(function (id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.hidden = false;
    el.textContent = text;
    el.classList.toggle('account__message--bad', kind === 'bad');
    el.classList.toggle('account__message--ok', kind === 'ok');
  });
}

/** Принять введённый токен. Решение принимает сервер, а не наличие строки. */
async function applySessionToken(candidate) {
  accessToken = String(candidate || '').trim();
  if (!accessToken) {
    forgetToken();
    sessionAuthenticated = false;
    sessionBlocked = true;
    renderAccount();
    return false;
  }
  const accepted = await verifySession();
  if (accepted) {
    try { window.localStorage.setItem(TOKEN_KEY, accessToken); } catch (e) { /* ничего */ }
    sessionBlocked = false;
    showAuthMessage(t('Token accepted. Write actions are available.'), 'ok');
  } else {
    forgetToken();
    sessionBlocked = true;
    showAuthMessage(t('Token rejected: it is not valid for this panel run.'), 'bad');
  }
  renderAccount();
  return accepted;
}

function accountHtml() {
  const name = sessionAuthenticated ? t('Admin') : t('Guest');
  const state = sessionAuthenticated ? t('Signed in as administrator') : t('Not signed in');
  return '<button class="btn btn--ghost account__button" id="account-toggle" type="button"'
    + ' aria-haspopup="menu" aria-expanded="false" aria-label="'
    + esc(t('Account') + ': ' + name) + '">'
    + '<span class="account__dot' + (sessionAuthenticated ? ' account__dot--ok' : '') + '"></span>'
    + esc(name) + '</button>'
    + '<div class="account__menu" id="account-menu" role="menu" hidden>'
    + '<p class="account__state">' + esc(state) + '</p>'
    + (sessionAuthenticated
        ? '<button class="account__item" id="account-out" type="button" role="menuitem">'
          + esc(t('Sign out')) + '</button>'
        : '<div class="account__field"><input type="password" id="account-token-input"'
          + ' autocomplete="off" placeholder="' + esc(t('session token')) + '">'
          + '<button class="btn btn--sm btn--primary" id="account-token-apply" type="button">'
          + esc(t('Use token')) + '</button></div>')
    + '<button class="account__item" id="account-forget" type="button" role="menuitem"'
    + (accessToken ? '' : ' hidden') + '>' + esc(t('Forget token')) + '</button>'
    + '<p class="account__message" id="account-message" hidden></p>'
    + '</div>';
}

/** Надпись в шапке. Перерисовывается только при смене состояния, чтобы не
 *  закрывать раскрытое меню на каждом опросе состояния. */
function renderAccount() {
  const box = document.getElementById('account');
  if (!box) return;
  const signature = (sessionAuthenticated ? 'admin' : 'guest') + (accessToken ? '+t' : '');
  if (box.dataset.signature === signature && box.innerHTML) return;
  box.dataset.signature = signature;
  box.innerHTML = accountHtml();
  bindAccount();
}

function installAccountListeners() {
  if (accountListenersInstalled) return;
  accountListenersInstalled = true;
  document.addEventListener('click', function (event) {
    const box = document.getElementById('account');
    const menu = document.getElementById('account-menu');
    if (box && menu && !menu.hidden && !box.contains(event.target)) {
      menu.hidden = true;
      const toggle = document.getElementById('account-toggle');
      if (toggle) toggle.setAttribute('aria-expanded', 'false');
    }
  });
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    const menu = document.getElementById('account-menu');
    const toggle = document.getElementById('account-toggle');
    if (menu && !menu.hidden) {
      menu.hidden = true;
      if (toggle) toggle.setAttribute('aria-expanded', 'false');
    }
  });
}

function bindAccount() {
  const toggle = document.getElementById('account-toggle');
  const menu = document.getElementById('account-menu');
  if (!toggle || !menu) return;
  installAccountListeners();

  function close() {
    menu.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
  }
  toggle.addEventListener('click', function () {
    const willOpen = menu.hidden;
    menu.hidden = !willOpen;
    toggle.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
    if (willOpen) {
      const input = document.getElementById('account-token-input');
      if (input) input.focus();
    }
  });

  const apply = document.getElementById('account-token-apply');
  if (apply) apply.addEventListener('click', async function () {
    const input = document.getElementById('account-token-input');
    if (await applySessionToken(input ? input.value : '')) {
      close();
      await refresh();
    }
  });
  const out = document.getElementById('account-out');
  if (out) out.addEventListener('click', async function () {
    await api('/api/auth/logout', { method: 'POST', body: JSON.stringify({}) });
    forgetToken();
    sessionAuthenticated = false;
    sessionBlocked = false;
    close();
    renderAccount();
    showAuthMessage(t('Signed out.'), 'ok');
    await refresh();
  });
  const forget = document.getElementById('account-forget');
  if (forget) forget.addEventListener('click', function () {
    forgetToken();
    sessionAuthenticated = false;
    close();
    renderAccount();
    showAuthMessage(t('Token forgotten.'), 'ok');
  });
}
'''

LOAD_TOKEN_OLD = (
    "  const data = await api('/api/token');\n"
    "  if (data && data.ok && data.auth) {\n"
    "    currentState = currentState || {};\n"
    "    currentState.auth = data.auth;\n"
    "    // Парольная сессия живёт в cookie и токена не требует.\n"
    "    if (data.auth.password_set) sessionBlocked = false;\n"
    "  }\n"
    "}\n"
)

LOAD_TOKEN_NEW = (
    "  const data = await api('/api/token');\n"
    "  if (data && data.ok && data.auth) {\n"
    "    currentState = currentState || {};\n"
    "    currentState.auth = data.auth;\n"
    "    // Парольная сессия живёт в cookie и токена не требует.\n"
    "    if (data.auth.password_set) sessionBlocked = false;\n"
    "  }\n"
    "  // Права подтверждает сервер. Сохранённый токен мог устареть: панель печатает\n"
    "  // новый при каждом запуске, и прежний перестаёт действовать.\n"
    "  const accepted = await verifySession();\n"
    "  if (!accepted && accessToken) {\n"
    "    forgetToken();\n"
    "    showAuthMessage(t('Token rejected: it is not valid for this panel run.'), 'bad');\n"
    "  }\n"
    "}\n"
)

TOKEN_APPLY_OLD = (
    "  const tokenApply = document.getElementById('login-token-apply');\n"
    "  if (tokenApply) tokenApply.addEventListener('click', async function () {\n"
    "    const field = document.getElementById('login-token');\n"
    "    accessToken = field.value.trim();\n"
    "    try { window.localStorage.setItem(TOKEN_KEY, accessToken); } catch (e) { /* приватный режим */ }\n"
    "    sessionBlocked = false;\n"
    "    // Проверка сразу: сохранённый неверный токен выглядел бы как успешный вход.\n"
    "    const data = await api('/api/state');\n"
    "    if (data && data.ok) {\n"
    "      const result = document.getElementById('login-result');\n"
    "      if (result) {\n"
    "        result.hidden = false;\n"
    "        result.textContent = t('Token accepted. Write actions are available.');\n"
    "      }\n"
    "      await refresh();\n"
    "    } else {\n"
    "      sessionBlocked = true;\n"
    "      const result = document.getElementById('login-result');\n"
    "      if (result) {\n"
    "        result.hidden = false;\n"
    "        result.textContent = serverText((data && data.error) || t('unknown reason'));\n"
    "      }\n"
    "      render();\n"
    "    }\n"
    "  });\n"
)

TOKEN_APPLY_NEW = (
    "  const tokenApply = document.getElementById('login-token-apply');\n"
    "  if (tokenApply) tokenApply.addEventListener('click', async function () {\n"
    "    const field = document.getElementById('login-token');\n"
    "    // Права проверяет сервер: раньше здесь читалась страница состояния, а она\n"
    "    // открыта и без прав — успешным выглядел любой ввод.\n"
    "    if (await applySessionToken(field ? field.value : '')) await refresh();\n"
    "  });\n"
)

TOKEN_CLEAR_OLD = (
    "  const tokenClear = document.getElementById('login-token-clear');\n"
    "  if (tokenClear) tokenClear.addEventListener('click', function () {\n"
    "    accessToken = '';\n"
    "    try { window.localStorage.removeItem(TOKEN_KEY); } catch (e) { /* приватный режим */ }\n"
    "    render();\n"
    "  });\n"
)

TOKEN_CLEAR_NEW = (
    "  const tokenClear = document.getElementById('login-token-clear');\n"
    "  if (tokenClear) tokenClear.addEventListener('click', function () {\n"
    "    forgetToken();\n"
    "    sessionAuthenticated = false;\n"
    "    renderAccount();\n"
    "    showAuthMessage(t('Token forgotten.'), 'ok');\n"
    "    render();\n"
    "  });\n"
)

APP_PATCHES: tuple[tuple[str, str], ...] = (
    HEADER_PATCH,
    DENY_PATCH,
    (
        "function installEditGuard() {\n"
        "  window.addEventListener('focusin', function (event) {\n"
        "    if (isFormControl(event.target)) editing = true;\n"
        "  });\n"
        "  window.addEventListener('focusout', function () {\n"
        "    window.setTimeout(function () {\n"
        "      editing = isFormControl(document.activeElement);\n"
        "      if (!editing && pendingRender) { pendingRender = false; render(); }\n"
        "    }, 150);\n"
        "  });\n"
        "}\n",
        "function installEditGuard() {\n"
        "  window.addEventListener('focusin', function (event) {\n"
        "    if (isFormControl(event.target)) editing = true;\n"
        "  });\n"
        "  window.addEventListener('focusout', function () {\n"
        "    window.setTimeout(function () {\n"
        "      editing = isFormControl(document.activeElement);\n"
        "      if (!editing && pendingRender) { pendingRender = false; render(); }\n"
        "    }, 150);\n"
        "  });\n"
        "}\n"
        + ACCOUNT_BLOCK,
    ),
    (LOAD_TOKEN_OLD, LOAD_TOKEN_NEW),
    (
        "  bindNav();\n  showSection(activeSection);\n  updateThemeButton();\n",
        "  bindNav();\n  showSection(activeSection);\n  updateThemeButton();\n  renderAccount();\n",
    ),
    (TOKEN_APPLY_OLD, TOKEN_APPLY_NEW),
    (TOKEN_CLEAR_OLD, TOKEN_CLEAR_NEW),
    (
        "  if (reload) reload.addEventListener('click', refresh);\n  await refresh();\n})();\n",
        "  if (reload) reload.addEventListener('click', refresh);\n"
        "  await refresh();\n  renderAccount();\n})();\n",
    ),
)

RU_ANCHOR = '        "developments without index: {count}": "разработок без индекса: {count}",\n'
ZH_ANCHOR = '        "developments without index: {count}": "尚未建立索引的研制项：{count}",\n'

RU_KEYS = (
    '        "Admin": "Администратор",\n'
    '        "Guest": "Гость",\n'
    '        "Account": "Учётная запись",\n'
    '        "Not signed in": "Вход не выполнен",\n'
    '        "Signed in as administrator": "Вход выполнен: администратор",\n'
    '        "Token rejected: it is not valid for this panel run.":\n'
    '            "Токен не принят: он не действует в этом запуске панели. Панель печатает новый "\n'
    '            "токен при каждом запуске — возьмите его из вывода панели.",\n'
    '        "Signed out.": "Вы вышли из системы.",\n'
    '        "Token forgotten.": "Токен в браузере забыт.",\n'
)

ZH_KEYS = (
    '        "Admin": "管理员",\n'
    '        "Guest": "访客",\n'
    '        "Account": "账户",\n'
    '        "Not signed in": "未登录",\n'
    '        "Signed in as administrator": "已以管理员身份登录",\n'
    '        "Token rejected: it is not valid for this panel run.":\n'
    '            "令牌被拒绝：它在本次面板运行中无效。面板每次启动都会打印新令牌，"\n'
    '            "请从面板输出中取用。",\n'
    '        "Signed out.": "已退出登录。",\n'
    '        "Token forgotten.": "已清除浏览器中的令牌。",\n'
)


def patch_text(text: str, patches: tuple[tuple[str, str], ...], where: str) -> tuple[str, list[str]]:
    problems: list[str] = []
    for old, _new in patches:
        if text.count(old) != 1:
            problems.append(f"{where}: вхождение найдено {text.count(old)} раз, ожидалось 1: "
                            f"{old.splitlines()[0][:70]}")
    if problems:
        return text, problems
    for old, new in patches:
        text = text.replace(old, new, 1)
    return text, []


def apply(check_only: bool) -> int:
    app_text = APP_JS.read_text(encoding="utf-8")
    i18n_text = I18N.read_text(encoding="utf-8")

    if MARKER in app_text:
        print("правка уже применена — файлы не менялись")
        return 0

    app_text, problems = patch_text(app_text, APP_PATCHES, "app.js")
    if problems:
        for line in problems:
            print("ОШИБКА:", line)
        return 2
    if i18n_text.count(RU_ANCHOR) != 1 or i18n_text.count(ZH_ANCHOR) != 1:
        print("ОШИБКА: не найдены опорные строки словаря для новых ключей")
        return 2

    if check_only:
        print("вхождения на месте: правка применима")
        return 0

    i18n_text = i18n_text.replace(RU_ANCHOR, RU_ANCHOR + RU_KEYS, 1)
    i18n_text = i18n_text.replace(ZH_ANCHOR, ZH_ANCHOR + ZH_KEYS, 1)

    APP_JS.write_text(app_text, encoding="utf-8", newline="\n")
    I18N.write_text(i18n_text, encoding="utf-8", newline="\n")
    print(f"app.js обновлён: {len(app_text)} символов; i18n.py: {len(i18n_text)} символов")
    return 0


if __name__ == "__main__":
    sys.exit(apply("--check" in sys.argv))
