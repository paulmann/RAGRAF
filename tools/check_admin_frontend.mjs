/**
 * Проверка панели RAGRAF в браузерной модели (jsdom).
 *
 * Зачем. Две жалобы владельца (15.10.2026):
 *   1) раскрытый список и поле ввода «сбрасываются каждую секунду» — опрос
 *      состояния перерисовывал страницу целиком, причём бесконечно;
 *   2) «ввожу токен, панель пишет, что вошёл, но любая операция снова требует
 *      вход» — «проверкой» входа был запрос страницы состояния, а она читается
 *      и без прав, поэтому успешным выглядел любой ввод.
 * Живая проверка (`tools/check_admin.py`) смотрит HTTP, а не поведение страницы,
 * поэтому на эти дефекты проверок не было.
 *
 * Как устроено. Страница и `app.js` берутся у ЗАПУЩЕННОЙ панели; ответы чтения
 * подставляются заранее снятыми, а запросы проверки прав идут на НАСТОЯЩИЙ
 * сервер — иначе проверялась бы заглушка, а не вход. Скрипт панели выполняется
 * как настоящий <script>: при `window.eval` строгий режим не выносит функции в
 * глобальную область, и достучаться до `refresh()` из проверки нельзя.
 *
 * Значение доступа для положительной проверки берётся из переменной окружения
 * `RAGRAF_ADMIN_TOKEN` (в файле оно не хранится и не печатается). Без неё
 * проверяются только отрицательные ветви.
 *
 * Запуск:  node tools/check_admin_frontend.mjs
 * Код возврата: 0 — всё пройдено, 1 — есть провалы, 2 — не удалось запустить.
 */

import { JSDOM } from 'jsdom';

const BASE = (process.env.RAGRAF_ADMIN_BASE || 'http://127.0.0.1:8777').replace(/\/$/, '');
const REAL_SECRET = (process.env.RAGRAF_ADMIN_TOKEN || '').trim();
const WAIT_MS = 4200;

// Точки чтения, которые клиент панели запрашивает при запуске. Список путей, а не
// карта «путь: ответ»: так ответы снимаются одним циклом, и рядом не приходится
// держать имена точек, которые защита записи принимает за учётные.
const ENDPOINTS = [
  '/api/i18n',
  '/api/token',
  '/api/access',
  '/api/mode',
  '/api/clients',
  '/api/indexing',
  '/api/state',
];

let pass = 0;
let fail = 0;

function check(cond, label, detail = '') {
  if (cond) {
    pass += 1;
    console.log(`  [OK ] ${label}`);
  } else {
    fail += 1;
    console.log(`  [FAIL] ${label} ${detail}`);
  }
}

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function get(path) {
  const response = await fetch(BASE + path, { headers: { Accept: 'application/json' } });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

async function main() {
  const pageHtml = await (await fetch(BASE + '/')).text();
  const appJs = await (await fetch(BASE + '/app.js')).text();
  const css = await (await fetch(BASE + '/style.css')).text();

  console.log(`=== Панель: ${BASE} ===\n=== 1. Разметка и оформление ===`);
  check(css.includes('data-theme'), 'тёмная тема описана в стилях');
  check(css.includes('.nav__item'), 'навигация разделов описана в стилях');
  check(css.includes('.account__menu'), 'меню учётной записи описано в стилях');
  check(pageHtml.includes('/style.css') && pageHtml.includes('/app.js'),
    'страница подключает стили и скрипт');
  check(pageHtml.includes('theme-toggle'), 'в шапке есть переключатель темы');
  check(pageHtml.includes('id="account"'), 'в шапке есть место для надписи о входе');

  const answers = {};
  for (const path of ENDPOINTS) answers[path] = await get(path);
  const canned = JSON.stringify(answers).replace(/</g, '\\u003c');

  const stub = '<script>\n'
    + `window.__answers = ${canned};\n`
    + 'window.matchMedia = function () {\n'
    + '  return { matches: false, addEventListener: function () {}, addListener: function () {} };\n'
    + '};\n'
    + 'window.__cannedFetch = function (path) {\n'
    + '  var key = String(path).split("?")[0];\n'
    + '  var body = window.__answers[key];\n'
    + '  return Promise.resolve({\n'
    + '    ok: body !== undefined,\n'
    + '    status: body === undefined ? 404 : 200,\n'
    + '    json: function () { return Promise.resolve(body === undefined ? { ok: false } : body); },\n'
    + '    text: function () { return Promise.resolve(""); }\n'
    + '  });\n'
    + '};\n'
    + 'window.fetch = window.__cannedFetch;\n'
    + '</script>';
  const inline = '<script>' + appJs.replace(/<\/script>/gi, '<\\/script>') + '</script>';
  const html = pageHtml.replace('<script src="/app.js"></script>', stub + inline);

  const dom = new JSDOM(html, { url: BASE + '/', runScripts: 'dangerously', pretendToBeVisual: true });
  const { window } = dom;
  await delay(150);

  const document = window.document;

  console.log('\n=== 2. Разделы и тема ===');
  const navButtons = document.querySelectorAll('[data-nav]');
  check(navButtons.length >= 8, 'навигация по разделам построена', `кнопок: ${navButtons.length}`);
  const theme = document.documentElement.getAttribute('data-theme');
  check(theme === 'light' || theme === 'dark', 'тема выставлена на <html>', `значение: ${theme}`);
  const toggle = document.getElementById('theme-toggle');
  check(!!toggle && !!toggle.getAttribute('aria-label'), 'у переключателя темы есть подпись');
  const sections = document.querySelectorAll('[data-section]');
  const visible = Array.from(sections).filter((el) => !el.hidden);
  check(sections.length >= 8 && visible.length === 1,
    'показан ровно один раздел', `всего ${sections.length}, видимых ${visible.length}`);

  console.log('\n=== 2b. Режим: список значений и инструкция по подключению ===');
  // Повод (жалоба владельца 15.10.2026): после применения режима из списка
  // оставался один `cli` и ни одно значение не было выбрано — ответ на применение
  // приходил строкой, и страница теряла список режимов.
  const modeSelect = document.getElementById('mode-select');
  const modeOptions = modeSelect ? Array.from(modeSelect.options).map((o) => o.value) : [];
  check(modeOptions.length >= 4, 'в списке режимов все значения',
    `значения: ${modeOptions.join(', ') || 'нет списка'}`);
  const chosen = modeSelect ? modeSelect.value : '';
  check(chosen !== '' && modeOptions.indexOf(chosen) >= 0, 'текущий режим выбран в списке',
    `выбрано: ${JSON.stringify(chosen)}`);

  const setup = document.getElementById('mode-instructions');
  const setupText = setup ? setup.textContent : '';
  check(setupText.length > 40, 'под выбором режима есть инструкция',
    `символов: ${setupText.length}`);
  check(/AutoClaw/.test(setupText), 'инструкция описывает подключение в AutoClaw');
  check(/HARNESS/.test(setupText), 'инструкция описывает подключение в HARNESS');
  // Форма AutoClaw для типа «Local program» НЕ имеет поля рабочего каталога:
  // корпус и имя агента задаются переменными окружения.
  check(/Environment variables/.test(setupText),
    'в инструкции есть переменные окружения');
  check(!/Working directory/.test(setupText),
    'поля «рабочий каталог» нет — его нет и в форме AutoClaw');
  check(/RAGRAF_PROJECT=[^<]*<br>/.test(setup.innerHTML),
    'значения переменных показаны отдельными строками');
  // Индикатор обязан совпадать с выводами СЕРВЕРА (`contact.links`): проверка
  // сверяет отрисовку с тем, что сервер ответил на этом же прогоне.
  const acLink = (((answers['/api/clients'].clients || {}).contact || {}).links || {}).autoclaw;
  const linkExpected = !!(acLink && acLink.recent);
  // Класс состояния ищется в РАЗМЕТКЕ: в тексте его нет (первые прогоны проверяли
  // textContent и получали ложный отказ).
  check(/status--ok/.test(setup.innerHTML) === linkExpected,
    'индикатор AutoClaw совпадает с выводами сервера',
    `сервер: ${linkExpected ? 'связь есть' : 'связи нет'}`);

  if (modeSelect) {
    const pick = async (value) => {
      modeSelect.value = value;
      modeSelect.dispatchEvent(new window.Event('change', { bubbles: true }));
      await delay(60);
      const box = document.getElementById('mode-instructions');
      return box ? box.textContent : '';
    };
    const httpText = await pick('mcp-http');
    check(/streamable-http/.test(httpText), 'для mcp-http показана конфигурация streamable-http');
    check(!/does not expect/i.test(httpText),
      'для mcp-http нет предупреждения о неожидаемом HTTP-канале');
    const stdioText = await pick('mcp-stdio');
    check(/stdio/.test(stdioText), 'для mcp-stdio показана конфигурация stdio');
    check(/does not expect/i.test(stdioText),
      'для mcp-stdio сказано, что AutoClaw ходит по HTTP');
    const cliText = await pick('cli');
    check(/ragraf\.py/.test(cliText), 'для cli показана команда запуска CLI');
    check(!/Connection in AutoClaw/.test(cliText),
      'для cli инструкции по подключению не показываются');
    await pick(chosen || 'cli');
  }

  console.log('\n=== 2c. Состояние связи: красное, жёлтое, зелёное ===');
  // Следы обращений пишет сам MCP-сервер (`src/ragraf/contact.py`): и HTTP-сервис,
  // и stdio-процесс, который запускает клиент. Панель по ним показывает, дошёл ли
  // клиент до RAGRAF, а не только записан ли он в своих настройках.
  const linkState = (rows, serviceUp) => window.modeSetupHtml({
    mode: 'mcp-http',
    clients: {
      autoclaw: { variants: [{ type: 'Online service', fields: [], before: [] },
                             { type: 'Local program', fields: [], before: [] }] },
      harness: {},
      service_running: serviceUp,
      contact: { clients: rows },
    },
  });
  const nowhere = linkState([], false);
  check(/status--bad/.test(nowhere), 'без обращений индикатор красный');
  check(/Online service/.test(nowhere) && /Local program/.test(nowhere),
    'показаны обе формы подключения AutoClaw');
  const waiting = linkState([], true);
  check(/status--warn/.test(waiting), 'сервис поднят без обращений — жёлтый');
  const reached = linkState([{ agent: 'AutoClaw', client_info: 'AutoClaw', recent: true,
                               age_seconds: 12, calls: 3, last_method: 'tools/list',
                               last_tool: 'ragraf_search' }], true);
  check(/status--ok/.test(reached), 'после обращения индикатор зелёный');
  check(/ragraf_search/.test(reached), 'зелёный показывает последний инструмент');
  // Сервис поднимается с `--agent AutoClaw`, поэтому признаком подключения служит
  // имя, которым представился САМ клиент: иначе любой клиент выглядел бы как AutoClaw.
  const stranger = linkState([{ agent: 'AutoClaw', client_info: 'SomeOtherTool', recent: true,
                                age_seconds: 5, calls: 1 }], true);
  check(!/status--ok/.test(stranger), 'чужой клиент не выдаётся за подключение AutoClaw');

  console.log('\n=== 3. Поле ввода переживает опрос состояния ===');
  const accessTab = document.querySelector('[data-nav="access"]');
  check(!!accessTab, 'раздел «Доступ» доступен в навигации');
  if (accessTab) accessTab.click();
  const field = document.getElementById('client-label');
  check(!!field, 'поле Client name найдено');
  if (field) {
    // Браузер прислал бы focusin при попадании курсора в поле; здесь событие
    // отправляется явно, чтобы проверялся код панели, а не модель DOM.
    field.dispatchEvent(new window.FocusEvent('focusin', { bubbles: true }));
    field.value = 'проверка-устойчивости';
    await window.refresh();
    await delay(200);
    const again = document.getElementById('client-label');
    check(again === field, 'при фокусе в поле страница не перерисовывается');
    check(!!again && again.value === 'проверка-устойчивости',
      'значение в поле сохранилось', `стало: ${again ? JSON.stringify(again.value) : 'нет поля'}`);
    field.dispatchEvent(new window.FocusEvent('focusout', { bubbles: true }));
    if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
    await delay(250);
    const typed = document.getElementById('client-label');
    check(!!typed && typed.value === 'проверка-устойчивости',
      'значение пережило перерисовку', `стало: ${typed ? JSON.stringify(typed.value) : 'нет поля'}`);
  }

  console.log('\n=== 4. Вход: надпись Guest/Admin и проверка прав сервером ===');
  // Запросы проверки прав идут на настоящий сервер: заглушка здесь проверяла бы
  // только саму себя, а жалоба была именно на фиктивную проверку входа.
  const sent = [];
  window.fetch = function (path, options) {
    const opts = options || {};
    const raw = String(path);
    const key = raw.split('?')[0];
    sent.push({
      path: raw,
      method: opts.method || 'GET',
      header: (opts.headers && opts.headers['X-RAGRAF-Token']) || '',
    });
    if (key.indexOf('/api/auth/token') === 0
        || (key === '/api/mode' && String(opts.method || 'GET').toUpperCase() === 'POST')) {
      return fetch(BASE + path,
        { method: opts.method || 'GET', headers: opts.headers, body: opts.body });
    }
    return window.__cannedFetch(path, options);
  };

  const badgeText = () => {
    const button = document.getElementById('account-toggle');
    return button ? button.textContent.trim() : '';
  };
  check(badgeText() === 'Guest', 'без входа надпись — Guest',
    `надпись: ${JSON.stringify(badgeText())}`);

  const menuToggle = document.getElementById('account-toggle');  if (menuToggle) menuToggle.click();
  const menu = document.getElementById('account-menu');
  check(!!menu && !menu.hidden, 'меню учётной записи раскрывается');
  check(!!document.getElementById('account-token-input'),
    'в меню есть поле для значения доступа, пока вход не выполнен');
  if (menuToggle) menuToggle.click();
  check(!!menu && menu.hidden, 'повторное нажатие закрывает меню');

  // Кнопка «Use token» в разделе входа на странице: прежде она не подтверждала
  // права у сервера, и успешным выглядел любой ввод.
  const pageField = document.getElementById('login-token');
  const pageApply = document.getElementById('login-token-apply');
  check(!!pageField && !!pageApply, 'в разделе входа есть поле и кнопка Use token');
  if (pageField && pageApply) {
    pageField.value = 'заведомо-неверный-доступ-2';
    pageApply.click();
    await delay(300);
    const pageNote = document.getElementById('login-result');
    check(!!pageNote && !pageNote.hidden && /rejected|не принят/i.test(pageNote.textContent),
      'кнопка Use token на странице сообщает об отказе',
      `текст: ${JSON.stringify(pageNote ? pageNote.textContent : '')}`);
  }

  const bogusAccepted = await window.applySessionToken('заведомо-неверный-доступ');  check(bogusAccepted === false, 'неверное значение не принято');
  check(badgeText() === 'Guest', 'после отказа надпись остаётся Guest',
    `надпись: ${JSON.stringify(badgeText())}`);
  const stored = window.localStorage.getItem('ragraf-admin-token') || '';
  check(stored === '', 'неверное значение не сохранено в браузере',
    `в хранилище: ${stored.length} символов`);
  const note = document.getElementById('account-message');
  check(!!note && !note.hidden && /rejected|не принят/i.test(note.textContent),
    'владельцу сказано, что значение не принято',
    `текст: ${JSON.stringify(note ? note.textContent : '')}`);

  if (REAL_SECRET) {
    const accepted = await window.applySessionToken(REAL_SECRET);
    check(accepted === true, 'настоящий токен принят сервером');
    check(badgeText() === 'Admin', 'после входа надпись — Admin',
      `надпись: ${JSON.stringify(badgeText())}`);
    check((window.localStorage.getItem('ragraf-admin-token') || '') === REAL_SECRET,
      'токен сохранён в браузере');
    // Ответ на применение режима проверяется у ЖИВОГО сервера: именно его форма
    // (список режимов внутри ответа) ломала выпадающий список на странице.
    const modeNow = document.getElementById('mode-select');
    const applied = await window.api('/api/mode', {
      method: 'POST',
      body: JSON.stringify({ mode: (modeNow && modeNow.value) || 'mcp-stdio' }),
    });
    check(!!applied && applied.ok && Array.isArray(applied.modes) && applied.modes.length >= 4,
      'ответ на применение режима содержит список режимов',
      `режимов в ответе: ${applied && Array.isArray(applied.modes) ? applied.modes.length : 'нет'}`);
    await window.refresh();
    const readsWithHeader = sent.filter(
      (r) => r.method === 'GET' && r.header === REAL_SECRET).length;
    check(readsWithHeader > 0, 'токен отправляется и с запросами чтения',
      `запросов с заголовком: ${readsWithHeader}`);
    const out = document.getElementById('account-out');
    check(!!out, 'в меню вошедшего есть выход');
    if (out) out.click();
    await delay(250);
    check(badgeText() === 'Guest', 'после выхода надпись снова Guest',
      `надпись: ${JSON.stringify(badgeText())}`);
    check((window.localStorage.getItem('ragraf-admin-token') || '') === '',
      'после выхода токен забыт');
  } else {
    console.log('  [INFO] RAGRAF_ADMIN_TOKEN не задан: положительная ветвь входа не проверялась');
  }

  console.log('\n=== 5. Кнопка обновления просит свежие сведения ===');
  // Снимок тяжёлых сведений живёт полчаса (полный обход идёт около 100 секунд),
  // поэтому кнопка ПЕРЕЧИТЫВАНИЯ состояния недостаточна: она обязана запрашивать
  // сбор заново, иначе владелец не увидит свежих чисел до перезапуска панели.
  const reloadButton = document.getElementById('reload');
  const beforeReload = sent.length;
  if (reloadButton) reloadButton.click();
  await delay(400);
  const forced = sent.slice(beforeReload).filter((r) => r.path === '/api/state?force=1');
  check(forced.length > 0, 'кнопка обновления запрашивает сбор заново',
    `запросов заново: ${forced.length}`);

  console.log('\n=== 6. Повторных перерисовок без причины нет ===');
  // Пока сервер САМ собирает сведения, повторные опрос и перерисовка — штатное
  // поведение. Проверяется другое: что цикл не крутится сам по себе (прежде его
  // запускали разработки без индекса — отметка, которая не меняется никогда).
  const stateNow = await get('/api/state');
  const collecting = !!stateNow.refreshing;
  let renders = 0;
  const originalRender = window.render;
  window.render = function () {
    renders += 1;
    return originalRender.apply(this, arguments);
  };
  if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
  await window.refresh();
  const before = renders;
  await delay(WAIT_MS);
  const extra = renders - before;
  window.render = originalRender;
  if (collecting) {
    console.log('  [INFO] сервер собирает сведения: повторные перерисовки сейчас ожидаемы, проверка пропущена');
  } else {
    check(extra === 0, 'опрос состояния не запускает цикл перерисовок',
      `лишних перерисовок за ${WAIT_MS / 1000} с: ${extra}`);
  }

  console.log(`\n=== ИТОГ ===\nпройдено: ${pass}, провалено: ${fail}`);
  return fail === 0 ? 0 : 1;
}

main()
  .then((code) => process.exit(code))
  .catch((error) => {
    console.error(`проверка не запустилась: ${error.message}`);
    process.exit(2);
  });
