/* ==========================================================================
   RAGRAF — панель управления: клиентская логика
   ==========================================================================
   Принципы:
     * никаких внешних библиотек: панель работает без сети и без сборки;
     * все данные приходят из /api/state одним запросом: одна точка правды;
     * изменяющие запросы несут токен доступа (получен при загрузке);
     * результат изменения показывается явно: «сохранено» и что именно.

   Устройство страницы
     Постоянный левый сайдбар (разделы по группам), компактный топбар (палитра
     команд, язык, тема, состояние, учётная запись, обновление) и область
     раздела. Разделов десять: «Обзор» добавлен к прежним девяти и собирается из
     того же состояния — отдельной точки API у него нет. Прежние разделы
     сохранили свои запросы и обработчики: правка оформления не меняет данные.

   Язык интерфейса
     Строк в разметке нет: каждый текст проходит через `t()`, а словарь
     переводов приходит из `/api/i18n`. Источник правды один — `ragraf/i18n.py`
     на сервере, поэтому перевести панель можно, не правя этот файл. Язык по
     умолчанию английский; выбор хранится в localStorage и переживает
     перезагрузку страницы.

   Что панель НЕ делает
     Не решает сама, «можно ли» изменять: права проверяет сервер, а панель лишь
     показывает, что запись сейчас не пройдёт, и объясняет, что для этого нужно.
     Кнопка, отключённая в интерфейсе, — подсказка, а не защита.
   ========================================================================== */

'use strict';

const TOKEN_KEY = 'ragraf-admin-token';
const LANG_KEY = 'ragraf-admin-lang';
const SECTION_KEY = 'ragraf-admin-section';
const DENSITY_KEY = 'ragraf-admin-density';
let accessToken = '';
let currentState = null;
// Признак «изменяющие действия не пройдут»: сервер отвечает отказом, когда
// ни парольной сессии, ни токена сессии нет. Панель обязана сказать об этом
// словами и дать способ войти: иначе кнопка «Сохранить» молча не работает.
let sessionBlocked = false;

/* --------------------------------------------------------------------------
   Состояние интерфейса
   --------------------------------------------------------------------------
   Отдельно от состояния СЕРВЕРА (`currentState`): то, что выбрал человек —
   поиск, фильтр, сортировка, страница, открытая разработка. Живёт между
   перерисовками, иначе опрос состояния сбрасывал бы выбор каждые несколько
   секунд. Раньше это было невозможно: разделы рисовались один раз и целиком.
   -------------------------------------------------------------------------- */
const UI = {
  dashboard: {},
  projects: { q: '', state: 'all', sort: { key: 'category', dir: 'asc' }, page: 0, perPage: 10, sel: [], open: null },
  indexing: {},
  quality: { q: '', onlyIssues: false, sel: '' },
  usage: { sel: '', sort: { key: 'calls', dir: 'desc' } },
  storage: { q: '', open: '' },
  settings: { tab: 'global', q: '', sel: '' },
  mode: { tab: 'autoclaw', transport: 'stdio' },
  access: { q: '', channel: 'all', result: 'all', page: 0, perPage: 12 },
  databases: {},
};

//: Вкладка, которой надо вернуть фокус после перерисовки: перерисовка заменяет
//: разметку, и без этого стрелки переводили бы фокус в никуда.
let pendingTabFocus = null;
//: Куда вернуть фокус после закрытия слоя поверх страницы (палитра, диалог):
//: фокус обязан вернуться к тому, кто слой открыл, а не потеряться.
let overlayReturnFocus = null;

//: Разделы панели. `id` — он же `data-nav`/`data-section`: по нему работает и
//: сохранение выбранного раздела, и переход из палитры команд.
const NAV = [
  { group: 'Monitoring', items: [{ id: 'dashboard', icon: 'gauge' }] },
  {
    group: 'Index',
    items: [
      { id: 'catalog', icon: 'folder' },
      { id: 'indexing', icon: 'refresh' },
      { id: 'quality', icon: 'shield' },
      { id: 'storage', icon: 'database' },
    ],
  },
  { group: 'Observability', items: [{ id: 'usage', icon: 'activity' }] },
  {
    group: 'System',
    items: [
      { id: 'settings', icon: 'sliders' },
      { id: 'mode', icon: 'network' },
      { id: 'access', icon: 'key' },
      { id: 'databases', icon: 'hdd' },
    ],
  },
];

const SECTIONS = NAV.reduce(function (all, group) { return all.concat(group.items); }, []);

/**
 * Название раздела и группы.
 *
 * Ключ словаря записан ЛИТЕРАЛОМ в каждой ветви, а не взят из поля таблицы.
 * Причина в проверке переводов: `t(section.label)` она разобрать не может, и
 * пропущенный перевод названия раздела остался бы незамеченным — а именно
 * названия видит человек раньше всего остального.
 */
function sectionLabel(id) {
  switch (id) {
    case 'dashboard': return t('Overview');
    case 'catalog': return t('Developments');
    case 'indexing': return t('Indexing');
    case 'quality': return t('Index quality');
    case 'storage': return t('Database sizes');
    case 'usage': return t('Usage and effect');
    case 'settings': return t('Settings');
    case 'mode': return t('Mode and clients');
    case 'access': return t('Access');
    case 'databases': return t('Databases');
    default: return id;
  }
}

function groupLabel(name) {
  switch (name) {
    case 'Monitoring': return t('Monitoring');
    case 'Index': return t('Index');
    case 'Observability': return t('Observability');
    case 'System': return t('System');
    default: return name;
  }
}

/* --------------------------------------------------------------------------
   Иконки
   --------------------------------------------------------------------------
   Свой набор вместо библиотеки: панель не грузит ничего извне. Контуры взяты
   из lucide (ISC), что и записано в docs/THIRD_PARTY.md.
   -------------------------------------------------------------------------- */
const ICONS = {
  gauge: '<path d="M12 14 15.5 9.5"/><path d="M20.5 15.5A9 9 0 1 0 3.5 15.5"/><circle cx="12" cy="14" r="1.6"/>',
  folder: '<path d="M3 7.5A2 2 0 0 1 5 5.5h3.9a2 2 0 0 1 1.6.8l.9 1.2H19a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-2.64-6.36L21 8"/><path d="M21 3v5h-5"/>',
  shield: '<path d="M12 21.5s7.5-3.6 7.5-9.4V5.6L12 2.8 4.5 5.6v6.5c0 5.8 7.5 9.4 7.5 9.4z"/><path d="m9.3 12 2 2 3.5-3.8"/>',
  database: '<ellipse cx="12" cy="5.5" rx="8" ry="3"/><path d="M4 5.5v13c0 1.66 3.58 3 8 3s8-1.34 8-3v-13"/><path d="M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3"/>',
  activity: '<path d="M22 12h-4l-3 8.5L9 3.5 6 12H2"/>',
  sliders: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h10M18 18h2"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="16" cy="18" r="2"/>',
  network: '<rect x="9" y="2.5" width="6" height="5.5" rx="1.5"/><rect x="2.5" y="16" width="6" height="5.5" rx="1.5"/><rect x="15.5" y="16" width="6" height="5.5" rx="1.5"/><path d="M12 8v4M5.5 16v-2.5h13V16"/>',
  key: '<circle cx="7.8" cy="15.8" r="4.3"/><path d="m10.9 12.8 8-8"/><path d="m17.4 6.3 2 2"/><path d="m14.6 9.1 2 2"/>',
  hdd: '<path d="M5.5 5h13L22 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-17A1.5 1.5 0 0 1 2 18.5V14z"/><path d="M2 14h20"/><path d="M6 17.2h.01M9.5 17.2h.01"/>',
  search: '<circle cx="11" cy="11" r="7.2"/><path d="m20.5 20.5-4.3-4.3"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M2.5 12h2M19.5 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"/>',
  moon: '<path d="M20.5 14.3A8.6 8.6 0 0 1 9.7 3.5a8.6 8.6 0 1 0 10.8 10.8z"/>',
  monitor: '<rect x="2.5" y="3.5" width="19" height="13" rx="2"/><path d="M8.5 20.5h7M12 16.5v4"/>',
  user: '<circle cx="12" cy="8" r="3.6"/><path d="M4.8 20.2a7.2 7.2 0 0 1 14.4 0"/>',
  logout: '<path d="M9.5 21H5.5A1.5 1.5 0 0 1 4 19.5v-15A1.5 1.5 0 0 1 5.5 3h4"/><path d="m15.5 16.5 4.5-4.5-4.5-4.5"/><path d="M20 12H9.5"/>',
  chevronD: '<path d="m6 9.5 6 6 6-6"/>',
  chevronR: '<path d="m9.5 6 6 6-6 6"/>',
  chevronL: '<path d="m14.5 6-6 6 6 6"/>',
  check: '<path d="M20 6.5 9.4 17.1 4 11.7"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  alert: '<path d="M10.3 3.9 2.5 17.4A2 2 0 0 0 4.2 20.4h15.6a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9.5v4M12 17h.01"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  octagon: '<path d="M7.9 2.5h8.2L21.5 7.9v8.2L16.1 21.5H7.9L2.5 16.1V7.9z"/><path d="M12 7.5v5M12 16h.01"/>',
  trash: '<path d="M3.5 6.5h17"/><path d="M19 6.5v13a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 19.5v-13"/><path d="M8.5 6.5V4.8A1.3 1.3 0 0 1 9.8 3.5h4.4a1.3 1.3 0 0 1 1.3 1.3v1.7"/><path d="M10.5 11v6M13.5 11v6"/>',
  play: '<path d="M7 4.5 19 12 7 19.5z"/>',
  layers: '<path d="m12 2.8 9 4.6-9 4.6-9-4.6z"/><path d="m3 12.4 9 4.6 9-4.6"/><path d="m3 17 9 4.6 9-4.6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5.2l3.4 2"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5.5 15H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v.5"/>',
  terminal: '<path d="m4.5 17 6-5.5-6-5.5"/><path d="M12.5 19h7"/>',
  lock: '<rect x="3.5" y="10.5" width="17" height="10.5" rx="2"/><path d="M7.5 10.5V7a4.5 4.5 0 0 1 9 0v3.5"/>',
  cpu: '<rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9.5" y="9.5" width="5" height="5"/><path d="M9 2.5v2.5M15 2.5v2.5M9 19v2.5M15 19v2.5M2.5 9H5M2.5 15H5M19 9h2.5M19 15h2.5"/>',
  zap: '<path d="M13.5 2.5 4 14h6.5l-.5 7.5L20 10h-6.8z"/>',
  file: '<path d="M14 2.5H7a2 2 0 0 0-2 2v15a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7.5z"/><path d="M14 2.5V8h5"/>',
  hash: '<path d="M4 9.5h16M4 15h16M10.5 3 8.5 21M16 3l-2 18"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  arrowR: '<path d="M4.5 12h15M13 5.5l6.5 6.5-6.5 6.5"/>',
  menu: '<path d="M3.5 6.5h17M3.5 12h17M3.5 17.5h17"/>',
  cmd: '<path d="M8.5 3.5A2.5 2.5 0 1 0 11 6v12a2.5 2.5 0 1 1-2.5-2.5h7A2.5 2.5 0 1 0 13 18V6a2.5 2.5 0 1 1 2.5 2.5h-7"/>',
  box: '<path d="M21 8.2 12 3 3 8.2v7.6L12 21l9-5.2z"/><path d="m3 8.2 9 5.2 9-5.2M12 21v-7.6"/>',
  dot: '<circle cx="12" cy="12" r="4"/>',
  spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18"/>',
  link: '<path d="M10 13.5a4 4 0 0 0 5.7 0l2.8-2.8a4 4 0 0 0-5.7-5.7l-1.4 1.4"/><path d="M14 10.5a4 4 0 0 0-5.7 0l-2.8 2.8a4 4 0 0 0 5.7 5.7l1.4-1.4"/>',
  history: '<path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1L3.5 8.5"/><path d="M3.5 4v4.5H8"/><path d="M12 7.5V12l3 1.8"/>',
  rows: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9.5h18M3 15h18"/>',
  type: '<path d="M4 7V5h16v2M9 5v14M15 5v14M7 19h4M13 19h4"/>',
};

/** Значок. `nm` — имя из набора, `cls` — размер и цвет через классы. */
function icon(nm, cls, sw) {
  const path = ICONS[nm] || '';
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="'
    + (sw || 1.7) + '" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="'
    + esc(cls || 'h-4 w-4') + '">' + path + '</svg>';
}

/* --------------------------------------------------------------------------
   Язык интерфейса
   -------------------------------------------------------------------------- */

const I18N = {
  languages: ['en'],
  defaultLanguage: 'en',
  titles: { en: 'English' },
  catalogue: {},
  current: 'en',
  loaded: false,
};

/** Язык по умолчанию, если пользователь ещё не выбирал. */
function preferredLanguage() {
  let saved = '';
  try { saved = window.localStorage.getItem(LANG_KEY) || ''; } catch (e) { saved = ''; }
  if (I18N.languages.indexOf(saved) >= 0) return saved;
  const nav = String((navigator.languages && navigator.languages[0]) || navigator.language || '');
  const base = nav.toLowerCase().split('-')[0];
  if (I18N.languages.indexOf(base) >= 0) return base;
  return I18N.defaultLanguage;
}

/** Перевести строку. Нет перевода — показывается ключ, то есть английский. */
function t(key, params) {
  const table = I18N.catalogue[I18N.current] || {};
  let text = Object.prototype.hasOwnProperty.call(table, key) ? table[key] : key;
  if (params) {
    Object.keys(params).forEach(function (name) {
      text = text.split('{' + name + '}').join(String(params[name]));
    });
  }
  return text;
}

/**
 * Перевести текст, пришедший с сервера.
 * Часть полей отдана модулями, которые пишут по-русски (настройки, пробы,
 * слои баз). Их значения переводятся здесь по таблице «значение → ключ»:
 * менять сами модули ради подписи нельзя, а показать английский текст нужно.
 */
function serverText(value) {
  const text = value === null || value === undefined ? '' : String(value);
  const map = (I18N.catalogue[I18N.current] || {})._server || {};
  return Object.prototype.hasOwnProperty.call(map, text) ? map[text] : text;
}

/** Язык для чисел и дат: он же определяет разделитель разрядов. */
function localeTag() {
  return I18N.current === 'zh' ? 'zh-CN' : (I18N.current === 'ru' ? 'ru-RU' : 'en-US');
}

/* --------------------------------------------------------------------------
   Утилиты
   -------------------------------------------------------------------------- */

/** Экранирование: значения приходят из данных, а не из разметки. */
function esc(value) {
  return String(value === null || value === undefined ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/** Человекочитаемый размер. Единицы — из словаря: Б/КБ и B/KB различаются. */
function humanBytes(bytes) {
  const n = Number(bytes || 0);
  if (!isFinite(n)) return '—';
  const units = ['B', 'KB', 'MB', 'GB'];
  let index = 0;
  let value = n;
  while (value >= 1024 && index < units.length - 1) { value = value / 1024; index += 1; }
  const rounded = index === 0 ? String(Math.round(value)) : value.toFixed(1);
  return rounded + ' ' + t(units[index]);
}

function num(value) {
  return Number(value || 0).toLocaleString(localeTag());
}

/** Длительность стадии: единица измерения берётся из словаря, а не из строки. */
function secondsText(value) {
  const n = Number(value || 0);
  return t(' {s} s', { s: n < 10 ? n.toFixed(3) : n.toFixed(1) });
}

/** Длительность обращения: до секунды — миллисекунды, дальше — секунды. */
function millisText(value) {
  const n = Number(value || 0);
  if (n >= 1000) return t(' {s} s', { s: (n / 1000).toFixed(n >= 10000 ? 0 : 1) });
  return t(' {ms} ms', { ms: n.toFixed(1) });
}

/** Большие числа — в млн/тыс., чтобы столбец не разрастался по горизонтали. */
function bigNum(value) {
  const n = Number(value || 0);
  if (n >= 1e6) return (n / 1e6).toFixed(2) + ' ' + t('M');
  if (n >= 1e4) return (n / 1e3).toFixed(1) + ' ' + t('K');
  return num(n);
}

/** Дата и время в языке интерфейса. Принимает ISO-строку или секунды. */
function stamp(value) {
  if (!value) return '—';
  let date;
  if (typeof value === 'number') {
    date = new Date(value * 1000);
  } else {
    date = new Date(String(value));
  }
  if (isNaN(date.getTime())) return String(value);
  return date.toLocaleString(localeTag());
}

/** Обрезать длинный текст по границе слова, а не посреди слова. */
function truncate(text, limit) {
  const value = String(text || '');
  if (value.length <= limit) return value;
  const cut = value.slice(0, limit - 1);
  const space = cut.lastIndexOf(' ');
  return (space > limit * 0.6 ? cut.slice(0, space) : cut) + '…';
}

/** Положить значение в буфер обмена. Отказ (приватный режим) не ломает страницу. */
function copyText(value) {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(String(value));
      return true;
    }
  } catch (e) { /* приватный режим: буфер обмена недоступен */ }
  return false;
}

function setStatus(text, kind) {
  const el = document.getElementById('status');
  if (!el) return;
  el.className = 'status' + (kind ? ' status--' + kind : '');
  el.innerHTML = (kind ? icon(kind === 'ok' ? 'check' : kind === 'bad' ? 'octagon' : 'clock', 'h-3.5 w-3.5', 2) : '')
    + '<span>' + esc(text) + '</span>';
}

/**
 * Можно ли сейчас изменять данные.
 *
 * Это ПОДСКАЗКА для интерфейса, а не проверка прав: решение принимает сервер.
 * Токен сессии или парольная сессия в cookie означают, что запись пройдёт;
 * иначе кнопки отключены и рядом сказано, что нужно сделать.
 */
function writeAllowed() {
  if (sessionBlocked) return false;
  if (sessionAuthenticated) return true;
  const auth = (currentState && currentState.auth) || {};
  return !!auth.password_set;
}

/** Обращение к API с токеном для изменяющих запросов. */
async function api(path, options) {
  const opts = Object.assign({}, options || {});
  opts.headers = Object.assign({ 'Accept': 'application/json' }, opts.headers || {});
  if (opts.body) {
    opts.headers['Content-Type'] = 'application/json';
  }
  // Токен идёт С ЛЮБЫМ запросом, а не только с телом. Прежнее условие делало
  // проверку входа фиктивной: страница состояния читается и без прав,
  // поэтому успешным выглядел любой ввод, а первая запись получала отказ.
  if (accessToken) {
    opts.headers['X-RAGRAF-Token'] = accessToken;
  }
  let response;
  try {
    response = await fetch(path, opts);
  } catch (e) {
    // Сетевой сбой — это отказ, а не «ждём дальше»: без этой ветки страница
    // оставалась с надписью «собираю…» и ошибкой в консоли, которую не видно.
    return { ok: false, error: t('Could not reach the panel: {reason}',
      { reason: String((e && e.message) || e) }) };
  }
  let data = null;
  try { data = await response.json(); } catch (e) { data = { ok: false, error: t('response could not be parsed') }; }
  if (response.status === 401 || response.status === 403) {
    // Отказ доступа запоминается: раздел входа показывается сразу, а не только
    // после нажатия кнопки, которая ничего не сохранит.
    // Исключение — сама проверка прав: её отказ означает «не вошёл», и
    // отмечать его как «права отобрали» незачем.
    if (String(path).indexOf('/api/auth/') !== 0) {
      sessionBlocked = true;
      sessionAuthenticated = false;
      renderAccount();
    }
  }
  if (!response.ok && data && !data.error) {
    data.error = t('status code {code}', { code: response.status });
  }
  return data;
}

/**
 * Загрузить словарь переводов. Ошибка загрузки не отменяет панель:
 * без словаря показывается английский текст, а не пустая страница.
 */
async function loadLanguages() {
  const data = await api('/api/i18n');
  if (data && data.ok) {
    I18N.languages = data.languages || ['en'];
    I18N.defaultLanguage = data.default || 'en';
    I18N.titles = data.titles || { en: 'English' };
    I18N.catalogue = data.catalogue || {};
    I18N.loaded = true;
  }
  I18N.current = preferredLanguage();
}

function applyLanguage(lang) {
  I18N.current = I18N.languages.indexOf(lang) >= 0 ? lang : I18N.defaultLanguage;
  try { window.localStorage.setItem(LANG_KEY, I18N.current); } catch (e) { /* приватный режим */ }
  document.documentElement.lang = I18N.current;
  document.title = t('RAGRAF — control panel');
  // Текст, который браузер показывает до первого запроса, тоже переводится:
  // иначе до ответа сервера видны английские подписи, а после — другие.
  document.querySelectorAll('[data-i18n]').forEach(function (el) {
    el.textContent = t(el.getAttribute('data-i18n'));
  });
  // Доступное имя иконочной кнопки — тоже текст интерфейса: без него кнопка
  // читается экранной читалкой как «кнопка» без названия.
  document.querySelectorAll('[data-i18n-aria]').forEach(function (el) {
    el.setAttribute('aria-label', t(el.getAttribute('data-i18n-aria')));
  });
  // Подпись кнопки темы тоже переводится: она собрана из ключей словаря.
  updateThemeButton();
}

function fillLanguageSelect() {
  const select = document.getElementById('lang-select');
  if (!select) return;
  select.innerHTML = I18N.languages.map(function (code) {
    const title = I18N.titles[code] || code;
    return '<option value="' + esc(code) + '"'
      + (code === I18N.current ? ' selected' : '') + '>' + esc(title) + '</option>';
  }).join('');
  select.addEventListener('change', function () {
    applyLanguage(select.value);
    if (currentState) render();
  });
}

async function loadToken() {
  // Токен сессии сохраняется между обновлениями страницы: сервер слушает только
  // localhost, и повторно печатать его администратору не нужно. Сам секрет
  // сервер не отдаёт (`/api/token` сообщает лишь состояние доступа), поэтому
  // токен берётся только из памяти браузера — его вводит владелец.
  try { accessToken = window.localStorage.getItem(TOKEN_KEY) || ''; } catch (e) { accessToken = ''; }
  const data = await api('/api/token');
  if (data && data.ok && data.auth) {
    currentState = currentState || {};
    currentState.auth = data.auth;
    // Парольная сессия живёт в cookie и токена не требует.
    if (data.auth.password_set) sessionBlocked = false;
  }
  // Права подтверждает сервер. Сохранённый токен мог устареть: панель печатает
  // новый при каждом запуске, и прежний перестаёт действовать.
  const accepted = await verifySession();
  if (!accepted && accessToken) {
    forgetToken();
    showAuthMessage(t('Token rejected: this value is not valid for this panel. Take it from .ragraf/admin/session_key or from the start-up output.'), 'bad');
  }
}

/* --------------------------------------------------------------------------
   Примитивы разметки
   --------------------------------------------------------------------------
   Все они возвращают СТРОКУ разметки: страница собирается целиком и
   подставляется в контейнер. Значения из данных проходят через `esc`, поэтому
   ни одно поле ответа не может стать разметкой.
   -------------------------------------------------------------------------- */

const TONES = ['ok', 'warn', 'bad', 'info', 'primary', 'neutral'];
const CHIP_ICONS = { ok: 'check', warn: 'alert', bad: 'octagon', info: 'info', primary: 'spark', neutral: 'dot' };
const CHIP_ICON_SW = { ok: 2.4, warn: 2, bad: 2, info: 2, primary: 1.8, neutral: 3 };

/** Подпись состояния: цвет + значок + текст. Цвет без текста не показывается. */
function chip(text, tone, opts) {
  const o = opts || {};
  const t2 = TONES.indexOf(tone) >= 0 ? tone : 'neutral';
  const cls = ['chip', 'chip--' + t2];
  if (o.mono) cls.push('chip--mono');
  if (o.cls) cls.push(o.cls);
  const body = (o.icon === false ? '' : icon(o.icon || CHIP_ICONS[t2], 'h-3 w-3', CHIP_ICON_SW[t2]))
    + '<span>' + esc(text) + '</span>';
  return '<span class="' + cls.join(' ') + '"' + (o.title ? ' title="' + esc(o.title) + '"' : '') + '>' + body + '</span>';
}

/** Кнопка. Иерархия одна на всю панель: primary / secondary / ghost / danger. */
function button(opts) {
  const o = opts || {};
  const cls = ['btn'];
  if (o.v && o.v !== 'secondary') cls.push('btn--' + o.v);
  if (o.s) cls.push('btn--' + o.s);
  if (o.icon && !o.label) cls.push('btn--icon');
  if (o.cls) cls.push(o.cls);
  const attrs = [
    'type="button"',
    'class="' + cls.join(' ') + '"',
  ];
  if (o.id) attrs.push('id="' + esc(o.id) + '"');
  if (o.action) attrs.push('data-action="' + esc(o.action) + '"');
  Object.keys(o.data || {}).forEach(function (key) {
    attrs.push('data-' + key + '="' + esc(o.data[key]) + '"');
  });
  if (o.disabled) attrs.push('disabled');
  if (o.title) attrs.push('title="' + esc(o.title) + '"');
  if (o.aria) attrs.push(o.aria);
  const body = (o.icon ? icon(o.icon, o.iconSize || 'h-3.5 w-3.5') : '')
    + (o.label ? '<span>' + esc(o.label) + '</span>' : '');
  return '<button ' + attrs.join(' ') + '>' + body + '</button>';
}

/** Карточка: шапка с названием, пояснением и действиями плюс тело. */
function card(opts) {
  const o = opts || {};
  const head = (o.title || o.actions)
    ? '<div class="card__head">'
      + '<div class="card__headtext">'
      + (o.icon ? '<span class="card__icon">' + icon(o.icon, 'h-4 w-4') + '</span>' : '')
      + '<div class="min-w-0">'
      + '<h2 class="card__title">' + esc(o.title || '') + '</h2>'
      + (o.desc ? '<p class="card__desc">' + esc(o.desc) + '</p>' : '')
      + '</div></div>'
      + (o.actions ? '<div class="card__actions">' + o.actions + '</div>' : '')
      + '</div>'
    : '';
  const body = o.flush
    ? '<div class="card__body--flush">' + (o.body || '') + '</div>'
    : (o.body ? '<div class="card__body">' + o.body + '</div>' : '');
  const foot = o.foot ? '<div class="card__foot">' + o.foot + '</div>' : '';
  return '<section class="card' + (o.wide ? ' card--wide' : '') + (o.cls ? ' ' + o.cls : '') + '">'
    + head + body + foot + '</section>';
}

/** Показатель: значение, подпись и пояснение. Карточка — только для чисел. */
function metric(opts) {
  const o = opts || {};
  const tone = o.tone && o.tone !== 'neutral' ? ' metric--' + o.tone : '';
  return '<div class="metric' + tone + '">'
    + '<div class="metric__label">' + (o.icon ? icon(o.icon, 'h-3.5 w-3.5') : '')
    + '<span class="truncate">' + esc(o.label || '') + '</span></div>'
    + '<div class="metric__value"' + (o.title ? ' title="' + esc(o.title) + '"' : '') + '>'
    + esc(o.value) + '</div>'
    + (o.note ? '<div class="metric__note">' + esc(o.note) + '</div>' : '')
    + '</div>';
}

/** Состояние раздела словами и значком: так выглядит loading / empty / error. */
function callout(opts) {
  const o = opts || {};
  return '<div class="callout callout--' + (o.tone || 'neutral') + '">'
    + icon(o.icon || (o.tone === 'bad' ? 'octagon' : o.tone === 'warn' ? 'alert'
      : o.tone === 'ok' ? 'check' : 'info'), 'h-[18px] w-[18px]', 1.9)
    + '<div class="min-w-0 flex-1">'
    + '<p class="callout__title">' + esc(o.title || '') + '</p>'
    + (o.desc ? '<p class="callout__desc">' + esc(o.desc) + '</p>' : '')
    + '</div>'
    + (o.action ? '<div class="shrink-0">' + o.action + '</div>' : '')
    + '</div>';
}

/** Отказ по правам: одна формулировка на все разделы. */
function deniedCallout() {
  return callout({
    tone: 'warn', icon: 'lock', title: t('Write actions are unavailable'),
    desc: t('Sign in with the administrator password or paste the session token printed when the panel started.'),
  });
}

function progressHtml(value, tone, label) {
  const pct = Math.min(100, Math.max(0, Number(value) || 0));
  const cls = 'progress__bar' + (tone && tone !== 'primary' ? ' progress__bar--' + tone : '');
  return '<div class="progress" role="progressbar" aria-valuenow="' + Math.round(pct)
    + '" aria-valuemin="0" aria-valuemax="100" aria-label="' + esc(label || t('progress')) + '">'
    + '<div class="' + cls + '" style="width:' + pct.toFixed(1) + '%"></div></div>';
}

/** Переключатель: дорожка рисуется здесь, поле остаётся доступным с клавиатуры. */
function switchHtml(opts) {
  const o = opts || {};
  return '<label class="switch"' + (o.title ? ' title="' + esc(o.title) + '"' : '') + '>'
    + '<input type="checkbox"' + (o.id ? ' id="' + esc(o.id) + '"' : '')
    + (o.action ? ' data-action="' + esc(o.action) + '"' : '')
    + Object.keys(o.data || {}).map(function (key) {
      return ' data-' + key + '="' + esc(o.data[key]) + '"';
    }).join('')
    + (o.checked ? ' checked' : '') + (o.disabled ? ' disabled' : '') + '>'
    + '<span class="switch__track"></span>'
    + (o.label ? '<span>' + esc(o.label) + '</span>' : '')
    + '</label>';
}

/** Вкладки: состав тот же, что у панели, поэтому и разметка одна.
 *
 *  Разметка следует набору ролей tablist/tab: у каждой вкладки есть `id` и
 *  `aria-controls` на свою панель, а `tabindex` — «бегающий» (0 у выбранной,
 *  -1 у остальных). Неактивная вкладка при этом достижима стрелками — обработка
 *  стрелок ниже в общем обработчике клавиатуры. Без неё `tabindex="-1"` сделал
 *  бы её недостижимой с клавиатуры вовсе. */
function tabsHtml(opts) {
  const o = opts || {};
  return '<div class="tabs" role="tablist" aria-label="' + esc(o.label || t('Sections')) + '">'
    + o.items.map(function (item) {
      const active = item.k === o.value;
      return '<button type="button" class="tab" role="tab" id="tab-' + esc(o.group) + '-' + esc(item.k) + '"'
        + ' aria-selected="' + (active ? 'true' : 'false') + '"'
        + (o.panel ? ' aria-controls="' + esc(o.panel) + '"' : '')
        + ' tabindex="' + (active ? '0' : '-1') + '" data-action="tab" data-group="' + esc(o.group) + '"'
        + ' data-tab="' + esc(item.k) + '">'
        + (item.icon ? icon(item.icon, 'h-3.5 w-3.5') : '')
        + '<span>' + esc(item.label) + '</span>'
        + (item.count !== undefined && item.count !== null
          ? '<span class="tab__count">' + esc(item.count) + '</span>' : '')
        + '</button>';
    }).join('')
    + '</div>';
}

function emptyHtml(opts) {
  const o = opts || {};
  return '<div class="empty">' + icon(o.icon || 'search', 'h-6 w-6', 1.6)
    + '<p class="empty__title">' + esc(o.title || t('no data')) + '</p>'
    + (o.desc ? '<p class="empty__desc">' + esc(o.desc) + '</p>' : '')
    + (o.action ? '<div>' + o.action + '</div>' : '')
    + '</div>';
}

function skeletonHtml(cls) {
  return '<div class="skeleton ' + esc(cls || 'h-24') + '"></div>';
}

/** Копируемое значение: щелчок кладёт строку в буфер обмена. */
function copyValue(value, opts) {
  const o = opts || {};
  const shown = o.shown || value;
  return '<button type="button" class="copy' + (o.cls ? ' ' + o.cls : '') + '"'
    + ' data-action="copy" data-value="' + esc(value) + '"'
    + ' title="' + esc(t('Copy') + ': ' + value) + '"'
    + ' aria-label="' + esc(t('Copy') + ': ' + shown) + '">'
    + '<span class="copy__text">' + esc(shown) + '</span>'
    + icon('copy', 'h-3 w-3', 1.8)
    + '</button>';
}

function codeBlock(title, code) {
  return '<div class="codeblock">'
    + '<div class="codeblock__head"><span class="codeblock__title">' + esc(title) + '</span>'
    + button({ v: 'ghost', s: 'xs', icon: 'copy', label: t('Copy'), action: 'copy', data: { value: code } })
    + '</div>'
    + '<pre><code>' + esc(code) + '</code></pre>'
    + '</div>';
}

/** Поле поиска: имя нужно для доступности, подсказка — для человека.
 *  `id` выводится из имени действия: поле участвует в живом поиске, и после
 *  перерисовки фокус с позицией курсора возвращаются именно по нему. */
function searchField(opts) {
  const o = opts || {};
  const id = o.id || ('f-' + String(o.action || 'search'));
  return '<div class="search-field' + (o.cls ? ' ' + o.cls : '') + '">'
    + icon('search', 'search-field__icon h-4 w-4')
    + '<input type="search" id="' + esc(id) + '" data-action="' + esc(o.action) + '" data-live="1"'
    + Object.keys(o.data || {}).map(function (key) {
      return ' data-' + key + '="' + esc(o.data[key]) + '"';
    }).join('')
    + ' value="' + esc(o.value || '') + '" placeholder="' + esc(o.placeholder || '') + '"'
    + ' aria-label="' + esc(o.aria || o.placeholder || t('Search')) + '">'
    + '</div>';
}

/** Заголовок раздела: название, пояснение и действия. */
function pageHead(opts) {
  const o = opts || {};
  return '<div class="page-head">'
    + '<div class="page-head__text">'
    + '<h1 class="page-head__title">' + esc(o.title) + '</h1>'
    + (o.desc ? '<p class="page-head__desc">' + esc(o.desc) + '</p>' : '')
    + '</div>'
    + (o.actions ? '<div class="page-head__actions">' + o.actions + '</div>' : '')
    + '</div>';
}

/* --- таблицы: сортировка, пагинация, плотность ---------------------------- */

/** Заголовок столбца с сортировкой. Сортирует сервер... нет: список уже на руках. */
function th(label, opts) {
  const o = opts || {};
  const cls = [];
  if (o.num) cls.push('num');
  if (o.cls) cls.push(o.cls);
  if (!o.sort) {
    return '<th scope="col"' + (cls.length ? ' class="' + cls.join(' ') + '"' : '') + '>'
      + esc(label) + '</th>';
  }
  const active = o.sort.key === o.key;
  const dir = active ? o.sort.dir : '';
  const aria = active ? (dir === 'asc' ? 'ascending' : 'descending') : 'none';
  return '<th scope="col" aria-sort="' + aria + '"'
    + (cls.length ? ' class="' + cls.join(' ') + '"' : '') + '>'
    + '<button type="button" class="th-sort" aria-sort="' + aria + '" data-action="sort"'
    + ' data-scope="' + esc(o.scope) + '" data-key="' + esc(o.key) + '">'
    + '<span>' + esc(label) + '</span>' + icon('chevronD', 'h-3 w-3', 2.2)
    + '</button></th>';
}

/** Отсортировать строки по значению, которое достаёт `get`. */
function sortRows(rows, sort, get) {
  const value = get[sort.key];
  if (!value) return rows.slice();
  return rows.slice().sort(function (a, b) {
    const x = value(a);
    const y = value(b);
    let result;
    if (typeof x === 'string' || typeof y === 'string') {
      result = String(x).localeCompare(String(y), localeTag());
    } else {
      result = (Number(x) || 0) - (Number(y) || 0);
    }
    return sort.dir === 'asc' ? result : -result;
  });
}

/** Пагинация: сколько всего, что показано и переход по страницам. */
function pagerHtml(scope, total, page, perPage) {
  const pages = Math.max(1, Math.ceil(total / perPage));
  const current = Math.min(page, pages - 1);
  const from = total ? current * perPage + 1 : 0;
  const to = Math.min(total, (current + 1) * perPage);
  const numbers = [];
  const first = Math.max(0, Math.min(current - 2, pages - 5));
  for (let i = first; i < Math.min(pages, first + 5); i += 1) numbers.push(i);
  return '<div class="card__foot">'
    + '<label class="inline small muted">' + esc(t('Per page'))
    + ' <select data-action="per-page" data-scope="' + esc(scope) + '" aria-label="' + esc(t('Per page')) + '">'
    + [10, 25, 50].map(function (n) {
      return '<option value="' + n + '"' + (n === perPage ? ' selected' : '') + '>' + n + '</option>';
    }).join('')
    + '</select></label>'
    + '<span class="mono tnum small muted">' + esc(from + '–' + to + ' / ' + num(total)) + '</span>'
    + '<span class="toolbar__spacer"></span>'
    + button({
      v: 'secondary', s: 'icon-sm', icon: 'chevronL', aria: 'aria-label="' + esc(t('Previous page')) + '"',
      action: 'page', data: { scope: scope, page: current - 1 }, disabled: current <= 0,
    })
    + numbers.map(function (i) {
      return '<button type="button" class="btn ' + (i === current ? 'btn--primary' : 'btn--ghost')
        + ' btn--icon-sm" data-action="page" data-scope="' + esc(scope) + '" data-page="' + i + '"'
        + ' aria-label="' + esc(t('Page {n}', { n: i + 1 })) + '"'
        + (i === current ? ' aria-current="page"' : '') + '>'
        + '<span class="mono tnum">' + (i + 1) + '</span></button>';
    }).join('')
    + button({
      v: 'secondary', s: 'icon-sm', icon: 'chevronR', aria: 'aria-label="' + esc(t('Next page')) + '"',
      action: 'page', data: { scope: scope, page: current + 1 }, disabled: current >= pages - 1,
    })
    + '</div>';
}

/* --------------------------------------------------------------------------
   Графики
   --------------------------------------------------------------------------
   Без библиотек: панель работает офлайн, а нужны только столбцы и кольцо.
   Столбцы — разметкой (подписи остаются текстом и читаются экранной читалкой),
   кольцо — SVG: дуга иначе не рисуется.
   -------------------------------------------------------------------------- */

const TONE_VAR = { ok: 'var(--ok)', warn: 'var(--warn)', bad: 'var(--bad)', info: 'var(--info)', primary: 'var(--primary)', neutral: 'var(--foreground-3)' };

/** Горизонтальные столбцы: подпись, полоса, значение. */
function barRows(items, opts) {
  const o = opts || {};
  if (!items.length) return emptyHtml({ icon: 'activity', title: o.empty || t('no data') });
  const max = items.reduce(function (top, item) { return Math.max(top, Number(item.value) || 0); }, 0) || 1;
  return '<ul class="bars">' + items.map(function (item) {
    const pct = ((Number(item.value) || 0) / max) * 100;
    return '<li class="bar-row">'
      + '<span class="bar-row__label mono" title="' + esc(item.label) + '">' + esc(item.label) + '</span>'
      + '<span class="bar-row__track"><span class="bar-row__fill" style="width:' + pct.toFixed(2)
      + '%;background:' + (TONE_VAR[item.tone] || TONE_VAR.primary) + '"></span></span>'
      + '<span class="bar-row__value mono tnum">' + esc(item.text !== undefined ? item.text : num(item.value)) + '</span>'
      + '</li>';
  }).join('') + '</ul>';
}

/** Кольцо состояний с легендой: доли видны и цифрами, а не только цветом. */
function donut(items, centre) {
  const list = items.filter(function (item) { return (Number(item.value) || 0) > 0; });
  const total = list.reduce(function (sum, item) { return sum + (Number(item.value) || 0); }, 0);
  const radius = 42;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;
  const arcs = list.map(function (item) {
    const length = total ? ((Number(item.value) || 0) / total) * circumference : 0;
    const arc = '<circle class="donut__ring" cx="60" cy="60" r="' + radius + '" stroke="'
      + (TONE_VAR[item.tone] || TONE_VAR.neutral) + '" stroke-dasharray="' + length.toFixed(2) + ' '
      + (circumference - length).toFixed(2) + '" stroke-dashoffset="' + (-offset).toFixed(2) + '"/>';
    offset += length;
    return arc;
  }).join('');
  return '<div class="donut">'
    + '<svg viewBox="0 0 120 120" role="img" aria-label="' + esc(t('Index states')) + '">'
    + '<g transform="rotate(-90 60 60)">'
    + '<circle class="donut__ring" cx="60" cy="60" r="' + radius + '" stroke="var(--card-3)"/>'
    + arcs + '</g>'
    + '<text x="60" y="58" text-anchor="middle" class="chart__value" style="font-size:1.25rem;fill:var(--foreground)">'
    + esc(centre === undefined ? String(total) : centre) + '</text>'
    + '<text x="60" y="74" text-anchor="middle" class="chart__value">' + esc(t('Total')) + '</text>'
    + '</svg>'
    + '<ul class="legend">' + list.map(function (item) {
      return '<li class="legend__row">'
        + '<span class="legend__dot" style="background:' + (TONE_VAR[item.tone] || TONE_VAR.neutral) + '"></span>'
        + '<span>' + esc(item.label) + '</span>'
        + '<span class="legend__value">' + num(item.value) + '</span>'
        + '</li>';
    }).join('') + '</ul></div>';
}

/* --------------------------------------------------------------------------
   Тема оформления
   --------------------------------------------------------------------------
   Выбор пользователя важнее системной настройки: 'system' | 'light' | 'dark'.
   Значение живёт в localStorage и переживает перезагрузку; до первого выбора
   тему задаёт системная настройка. Тот же выбор применяет скрипт в <head>,
   чтобы страница не мигала светлым на тёмной системе.
   -------------------------------------------------------------------------- */

const THEME_KEY = 'ragraf-admin-theme';
const THEME_MODES = ['system', 'light', 'dark'];
const THEME_ICONS = { system: 'monitor', light: 'sun', dark: 'moon' };

/** Системная настройка: что выбрал бы браузер без нашего вмешательства. */
function systemTheme() {
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
    ? 'dark' : 'light';
}

function savedTheme() {
  let value = '';
  try { value = window.localStorage.getItem(THEME_KEY) || ''; } catch (e) { value = ''; }
  return THEME_MODES.indexOf(value) >= 0 ? value : 'system';
}

function resolvedTheme() {
  const mode = savedTheme();
  return mode === 'system' ? systemTheme() : mode;
}

function themeName(mode) {
  if (mode === 'dark') return t('Dark');
  if (mode === 'light') return t('Light');
  return t('System theme');
}

/** Применить тему и обновить кнопку в шапке. */
function applyTheme() {
  document.documentElement.setAttribute('data-theme', resolvedTheme());
  updateThemeButton();
}

/** Кнопка показывает ДЕЙСТВУЮЩУЮ тему, а выбор из трёх режимов — в меню. */
function updateThemeButton() {
  const button = document.getElementById('theme-toggle');
  if (!button) return;
  const text = t('Theme') + ': ' + themeName(savedTheme());
  button.setAttribute('title', text);
  button.setAttribute('aria-label', text);
  button.innerHTML = icon(THEME_ICONS[resolvedTheme()] || 'monitor', 'h-[17px] w-[17px]');
}

function setThemeMode(mode) {
  const value = THEME_MODES.indexOf(mode) >= 0 ? mode : 'system';
  try { window.localStorage.setItem(THEME_KEY, value); } catch (e) { /* приватный режим */ }
  applyTheme();
  renderThemeMenu();
  // Выбор сделан — меню закрывается: иначе оно закрывало бы собой шапку.
  const menu = document.getElementById('theme-menu');
  const toggle = document.getElementById('theme-toggle');
  if (menu) menu.hidden = true;
  if (toggle) toggle.setAttribute('aria-expanded', 'false');
  toast({ tone: 'info', title: t('Theme') + ': ' + themeName(value) });
}

/** Круговой перебор — из палитры команд: там на один пункт одна строка. */
function cycleTheme() {
  const current = savedTheme();
  const next = current === 'system' ? 'light' : (current === 'light' ? 'dark' : 'system');
  setThemeMode(next);
}

function renderThemeMenu() {
  const menu = document.getElementById('theme-menu');
  if (!menu) return;
  const current = savedTheme();
  menu.innerHTML = '<p class="menu__label">' + esc(t('Theme')) + '</p>'
    + THEME_MODES.map(function (mode) {
      return '<button type="button" class="menu__item" role="menuitemradio"'
        + ' aria-checked="' + (mode === current ? 'true' : 'false') + '"'
        + ' data-action="theme" data-theme="' + mode + '">'
        + icon(THEME_ICONS[mode], 'h-3.5 w-3.5')
        + '<span class="flex-1">' + esc(themeName(mode)) + '</span>'
        + (mode === current ? icon('check', 'h-3.5 w-3.5 text-[var(--primary)]', 2.4) : '')
        + '</button>';
    }).join('');
}

/** Следить за системной темой, пока выбор пользователя — «как в системе». */
function installSystemThemeWatch() {
  if (!window.matchMedia) return;
  const query = window.matchMedia('(prefers-color-scheme: dark)');
  if (query.addEventListener) {
    query.addEventListener('change', function () {
      if (savedTheme() === 'system') applyTheme();
    });
  }
}

/* --------------------------------------------------------------------------
   Плотность строк
   --------------------------------------------------------------------------
   Одна настройка на всю панель: у плотной таблицы меньше вертикальный отступ.
   Форма значения та же, что у темы: атрибут на <html> и запись в localStorage.
   -------------------------------------------------------------------------- */

function savedDensity() {
  let value = '';
  try { value = window.localStorage.getItem(DENSITY_KEY) || ''; } catch (e) { value = ''; }
  return value === 'compact' ? 'compact' : 'comfortable';
}

function applyDensity() {
  document.documentElement.setAttribute('data-density', savedDensity());
}

/** Подпись плотности. Ключ словаря выбирается ЛИТЕРАЛОМ в каждой ветви:
 *  вызов вида `t(условие ? 'A' : 'B')` статически не проверяется, и пропущенный
 *  перевод в русской панели был бы не виден никому. */
function densityLabel(mode) {
  return mode === 'compact' ? t('Compact') : t('Comfortable');
}

function toggleDensity() {
  const next = savedDensity() === 'compact' ? 'comfortable' : 'compact';
  try { window.localStorage.setItem(DENSITY_KEY, next); } catch (e) { /* приватный режим */ }
  applyDensity();
  render();
  toast({ tone: 'info', title: t('Density') + ': ' + densityLabel(next) });
}

/* --------------------------------------------------------------------------
   Страж редактирования
   --------------------------------------------------------------------------
   Полная перерисовка контейнера уничтожает раскрытый список и уже введённый
   текст. Поэтому пока фокус внутри поля формы, страница не рисуется, а
   запрошенная перерисовка откладывается до ухода фокуса. Это же условие
   снимает и «сбрасывание через секунду»: причина была в том, что опрос
   состояния рисовал страницу заново на каждом цикле.
   -------------------------------------------------------------------------- */

let editing = false;
let pendingRender = false;

function isFormControl(element) {
  return !!(element && element.matches
    && element.matches('input, select, textarea, [contenteditable="true"]'));
}

/** Поле «живого» поиска: список обновляется прямо во время набора, поэтому
 *  страховка «не рисовать при фокусе» на него не распространяется — иначе
 *  владелец видел бы устаревший список до ухода из поля. Фокус и позиция
 *  курсора в таком поле восстанавливаются после перерисовки. */
function isLiveField(element) {
  return !!(element && element.matches && element.matches('[data-live]'));
}

function guardedControl(element) {
  return isFormControl(element) && !isLiveField(element);
}

function installEditGuard() {
  window.addEventListener('focusin', function (event) {
    if (guardedControl(event.target)) editing = true;
  });
  window.addEventListener('focusout', function () {
    window.setTimeout(function () {
      editing = guardedControl(document.activeElement);
      if (!editing && pendingRender) { pendingRender = false; render(); }
    }, 150);
  });
}

/** Значения полей перед перерисовкой, чтобы введённое не исчезло. */
function snapshotFormValues() {
  const saved = [];
  document.querySelectorAll('input, select, textarea').forEach(function (el) {
    if (!el.id && !el.getAttribute('data-key') && !el.getAttribute('data-project')) return;
    saved.push({
      id: el.id || '',
      key: el.getAttribute('data-key') || '',
      project: el.getAttribute('data-project') || '',
      value: el.value,
    });
  });
  return saved;
}

function restoreFormValues(saved) {
  (saved || []).forEach(function (item) {
    let el = null;
    if (item.id) el = document.getElementById(item.id);
    if (!el && item.key) {
      el = document.querySelector(item.project
        ? '[data-key="' + item.key + '"][data-project="' + item.project + '"]'
        : '[data-key="' + item.key + '"]');
    }
    if (el && el.value !== item.value) el.value = item.value;
  });
}

/* --------------------------------------------------------------------------
   Тосты
   --------------------------------------------------------------------------
   Результат действия виден и в разделе (плашка), и всплывающим уведомлением:
   первое остаётся на месте как запись, второе заметно, если человек смотрит в
   другой конец страницы.
   -------------------------------------------------------------------------- */

const TOAST_ICONS = { ok: 'check', warn: 'alert', bad: 'octagon', info: 'info' };
let toasts = [];

function toast(opts) {
  const o = opts || {};
  const tone = o.tone || 'info';
  const id = 't' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  toasts = toasts.concat([{ id: id, title: String(o.title || ''), desc: o.desc || '', tone: tone }]);
  renderToasts();
  // Уведомление не должно жить вечно, но и исчезать мгновенно нельзя: 6 с
  // хватает, чтобы прочитать строку и пояснение под ней.
  window.setTimeout(function () {
    toasts = toasts.filter(function (item) { return item.id !== id; });
    renderToasts();
  }, 6000);
}

function renderToasts() {
  const box = document.getElementById('toasts');
  if (!box) return;
  box.innerHTML = toasts.map(function (item) {
    return '<div class="toast toast--' + item.tone + '" role="status" aria-live="polite">'
      + '<span class="toast__icon">' + icon(TOAST_ICONS[item.tone] || 'info', 'h-3 w-3', 2.4) + '</span>'
      + '<div class="toast__body"><p class="toast__title">' + esc(item.title) + '</p>'
      + (item.desc ? '<p class="toast__desc">' + esc(item.desc) + '</p>' : '')
      + '</div></div>';
  }).join('');
}

/* --------------------------------------------------------------------------
   Диалог подтверждения
   --------------------------------------------------------------------------
   Разрушающее действие не выполняется без явного согласия, и согласие
   спрашивается ДИАЛОГОМ, а не системным окном браузера: системное окно не
   переводится словарём панели и не показывает, что именно будет удалено.
   -------------------------------------------------------------------------- */

let dialogState = null;
let dialogResolve = null;

/** Спросить подтверждение. Возвращает обещание: true — человек согласился. */
function confirmDialog(opts) {
  const o = opts || {};
  return new Promise(function (resolve) {
    dialogState = {
      tone: o.tone || 'primary',
      title: o.title || t('Confirm'),
      desc: o.desc || '',
      confirm: o.confirm || t('Confirm'),
      cancel: o.cancel || t('Cancel'),
      irreversible: !!o.irreversible,
    };
    dialogResolve = resolve;
    overlayReturnFocus = document.activeElement;
    renderDialog();
  });
}

function closeDialog(answer) {
  const resolve = dialogResolve;
  dialogState = null;
  dialogResolve = null;
  const box = document.getElementById('dialog');
  if (box) box.innerHTML = '';
  restoreOverlayFocus();
  if (resolve) resolve(!!answer);
}

/** Удержать фокус внутри слоя: Tab и Shift+Tab ходят по кругу, а не уходят
 *  в страницу под слоем. Без этого клавиатурный пользователь проваливается
 *  в невидимое содержимое и теряет место, где находится. */
function keepFocusInside(event, scope) {
  const items = scope.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]),'
    + ' select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])');
  if (!items.length) {
    event.preventDefault();
    return;
  }
  const first = items[0];
  const last = items[items.length - 1];
  if (!scope.contains(document.activeElement)) {
    event.preventDefault();
    first.focus();
    return;
  }
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function renderDialog() {
  const box = document.getElementById('dialog');
  if (!box) return;
  if (!dialogState) { box.innerHTML = ''; return; }
  const d = dialogState;
  const bad = d.tone === 'bad';
  box.innerHTML = '<div class="overlay overlay--center">'
    + '<div class="dialog" role="alertdialog" aria-modal="true" aria-labelledby="dialog-title"'
    + ' aria-describedby="dialog-desc">'
    + '<div class="dialog__head">'
    + '<div class="min-w-0"><h2 class="dialog__title" id="dialog-title">' + esc(d.title) + '</h2>'
    + (d.desc ? '<p class="dialog__desc" id="dialog-desc">' + esc(d.desc) + '</p>' : '')
    + '</div>'
    + button({ v: 'ghost', s: 'icon-sm', icon: 'x', aria: 'aria-label="' + esc(t('Close')) + '"', action: 'dialog-cancel' })
    + '</div>'
    + '<div class="dialog__body">'
    + callout({
      tone: bad ? 'bad' : 'info', icon: bad ? 'octagon' : 'info',
      title: bad
        ? t('The action is irreversible: the index data will be deleted.')
        : t('The task will be queued and performed with a full rebuild.'),
      desc: bad
        ? t('The development files are not changed. The index will have to be built again.')
        : t('Scan, AST, reference resolution, code property graph and vector encoding.'),
    })
    + '</div>'
    + '<div class="dialog__foot">'
    + button({ v: 'ghost', s: 'sm', label: d.cancel, action: 'dialog-cancel' })
    + button({
      v: bad ? 'destructive' : 'primary', s: 'sm',
      icon: bad ? 'trash' : 'check', label: d.confirm, action: 'dialog-ok', id: 'dialog-ok',
    })
    + '</div></div></div>';
  const ok = document.getElementById('dialog-ok');
  if (ok) ok.focus();
}

/* --------------------------------------------------------------------------
   Палитра команд
   --------------------------------------------------------------------------
   Ctrl+K открывает список разделов, действий и разработок. Пункты строятся из
   того же состояния, что и страница: отдельного списка команд нет, поэтому
   палитра не может разойтись с интерфейсом.
   -------------------------------------------------------------------------- */

let paletteOpen = false;
let paletteQuery = '';
let paletteIndex = 0;

function paletteItems() {
  const items = [];
  SECTIONS.forEach(function (section) {
    items.push({
      group: t('Sections'), icon: section.icon, label: sectionLabel(section.id),
      run: function () { go(section.id); },
    });
  });
  const stale = staleEntries();
  items.push({
    group: t('Actions'), icon: 'refresh',
    label: t('Reindex all out of date ({count})', { count: num(stale.length) }),
    hint: num(stale.length),
    run: function () { if (stale.length) doReindex(stale.map(function (e) { return e.category; })); },
  });
  items.push({
    group: t('Actions'), icon: THEME_ICONS[resolvedTheme()] || 'monitor',
    label: t('Theme') + ': ' + themeName(resolvedTheme()), run: cycleTheme,
  });
  items.push({
    group: t('Actions'), icon: 'type', label: t('Language') + ': ' + (I18N.titles[I18N.current] || I18N.current),
    run: function () {
      const at = I18N.languages.indexOf(I18N.current);
      const next = I18N.languages[(at + 1) % I18N.languages.length];
      applyLanguage(next);
      fillLanguageSelect();
      render();
    },
  });
  items.push({ group: t('Actions'), icon: 'sliders', label: t('Density') + ': ' + densityLabel(savedDensity()), run: toggleDensity });
  items.push({ group: t('Actions'), icon: 'refresh', label: t('Refresh'), run: function () { refresh(true); } });
  (currentState && currentState.catalog && currentState.catalog.entries || [])
    .slice().sort(function (a, b) { return String(a.category).localeCompare(String(b.category), localeTag()); })
    .forEach(function (entry) {
      items.push({
        group: t('Developments'), icon: 'folder', label: String(entry.category),
        hint: stateLabel(entry).text,
        run: function () { UI.projects.q = String(entry.category); UI.projects.page = 0; go('catalog'); },
      });
    });
  return items;
}

//: Сколько пунктов палитра показывает до ввода. Значение выбрано так, чтобы
//: в него поместились ВСЕ разделы и ВСЕ действия: иначе «плотность» и
//: «обновить» находились бы только набором текста, то есть были бы скрыты от
//: того, кто не знает их названия. Разработки появляются по мере ввода — их
//: десятки, и в общий список они не помещаются.
const PALETTE_DEFAULT_LIMIT = 16;

function filteredPaletteItems() {
  const query = paletteQuery.trim().toLowerCase();
  const all = paletteItems();
  if (!query) return all.slice(0, PALETTE_DEFAULT_LIMIT);
  return all.filter(function (item) {
    return (item.label + ' ' + (item.hint || '') + ' ' + item.group).toLowerCase().indexOf(query) >= 0;
  }).slice(0, 26);
}

function openPalette() {
  paletteOpen = true;
  paletteQuery = '';
  paletteIndex = 0;
  overlayReturnFocus = document.activeElement;
  renderPalette();
  const input = document.getElementById('palette-input');
  if (input) input.focus();
}

/** Вернуть фокус тому, кто открыл слой. Слой — не место, где фокус теряется. */
function restoreOverlayFocus() {
  const el = overlayReturnFocus;
  overlayReturnFocus = null;
  if (el && el.focus && document.contains(el)) el.focus();
}

function closePalette() {
  paletteOpen = false;
  const box = document.getElementById('palette');
  if (box) box.innerHTML = '';
  restoreOverlayFocus();
}

function paletteMove(step) {
  const items = filteredPaletteItems();
  if (!items.length) return;
  paletteIndex = (paletteIndex + step + items.length) % items.length;
  renderPalette();
  const active = document.querySelector('[data-palette-index="' + paletteIndex + '"]');
  if (active && active.scrollIntoView) active.scrollIntoView({ block: 'nearest' });
}

function paletteRun() {
  const items = filteredPaletteItems();
  const item = items[paletteIndex];
  if (!item) return;
  closePalette();
  item.run();
}

function renderPalette() {
  const box = document.getElementById('palette');
  if (!box) return;
  if (!paletteOpen) { box.innerHTML = ''; return; }
  const items = filteredPaletteItems();
  if (paletteIndex >= items.length) paletteIndex = 0;
  let lastGroup = null;
  const list = items.map(function (item, index) {
    const head = item.group !== lastGroup
      ? '<p class="palette__group">' + esc(item.group) + '</p>' : '';
    lastGroup = item.group;
    return head + '<button type="button" class="palette__item" role="option"'
      + ' data-palette-index="' + index + '" aria-selected="' + (index === paletteIndex ? 'true' : 'false') + '">'
      + icon(item.icon, 'h-4 w-4')
      + '<span class="palette__label">' + esc(item.label) + '</span>'
      + (item.hint ? '<span class="palette__hint">' + esc(item.hint) + '</span>' : '')
      + '</button>';
  }).join('');
  box.innerHTML = '<div class="overlay overlay--top" data-action="palette-scrim">'
    + '<div class="dialog palette" role="dialog" aria-modal="true" aria-label="' + esc(t('Command palette')) + '">'
    + '<div class="palette__search">' + icon('search', 'h-4 w-4')
    + '<input id="palette-input" type="text" value="' + esc(paletteQuery) + '" autocomplete="off"'
    + ' placeholder="' + esc(t('Section, project or action…')) + '"'
    + ' aria-label="' + esc(t('Section, project or action…')) + '" role="combobox" aria-expanded="true"'
    + ' aria-controls="palette-list">'
    + '<kbd class="kbd">Esc</kbd></div>'
    + '<div class="palette__list" id="palette-list" role="listbox" aria-label="' + esc(t('Command palette')) + '">'
    + (list || emptyHtml({ icon: 'search', title: t('no data') }))
    + '</div>'
    + '<div class="palette__foot">'
    + '<span class="inline"><kbd class="kbd">↑↓</kbd>' + esc(t('navigation')) + '</span>'
    + '<span class="inline"><kbd class="kbd">↵</kbd>' + esc(t('run')) + '</span>'
    + '<span class="toolbar__spacer"></span>'
    + '<span class="inline"><kbd class="kbd">Ctrl K</kbd>' + esc(t('open')) + '</span>'
    + '</div></div></div>';
  const input = document.getElementById('palette-input');
  if (input) {
    input.addEventListener('input', function () {
      paletteQuery = input.value;
      paletteIndex = 0;
      const at = input.selectionStart;
      renderPalette();
      const again = document.getElementById('palette-input');
      if (again) { again.focus(); if (at !== null) again.setSelectionRange(at, at); }
    });
  }
}

/* --------------------------------------------------------------------------
   Кто вошёл: проверка прав и надпись в шапке
   --------------------------------------------------------------------------
   Прежде «вход» проверялся запросом состояния, а он проходит и без прав: любой
   ввод выглядел успешным, и только первая запись показывала отказ. Теперь права
   подтверждает сервер, а в шапке видно состояние — Guest или Admin с меню.
   -------------------------------------------------------------------------- */

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
    showAuthMessage(t('Token rejected: this value is not valid for this panel. Take it from .ragraf/admin/session_key or from the start-up output.'), 'bad');
  }
  renderAccount();
  return accepted;
}

function accountHtml() {
  const name = sessionAuthenticated ? t('Admin') : t('Guest');
  const state = sessionAuthenticated ? t('Signed in as administrator') : t('Not signed in');
  return '<button class="btn btn--ghost account__button" id="account-toggle" type="button"'
    + ' aria-haspopup="menu" aria-expanded="false" aria-label="'
    + esc(t('Account') + ': ' + name) + '" data-action="account-menu">'
    + '<span class="account__dot' + (sessionAuthenticated ? ' account__dot--ok' : '') + '"></span>'
    + '<span class="hidden max-w-[7rem] truncate sm:block">' + esc(name) + '</span>'
    + icon('chevronD', 'h-3.5 w-3.5 opacity-60')
    + '</button>'
    + '<div class="account__menu" id="account-menu" role="menu" hidden>'
    + '<p class="account__state">' + esc(state) + '</p>'
    + (sessionAuthenticated
        ? '<button class="account__item" id="account-out" type="button" role="menuitem" data-action="account-out">'
          + icon('logout', 'h-3.5 w-3.5') + '<span>' + esc(t('Sign out')) + '</span></button>'
        : '<div class="account__field"><input type="password" id="account-token-input"'
          + ' autocomplete="off" placeholder="' + esc(t('session token')) + '"'
          + ' aria-label="' + esc(t('session token')) + '">'
          + '<button class="btn btn--sm btn--primary" id="account-token-apply" type="button"'
          + ' data-action="account-token-apply">' + esc(t('Use token')) + '</button></div>')
    + '<button class="account__item" id="account-forget" type="button" role="menuitem"'
      + ' data-action="account-forget"' + (accessToken ? '' : ' hidden') + '>'
      + icon('trash', 'h-3.5 w-3.5') + '<span>' + esc(t('Forget token')) + '</span></button>'
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
}

/** Щелчок мимо меню и Escape закрывают его: одно правило на оба случая. */
function installAccountListeners() {
  if (accountListenersInstalled) return;
  accountListenersInstalled = true;
  document.addEventListener('click', function (event) {
    const box = document.getElementById('account');
    const menu = document.getElementById('account-menu');
    if (box && menu && !menu.hidden && !box.contains(event.target)) {
      closeAccountMenu();
    }
    // Меню темы — такое же всплывающее: мимо него щёлкнули — оно закрылось.
    const themeMenu = document.getElementById('theme-menu');
    const themeToggle = document.getElementById('theme-toggle');
    if (themeMenu && !themeMenu.hidden && themeToggle && !themeToggle.contains(event.target)
      && !themeMenu.contains(event.target)) {
      themeMenu.hidden = true;
      themeToggle.setAttribute('aria-expanded', 'false');
    }
  });
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    closeAccountMenu();
  });
}

function closeAccountMenu() {
  const menu = document.getElementById('account-menu');
  const toggle = document.getElementById('account-toggle');
  if (menu) menu.hidden = true;
  if (toggle) toggle.setAttribute('aria-expanded', 'false');
}

function accountMenuToggle() {
  const menu = document.getElementById('account-menu');
  const toggle = document.getElementById('account-toggle');
  if (!menu || !toggle) return;
  const willOpen = menu.hidden;
  menu.hidden = !willOpen;
  toggle.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
  if (willOpen) {
    const input = document.getElementById('account-token-input');
    if (input) input.focus();
  }
}

async function accountUseToken() {
  const input = document.getElementById('account-token-input');
  const accepted = await applySessionToken(input ? input.value : '');
  // При отказе меню НЕ перестраивается: оно закрывалось вместе с сообщением,
  // и нажатие выглядело как «ничего не произошло».
  if (accepted) {
    renderAccount();
    closeAccountMenu();
    await refresh();
  }
}

async function accountSignOut() {
  await api('/api/auth/logout', { method: 'POST', body: JSON.stringify({}) });
  forgetToken();
  sessionAuthenticated = false;
  sessionBlocked = false;
  closeAccountMenu();
  renderAccount();
  showAuthMessage(t('Signed out.'), 'ok');
  await refresh();
}

function accountForgetToken() {
  forgetToken();
  sessionAuthenticated = false;
  closeAccountMenu();
  renderAccount();
  showAuthMessage(t('Token forgotten.'), 'ok');
  render();
}

async function loginApply() {
  const field = document.getElementById('login-password');
  const data = await api('/api/auth/login', {
    method: 'POST', body: JSON.stringify({ password: field ? field.value : '' }),
  });
  const out = document.getElementById('login-result');
  if (out) {
    out.hidden = false;
    out.textContent = data.ok
      ? t('Signed in. Write actions are available.')
      : serverText(data.error || t('could not'));
  }
  if (data.ok) {
    // Парольная сессия подтверждается у сервера: без этого надпись в шапке
    // осталась бы Guest, хотя запись уже разрешена.
    sessionBlocked = false;
    await verifySession();
    renderAccount();
    await refresh();
  }
}

async function loginSignOut() {
  await api('/api/auth/logout', { method: 'POST', body: JSON.stringify({}) });
  forgetToken();
  sessionAuthenticated = false;
  sessionBlocked = false;
  renderAccount();
  showAuthMessage(t('Signed out.'), 'ok');
  await refresh();
}

/**
 * Раздел «Вход». Показывается, когда изменяющие действия не пройдут:
 * сервер требует либо парольную сессию, либо токен сессии с запуска панели.
 * Без этого раздела кнопки сохранения отказывали молча — отказ приходил
 * кодом 403, а панель не говорила, что с этим делать.
 */
function renderSession(state) {
  const auth = (state && state.auth) || {};
  const password = !!auth.password_set;
  const signedIn = !!accessToken && !sessionBlocked;
  // Скрывать блок можно только когда изменяющие действия заведомо доступны:
  // либо задан пароль (вход по нему), либо в браузере лежит принятый токен.
  // Иначе владелец должен видеть, что сохранение не сработает, ДО нажатия
  // кнопки, а не получать отказ кодом 403 без объяснения.
  if (password && !sessionBlocked) return '';
  if (signedIn) return '';

  const fields = '<div class="stack">'
    + (password
      ? '<div class="field"><label class="field__label" for="login-password">'
        + esc(t('Administrator password')) + '</label>'
        + '<div class="inline"><input type="password" id="login-password" autocomplete="current-password"'
        + ' placeholder="' + esc(t('administrator password')) + '">'
        + button({ v: 'secondary', s: 'md', icon: 'lock', label: t('Sign in'), action: 'login-apply', id: 'login-apply' })
        + button({ v: 'ghost', s: 'md', label: t('Sign out'), action: 'login-out', id: 'login-out' })
        + '</div></div>'
      : '<p class="small muted">' + esc(t('No administrator password is set: sign-in is '
        + 'possible only with the session token printed when the panel started.')) + '</p>')
    + '<div class="field"><label class="field__label" for="login-token">'
      + esc(t('Session token')) + '</label>'
      + '<div class="inline"><input type="password" id="login-token" autocomplete="off"'
      + ' placeholder="' + esc(t('session token')) + '">'
      + button({ v: 'primary', s: 'md', icon: 'key', label: t('Use token'), action: 'login-token-apply', id: 'login-token-apply' })
      + button({ v: 'ghost', s: 'md', icon: 'trash', label: t('Forget token'), action: 'login-token-clear', id: 'login-token-clear' })
      + '</div>'
      + '<p class="field__hint">' + esc(t('The value is printed at start-up and kept in .ragraf/admin/session_key, '
        + 'so it survives restarts.')) + '</p></div>'
    + '<p class="account__message" id="login-result" hidden></p>'
    + '</div>';

  return card({
    icon: 'lock', title: t('Sign in'),
    desc: t('Write actions are unavailable: sign in with the administrator '
      + 'password or paste the session token printed when the panel started.'),
    body: fields,
  });
}

/* --------------------------------------------------------------------------
   Состояние разработок: одна точка разбора ответа
   --------------------------------------------------------------------------
   Свежесть приходит тремя разными полями (`hash_matches`, `check_error`,
   отсутствие отметки), и толковать их в каждом разделе по-своему нельзя:
   иначе «отстал» в одном месте и «ждём данных» в другом.
   -------------------------------------------------------------------------- */

function catalogEntries() {
  return (currentState && currentState.catalog && currentState.catalog.entries) || [];
}

function detailMap() {
  return (currentState && currentState.detail) || {};
}

function detailNames() {
  return Object.keys(detailMap());
}

/** Состояние разработки: 'ok' | 'warn' | 'bad' | 'pending'. */
function entryState(entry) {
  if (!entry) return 'pending';
  if (entry.check_error) return 'bad';
  if (entry.hash_matches === true) return 'ok';
  if (entry.hash_matches === false) return 'warn';
  return 'pending';
}

/** Подпись состояния: текст, тон и значок в одном месте. */
function stateLabel(entry) {
  const state = entryState(entry);
  if (state === 'ok') return { text: t('index is up to date'), tone: 'ok', icon: 'check' };
  if (state === 'warn') return { text: t('reindexing required'), tone: 'warn', icon: 'alert' };
  if (state === 'bad') {
    return {
      text: t('could not check: {reason}', { reason: serverText(entry.check_error) }),
      tone: 'bad', icon: 'octagon',
    };
  }
  // Сбор идёт в фоне: «не индексировалась» здесь было бы неправдой —
  // состояние просто ещё не посчитано.
  return { text: t('waiting for data…'), tone: 'neutral', icon: 'clock' };
}

/** Разработки, требующие переиндексации: их и ставит в очередь кнопка. */
function staleEntries() {
  return catalogEntries().filter(function (entry) { return entryState(entry) === 'warn'; });
}

/** Размер баз разработки: три файла складываются в один показатель. */
function entryBytes(entry) {
  const files = (entry && entry.size && entry.size.files) || [];
  return files.reduce(function (sum, file) { return sum + (Number(file.bytes) || 0); }, 0);
}

/** Строка «граф / лексика / журнал» — состав баз, а не их сумма. */
function sizeBreakdown(entry) {
  return ((entry && entry.size && entry.size.files) || []).map(function (file) {
    return { title: serverText(file.title), bytes: Number(file.bytes) || 0, wal: Number(file.wal_bytes) || 0, missing: !!file.missing, path: file.path };
  });
}

/* --------------------------------------------------------------------------
   Разбор разделов «Качество индекса» и «Использование»
   --------------------------------------------------------------------------
   Сервер отдаёт их вложенными объектами и в двух видах: значение либо ошибка с
   подсказкой. Разбор один на всю панель — иначе «нет данных» и «сбой чтения»
   выглядели бы одинаково.
   -------------------------------------------------------------------------- */

function qualityInfo(name) {
  const section = detailMap()[name] || {};
  const raw = section.quality || {};
  if (!Object.keys(raw).length) return { pending: true };
  if (raw.error) return { error: raw.error, errorHint: raw.error_hint || '' };
  const quality = raw.quality || {};
  return {
    rev: raw.rev || '',
    quality: quality,
    vectors: quality.vectors || {},
    chunks: quality.chunks || {},
    signatures: quality.signatures || {},
    problems: (quality.problems || []).map(serverText),
    healthy: !!quality.healthy,
    stages: raw.stages || [],
    compute: raw.compute || {},
  };
}

function usageInfo(name) {
  const section = detailMap()[name] || {};
  const raw = section.usage || {};
  if (!Object.keys(raw).length) return { pending: true };
  if (raw.error) return { error: raw.error, errorHint: raw.error_hint || '' };
  return {
    calls: Number(raw.calls_total || 0),
    sessions: Number(raw.sessions_total || 0),
    facts: Number(raw.facts_total || 0),
    verif: Number(raw.verifiable_ratio || 0),
    tokens: Number(raw.est_tokens_total || 0),
    lines: Number(raw.est_lines_not_read || 0),
    agents: raw.by_agent || [],
    kinds: raw.by_agent_kind || [],
    ops: raw.by_op || [],
    args: raw.by_arg || [],
  };
}

/** Ошибка разбора раздела: причина и подсказка приходят текстом сервера,
 *  а запасное пояснение — из словаря, литералом в каждой ветви. */
function sectionError(info, kind) {
  const fallback = kind === 'usage'
    ? t('The usage section could not be read')
    : t('The quality section could not be read');
  return callout({
    tone: 'warn', icon: 'info',
    title: t('Statistics unavailable: {reason}', { reason: serverText(info.error) }),
    desc: info.errorHint ? serverText(info.errorHint) : fallback,
  });
}

/** Итоги по всем разработкам: складываются только измеренные величины. */
function usageTotals() {
  const names = detailNames();
  const totals = { calls: 0, facts: 0, tokens: 0, lines: 0, sessions: 0, verif: 0, measured: 0, errors: 0 };
  names.forEach(function (name) {
    const info = usageInfo(name);
    if (info.error) { totals.errors += 1; return; }
    if (info.pending) return;
    totals.calls += info.calls;
    totals.facts += info.facts;
    totals.tokens += info.tokens;
    totals.lines += info.lines;
    totals.sessions = Math.max(totals.sessions, info.sessions);
    totals.verif += info.facts * info.verif;
    totals.measured += info.facts;
  });
  totals.verif = totals.measured ? (totals.verif / totals.measured) * 100 : 0;
  return totals;
}

/** Операции фасада по всем разработкам: вызовы, находки, средняя длительность. */
function opsAggregate() {
  const map = {};
  detailNames().forEach(function (name) {
    const info = usageInfo(name);
    if (info.error || info.pending) return;
    info.ops.forEach(function (op) {
      const row = map[op.op] || { op: op.op, calls: 0, found: 0, facts: 0, spans: 0, weight: 0 };
      const calls = Number(op.calls || 0);
      row.calls += calls;
      row.found += Number(op.found_calls || 0);
      row.facts += Number(op.facts || 0);
      row.spans += Number(op.with_span || 0);
      row.weight += Number(op.avg_ms || 0) * calls;
      map[op.op] = row;
    });
  });
  return Object.keys(map).map(function (key) {
    const row = map[key];
    row.avg = row.calls ? row.weight / row.calls : 0;
    return row;
  }).sort(function (a, b) { return b.calls - a.calls; });
}

/** Размер баз одной разработки: сумма трёх файлов плюс журналы рядом. */
function entryTotalBytes(entry) {
  return ((entry && entry.size && entry.size.files) || [])
    .reduce(function (sum, file) { return sum + (Number(file.bytes) || 0) + (Number(file.wal_bytes) || 0); }, 0);
}

/* --------------------------------------------------------------------------
   Раздел «Обзор»
   --------------------------------------------------------------------------
   Главная операционная задача: за один взгляд понять, что сломано и что
   требует вмешательства, и попасть в нужный раздел за один щелчок. Своих
   запросов у раздела нет — он собирается из того же состояния, что и остальные.
   -------------------------------------------------------------------------- */

/** Что требует внимания: ошибки, отсутствующий индекс, аномалии качества. */
function attentionItems() {
  const items = [];
  const entries = catalogEntries();
  entries.forEach(function (entry) {
    if (entryState(entry) === 'bad') {
      items.push({
        tone: 'bad', icon: 'octagon', name: String(entry.category),
        title: t('could not check: {reason}', { reason: serverText(entry.check_error) }),
        desc: String(entry.root || ''), section: 'catalog',
      });
    }
  });
  entries.filter(function (entry) {
    return entryState(entry) !== 'bad' && !entry.rev_id && !entry.state;
  }).forEach(function (entry) {
    items.push({
      tone: 'bad', icon: 'database', name: String(entry.category),
      title: t('The index of this development has not been built yet'),
      desc: t('Reindex it to fill the sections in'), section: 'indexing',
    });
  });
  detailNames().forEach(function (name) {
    const info = qualityInfo(name);
    if (info.error || info.pending) return;
    if (info.problems.length) {
      items.push({
        tone: 'warn', icon: 'alert', name: name,
        title: info.problems.join('; '),
        desc: t('The values do not block facts but reduce the completeness of matches'),
        section: 'quality',
      });
    }
  });
  const stale = staleEntries();
  if (stale.length) {
    items.push({
      tone: 'warn', icon: 'refresh',
      name: stale.length === 1 ? String(stale[0].category) : t('Developments'),
      title: t('developments need reindexing: {count}', { count: stale.length }),
      desc: stale.map(function (entry) { return entry.category; }).slice(0, 6).join(', '),
      section: 'indexing',
    });
  }
  (currentState && currentState.clients && currentState.clients.contact
    ? [].concat(currentState.clients.contact.clients || []) : [])
    .forEach(function (row) {
      if (row && row.recent) return;
      items.push({
        tone: 'warn', icon: 'network', name: String(row.client_info || row.agent || ''),
        title: t('No calls for {ago}: the connection may be broken',
          { ago: agoText(row.age_seconds) }),
        desc: t('Check the client connection or the access value'),
        section: 'mode',
      });
    });
  return items;
}

function renderDashboard() {
  const entries = catalogEntries();
  const stale = staleEntries().length;
  const broken = entries.filter(function (entry) { return entryState(entry) === 'bad'; }).length;
  const pending = entries.filter(function (entry) { return entryState(entry) === 'pending'; }).length;
  const totalBytes = entries.reduce(function (sum, entry) { return sum + entryTotalBytes(entry); }, 0);
  const totals = usageTotals();
  const attention = attentionItems();

  const kpis = '<div class="metrics metrics--wide">'
    + metric({ label: t('Developments'), value: num(entries.length), icon: 'folder',
      note: t('{nodes} nodes · {edges} edges', {
        nodes: num(entries.reduce(function (s, e) { return s + (Number(e.nodes) || 0); }, 0)),
        edges: bigNum(entries.reduce(function (s, e) { return s + (Number(e.edges) || 0); }, 0)),
      }) })
    + metric({ label: t('Out of date'), value: num(stale), tone: stale ? 'warn' : 'ok', icon: 'refresh',
      note: stale ? t('reindexing required') : t('All indexes are up to date') })
    + metric({ label: t('Errors'), value: num(broken), tone: broken ? 'bad' : 'ok', icon: 'octagon',
      note: broken ? t('The corpus directory does not exist') : t('no problems found') })
    + metric({ label: t('Database sizes'), value: humanBytes(totalBytes), tone: 'primary', icon: 'database',
      note: t('code graph, lexicon and vectors, journal') })
    + metric({ label: t('Calls'), value: bigNum(totals.calls), icon: 'activity',
      note: t('to facade operations') })
    + metric({ label: t('Facts'), value: bigNum(totals.facts), tone: totals.verif >= 80 ? 'ok' : 'warn',
      icon: 'shield', note: t('Verifiability') + ' ' + totals.verif.toFixed(1) + '%' })
    + '</div>';

  const attentionCard = card({
    wide: true,
    icon: 'alert', title: t('Requires attention'),
    desc: t('Errors and quality anomalies that affect the reliability of facts'),
    actions: button({
      v: 'ghost', s: 'sm', icon: 'shield', label: t('Index quality'),
      action: 'nav', data: { nav: 'quality' },
    }),
    flush: true,
    body: attention.length
      ? '<ul>' + attention.slice(0, 9).map(function (item) {
        return '<li class="callout-row">'
          + '<span class="callout-row__icon callout-row__icon--' + item.tone + '">'
          + icon(item.icon, 'h-3.5 w-3.5', 2.1) + '</span>'
          + '<div class="min-w-0 flex-1">'
          + '<p class="callout-row__title"><b class="mono">' + esc(item.name) + '</b> '
          + esc(truncate(item.title, 120)) + '</p>'
          + '<p class="callout-row__desc mono">' + esc(truncate(item.desc, 150)) + '</p>'
          + '</div>'
          + button({
            v: 'ghost', s: 'xs', icon: 'chevronR', label: t('Open'),
            action: 'nav', data: { nav: item.section },
          })
          + '</li>';
      }).join('') + '</ul>'
      : emptyHtml({ icon: 'check', title: t('no problems found'), desc: t('All indexes are up to date') }),
  });

  const stateCard = card({
    icon: 'gauge', title: t('Index states'),
    desc: t('by {count} developments', { count: num(entries.length) }),
    body: donut([
      { label: t('index is up to date'), value: entries.length - stale - broken - pending, tone: 'ok' },
      { label: t('reindexing required'), value: stale, tone: 'warn' },
      { label: t('Errors'), value: broken, tone: 'bad' },
      { label: t('waiting for data…'), value: pending, tone: 'neutral' },
    ], num(entries.length)),
  });

  const callsRank = detailNames().map(function (name) {
    const info = usageInfo(name);
    return { label: name, value: info.error || info.pending ? 0 : info.calls };
  }).filter(function (item) { return item.value > 0; })
    .sort(function (a, b) { return b.value - a.value; }).slice(0, 8);

  const callsCard = card({
    icon: 'activity', title: t('Top developments by calls'),
    desc: t('who is really used by agents'),
    body: barRows(callsRank, { empty: t('no data') }),
  });

  const sizeRank = entries.filter(function (entry) { return entryTotalBytes(entry) > 0; })
    .map(function (entry) { return { label: String(entry.category), value: entryTotalBytes(entry), text: humanBytes(entryTotalBytes(entry)) }; })
    .sort(function (a, b) { return b.value - a.value; }).slice(0, 8);

  const sizeCard = card({
    icon: 'database', title: t('Database sizes'),
    desc: t('code graph, lexicon and vectors, journal'),
    actions: button({
      v: 'ghost', s: 'sm', icon: 'arrowR', label: t('Database sizes'),
      action: 'nav', data: { nav: 'storage' },
    }),
    body: barRows(sizeRank, { empty: t('no data') }),
  });

  const ops = opsAggregate();
  const opsCard = card({
    icon: 'terminal', title: t('Facade operations'),
    desc: t('aggregate across all developments: calls, facts, average duration'),
    flush: true,
    body: ops.length
      ? '<div class="table-wrap"><table><thead><tr>'
        + '<th scope="col">' + esc(t('Operation')) + '</th>'
        + '<th scope="col" class="num">' + esc(t('Calls short')) + '</th>'
        + '<th scope="col" class="num">' + esc(t('Facts short')) + '</th>'
        + '<th scope="col" class="num">' + esc(t('With reference')) + '</th>'
        + '<th scope="col" class="num">' + esc(t('Average')) + '</th>'
        + '</tr></thead><tbody>'
        + ops.slice(0, 12).map(function (op) {
          return '<tr><td class="mono">' + esc(op.op) + '</td>'
            + '<td class="num">' + num(op.calls) + '</td>'
            + '<td class="num">' + num(op.facts) + '</td>'
            + '<td class="num">' + num(op.spans) + '</td>'
            + '<td class="num' + (op.avg > 8000 ? ' text-[var(--warn)]' : '') + '">' + esc(millisText(op.avg)) + '</td>'
            + '</tr>';
        }).join('')
        + '</tbody></table></div>'
      : emptyHtml({ icon: 'terminal', title: t('no data'), desc: t('Collecting information about developments…') }),
  });

  const log = ((currentState && currentState.access && currentState.access.log
    && currentState.access.log.entries) || []).slice(0, 9);
  const logCard = card({
    icon: 'history', title: t('Recent events'),
    desc: t('access log of calls'),
    actions: button({
      v: 'ghost', s: 'sm', icon: 'arrowR', label: t('Access log'),
      action: 'nav', data: { nav: 'access' },
    }),
    flush: true,
    body: log.length
      ? '<ul>' + log.map(function (row) {
        return '<li class="event-row">'
          + chip(row.result, row.result === 'ok' ? 'ok' : row.result === 'denied' ? 'bad' : 'warn', { icon: false })
          + '<div class="min-w-0 flex-1">'
          + '<p class="mono truncate">' + esc(row.operation || '') + '</p>'
          + '<p class="small muted truncate">' + esc([stamp(Number(row.ts)), row.surface,
            serverText(row.label || '')].filter(Boolean).join(' · ')) + '</p>'
          + '</div></li>';
      }).join('') + '</ul>'
      : emptyHtml({ icon: 'history', title: t('no records') }),
  });

  return '<section class="section" data-section="dashboard">'
    + pageHead({
      title: t('Overview'),
      desc: t('Summary of corpora, index states and the effect for agents. Critical states come first.'),
      actions: button({
        v: 'secondary', s: 'md', icon: 'network', label: t('Mode and clients'),
        action: 'nav', data: { nav: 'mode' },
      }) + button({
        v: 'primary', s: 'md', icon: 'refresh',
        label: t('Reindex all out of date ({count})', { count: num(stale) }),
        action: 'reindex-stale', disabled: !writeAllowed() || !stale,
        title: writeAllowed() ? '' : t('Write actions are unavailable'),
      }),
    })
    + kpis
    + '<div class="cols-3">' + attentionCard + stateCard + '</div>'
    + '<div class="cols-2">' + callsCard + sizeCard + '</div>'
    + '<div class="cols-2">' + opsCard + logCard + '</div>'
    + '</section>';
}

/* --------------------------------------------------------------------------
   Раздел «Разработки»
   --------------------------------------------------------------------------
   Главная задача раздела — найти нужную разработку и понять, актуален ли её
   индекс, а затем либо переиндексировать, либо запретить индексацию. Поэтому
   список плотный, с поиском, фильтром по состоянию, сортировкой по столбцу,
   страницами и массовыми действиями; подробности раскрываются в строке.
   -------------------------------------------------------------------------- */

/** Подробности разработки: качество, использование, базы и стадии. */
function projectDetails(entry) {
  const name = String(entry.category);
  const quality = qualityInfo(name);
  const usage = usageInfo(name);

  const rows = function (pairs) {
    return '<dl class="kv">' + pairs.map(function (pair) {
      return '<div class="kv__row"><dt>' + esc(pair[0]) + '</dt><dd'
        + (pair[2] ? ' class="' + pair[2] + '"' : '') + '>' + pair[1] + '</dd></div>';
    }).join('') + '</dl>';
  };

  const qualityBody = quality.pending
    ? '<p class="small muted">' + esc(t('Collecting information about developments…')) + '</p>'
    : (quality.error
      ? '<p class="small muted">' + esc(serverText(quality.error)) + '</p>'
      : rows([
        [t('Vectors'), esc(quality.vectors.available
          ? (quality.vectors.normalized ? t('normalized') : t('not normalized')) : t('none at all')),
          quality.vectors.normalized ? '' : 'warn'],
        [t('Chunks'), '<span class="mono tnum">' + num(quality.chunks.total) + '</span>'],
        [t('Over budget'), '<span class="mono tnum">' + num(quality.chunks.over_budget) + '</span>',
          quality.chunks.over_budget ? 'warn' : ''],
        [t('Functions'), '<span class="mono tnum">' + num(quality.signatures.functions) + '</span>'],
        [t('Truncated signatures'), '<span class="mono tnum">' + num(quality.signatures.truncated) + '</span>',
          quality.signatures.truncated ? 'warn' : ''],
        [t('Files without symbols'), '<span class="mono tnum">' + num(quality.chunks.files_without_symbols) + '</span>'],
      ]));

  const usageBody = usage.pending
    ? '<p class="small muted">' + esc(t('Collecting information about developments…')) + '</p>'
    : (usage.error
      ? '<p class="small muted">' + esc(t('Statistics unavailable: {reason}', { reason: serverText(usage.error) })) + '</p>'
      : rows([
        [t('Calls'), '<span class="mono tnum">' + num(usage.calls) + '</span>'],
        [t('Sessions'), '<span class="mono tnum">' + num(usage.sessions) + '</span>'],
        [t('Facts'), '<span class="mono tnum">' + num(usage.facts) + '</span>'],
        [t('Verifiability'), '<span class="mono tnum">' + (usage.verif * 100).toFixed(1) + '%</span>'],
        [t('Tokens (estimate)'), '<span class="mono tnum">' + bigNum(usage.tokens) + '</span>'],
        [t('Lines not read as files'), '<span class="mono tnum">' + bigNum(usage.lines) + '</span>'],
      ]));

  const files = sizeBreakdown(entry);
  const sizeBody = files.length
    ? rows(files.map(function (file) {
      return [file.title, '<span class="mono tnum">' + humanBytes(file.bytes) + '</span>'
        + (file.wal ? ' <span class="small muted">+ ' + humanBytes(file.wal) + ' ' + esc(t('journal')) + '</span>' : '')
        + (file.missing ? ' <span class="small">' + esc(t('file not found')) + '</span>' : '')];
    }).concat([[t('Total'), '<span class="mono tnum">' + humanBytes(entryBytes(entry)) + '</span>', 'strong']]))
    : '<p class="small muted">' + esc(t('no data')) + '</p>';

  const stages = (quality.stages || []).filter(function (stage) { return Number(stage.seconds) > 0; });
  const stageBody = stages.length
    ? '<ul class="stack">' + stages.map(function (stage) {
      return '<li class="inline"><span class="mono small" style="width:5.5rem">' + esc(stage.stage) + '</span>'
        + '<span class="flex-1">' + progressHtml(0, 'primary', stage.stage) + '</span>'
        + '<span class="mono tnum small muted">' + esc(secondsText(stage.seconds)) + '</span></li>';
    }).join('') + '</ul>'
    : '<p class="small muted">' + esc(t('no data')) + '</p>';

  const item = function (title, iconName, body) {
    return '<div class="detail-panel">'
      + '<p class="detail-panel__title">' + icon(iconName, 'h-3.5 w-3.5') + esc(title) + '</p>'
      + body + '</div>';
  };

  return '<div class="a-fade detail-grid">'
    + item(t('Index quality'), 'shield', qualityBody)
    + item(t('Usage and effect'), 'activity', usageBody)
    + item(t('Database sizes'), 'database', sizeBody)
    + item(t('Mode and clients'), 'sliders',
      '<p class="small muted mono">' + esc(entry.config_path || '') + '</p>'
      + (entry.config_path ? copyValue(String(entry.config_path), { shown: t('Copy') }) : '')
      + (entry.rev_id ? '<p class="small muted">' + esc(t('Revision') + ': ' + entry.rev_id) + '</p>' : ''))
    + '</div>';
}

const PROJECT_FILTERS = ['all', 'ok', 'warn', 'bad'];

/** Подпись фильтра по состоянию. Ключ — ЛИТЕРАЛОМ в каждой ветви: вызов вида
 *  `t(filter.label)` статически не проверяется, и непереведённая подпись
 *  фильтра осталась бы незамеченной. */
function projectFilterLabel(kind) {
  switch (kind) {
    case 'all': return t('All');
    case 'ok': return t('Up to date');
    case 'warn': return t('Out of date');
    case 'bad': return t('Errors');
    default: return kind;
  }
}

function projectsFiltered() {
  const query = UI.projects.q.trim().toLowerCase();
  return catalogEntries().filter(function (entry) {
    if (query) {
      const haystack = [entry.category, entry.root, entry.rev_id, entry.corpus_hash,
        entry.capability].join(' ').toLowerCase();
      if (haystack.indexOf(query) < 0) return false;
    }
    if (UI.projects.state === 'all') return true;
    return entryState(entry) === UI.projects.state;
  });
}

function renderProjects() {
  const all = catalogEntries();
  if (!all.length) {
    // Пустой список — это ДВА разных состояния: «ещё идёт сбор» и «реестр пуст».
    // Пока они выглядели одинаково, пустая таблица читалась как поломка.
    const collecting = (currentState && currentState.collecting) || {};
    if (collecting.running) {
      return '<section class="section" data-section="catalog">'
        + pageHead({
          title: t('Developments'),
          desc: t('Collecting information about developments…'),
        })
        + card({
          flush: true,
          body: '<div class="card__body">'
            + skeletonHtml('h-6 w-56') + '<div class="mt-2">' + skeletonHtml('h-6 w-40') + '</div>'
            + '<div class="mt-3">' + skeletonHtml('h-28') + '</div>'
            + '</div>',
        })
        + '</section>';
    }
    return '<section class="section" data-section="catalog">'
      + pageHead({ title: t('Developments') })
      + card({
        body: emptyHtml({
          icon: 'folder', title: t('No developments in the registry'),
          desc: t('Register a corpus with the command below'),
        }) + '<div class="mt-3">' + codeBlock(t('Working through the shell (CLI)'), 'ragraf register <path>') + '</div>',
      })
      + '</section>';
  }

  const rows = projectsFiltered();
  const sort = UI.projects.sort;
  const sorted = sortRows(rows, sort, {
    category: function (entry) { return String(entry.category).toLowerCase(); },
    state: function (entry) { return entryState(entry); },
    files: function (entry) { return Number(entry.files) || 0; },
    nodes: function (entry) { return Number(entry.nodes) || 0; },
    edges: function (entry) { return Number(entry.edges) || 0; },
    size: function (entry) { return entryBytes(entry); },
    calls: function (entry) {
      const info = usageInfo(String(entry.category));
      return info.error || info.pending ? -1 : info.calls;
    },
  });

  const perPage = UI.projects.perPage;
  const total = sorted.length;
  const pages = Math.max(1, Math.ceil(total / perPage));
  const page = Math.min(UI.projects.page, pages - 1);
  UI.projects.page = page;
  const view = sorted.slice(page * perPage, page * perPage + perPage);
  const sel = UI.projects.sel;
  const selectedIds = sel.filter(function (id) {
    return all.some(function (entry) { return entry.category === id; });
  });
  UI.projects.sel = selectedIds;

  const counts = {
    all: all.length,
    ok: all.filter(function (e) { return entryState(e) === 'ok'; }).length,
    warn: all.filter(function (e) { return entryState(e) === 'warn'; }).length,
    bad: all.filter(function (e) { return entryState(e) === 'bad'; }).length,
  };

  const toolbar = '<div class="toolbar">'
    + searchField({
      action: 'projects-search', value: UI.projects.q,
      placeholder: t('Search by name, path, revision or checksum…'),
      aria: t('Search developments'),
    })
    + tabsHtml({
      group: 'projects-state', value: UI.projects.state, label: t('Index state'),
      panel: 'projects-panel',
      items: PROJECT_FILTERS.map(function (kind) {
        return { k: kind, label: projectFilterLabel(kind), count: num(counts[kind]) };
      }),
    })
    + button({
      v: 'ghost', s: 'sm', icon: 'x', label: t('Reset'), action: 'projects-reset',
      disabled: !UI.projects.q && UI.projects.state === 'all',
    })
    + '<span class="toolbar__spacer"></span>'
    + '<span class="mono tnum small muted">' + esc(num(total) + ' / ' + num(all.length)) + '</span>'
    + '</div>';

  const bulk = selectedIds.length
    ? '<div class="bulkbar a-up">'
      + '<span>' + esc(t('Selected')) + ': <b class="mono">' + selectedIds.length + '</b></span>'
      + '<span class="toolbar__spacer"></span>'
      + button({
        v: 'secondary', s: 'sm', icon: 'refresh', label: t('Reindex selected'),
        action: 'bulk-reindex', disabled: !writeAllowed(),
      })
      + button({
        v: 'secondary', s: 'sm', icon: 'check', label: t('Allow indexing'),
        action: 'bulk-allow', disabled: !writeAllowed(),
      })
      + button({
        v: 'danger', s: 'sm', icon: 'x', label: t('Forbid indexing'),
        action: 'bulk-forbid', disabled: !writeAllowed(),
      })
      + button({
        v: 'ghost', s: 'sm', label: t('Cancel'), action: 'bulk-clear',
      })
      + '</div>'
    : '';

  const head = '<thead><tr>'
    + '<th scope="col" class="w-9">'
    + '<input type="checkbox" data-action="projects-all" aria-label="' + esc(t('Select all on page')) + '"'
    + (view.length && view.every(function (entry) { return selectedIds.indexOf(String(entry.category)) >= 0; }) ? ' checked' : '')
    + '></th>'
    + th(t('Development'), { sort: sort, key: 'category', scope: 'projects', cls: 'wrap' })
    + '<th scope="col">' + esc(t('Revision')) + '</th>'
    + th(t('Index state'), { sort: sort, key: 'state', scope: 'projects' })
    + th(t('Files'), { num: true, sort: sort, key: 'files', scope: 'projects' })
    + th(t('Nodes'), { num: true, sort: sort, key: 'nodes', scope: 'projects' })
    + th(t('Edges'), { num: true, sort: sort, key: 'edges', scope: 'projects' })
    + th(t('Database size'), { num: true, sort: sort, key: 'size', scope: 'projects' })
    + '<th scope="col">' + esc(t('Corpus checksum')) + '</th>'
    + th(t('Calls short'), { num: true, sort: sort, key: 'calls', scope: 'projects' })
    + '<th scope="col">' + esc(t('Indexing')) + '</th>'
    + '<th scope="col" class="num">' + esc(t('Actions')) + '</th>'
    + '</tr></thead>';

  const body = view.map(function (entry) {
    const id = String(entry.category);
    const state = stateLabel(entry);
    const usage = usageInfo(id);
    const isOpen = UI.projects.open === id;
    const selected = selectedIds.indexOf(id) >= 0;
    const allowed = entry.index_enabled !== false;

    const mainRow = '<tr' + (selected ? ' aria-selected="true"' : '') + '>'
      + '<td><input type="checkbox" data-action="projects-select" data-id="' + esc(id) + '"'
      + ' aria-label="' + esc(t('Select') + ': ' + id) + '"' + (selected ? ' checked' : '') + '></td>'
      + '<td class="wrap"><div class="inline">'
      + '<button type="button" class="linklike mono" data-action="projects-open" data-id="' + esc(id) + '">'
      + esc(id) + '</button>'
      + (entry.capability ? '<span class="rev">' + esc(entry.capability) + '</span>' : '')
      + '</div>' + copyValue(String(entry.root || '')) + '</td>'
      + '<td class="mono nowrap">' + esc(entry.rev_id || '—') + '</td>'
      + '<td>' + chip(state.text, state.tone, { icon: state.icon }) + '</td>'
      + '<td class="num">' + num(entry.files) + '</td>'
      + '<td class="num">' + num(entry.nodes) + '</td>'
      + '<td class="num">' + num(entry.edges) + '</td>'
      + '<td class="num">' + (entryBytes(entry) ? humanBytes(entryBytes(entry)) : '—') + '</td>'
      + '<td>' + copyValue(String(entry.corpus_hash || ''), { shown: String(entry.corpus_hash || '').slice(0, 16) + '…' }) + '</td>'
      + '<td class="num">' + (usage.error || usage.pending ? '—' : num(usage.calls)) + '</td>'
      + '<td>' + switchHtml({
        checked: allowed, disabled: !writeAllowed(), action: 'entry-enabled', data: { id: id },
        label: allowed ? t('indexing allowed') : t('indexing forbidden'),
        title: allowed ? t('indexing allowed') : t('indexing forbidden'),
      }) + '</td>'
      + '<td class="num nowrap">'
      + (entryState(entry) !== 'ok' && allowed
        ? button({
          v: 'soft', s: 'xs', icon: 'refresh', label: t('Reindex'), disabled: !writeAllowed(),
          action: 'reindex-one', data: { id: id },
        })
        : '')
      + button({
        v: 'ghost', s: 'icon-xs', icon: isOpen ? 'chevronD' : 'chevronR',
        action: 'projects-open', data: { id: id },
        aria: 'aria-expanded="' + (isOpen ? 'true' : 'false') + '" aria-label="'
          + esc(t('Details') + ': ' + id) + '"',
      })
      + (entry.rev_id || entry.state === 'indexed'
        ? button({
          v: 'ghost', s: 'icon-xs', icon: 'trash', action: 'clear-index', data: { id: id },
          disabled: !writeAllowed(), aria: 'aria-label="' + esc(t('Delete index') + ': ' + id) + '"',
        })
        : '')
      + '</td></tr>';

    const detailRow = isOpen
      ? '<tr><td colspan="12" class="!p-0">' + projectDetails(entry) + '</td></tr>' : '';

    return mainRow + detailRow;
  }).join('');

  const table = total
    ? '<div class="table-wrap table-wrap--tall"><table>' + head + '<tbody>' + body + '</tbody></table></div>'
    : emptyHtml({
      icon: 'search', title: t('Nothing found for the given conditions'),
      action: button({
        v: 'secondary', s: 'sm', label: t('Reset'), action: 'projects-reset',
      }),
    });

  return '<section class="section" data-section="catalog">'
    + pageHead({
      title: t('Developments'),
      desc: t('files / nodes / edges · corpus checksum'),
      actions: button({
        v: 'primary', s: 'md', icon: 'refresh',
        label: t('Reindex all out of date ({count})', { count: num(staleEntries().length) }),
        action: 'reindex-stale', disabled: !writeAllowed() || !staleEntries().length,
      }),
    })
    + (writeAllowed() ? '' : deniedCallout())
    + card({
      flush: true,
      body: toolbar
        + '<div id="projects-panel" role="tabpanel" tabindex="0"'
        + ' aria-labelledby="tab-projects-state-' + esc(UI.projects.state) + '">'
        + bulk + table + (total ? pagerHtml('projects', total, page, perPage) : '')
        + '</div>',
    })
    + '</section>';
}

/* --------------------------------------------------------------------------
   Раздел «Индексация»
   --------------------------------------------------------------------------
   Ход работы берётся у сервера (`state.indexing.job`), а не рисуется по
   догадке: панель показывает, сколько разработок уже пройдено, какая идёт
   сейчас и чем закончились прежние. Отдельной очереди у панели нет — очередь
   ведёт сервер.
   -------------------------------------------------------------------------- */

function renderIndexing() {
  const info = (currentState && currentState.indexing) || {};
  const job = info.job || {};
  const stale = info.stale || staleEntries().map(function (entry) { return String(entry.category); });
  const running = !!job.running;
  const results = job.results || [];
  const failed = results.filter(function (row) { return !row.ok; });
  const total = Number(job.total) || 0;
  const done = Number(job.done) || 0;
  const percent = total ? (done / total) * 100 : (running ? 0 : 100);

  const controls = '<div class="inline">'
    + button({
      v: 'primary', s: 'md', icon: 'play',
      label: t('Reindex all out of date ({count})', { count: num(stale.length) }),
      action: 'reindex-stale', disabled: !writeAllowed() || running || !stale.length,
    })
    + button({
      v: 'secondary', s: 'md', icon: 'zap', label: t('Reindex') + ': ragraf',
      action: 'reindex-one', data: { id: 'ragraf' }, disabled: !writeAllowed() || running,
    })
    + button({
      v: 'ghost', s: 'md', icon: 'refresh', label: t('Refresh state'),
      action: 'indexing-refresh', id: 'reindex-refresh',
    })
    + chip(running
      ? t('Indexing {done} of {total}: {current}…', {
        done: num(done), total: num(total), current: job.current || '—' })
      : t('indexing is idle'), running ? 'info' : 'neutral', { icon: running ? 'refresh' : 'dot' })
    + '</div>';

  const staleList = stale.length
    ? '<div class="mt-3"><p class="small muted">' + esc(t('Out of date: ')) + '</p><div class="inline mt-1">'
      + stale.map(function (name) {
        return button({
          v: 'danger', s: 'xs', icon: 'refresh', label: name,
          action: 'reindex-one', data: { id: name }, disabled: !writeAllowed() || running,
        });
      }).join('') + '</div></div>'
    : '<p class="small muted mt-3">' + esc(t('All indexes are up to date')) + '</p>';

  const failures = failed.length
    ? '<div class="mt-3">' + callout({
      tone: 'bad', icon: 'octagon', title: t('indexing failed'),
      desc: failed.map(function (row) {
        return String(row.category) + ': ' + t(row.error || 'indexing failed');
      }).join('; '),
    }) + '</div>'
    : '';

  const queueBody = running || results.length
    ? '<div class="stack">'
      + '<div>'
      + '<div class="inline"><span class="small muted">'
      + esc(t('Indexing {done} of {total}: {current}…', {
        done: num(done), total: num(total), current: job.current || '—' }))
      + '</span><span class="toolbar__spacer"></span>'
      + '<span class="mono tnum small">' + Math.round(percent) + '%</span></div>'
      + '<div class="mt-1">' + progressHtml(percent, running ? 'primary' : 'ok', t('Indexing')) + '</div>'
      + '</div>'
      + (results.length
        ? '<ul class="stack">' + results.slice(-12).reverse().map(function (row) {
          return '<li class="queue-row">'
            + chip(row.ok ? t('ready') : t('indexing failed'), row.ok ? 'ok' : 'bad', { icon: row.ok ? 'check' : 'octagon' })
            + '<span class="mono truncate flex-1">' + esc(String(row.category || '')) + '</span>'
            + (row.error ? '<span class="small muted truncate">' + esc(t(row.error)) + '</span>' : '')
            + '</li>';
        }).join('') + '</ul>'
        : '')
      + '</div>'
    : emptyHtml({
      icon: 'refresh', title: t('indexing is idle'),
      desc: t('Start a reindex: progress and the log will appear here.'),
    });

  const timeline = [
    [t('Started'), job.started_at],
    [t('Finished'), job.finished_at],
  ].filter(function (pair) { return pair[1]; });

  const logCard = card({
    icon: 'terminal', title: t('Run log'),
    desc: t('what the server reports about the current run'),
    flush: true,
    body: (timeline.length || results.length)
      ? '<div class="table-wrap"><table><tbody>'
        + timeline.map(function (pair) {
          return '<tr><td class="small muted">' + esc(pair[0]) + '</td>'
            + '<td class="mono">' + esc(stamp(pair[1])) + '</td></tr>';
        }).join('')
        + results.slice(-14).reverse().map(function (row) {
          return '<tr><td class="mono">' + esc(String(row.category || '')) + '</td>'
            + '<td>' + (row.ok ? chip(t('ready'), 'ok') : chip(t(row.error || 'indexing failed'), 'bad')) + '</td></tr>';
        }).join('')
        + '</tbody></table></div>'
      : emptyHtml({ icon: 'terminal', title: t('no output'), desc: t('Start a reindex to see the log.') }),
  });

  const rows = catalogEntries().map(function (entry) {
    const id = String(entry.category);
    const allowed = entry.index_enabled !== false;
    const usage = usageInfo(id);
    return '<tr>'
      + '<td class="mono">' + esc(id) + '</td>'
      + '<td class="mono">' + esc(entry.rev_id || '—') + '</td>'
      + '<td>' + chip(stateLabel(entry).text, stateLabel(entry).tone, { icon: stateLabel(entry).icon }) + '</td>'
      + '<td>' + (entry.capability ? chip(entry.capability, entry.capability === 'full' ? 'ok' : 'warn', { icon: false }) : '—') + '</td>'
      + '<td class="num">' + (usage.error || usage.pending ? '—' : num(usage.calls)) + '</td>'
      + '<td>' + chip(allowed ? t('indexing allowed') : t('indexing forbidden'),
        allowed ? 'ok' : 'neutral', { icon: allowed ? 'check' : 'x' }) + '</td>'
      + '<td class="num">' + button({
        v: 'soft', s: 'xs', icon: 'refresh', label: t('Reindex'),
        action: 'reindex-one', data: { id: id }, disabled: !writeAllowed() || running,
      }) + '</td></tr>';
  }).join('');

  return '<section class="section" data-section="indexing">'
    + pageHead({
      title: t('Indexing'),
      desc: t('reindex and include or exclude developments'),
      actions: chip(running ? t('running') : t('indexing is idle'), running ? 'info' : 'ok',
        { icon: running ? 'refresh' : 'check' }),
    })
    + (writeAllowed() ? '' : deniedCallout())
    + '<div class="cols-2">'
    + card({
      icon: 'refresh', title: t('Reindex'),
      desc: running ? t('running') : t('indexing is idle'),
      body: controls + staleList + failures,
    })
    + card({
      icon: 'clock', title: t('Queue'),
      desc: t('{done} of {total} done', { done: num(done), total: num(total) }),
      body: queueBody,
    })
    + '</div>'
    + logCard
    + card({
      icon: 'rows', title: t('State by development'),
      desc: t('revision, completeness and indexing permission'),
      flush: true,
      body: rows
        ? '<div class="table-wrap"><table><thead><tr>'
          + '<th scope="col">' + esc(t('Development')) + '</th>'
          + '<th scope="col">' + esc(t('Revision')) + '</th>'
          + '<th scope="col">' + esc(t('Index state')) + '</th>'
          + '<th scope="col">' + esc(t('capability')) + '</th>'
          + '<th scope="col" class="num">' + esc(t('Calls short')) + '</th>'
          + '<th scope="col">' + esc(t('Indexing')) + '</th>'
          + '<th scope="col" class="num">' + esc(t('Actions')) + '</th>'
          + '</tr></thead><tbody>' + rows + '</tbody></table></div>'
        : emptyHtml({ icon: 'folder', title: t('No developments') }),
    })
    + '</section>';
}

/* --------------------------------------------------------------------------
   Раздел «Качество индекса»
   --------------------------------------------------------------------------
   Вопрос раздела: можно ли доверять фактам этой разработки. Поэтому слева
   список с отметкой проблемных, справа — измерения выбранной: нормы векторов,
   размеры чанков, полнота сигнатур и время стадий. Значения не блокируют
   выдачу фактов, но снижают полноту совпадений — так и сказано словами.
   -------------------------------------------------------------------------- */

function qualityList() {
  const query = UI.quality.q.trim().toLowerCase();
  return catalogEntries().filter(function (entry) {
    if (query && String(entry.category).toLowerCase().indexOf(query) < 0) return false;
    if (!UI.quality.onlyIssues) return true;
    const info = qualityInfo(String(entry.category));
    if (info.error || info.pending) return true;
    return info.problems.length > 0 || !info.healthy;
  });
}

function qualityProblemCount() {
  return detailNames().filter(function (name) {
    const info = qualityInfo(name);
    return !info.error && !info.pending && info.problems.length > 0;
  }).length;
}

function renderQuality() {
  const entries = catalogEntries();
  if (!entries.length) {
    return '<section class="section" data-section="quality">'
      + pageHead({ title: t('Index quality') })
      + card({ body: emptyHtml({ icon: 'folder', title: t('No developments') }) })
      + '</section>';
  }

  const list = qualityList();
  const selectedId = entries.some(function (entry) { return String(entry.category) === UI.quality.sel; })
    ? UI.quality.sel : String(entries[0].category);
  UI.quality.sel = selectedId;
  const entry = entries.filter(function (row) { return String(row.category) === selectedId; })[0];
  const info = qualityInfo(selectedId);

  const listHtml = '<div class="scroller list">' + (list.length ? list.map(function (row) {
    const id = String(row.category);
    const rowInfo = qualityInfo(id);
    const hasProblem = !rowInfo.error && !rowInfo.pending && rowInfo.problems.length > 0;
    const broken = rowInfo.error || (entryState(row) === 'bad');
    const active = id === selectedId;
    return '<button type="button" class="list__item' + (active ? ' list__item--active' : '') + '"'
      + ' data-action="quality-select" data-id="' + esc(id) + '"'
      + (active ? ' aria-current="true"' : '') + '>'
      + '<span class="list__dot list__dot--' + (broken ? 'bad' : hasProblem ? 'warn' : 'ok') + '"></span>'
      + '<span class="min-w-0 flex-1"><span class="list__name">' + esc(id) + '</span>'
      + '<span class="list__sub mono">' + esc(row.rev_id || t('no data')) + '</span></span>'
      + (broken ? chip(t('Errors'), 'bad', { icon: false })
        : hasProblem ? chip(String(rowInfo.problems.length), 'warn', { icon: false })
          : chip(t('no problems found'), 'ok', { icon: false }))
      + '</button>';
  }).join('') : emptyHtml({ icon: 'search', title: t('Nothing found for the given conditions') }))
    + '</div>';

  const aside = card({
    cls: 'split__aside',
    body: '<div class="stack">'
      + searchField({
        action: 'quality-search', value: UI.quality.q,
        placeholder: t('Development'), aria: t('Search developments'),
      })
      + switchHtml({
        checked: UI.quality.onlyIssues, action: 'quality-only-issues',
        label: t('Only with problems'),
      })
      + '</div>' + listHtml,
  });

  let verdict;
  let details;
  if (!entry.rev_id && !entry.state) {
    verdict = chip(t('waiting for data…'), 'neutral', { icon: 'clock' });
    details = callout({
      tone: 'bad', icon: 'database',
      title: t('The index of this development has not been built yet'),
      desc: t('Reindex it to fill the sections in'),
      action: button({
        v: 'secondary', s: 'sm', icon: 'refresh', label: t('Reindex'),
        action: 'reindex-one', data: { id: selectedId }, disabled: !writeAllowed(),
      }),
    });
  } else if (info.error) {
    verdict = chip(t('Errors'), 'bad', { icon: 'octagon' });
    details = sectionError(info, 'quality');
  } else if (info.pending) {
    verdict = chip(t('waiting for data…'), 'neutral', { icon: 'clock' });
    details = callout({
      tone: 'info', icon: 'clock', title: t('Collecting information about developments…'),
      desc: t('The section is filled in from a background snapshot.'),
    });
  } else {
    verdict = info.problems.length
      ? chip(t('problems: {count}', { count: info.problems.length }), 'warn')
      : chip(t('no problems found'), 'ok');
    const stages = info.stages || [];
    const slowest = stages.reduce(function (top, stage) {
      return Math.max(top, Number(stage.seconds) || 0);
    }, 0) || 1;
    details = '<div class="stack">'
      + '<div class="metrics">'
      + metric({
        label: t('Vectors'), value: info.vectors.available
          ? (info.vectors.normalized ? t('normalized') : t('not normalized')) : t('none at all'),
        tone: info.vectors.available && !info.vectors.normalized ? 'warn' : 'neutral',
        icon: 'spark', note: t('norm p50 {value}', {
          value: info.vectors.norm_p50 === null || info.vectors.norm_p50 === undefined
            ? '—' : Number(info.vectors.norm_p50).toFixed(4),
        }),
      })
      + metric({ label: t('Chunks'), value: num(info.chunks.total), icon: 'layers',
        note: t('maximum {value} tokens', { value: num(info.chunks.max_tokens) }) })
      + metric({ label: t('Over budget'), value: num(info.chunks.over_budget),
        tone: info.chunks.over_budget ? 'warn' : 'ok', icon: 'alert',
        note: t('chunks longer than {value}', { value: num(info.chunks.budget_tokens) }) })
      + metric({ label: t('Files without symbols'), value: num(info.chunks.files_without_symbols),
        icon: 'file', note: t('wrapped into windows') })
      + metric({ label: t('Functions'), value: num(info.signatures.functions), icon: 'terminal',
        note: t('with parameters parsed') })
      + metric({ label: t('Truncated signatures'), value: num(info.signatures.truncated),
        tone: info.signatures.truncated ? 'warn' : 'ok', icon: 'hash',
        note: t('parameters lost in the text') })
      + metric({ label: t('Docblocks'), value: num(info.signatures.docblocks), icon: 'box',
        note: t('parameter contracts') })
      + '</div>'
      + (info.compute && info.compute.backend
        ? callout({
          tone: 'info', icon: 'cpu',
          title: t('Vector compute: ') + String(info.compute.backend)
            + (info.compute.device ? ' (' + String(info.compute.device) + ')' : ''),
          desc: (info.compute.uses_gpu ? t('GPU') : t('CPU'))
            + (info.compute.reason ? ' · ' + serverText(info.compute.reason) : ''),
        })
        : '')
      + (info.problems.length
        ? callout({
          tone: 'warn', icon: 'alert',
          title: t('Detected: ') + info.problems.join('; '),
          desc: t('The values do not block facts but reduce the completeness of matches'),
        })
        : '')
      + (stages.length
        ? card({
          icon: 'clock', title: t('Indexing stages'),
          desc: t('time and number of processed elements'),
          flush: true,
          body: '<div class="table-wrap"><table><thead><tr>'
            + '<th scope="col">' + esc(t('Stage')) + '</th>'
            + '<th scope="col" class="num">' + esc(t('Time')) + '</th>'
            + '<th scope="col" class="num">' + esc(t('Items')) + '</th>'
            + '<th scope="col">' + esc(t('Share')) + '</th>'
            + '</tr></thead><tbody>'
            + stages.map(function (stage) {
              const seconds = Number(stage.seconds) || 0;
              return '<tr><td class="mono">' + esc(stage.stage) + '</td>'
                + '<td class="num' + (seconds > 60 ? ' text-[var(--warn)]' : '') + '">' + esc(secondsText(seconds)) + '</td>'
                + '<td class="num">' + num(stage.items) + '</td>'
                + '<td style="width:28%">' + progressHtml((seconds / slowest) * 100, seconds > 60 ? 'warn' : 'primary', stage.stage) + '</td>'
                + '</tr>';
            }).join('')
            + '</tbody></table></div>',
        })
        : '')
      + '</div>';
  }

  return '<section class="section" data-section="quality">'
    + pageHead({
      title: t('Index quality'),
      desc: t('vector norms, chunk sizes, signature completeness'),
      actions: chip(
        qualityProblemCount()
          ? t('Problems found: {count}', { count: qualityProblemCount() })
          : t('no problems found'),
        qualityProblemCount() ? 'warn' : 'ok',
      ),
    })
    + '<div class="split">' + aside
    + '<div class="stack">'
    + card({
      icon: 'shield', title: selectedId,
      desc: entry.rev_id ? entry.rev_id + (entry.capability ? ' · ' + entry.capability : '') : t('no data'),
      actions: verdict,
      body: details,
    })
    + '</div></div>'
    + '</section>';
}

/* --------------------------------------------------------------------------
   Раздел «Использование и эффект»
   --------------------------------------------------------------------------
   Обращения и факты — измерение; токены и строки — оценка. Это различие
   написано в подписи раздела, а не только в документации: по нему решают,
   чему верить.
   -------------------------------------------------------------------------- */

function renderUsage() {
  const entries = catalogEntries();
  const totals = usageTotals();
  if (!entries.length || !detailNames().length) {
    const collecting = (currentState && currentState.detail_collecting) || {};
    return '<section class="section" data-section="usage">'
      + pageHead({ title: t('Usage and effect') })
      + card({
        body: collecting.running
          ? callout({
            tone: 'info', icon: 'clock', title: t('Collecting information about developments…'),
            desc: t('Tokens and lines are estimates, calls and facts are measured.'),
          })
          : emptyHtml({ icon: 'activity', title: t('no data'), desc: t('Collecting information about developments…') }),
      })
      + '</section>';
  }

  const sort = UI.usage.sort;
  const rows = catalogEntries().map(function (entry) {
    const info = usageInfo(String(entry.category));
    return {
      id: String(entry.category),
      calls: info.pending || info.error ? -1 : info.calls,
      facts: info.pending || info.error ? -1 : info.facts,
      verif: info.pending || info.error ? -1 : info.verif,
      tokens: info.pending || info.error ? -1 : info.tokens,
      lines: info.pending || info.error ? -1 : info.lines,
      info: info,
    };
  });
  const sorted = sortRows(rows, sort, {
    id: function (row) { return row.id.toLowerCase(); },
    calls: function (row) { return row.calls; },
    facts: function (row) { return row.facts; },
    verif: function (row) { return row.verif; },
    tokens: function (row) { return row.tokens; },
    lines: function (row) { return row.lines; },
  });

  const selectedId = sorted.some(function (row) { return row.id === UI.usage.sel; })
    ? UI.usage.sel : (sorted[0] ? sorted[0].id : '');
  UI.usage.sel = selectedId;
  const selected = sorted.filter(function (row) { return row.id === selectedId; })[0];
  const info = selected ? selected.info : {};

  const table = '<div class="table-wrap table-wrap--tall"><table><thead><tr>'
    + th(t('Development'), { sort: sort, key: 'id', scope: 'usage' })
    + th(t('Calls short'), { num: true, sort: sort, key: 'calls', scope: 'usage' })
    + th(t('Facts short'), { num: true, sort: sort, key: 'facts', scope: 'usage' })
    + th(t('Verifiability'), { num: true, sort: sort, key: 'verif', scope: 'usage' })
    + th(t('Tokens'), { num: true, sort: sort, key: 'tokens', scope: 'usage' })
    + th(t('Lines not read'), { num: true, sort: sort, key: 'lines', scope: 'usage' })
    + '</tr></thead><tbody>'
    + sorted.map(function (row) {
      const unavailable = row.calls < 0;
      return '<tr' + (row.id === selectedId ? ' aria-selected="true"' : '') + '>'
        + '<td><button type="button" class="linklike mono" data-action="usage-select" data-id="'
        + esc(row.id) + '">' + esc(row.id) + '</button></td>'
        + (unavailable
          ? '<td colspan="5" class="small muted">' + esc(t('Statistics unavailable: {reason}',
            { reason: serverText((row.info && row.info.error) || t('no data')) })) + '</td>'
          : '<td class="num">' + num(row.calls) + '</td>'
          + '<td class="num">' + num(row.facts) + '</td>'
          + '<td class="num' + (row.verif >= 0.9 ? ' text-[var(--ok)]' : row.verif >= 0.5 ? ' text-[var(--warn)]' : ' text-[var(--bad)]') + '">'
          + (row.verif * 100).toFixed(1) + '%</td>'
          + '<td class="num">' + bigNum(row.tokens) + '</td>'
          + '<td class="num">' + bigNum(row.lines) + '</td>')
        + '</tr>';
    }).join('')
    + '</tbody></table></div>';

  let detailBody = '';
  if (!selected) {
    detailBody = emptyHtml({ icon: 'activity', title: t('no data') });
  } else if (info.error) {
    detailBody = sectionError(info, 'usage');
  } else if (info.pending) {
    detailBody = callout({
      tone: 'info', icon: 'clock', title: t('Collecting information about developments…'),
      desc: t('The section is filled in from a background snapshot.'),
    });
  } else {
    detailBody = '<div class="stack">'
      + '<div class="metrics">'
      + metric({ label: t('Calls'), value: num(info.calls), icon: 'activity', note: t('to facade operations') })
      + metric({ label: t('Sessions'), value: num(info.sessions), icon: 'clock', note: t('tool runs') })
      + metric({ label: t('Facts'), value: num(info.facts), icon: 'shield', note: t('obtained from the index') })
      + metric({ label: t('Verifiability'), value: (info.verif * 100).toFixed(1) + '%',
        tone: info.verif >= 0.9 ? 'ok' : 'warn', icon: 'check', note: t('with a file:line reference') })
      + metric({ label: t('Tokens (estimate)'), value: bigNum(info.tokens), icon: 'hash', note: t('by response size') })
      + metric({ label: t('Lines not read as files'), value: bigNum(info.lines), icon: 'file',
        note: t('lines of code (estimate)') })
      + '</div>'
      + barRows(info.ops.slice(0, 8).map(function (op) {
        return { label: op.op, value: Number(op.calls) || 0, text: num(op.calls) };
      }), { empty: t('no data') })
      + card({
        icon: 'terminal', title: t('Operation'),
        desc: t('calls, findings, duration'),
        flush: true,
        body: info.ops.length
          ? '<div class="table-wrap"><table><thead><tr>'
            + '<th scope="col">' + esc(t('Operation')) + '</th>'
            + '<th scope="col" class="num">' + esc(t('Calls short')) + '</th>'
            + '<th scope="col" class="num">' + esc(t('Found')) + '</th>'
            + '<th scope="col" class="num">' + esc(t('Facts short')) + '</th>'
            + '<th scope="col" class="num">' + esc(t('With reference')) + '</th>'
            + '<th scope="col" class="num">' + esc(t('Average')) + '</th>'
            + '</tr></thead><tbody>'
            + info.ops.map(function (op) {
              return '<tr><td class="mono">' + esc(op.op) + '</td>'
                + '<td class="num">' + num(op.calls) + '</td>'
                + '<td class="num">' + num(op.found_calls) + '</td>'
                + '<td class="num">' + num(op.facts) + '</td>'
                + '<td class="num">' + num(op.with_span) + '</td>'
                + '<td class="num' + (Number(op.avg_ms) > 8000 ? ' text-[var(--warn)]' : '') + '">'
                + esc(millisText(op.avg_ms)) + '</td></tr>';
            }).join('')
            + '</tbody></table></div>'
          : emptyHtml({ icon: 'terminal', title: t('no data') }),
      })
      + card({
        icon: 'user', title: t('By agent (who used RAGRAF)'),
        flush: true,
        body: info.agents.length
          ? '<div class="table-wrap"><table><thead><tr>'
            + '<th scope="col">' + esc(t('Agent')) + '</th>'
            + '<th scope="col">' + esc(t('Family')) + '</th>'
            + '<th scope="col" class="num">' + esc(t('Sessions')) + '</th>'
            + '<th scope="col" class="num">' + esc(t('Calls short')) + '</th>'
            + '<th scope="col" class="num">' + esc(t('Facts short')) + '</th>'
            + '<th scope="col" class="num">' + esc(t('Lines not read')) + '</th>'
            + '<th scope="col" class="num">' + esc(t('Errors')) + '</th>'
            + '</tr></thead><tbody>'
            + info.agents.map(function (agent) {
              return '<tr><td class="mono">' + esc(agent.agent) + '</td>'
                + '<td>' + chip(serverText(agent.agent_kind), agent.agent_kind === 'external' ? 'info'
                  : agent.agent_kind === 'autoclaw' ? 'primary' : 'neutral', { icon: false }) + '</td>'
                + '<td class="num">' + num(agent.sessions) + '</td>'
                + '<td class="num">' + num(agent.calls) + '</td>'
                + '<td class="num">' + num(agent.facts) + '</td>'
                + '<td class="num">' + num(Math.round((Number(agent.replacing_chars) || 0) / 45)) + '</td>'
                + '<td class="num' + (Number(agent.errors) ? ' text-[var(--bad)]' : '') + '">' + num(agent.errors) + '</td>'
                + '</tr>';
            }).join('')
            + '</tbody></table></div>'
          : emptyHtml({ icon: 'user', title: t('no data') }),
      })
      + '</div>';
  }

  return '<section class="section" data-section="usage">'
    + pageHead({
      title: t('Usage and effect'),
      desc: t('tokens and lines are estimates, calls and facts are measured'),
    })
    + '<div class="metrics metrics--wide">'
    + metric({ label: t('Calls'), value: bigNum(totals.calls), icon: 'activity', note: t('to facade operations') })
    + metric({ label: t('Sessions'), value: num(totals.sessions), icon: 'clock', note: t('tool runs') })
    + metric({ label: t('Facts'), value: bigNum(totals.facts), icon: 'shield', note: t('obtained from the index') })
    + metric({ label: t('Verifiability'), value: totals.verif.toFixed(1) + '%',
      tone: totals.verif >= 80 ? 'ok' : 'warn', icon: 'check', note: t('with a file:line reference') })
    + metric({ label: t('Tokens (estimate)'), value: bigNum(totals.tokens), icon: 'hash', note: t('by response size') })
    + metric({ label: t('Lines not read as files'), value: bigNum(totals.lines), icon: 'file',
      note: t('lines of code (estimate)') })
    + '</div>'
    + '<div class="split">'
    + card({ cls: 'split__aside', icon: 'rows', title: t('Developments'), desc: t('click a row to see the details'),
      flush: true, body: table })
    + card({ icon: 'activity', title: selectedId || t('no data'),
      desc: t('tokens and lines are estimates, calls and facts are measured'),
      actions: selected && !info.pending && !info.error
        ? chip((info.verif * 100).toFixed(1) + '% ' + t('Verifiability'),
          info.verif >= 0.9 ? 'ok' : info.verif >= 0.5 ? 'warn' : 'bad') : '',
      body: detailBody })
    + '</div>'
    + '</section>';
}

/* --------------------------------------------------------------------------
   Раздел «Размеры баз»
   --------------------------------------------------------------------------
   Вопрос раздела: что занимает место и где. Поэтому сначала итог по видам баз,
   затем по разработкам: размер каждого файла и состав таблиц. Журнал (wal)
   показан отдельно — он не часть данных, а след незавершённой записи.
   -------------------------------------------------------------------------- */

function renderStorage() {
  const entries = catalogEntries();
  const query = UI.storage.q.trim().toLowerCase();
  const withSize = entries.filter(function (entry) {
    return ((entry.size && entry.size.files) || []).length > 0;
  });
  const list = withSize.filter(function (entry) {
    return !query || String(entry.category).toLowerCase().indexOf(query) >= 0;
  });

  const totals = { all: 0, graph: 0, lex: 0, ctl: 0 };
  withSize.forEach(function (entry) {
    (entry.size.files || []).forEach(function (file) {
      const bytes = (Number(file.bytes) || 0) + (Number(file.wal_bytes) || 0);
      totals.all += bytes;
      if (file.title === 'граф кода') totals.graph += bytes;
      else if (file.title === 'лексика и векторы') totals.lex += bytes;
      else totals.ctl += bytes;
    });
  });

  const chartItems = withSize.map(function (entry) {
    const bytes = entryTotalBytes(entry);
    return { label: String(entry.category), value: bytes, text: humanBytes(bytes) };
  }).sort(function (a, b) { return b.value - a.value; }).slice(0, 12);

  const blocks = list.map(function (entry) {
    const id = String(entry.category);
    const open = UI.storage.open === id;
    const files = sizeBreakdown(entry);
    const tables = ((entry.size && entry.size.tables) || []).slice(0, 24);
    const maxRows = tables.reduce(function (top, table) { return Math.max(top, Number(table.rows) || 0); }, 0) || 1;
    return '<div class="accordion' + (open ? ' accordion--open' : '') + '">'
      + '<button type="button" class="accordion__head" data-action="storage-open" data-id="' + esc(id) + '"'
      + ' aria-expanded="' + (open ? 'true' : 'false') + '">'
      + icon(open ? 'chevronD' : 'chevronR', 'h-4 w-4')
      + '<b class="mono truncate">' + esc(id) + '</b>'
      + '<span class="mono small muted truncate hide-sm">' + esc(String(entry.root || '')) + '</span>'
      + '<span class="toolbar__spacer"></span>'
      + chip(humanBytes(entryTotalBytes(entry)), 'primary', { icon: false, mono: true })
      + '</button>'
      + (open
        ? '<div class="accordion__body">'
        + '<div class="cols-2">'
        + '<div class="table-wrap"><table><thead><tr>'
        + '<th scope="col">' + esc(t('Database')) + '</th>'
        + '<th scope="col">' + esc(t('File')) + '</th>'
        + '<th scope="col" class="num">' + esc(t('Size')) + '</th>'
        + '</tr></thead><tbody>'
        + files.map(function (file) {
          return '<tr><td class="nowrap">' + esc(file.title) + '</td>'
            + '<td>' + copyValue(String(file.path || '')) + '</td>'
            + '<td class="num">' + humanBytes(file.bytes)
            + (file.wal ? '<div class="small muted">' + esc(t('including journal: {size}', { size: humanBytes(file.wal) })) + '</div>' : '')
            + (file.missing ? '<div class="small">' + esc(t('file not found')) + '</div>' : '')
            + '</td></tr>';
        }).join('')
        + '</tbody></table></div>'
        + '<div class="table-wrap"><table><thead><tr>'
        + '<th scope="col">' + esc(t('Table')) + '</th>'
        + '<th scope="col" class="num">' + esc(t('Rows')) + '</th>'
        + '<th scope="col">' + esc(t('Share')) + '</th>'
        + '</tr></thead><tbody>'
        + (tables.length
          ? tables.map(function (table) {
            return '<tr><td class="mono">' + esc(table.name) + '</td>'
              + '<td class="num">' + num(table.rows) + '</td>'
              + '<td style="width:34%">' + progressHtml(((Number(table.rows) || 0) / maxRows) * 100, 'primary', table.name) + '</td>'
              + '</tr>';
          }).join('')
          : '<tr><td colspan="3" class="small muted">' + esc(t('no data')) + '</td></tr>')
        + '</tbody></table></div>'
        + '</div></div>'
        : '')
      + '</div>';
  }).join('');

  return '<section class="section" data-section="storage">'
    + pageHead({
      title: t('Database sizes'),
      desc: t('first 24 tables of each database are shown'),
    })
    + '<div class="metrics metrics--wide">'
    + metric({ label: t('Total'), value: humanBytes(totals.all), tone: 'primary', icon: 'database',
      note: t('all databases of all developments') })
    + metric({ label: t('code graph'), value: humanBytes(totals.graph), icon: 'network', note: 'graph.sqlite' })
    + metric({ label: t('lexicon and vectors'), value: humanBytes(totals.lex), icon: 'spark', note: 'lexvec.sqlite' })
    + metric({ label: t('journal and telemetry'), value: humanBytes(totals.ctl), icon: 'history', note: 'control.sqlite' })
    + '</div>'
    + card({
      icon: 'database', title: t('Distribution of volume'),
      desc: t('{count} largest developments, MB', { count: num(chartItems.length) }),
      body: barRows(chartItems, { empty: t('no data') }),
    })
    + card({
      flush: true,
      body: '<div class="toolbar">'
        + searchField({
          action: 'storage-search', value: UI.storage.q,
          placeholder: t('Search developments'), aria: t('Search developments'),
        })
        + '<span class="toolbar__spacer"></span>'
        + '<span class="mono small muted">' + esc(t('{count} databases', { count: num(list.length) })) + '</span>'
        + '</div>'
        + (blocks || emptyHtml({ icon: 'database', title: t('no data'),
          desc: t('No database matches the filter') })),
    })
    + '</section>';
}

/* --------------------------------------------------------------------------
   Раздел «Настройки»
   --------------------------------------------------------------------------
   Два уровня: значения по умолчанию (глобальные) и переопределения в конфиге
   разработки. Источник каждого значения назван явно — иначе непонятно, почему
   правка не подействовала: ключ может быть задан в конфиге корпуса, а глобальное
   значение при этом другое.
   -------------------------------------------------------------------------- */

/** Значение настройки: имя — ключ конфига, пояснение — текст сервера. */
function settingControl(key, value, meta, opts) {
  const o = opts || {};
  const desc = serverText((meta.descriptions || {})[key] || '');
  const bounds = (meta.bounds || {})[key];
  const allowed = (meta.enums || {})[key];
  const id = o.id || ('g-' + key);
  const data = o.project
    ? ' data-project="' + esc(o.project) + '" data-key="' + esc(key) + '"'
    : ' data-global="' + esc(key) + '"';

  let control;
  if (typeof value === 'boolean') {
    control = '<select id="' + esc(id) + '"' + data + ' aria-describedby="' + esc(id) + '-hint">'
      + '<option value="true"' + (value ? ' selected' : '') + '>' + esc(t('enabled')) + '</option>'
      + '<option value="false"' + (!value ? ' selected' : '') + '>' + esc(t('disabled')) + '</option>'
      + '</select>';
  } else if (allowed && allowed.length) {
    // Перечисление: список допустимых значений. Без этой ветки строковое
    // значение ('docs') подставлялось бы в поле числа, и администратор видел
    // пустое поле вместо режима.
    control = '<select id="' + esc(id) + '"' + data + ' aria-describedby="' + esc(id) + '-hint">'
      + allowed.map(function (candidate) {
        return '<option value="' + esc(candidate) + '"'
          + (String(value) === String(candidate) ? ' selected' : '') + '>'
          + esc(candidate) + '</option>';
      }).join('')
      + '</select>';
  } else {
    control = '<input type="number" id="' + esc(id) + '"' + data + ' value="' + esc(value) + '"'
      + (bounds ? ' min="' + esc(bounds[0]) + '" max="' + esc(bounds[1]) + '"' : '')
      + ' aria-describedby="' + esc(id) + '-hint">';
  }

  const hint = desc
    ? desc + (bounds ? ' ' + t('allowed range {low} to {high}. Available',
      { low: bounds[0], high: bounds[1] }) : '')
    : (bounds ? t('allowed range {low} to {high}. Available', { low: bounds[0], high: bounds[1] }) : '');

  return '<div class="field">'
    + '<label class="field__label" for="' + esc(id) + '">'
    + '<span class="mono">' + esc(key) + '</span>'
    + (bounds ? '<span class="field__bounds">' + esc(num(bounds[0]) + '…' + num(bounds[1])) + '</span>' : '')
    + '</label>'
    + control
    + '<p class="field__hint" id="' + esc(id) + '-hint">' + esc(truncate(hint, 190)) + '</p>'
    + '</div>';
}

/** Собрать значения глобальных полей. Имена `data-global` — контракт с сервером. */
function collectGlobal() {
  const values = {};
  document.querySelectorAll('[data-global]').forEach(function (el) {
    const key = el.getAttribute('data-global');
    const raw = el.value;
    values[key] = (raw === 'true' || raw === 'false') ? raw === 'true' : Number(raw);
  });
  return values;
}

function collectProject(name) {
  const values = {};
  document.querySelectorAll('[data-project="' + name + '"]').forEach(function (el) {
    const key = el.getAttribute('data-key');
    const raw = el.value;
    values[key] = (raw === 'true' || raw === 'false') ? raw === 'true' : Number(raw);
  });
  return values;
}

/** Плашка результата в разделе: остаётся на месте как запись о действии. */
function showMessage(text, kind) {
  const box = document.getElementById('message');
  if (!box) return;
  box.className = 'notice' + (kind ? ' notice--' + kind : '');
  box.textContent = text;
  box.hidden = false;
}

async function saveGlobal() {
  const box = document.getElementById('save-global');
  if (box) box.disabled = true;
  setStatus(t('saving…'));
  const data = await api('/api/settings/global', {
    method: 'POST', body: JSON.stringify({ values: collectGlobal() }),
  });
  if (box) box.disabled = false;
  if (data && data.ok) {
    setStatus(t('settings saved'), 'ok');
    const note = t('Global settings saved. {note}', { note: serverText(data.note || '') });
    showMessage(note, 'ok');
    toast({ tone: 'ok', title: t('settings saved'), desc: note });
    await refresh();
  } else {
    setStatus(t('save error'), 'bad');
    const reason = t('Could not save: {reason}',
      { reason: serverText((data && data.error) || t('unknown reason')) });
    showMessage(reason, 'bad');
    toast({ tone: 'bad', title: t('save error'), desc: reason });
  }
}

async function saveProject(name) {
  const button = document.querySelector('[data-action="save-project"][data-id="' + name + '"]');
  if (button) button.disabled = true;
  setStatus(t('saving…'));
  const data = await api('/api/settings/project', {
    method: 'POST',
    body: JSON.stringify({ category: name, values: collectProject(name) }),
  });
  if (button) button.disabled = false;
  if (data && data.ok) {
    setStatus(t('development settings saved'), 'ok');
    const note = t('Written to {config}: {keys}. A backup copy was created next to it.',
      { config: data.config, keys: Object.keys(data.written || {}).join(', ') });
    showMessage(note, 'ok');
    toast({ tone: 'ok', title: t('development settings saved'), desc: note });
    await refresh();
  } else {
    setStatus(t('save error'), 'bad');
    const reason = t('Could not save: {reason}',
      { reason: serverText((data && data.error) || t('unknown reason')) });
    showMessage(reason, 'bad');
    toast({ tone: 'bad', title: t('save error'), desc: reason });
  }
}

function renderSettings() {
  const meta = (currentState && currentState.settings) || {};
  const global = meta.global_settings || {};
  const perProject = meta.per_project || {};
  const editable = (meta.editable || []).filter(function (key) {
    return Object.prototype.hasOwnProperty.call(global, key);
  });
  const names = Object.keys(perProject);
  const tab = UI.settings.tab;

  const tabs = tabsHtml({
    group: 'settings', value: tab, label: t('Settings'), panel: 'settings-panel',
    items: [
      { k: 'global', label: t('Defaults'), icon: 'sliders' },
      { k: 'project', label: t('By development'), icon: 'folder', count: num(names.length) },
    ],
  });

  const head = pageHead({
    title: t('Settings'),
    desc: t('auto-reindex thresholds and indexing parameters'),
    actions: tabs,
  });

  // Панель вкладок: `aria-labelledby` указывает на выбранную вкладку, а
  // `tabindex="0"` позволяет войти в содержимое с клавиатуры сразу после вкладки.
  const panelOpen = '<div class="stack" id="settings-panel" role="tabpanel" tabindex="0"'
    + ' aria-labelledby="tab-settings-' + esc(tab) + '">';
  const panelClose = '</div>';

  if (tab === 'global') {
    const visible = editable.filter(function (key) {
      const query = UI.settings.q.trim().toLowerCase();
      return !query || key.toLowerCase().indexOf(query) >= 0
        || serverText((meta.descriptions || {})[key] || '').toLowerCase().indexOf(query) >= 0;
    });
    return '<section class="section" data-section="settings">' + head
      + panelOpen
      + (writeAllowed() ? '' : deniedCallout())
      + card({
        icon: 'sliders', title: t('Defaults'),
        desc: t('they apply to developments where the key is not set separately'),
        actions: searchField({
          action: 'settings-search', value: UI.settings.q,
          placeholder: t('Setting'), aria: t('Setting'),
        }),
        body: visible.length
          ? '<div class="grid-2">' + visible.map(function (key) {
            return settingControl(key, global[key], meta, {});
          }).join('') + '</div>'
          : emptyHtml({ icon: 'search', title: t('Nothing found for the given conditions') }),
        foot: button({
          v: 'primary', s: 'md', icon: 'check', label: t('Save global settings'),
          action: 'save-global', id: 'save-global', disabled: !writeAllowed(),
        }) + '<span class="small muted">'
          + esc(t('they apply to developments where the key is not set separately')) + '</span>',
      })
      + panelClose
      + '</section>';
  }

  const query = UI.settings.q.trim().toLowerCase();
  const list = names.filter(function (name) { return !query || name.toLowerCase().indexOf(query) >= 0; });
  if (!names.length) {
    return '<section class="section" data-section="settings">' + head
      + panelOpen
      + card({ body: emptyHtml({ icon: 'folder', title: t('No developments') }) })
      + panelClose + '</section>';
  }
  const selected = names.indexOf(UI.settings.sel) >= 0 ? UI.settings.sel : names[0];
  UI.settings.sel = selected;
  const project = perProject[selected] || {};

  const aside = card({
    cls: 'split__aside',
    body: '<div class="stack">'
      + searchField({
        action: 'settings-project-search', value: UI.settings.q,
        placeholder: t('Development'), aria: t('Search developments'),
      })
      + '<div class="scroller list">' + (list.length ? list.map(function (name) {
        return '<button type="button" class="list__item' + (name === selected ? ' list__item--active' : '') + '"'
          + ' data-action="settings-select" data-id="' + esc(name) + '"'
          + (name === selected ? ' aria-current="true"' : '') + '>'
          + icon('sliders', 'h-3.5 w-3.5')
          + '<span class="min-w-0 flex-1"><span class="list__name">' + esc(name) + '</span></span>'
          + (project.config_exists === false && name === selected
            ? chip(t('config not found'), 'warn', { icon: false }) : '')
          + '</button>';
      }).join('') : emptyHtml({ icon: 'search', title: t('Nothing found for the given conditions') }))
        + '</div></div>',
  });

  const rows = editable.map(function (key) {
    const source = serverText(project[key + '__source'] || '—');
    const overridden = String(project[key + '__source'] || '') === 'проект';
    return '<tr>'
      + '<td><span class="mono">' + esc(key) + '</span>'
      + '<div class="mt-1">' + (overridden
        ? chip(t('set in the corpus config'), 'primary', { icon: 'check' })
        : chip(t('inherited globally'), 'neutral', { icon: 'dot' })) + '</div></td>'
      + '<td style="min-width:11rem">' + settingControl(key, project[key], meta,
        { project: selected, id: 'p-' + selected + '-' + key }) + '</td>'
      + '<td class="small muted">' + esc(truncate(serverText((meta.descriptions || {})[key] || ''), 130)) + '</td>'
      + '</tr>';
  }).join('');

  const table = rows
    ? '<div class="table-wrap"><table><thead><tr>'
      + '<th scope="col">' + esc(t('Setting')) + '</th>'
      + '<th scope="col">' + esc(t('Value')) + '</th>'
      + '<th scope="col">' + esc(t('Explanation')) + '</th>'
      + '</tr></thead><tbody>' + rows + '</tbody></table></div>'
    : '<div class="card__body">' + emptyHtml({ icon: 'sliders', title: t('no data') }) + '</div>';

  return '<section class="section" data-section="settings">' + head
    + panelOpen
    + (writeAllowed() ? '' : deniedCallout())
    + '<div class="split">' + aside
    + card({
      icon: 'sliders', title: selected + ' — '
        + (project.config_exists ? t('config found') : t('config not found')),
      desc: t('the change affects only future checks; the current revision is unchanged'),
      actions: copyValue(String(project.config_path || ''), { shown: t('Copy') }),
      flush: true,
      body: table,
      foot: button({
        v: 'primary', s: 'md', icon: 'check', label: t('Save to corpus config'),
        action: 'save-project', data: { id: selected }, disabled: !writeAllowed(),
      }) + '<span class="small muted">'
        + esc(t('the change affects only future checks; the current revision is unchanged')) + '</span>',
    })
    + '</div>' + panelClose + '</section>';
}

/* --------------------------------------------------------------------------
   Раздел «Режим и клиенты»
   --------------------------------------------------------------------------
   Режим описывает ОЖИДАЕМЫЙ канал и проверяется пробой; второй канал остаётся
   доступен — CLI и MCP не конфликтуют. Инструкция по подключению приходит с
   сервера (`state.clients`), панель её не сочиняет: значения полей должны
   совпадать с тем, что ждёт клиент.
   -------------------------------------------------------------------------- */

/** Выбранный режим. Ответ на применение приходит СТРОКОЙ, на чтение — объектом. */
function modeValueOf(state) {
  const raw = (state && state.mode) || {};
  return typeof raw === 'string' ? raw : String(raw.mode || '');
}

/** Значение поля для таблицы: переносы строк сохраняются (ENV идёт строками). */
function fieldValueHtml(value) {
  return esc(value).replace(/\n/g, '<br>');
}

/** Запись о клиенте по имени: проба панели не должна выглядеть как подключение. */
function pickContact(kind, contact) {
  // Выводы считает сервер (`contact.links`): правило сопоставления имён одно,
  // иначе интерфейс и модуль следов разойдутся. Местное сравнение осталось
  // запасным — на случай ответа прежней версии панели.
  const links = (contact && contact.links) || {};
  if (links[kind]) return links[kind];
  const words = kind === 'harness' ? ['harness', 'deepseek', 'dsh']
    : ['autoclaw', 'zwork'];
  const rows = (contact && contact.clients) || [];
  for (let i = 0; i < rows.length; i += 1) {
    // Имя, которым представился САМ клиент, важнее имени из параметра
    // запуска: HTTP-сервис поднимается с `--agent AutoClaw`.
    const name = String(rows[i].client_info || rows[i].agent || '').toLowerCase();
    if (name && words.some(function (w) { return name.indexOf(w) >= 0; })) return rows[i];
  }
  return null;
}

/** Сколько прошло: словами, а не секундами. */
function agoText(seconds) {
  if (seconds === null || seconds === undefined) return '—';
  if (seconds < 90) return t('{n} seconds', { n: Math.max(1, Math.round(seconds)) });
  if (seconds < 5400) return t('{n} minutes', { n: Math.round(seconds / 60) });
  return t('{n} hours', { n: Math.round(seconds / 3600) });
}

/** Состояние связи: красное — обращений не видно, жёлтое — сервис без обращений,
 *  зелёное — обращение было. Следы пишет сам MCP-сервер (`ragraf/contact.py`). */
function contactLineHtml(kind, clients) {
  const name = kind === 'harness' ? 'HARNESS' : 'AutoClaw';
  const contact = (clients && clients.contact) || {};
  const record = pickContact(kind, contact);
  const serviceUp = !!(clients && clients.service_running);
  const who = record ? String(record.client_info || record.agent || '') : '';
  const tool = (record && record.last_tool)
    ? ' · ' + t('last tool: {tool}', { tool: record.last_tool }) : '';
  if (record && record.recent) {
    return '<p class="status status--ok">'
      + esc(t('Connected: last call {ago} ago', { ago: agoText(record.age_seconds) })
            + (who ? ' · ' + who : '') + tool)
      + '</p>';
  }
  if (kind === 'autoclaw' && serviceUp) {
    return '<p class="status status--warn">'
      + esc(t('The service is up, no calls from {name} yet', { name: name })) + '</p>';
  }
  if (record) {
    return '<p class="status status--warn">'
      + esc(t('No calls for {ago}: the connection may be broken',
              { ago: agoText(record.age_seconds) })) + '</p>';
  }
  return '<p class="status status--bad">'
    + esc(t('No connection from {name} yet', { name: name })) + '</p>';
}

/** Инструкция по подключению для выбранного режима: AutoClaw и HARNESS. */
function modeSetupHtml(state) {
  const value = modeValueOf(state);
  if (!value) return '';
  const clients = (state && state.clients) || {};
  const ac = clients.autoclaw || {};
  const hs = clients.harness || {};

  if (value === 'cli') {
    return '<p class="muted">' + esc(t('CLI needs no client: the agent calls'))
      + ' <code>python tools/ragraf.py -p &lt;config&gt; &lt;command&gt;</code>. '
      + esc(t('This channel works without AutoClaw or HARNESS settings.')) + '</p>';
  }

  const wantsHttp = value === 'mcp-http' || value === 'both';
  const wantsStdio = value === 'mcp-stdio' || value === 'both';
  const parts = [];

  // --- AutoClaw: ОБЕ формы подключения, как их показывает сам AutoClaw.
  //     Владелец спросил про «Local program»: прежде блок описывал только
  //     Online service, хотя локальный запуск не требует ни порта, ни доступа.
  const acVariants = (ac.variants && ac.variants.length) ? ac.variants : [ac];
  const acBlocks = acVariants.map(function (variant) {
    const fields = (variant.fields || []).map(function (f) {
      return '<tr><td>' + esc(f.label) + '</td><td><code>'
        + fieldValueHtml(f.value) + '</code></td></tr>';
    }).join('');
    const commands = (variant.before || []).map(function (line) {
      return '<li><code>' + esc(line) + '</code></li>';
    }).join('');
    return '<h4>' + esc(serverText(variant.type || '')) + '</h4>'
      + '<p class="muted">' + esc(serverText(variant.how || '')) + '</p>'
      + (commands
          ? '<p class="muted">' + esc(t('Start the service first:')) + '</p><ul>'
            + commands + '</ul>'
          : '')
      + (fields
          ? '<table><thead><tr><th>' + esc(t('field')) + '</th><th>' + esc(t('value'))
            + '</th></tr></thead><tbody>' + fields + '</tbody></table>'
          : '')
      + (variant.access_note ? '<p class="muted">' + esc(serverText(variant.access_note)) + '</p>' : '');
  }).join('');
  parts.push('<h4>' + esc(t('Connection in AutoClaw')) + '</h4>'
    + contactLineHtml('autoclaw', clients)
    + '<p class="muted">'
    + esc(t('Both forms are available: Online service and Local program.')) + '</p>'
    + acBlocks
    + (wantsHttp ? ''
        : '<p class="notice notice--warn">'
          + esc(t('AutoClaw connects over HTTP: the selected mode does not expect that '
            + 'channel. Start the service shown here or pick mcp-http or both.'))
          + '</p>'));

  // --- HARNESS: показываются только те варианты подключения, что отвечают
  //     выбранному каналу. Конфигурацию даёт сервер, страница её не сочиняет.
  const wanted = (wantsHttp && wantsStdio) ? ['stdio', 'streamable-http']
    : (wantsHttp ? ['streamable-http'] : ['stdio']);
  const rows = (hs.variants || []).filter(function (variant) {
    return wanted.indexOf(variant.transport) >= 0;
  }).map(function (variant) {
    return '<h4>' + esc(variant.transport) + '</h4>'
      + (variant.note ? '<p class="muted">' + esc(serverText(variant.note)) + '</p>' : '')
      + '<pre><code>' + esc(JSON.stringify(variant.entry, null, 1)) + '</code></pre>'
      + (variant.path
          ? '<p class="muted">' + esc(t('The profile file:')) + ' <code>' + esc(variant.path)
            + '</code></p>'
          : '');
  }).join('');
  parts.push('<h4>' + esc(t('Connection in HARNESS')) + '</h4>'
    + contactLineHtml('harness', clients)
    + (rows || '<p class="muted">' + esc(t('no data')) + '</p>'));

  return parts.join('');
}

function renderMode() {
  const raw = (currentState && currentState.mode) || {};
  const mode = (typeof raw === 'string') ? { mode: raw } : raw;
  const probe = (currentState && currentState.probe) || {};
  const clients = (currentState && currentState.clients) || {};
  const modes = (mode.modes && mode.modes.length) ? mode.modes : ['cli', 'mcp-stdio', 'mcp-http', 'both'];
  const ac = clients.autoclaw || {};
  const hs = clients.harness || {};
  const sagaai = clients.sagaai || {};

  const options = modes.map(function (value) {
    return '<option value="' + esc(value) + '"' + (value === mode.mode ? ' selected' : '') + '>'
      + esc(value) + '</option>';
  }).join('');

  const channels = (probe.channels || []).length
    ? (probe.channels || []).map(function (channel) {
      return '<tr><td class="mono">' + esc(serverText(channel.title)) + '</td>'
        + '<td>' + chip(channel.ok ? t('works') : t('none'), channel.ok ? 'ok' : 'bad') + '</td>'
        + '<td>' + esc(serverText(channel.detail || '')) + '</td>'
        + '<td class="small muted">' + esc(serverText(channel.action || '—')) + '</td></tr>';
    }).join('')
    : '<tr><td colspan="4" class="small muted">' + esc(t('no probes yet')) + '</td></tr>';

  const probeClients = (probe.clients || []).length
    ? (probe.clients || []).map(function (client) {
      return '<tr><td class="mono">' + esc(serverText(client.title)) + '</td>'
        + '<td>' + chip(client.ok ? t('connected') : t('none'), client.ok ? 'ok' : 'bad') + '</td>'
        + '<td>' + esc(serverText(client.detail || '')) + '</td>'
        + '<td class="small muted">' + esc(serverText(client.action || '—')) + '</td></tr>';
    }).join('')
    : '<tr><td colspan="4" class="small muted">' + esc(t('no data')) + '</td></tr>';

  const acFields = (ac.fields || []).map(function (f) {
    return '<tr><td>' + esc(f.label) + '</td><td><code>'
      + fieldValueHtml(f.value) + '</code></td></tr>';
  }).join('');

  const variants = (hs.variants || []).map(function (variant) {
    return '<option value="' + esc(variant.transport) + '"'
      + (variant.transport === UI.mode.transport ? ' selected' : '') + '>'
      + esc(variant.transport) + '</option>';
  }).join('');

  const verdict = probe.channels
    ? (probe.mode_ok
      ? chip(t('mode confirmed'), 'ok')
      : chip(t('mode NOT confirmed'), 'bad'))
    : chip(t('verification not started'), 'neutral', { icon: 'dot' });

  const channelCard = card({
    icon: 'network', title: t('RAGRAF operating channel'),
    desc: t('The mode describes the EXPECTED channel and is verified by a probe. '
      + 'The other channel stays available: CLI and MCP do not conflict.'),
    body: '<div class="inline">'
      + '<div class="field" style="min-width:11rem"><label class="field__label" for="mode-select">'
      + esc(t('RAGRAF operating channel')) + '</label>'
      + '<select id="mode-select" data-action="mode-select" aria-label="'
      + esc(t('RAGRAF operating channel')) + '">'
      + options + '</select></div>'
      + button({
        v: 'primary', s: 'md', icon: 'check', label: t('Apply and verify'),
        action: 'mode-apply', disabled: !writeAllowed(),
      })
      + button({
        v: 'secondary', s: 'md', icon: 'zap', label: t('Verify'),
        action: 'mode-probe', id: 'mode-probe',
      })
      + verdict
      + '</div>'
      + '<div class="mt-4" id="mode-instructions">' + modeSetupHtml(currentState) + '</div>',
  });

  const probeCards = '<div class="cols-2">'
    + card({
      icon: 'link', title: t('Channels'), desc: t('the result of the last probe for each transport'),
      flush: true,
      body: '<div class="table-wrap"><table><thead><tr>'
        + '<th scope="col">' + esc(t('channel')) + '</th>'
        + '<th scope="col">' + esc(t('state')) + '</th>'
        + '<th scope="col">' + esc(t('what is visible')) + '</th>'
        + '<th scope="col">' + esc(t('what to do')) + '</th>'
        + '</tr></thead><tbody>' + channels + '</tbody></table></div>',
    })
    + card({
      icon: 'user', title: t('Clients'), desc: t('registered connections and their agents'),
      flush: true,
      body: '<div class="table-wrap"><table><thead><tr>'
        + '<th scope="col">' + esc(t('client')) + '</th>'
        + '<th scope="col">' + esc(t('state')) + '</th>'
        + '<th scope="col">' + esc(t('what is visible')) + '</th>'
        + '<th scope="col">' + esc(t('what to do')) + '</th>'
        + '</tr></thead><tbody>' + probeClients + '</tbody></table></div>',
    })
    + '</div>';

  const autoclawCard = card({
    icon: 'box', title: t('AutoClaw: values for the “Add tool” form'),
    desc: serverText(ac.how || ''),
    body: (acFields
      ? '<div class="table-wrap"><table><thead><tr>'
        + '<th scope="col">' + esc(t('field')) + '</th><th scope="col">' + esc(t('value')) + '</th>'
        + '</tr></thead><tbody>' + acFields + '</tbody></table></div>'
      : emptyHtml({ icon: 'box', title: t('no data') }))
      + '<p class="small muted mt-3">' + esc(serverText(ac.access_note || '')) + '</p>'
      + '<div class="inline mt-3">'
      + '<div class="field" style="min-width:14rem"><label class="field__label" for="client-label">'
      + esc(t('client')) + '</label>'
      + '<input id="client-label" placeholder="' + esc(t('client name (for example AutoClaw)')) + '"></div>'
      + button({
        v: 'primary', s: 'md', icon: 'key', label: t('Grant access'),
        action: 'client-access', id: 'client-access', disabled: !writeAllowed(),
      })
      + '</div>'
      + '<p class="mono small mt-2" id="client-issued" hidden></p>',
  });

  const harnessCard = card({
    icon: 'terminal', title: t('DeepSeek Harness: profile block'),
    desc: t('profile: ') + String(hs.path || '') + (hs.installed
      ? t('; connection is configured, transport {transport}', { transport: hs.installed })
      : t('; no connection')),
    body: '<div class="inline">'
      + '<div class="field" style="min-width:11rem"><label class="field__label" for="harness-transport">'
      + esc(t('channel')) + '</label>'
      + '<select id="harness-transport" aria-label="' + esc(t('channel')) + '">' + variants + '</select></div>'
      + button({
        v: 'secondary', s: 'md', icon: 'check', label: t('Apply block'),
        action: 'harness-apply', id: 'harness-apply', disabled: !writeAllowed(),
      })
      + '</div>'
      + '<p class="mono small mt-2" id="harness-result" hidden></p>',
  });

  const sagaaiCard = card({
    icon: 'spark', title: 'SagaAI',
    desc: t('tool: ') + String(sagaai.tool_path || '') + ' — '
      + (sagaai.tool_exists ? t('found') : t('not found '))
      + t('; corpus config: ') + (sagaai.config_exists ? t('in place') : t('not found ')),
    body: '<p class="small muted">' + esc(serverText(sagaai.note || '')) + '</p>',
  });

  return '<section class="section" data-section="mode">'
    + pageHead({
      title: t('Mode and clients'),
      desc: t('The mode describes the EXPECTED channel and is verified by a probe. '
        + 'The other channel stays available: CLI and MCP do not conflict.'),
    })
    + (writeAllowed() ? '' : deniedCallout())
    + channelCard
    + probeCards
    + '<div class="cols-2">' + autoclawCard + harnessCard + '</div>'
    + sagaaiCard
    + '</section>';
}

/* --------------------------------------------------------------------------
   Раздел «Доступ»
   --------------------------------------------------------------------------
   Три вопроса по порядку: чем входят (пароль и токены), что этим делали
   (обращения по доступам) и что именно произошло (журнал). Значение нового
   токена показывается ОДИН раз: сервер его не хранит и повторно не отдаст.
   -------------------------------------------------------------------------- */

async function accessPasswordSet() {
  const pwd = document.getElementById('access-pwd');
  const current = document.getElementById('access-pwd-current');
  const data = await api('/api/auth/password', {
    method: 'POST',
    body: JSON.stringify({ password: pwd ? pwd.value : '', current: current ? current.value : '' }),
  });
  const text = data.ok ? t('password set') : serverText(data.error || t('could not'));
  showMessage(text, data.ok ? 'ok' : 'bad');
  toast({ tone: data.ok ? 'ok' : 'bad', title: text });
  await refresh();
}

async function accessPasswordClear() {
  const current = document.getElementById('access-pwd-current');
  const data = await api('/api/auth/password/clear', {
    method: 'POST', body: JSON.stringify({ current: current ? current.value : '' }),
  });
  const text = data.ok ? t('password cleared') : serverText(data.error || t('could not'));
  showMessage(text, data.ok ? 'ok' : 'bad');
  toast({ tone: data.ok ? 'warn' : 'bad', title: text });
  await refresh();
}

async function accessTokenCreate() {
  const label = document.getElementById('access-label');
  const note = document.getElementById('access-note');
  const scopes = document.getElementById('access-scopes');
  const data = await api('/api/tokens/create', {
    method: 'POST',
    body: JSON.stringify({
      label: label ? label.value : '',
      note: note ? note.value : '',
      scopes: [scopes ? scopes.value : 'read'],
    }),
  });
  const out = document.getElementById('access-issued');
  if (out) {
    out.hidden = false;
    out.textContent = data.ok && data.issued_once
      ? t('the value is issued once, copy it now: {value}', { value: data.issued_once.value })
      : serverText(data.error || t('could not create'));
  }
  if (data.ok && data.issued_once) {
    toast({ tone: 'ok', title: t('Create token'), desc: t('the value is issued once, copy it now: {value}', { value: data.issued_once.value }) });
  } else {
    toast({ tone: 'bad', title: t('could not create'), desc: serverText(data.error || '') });
  }
  await refresh();
}

async function accessTokenAction(action, tokenId) {
  const endpoints = { revoke: '/api/tokens/revoke', restore: '/api/tokens/restore' };
  const data = await api(endpoints[action], {
    method: 'POST', body: JSON.stringify({ token_id: tokenId }),
  });
  const ok = { revoke: t('access revoked'), restore: t('access restored') }[action];
  const text = data.ok ? ok : serverText(data.error || t('could not'));
  showMessage(text, data.ok ? 'ok' : 'bad');
  toast({ tone: data.ok ? 'warn' : 'bad', title: text });
  await refresh();
}

async function accessTokenDelete(tokenId) {
  const agreed = await confirmDialog({
    tone: 'bad', irreversible: true,
    title: t('Delete this access record? The call history will remain.'),
    confirm: t('delete'),
  });
  if (!agreed) return;
  const data = await api('/api/tokens/delete', {
    method: 'POST', body: JSON.stringify({ token_id: tokenId }),
  });
  const text = data.ok ? t('record deleted') : serverText(data.error || t('could not'));
  showMessage(text, data.ok ? 'ok' : 'bad');
  toast({ tone: data.ok ? 'warn' : 'bad', title: text });
  await refresh();
}

function renderAccess() {
  const access = (currentState && currentState.access) || {};
  const tokens = (access.tokens && access.tokens.tokens) || [];
  const log = access.log || {};
  const auth = (currentState && currentState.auth) || {};
  const allowed = writeAllowed();

  const passwordCard = card({
    icon: 'lock', title: t('Administrator password'),
    desc: auth.password_set
      ? t('password is set') + (auth.password_updated_at ? ' · ' + stamp(auth.password_updated_at) : '')
      : t('password is not set — sign-in is possible with the session token'),
    actions: auth.password_set ? chip(t('enabled'), 'ok') : chip(t('not set'), 'warn'),
    body: '<div class="grid-2">'
      + '<div class="field"><label class="field__label" for="access-pwd">'
      + esc(t('new password (at least {count} characters)', { count: auth.min_length || 8 })) + '</label>'
      + '<input type="password" id="access-pwd" autocomplete="new-password" placeholder="••••••••"'
      + (allowed ? '' : ' disabled') + '></div>'
      + '<div class="field"><label class="field__label" for="access-pwd-current">'
      + esc(t('current password (when changing)')) + '</label>'
      + '<input type="password" id="access-pwd-current" autocomplete="current-password" placeholder="••••••••"'
      + (allowed ? '' : ' disabled') + '></div>'
      + '</div>'
      + '<div class="row-actions">'
      + button({
        v: 'primary', s: 'md', icon: 'lock', label: t('Set password'),
        action: 'pwd-set', id: 'access-pwd-set', disabled: !allowed,
      })
      + button({
        v: 'danger', s: 'md', icon: 'x', label: t('Clear password'),
        action: 'pwd-clear', id: 'access-pwd-clear', disabled: !allowed || !auth.password_set,
      })
      + '</div>',
  });

  const tokenRows = tokens.length
    ? tokens.map(function (token) {
      const state = token.revoked_at ? t('revoked') : (token.active ? t('active') : t('inactive'));
      const tone = token.revoked_at ? 'bad' : (token.active ? 'ok' : 'neutral');
      return '<tr>'
        + '<td><b>' + esc(token.label) + '</b>'
        + (token.note ? '<div class="small muted">' + esc(serverText(token.note)) + '</div>' : '') + '</td>'
        + '<td>' + (token.scopes || []).map(function (scope) {
          return chip(scope, 'neutral', { icon: false, mono: true });
        }).join(' ') + '</td>'
        + '<td>' + chip(state, tone) + '</td>'
        + '<td class="num">' + num(token.use_count || 0) + '</td>'
        + '<td class="small nowrap">' + esc(stamp(token.last_used_at)) + '</td>'
        + '<td class="num nowrap">'
        + (token.revoked_at
          ? button({
            v: 'secondary', s: 'xs', icon: 'check', label: t('restore'),
            action: 'token-restore', data: { id: token.token_id }, disabled: !allowed,
          })
          : button({
            v: 'danger', s: 'xs', icon: 'x', label: t('revoke'),
            action: 'token-revoke', data: { id: token.token_id }, disabled: !allowed,
          }))
        + button({
          v: 'ghost', s: 'icon-xs', icon: 'trash', action: 'token-delete',
          data: { id: token.token_id }, disabled: !allowed,
          aria: 'aria-label="' + esc(t('delete') + ': ' + token.label) + '"',
        })
        + '</td></tr>';
    }).join('')
    : '<tr><td colspan="6" class="small muted">' + esc(t('no tokens issued')) + '</td></tr>';

  const tokensCard = card({
    icon: 'key', title: t('Issued tokens'),
    desc: t('The value of a new token is shown once and is not stored by the panel'),
    actions: chip(String(tokens.length), 'neutral', { icon: false, mono: true }),
    body: '<div class="grid-2">'
      + '<div class="field"><label class="field__label" for="access-label">'
      + esc(t('issued to (required)')) + '</label>'
      + '<input id="access-label" placeholder="AutoClaw"' + (allowed ? '' : ' disabled') + '></div>'
      + '<div class="field"><label class="field__label" for="access-note">'
      + esc(t('note')) + '</label>'
      + '<input id="access-note" placeholder="' + esc(t('note')) + '"' + (allowed ? '' : ' disabled') + '></div>'
      + '</div>'
      + '<div class="row-actions">'
      + '<div class="field" style="min-width:9rem"><label class="field__label" for="access-scopes">'
      + esc(t('scopes')) + '</label>'
      + '<select id="access-scopes"' + (allowed ? '' : ' disabled') + '>'
      + (access.scopes || ['read']).map(function (scope) {
        return '<option value="' + esc(scope) + '"' + (scope === 'read' ? ' selected' : '') + '>'
          + esc(scope) + '</option>';
      }).join('')
      + '</select></div>'
      + button({
        v: 'primary', s: 'md', icon: 'plus', label: t('Create token'),
        action: 'token-create', id: 'access-create', disabled: !allowed,
      })
      + '</div>'
      + '<p class="notice notice--ok mt-3" id="access-issued" hidden></p>'
      + '<div class="table-wrap mt-3"><table><thead><tr>'
      + '<th scope="col">' + esc(t('issued to')) + '</th>'
      + '<th scope="col">' + esc(t('scopes')) + '</th>'
      + '<th scope="col">' + esc(t('state short')) + '</th>'
      + '<th scope="col" class="num">' + esc(t('uses')) + '</th>'
      + '<th scope="col">' + esc(t('last use')) + '</th>'
      + '<th scope="col" class="num">' + esc(t('Actions')) + '</th>'
      + '</tr></thead><tbody>' + tokenRows + '</tbody></table></div>',
  });

  const byAccess = (log.by_token || []);
  const byAccessCard = card({
    icon: 'activity', title: t('Calls by access'),
    desc: t('identical “access + result” pairs are collapsed; the repeat count is in the row'),
    flush: true,
    body: byAccess.length
      ? '<div class="table-wrap"><table><thead><tr>'
        + '<th scope="col">' + esc(t('access')) + '</th>'
        + '<th scope="col" class="num">' + esc(t('Calls short')) + '</th>'
        + '<th scope="col" class="num">' + esc(t('successful')) + '</th>'
        + '<th scope="col" class="num">' + esc(t('denied')) + '</th>'
        + '<th scope="col" class="num">' + esc(t('errors')) + '</th>'
        + '<th scope="col">' + esc(t('Share')) + '</th>'
        + '</tr></thead><tbody>'
        + byAccess.map(function (row) {
          const calls = Number(row.calls) || 0;
          const rate = calls ? ((Number(row.ok) || 0) / calls) * 100 : 0;
          return '<tr><td>' + esc(serverText(row.label || t('(no access)'))) + '</td>'
            + '<td class="num">' + num(calls) + '</td>'
            + '<td class="num text-[var(--ok)]">' + num(row.ok) + '</td>'
            + '<td class="num text-[var(--warn)]">' + num(row.denied) + '</td>'
            + '<td class="num' + (Number(row.errors) ? ' text-[var(--bad)]' : '') + '">' + num(row.errors) + '</td>'
            + '<td style="width:16%">' + progressHtml(rate, rate > 80 ? 'ok' : rate > 40 ? 'warn' : 'bad',
              serverText(row.label || '')) + '</td></tr>';
        }).join('')
        + '</tbody></table></div>'
      : emptyHtml({ icon: 'activity', title: t('no data') }),
  });

  const entries = log.entries || [];
  const query = UI.access.q.trim().toLowerCase();
  const rows = entries.filter(function (row) {
    if (UI.access.channel !== 'all' && row.surface !== UI.access.channel) return false;
    if (UI.access.result !== 'all' && row.result !== UI.access.result) return false;
    if (!query) return true;
    return [row.operation, row.label, row.reason, row.principal, row.surface]
      .join(' ').toLowerCase().indexOf(query) >= 0;
  });
  const perPage = UI.access.perPage;
  const pages = Math.max(1, Math.ceil(rows.length / perPage));
  const page = Math.min(UI.access.page, pages - 1);
  UI.access.page = page;
  const view = rows.slice(page * perPage, page * perPage + perPage);
  const channels = [];
  entries.forEach(function (row) {
    if (row.surface && channels.indexOf(row.surface) < 0) channels.push(row.surface);
  });

  const logCard = card({
    icon: 'history', title: t('Access log'),
    desc: t('channel, operation, sign-in method and the reason for a refusal'),
    flush: true,
    body: '<div class="toolbar">'
      + searchField({
        action: 'access-search', value: UI.access.q,
        placeholder: t('operation, name or reason…'), aria: t('Access log'),
      })
      + '<div class="field" style="min-width:9rem"><label class="sr-only" for="access-channel">'
      + esc(t('channel')) + '</label>'
      + '<select id="access-channel" data-action="access-channel" aria-label="' + esc(t('channel')) + '">'
      + '<option value="all">' + esc(t('All channels')) + '</option>'
      + channels.map(function (name) {
        return '<option value="' + esc(name) + '"' + (name === UI.access.channel ? ' selected' : '') + '>'
          + esc(name) + '</option>';
      }).join('')
      + '</select></div>'
      + '<div class="field" style="min-width:9rem"><label class="sr-only" for="access-result">'
      + esc(t('result')) + '</label>'
      + '<select id="access-result" data-action="access-result" aria-label="' + esc(t('result')) + '">'
      + ['all', 'ok', 'error', 'denied'].map(function (value) {
        return '<option value="' + value + '"' + (value === UI.access.result ? ' selected' : '') + '>'
          + esc(value === 'all' ? t('All results') : value) + '</option>';
      }).join('')
      + '</select></div>'
      + '<span class="toolbar__spacer"></span>'
      + '<span class="mono small muted">' + esc(num(rows.length)) + '</span>'
      + '</div>'
      + (view.length
        ? '<div class="table-wrap table-wrap--tall"><table><thead><tr>'
          + '<th scope="col">' + esc(t('when')) + '</th>'
          + '<th scope="col">' + esc(t('surface')) + '</th>'
          + '<th scope="col">' + esc(t('operation')) + '</th>'
          + '<th scope="col">' + esc(t('name')) + '</th>'
          + '<th scope="col">' + esc(t('sign-in method')) + '</th>'
          + '<th scope="col">' + esc(t('result')) + '</th>'
          + '<th scope="col">' + esc(t('reason')) + '</th>'
          + '</tr></thead><tbody>'
          + view.map(function (row) {
            return '<tr>'
              + '<td class="mono nowrap small">' + esc(row.ts ? stamp(Number(row.ts)) : '—') + '</td>'
              + '<td>' + chip(row.surface, 'neutral', { icon: false }) + '</td>'
              + '<td class="mono">' + esc(row.operation) + '</td>'
              + '<td>' + esc(serverText(row.label || t('(no name)'))) + '</td>'
              + '<td class="mono small">' + esc(serverText(row.principal || '')) + '</td>'
              + '<td>' + chip(row.result, row.result === 'ok' ? 'ok' : row.result === 'denied' ? 'bad' : 'warn') + '</td>'
              + '<td class="small muted">' + esc(serverText(row.reason || '')) + '</td>'
              + '</tr>';
          }).join('')
          + '</tbody></table></div>'
        : emptyHtml({ icon: 'search', title: t('no records') }))
      + (rows.length > perPage ? pagerHtml('access', rows.length, page, perPage) : ''),
  });

  return '<section class="section" data-section="access">'
    + pageHead({
      title: t('Access'),
      desc: t('administrator password, tokens, calls by access and the log'),
    })
    + (allowed ? '' : deniedCallout())
    + '<div class="cols-2">' + passwordCard + tokensCard + '</div>'
    + byAccessCard
    + logCard
    + '</section>';
}

/* --------------------------------------------------------------------------
   Раздел «Базы данных»
   --------------------------------------------------------------------------
   RAGRAF читает СТРУКЦИЮ баз: базы, таблицы, колонки, типы, индексы,
   представления и связи. Значения колонок, признанных персональными данными
   или секретами, не собираются вовсе — на их месте стоит пометка «скрыто».
   Операций записи и изменения структуры нет: только чтение.
   -------------------------------------------------------------------------- */

/** Значения формы доступа к базам. Имена полей — контракт с сервером. */
function databaseFormValues() {
  function val(id) {
    const el = document.getElementById(id);
    return el ? el.value.trim() : '';
  }
  function numOrNull(id) {
    const raw = val(id);
    return raw === '' ? null : Number(raw);
  }
  function list(id) {
    return val(id).split(',').map(function (item) { return item.trim(); })
      .filter(function (item) { return item; });
  }
  return {
    enabled: !!(document.getElementById('db-enabled') || {}).checked,
    dialect: val('db-dialect'),
    host: val('db-host'),
    port: numOrNull('db-port'),
    user: val('db-user'),
    password: val('db-password'),
    databases_include: list('db-include'),
    databases_exclude: list('db-exclude'),
    samples_enabled: !!(document.getElementById('db-samples') || {}).checked,
    samples_max_rows: numOrNull('db-rows'),
    samples_max_bytes: numOrNull('db-bytes'),
    max_tables: numOrNull('db-tables'),
    timeout_seconds: numOrNull('db-timeout'),
  };
}

async function databaseReload() {
  const data = await api('/api/db');
  if (data && data.ok && currentState) {
    currentState.db = data;
    render();
  }
}

async function databaseSave() {
  setStatus(t('saving database settings…'));
  const data = await api('/api/db/save', { method: 'POST', body: JSON.stringify(databaseFormValues()) });
  if (data.ok) {
    showMessage(t('settings saved'), 'ok');
    toast({ tone: 'ok', title: t('settings saved') });
    if (currentState) {
      currentState.db = Object.assign({}, currentState.db, { access: data.access });
    }
    await databaseReload();
  } else {
    const text = serverText(data.error || t('could not save'));
    showMessage(text, 'bad');
    toast({ tone: 'bad', title: t('save error'), desc: text });
  }
  setStatus('');
}

async function databaseProbe() {
  setStatus(t('checking connection…'));
  // Значение из поля уходит только на эту пробу и не сохраняется.
  const data = await api('/api/db/probe', { method: 'POST', body: JSON.stringify(databaseFormValues()) });
  if (currentState) currentState.dbProbe = data;
  if (data.ok) {
    showMessage(t('connection works'), 'ok');
    toast({ tone: 'ok', title: t('connection works') });
  } else {
    const text = t('does not work: {reason}', { reason: serverText(data.reason || '') });
    showMessage(text, 'bad');
    toast({ tone: 'bad', title: t('Check connection'), desc: text });
  }
  render();
  setStatus('');
}

async function databaseCollect() {
  const samples = !!(document.getElementById('db-samples') || {}).checked;
  setStatus(samples ? t('collecting structure and samples…') : t('collecting structure…'));
  const data = await api('/api/db/collect', { method: 'POST', body: JSON.stringify({ samples: samples }) });
  if (data.ok) {
    const graph = data.graph || {};
    const text = t('collected and written: tables {tables}, relations {relations}, '
      + 'closed as personal data {sensitive}, samples {samples}', {
      tables: graph.tables || 0, relations: graph.relations || 0,
      sensitive: graph.sensitive_columns || 0, samples: data.samples || 0,
    });
    showMessage(text, 'ok');
    toast({ tone: 'ok', title: t('Index the structure'), desc: text });
    await databaseReload();
  } else {
    const text = serverText(data.reason || data.error || t('collection not performed'));
    showMessage(text, 'bad');
    toast({ tone: 'bad', title: t('collection not performed'), desc: text });
  }
  setStatus('');
}

function renderDatabases() {
  const db = (currentState && currentState.db) || {};
  const access = db.access || {};
  const summary = db.summary || {};
  const probe = (currentState && currentState.dbProbe) || null;
  const dialects = access.dialects || ['mysql', 'postgresql'];
  const allowed = writeAllowed();

  const probeBox = !probe ? '' : (probe.ok
    ? callout({
      tone: 'ok', icon: 'check',
      title: t('Connection works: {dialect}, server {server}. Databases visible: {databases}. '
        + 'Client: {client}.', {
        dialect: probe.dialect, server: probe.server,
        databases: probe.databases, client: probe.client,
      }),
    })
    : callout({
      tone: 'bad', icon: 'octagon',
      title: t('Connection does not work: {reason}',
        { reason: serverText(probe.reason || t('reason not reported')) }),
      desc: probe.action ? t('What to do: {action}', { action: serverText(probe.action) }) : '',
    }));

  const accessCard = card({
    icon: 'hdd', title: t('Access'),
    desc: t('environment variables take precedence over the configuration file'),
    body: '<div class="grid-2">'
      + '<div class="field"><span class="field__label">' + esc(t('Collection')) + '</span>'
      + '<div class="inline"><label class="switch">'
      + '<input type="checkbox" id="db-enabled"' + (access.enabled ? ' checked' : '')
      + (allowed ? '' : ' disabled') + '><span class="switch__track"></span>'
      + '<span>' + esc(access.enabled ? t('enabled') : t('disabled')) + '</span></label></div></div>'
      + '<div class="field"><label class="field__label" for="db-dialect">' + esc(t('DBMS')) + '</label>'
      + '<select id="db-dialect"' + (allowed ? '' : ' disabled') + '>'
      + dialects.map(function (dialect) {
        return '<option value="' + esc(dialect) + '"' + (dialect === access.dialect ? ' selected' : '') + '>'
          + esc(dialect) + '</option>';
      }).join('') + '</select></div>'
      + '<div class="field"><label class="field__label" for="db-host">' + esc(t('Host')) + '</label>'
      + '<input id="db-host" value="' + esc(access.host || '') + '"' + (allowed ? '' : ' disabled') + '></div>'
      + '<div class="field"><label class="field__label" for="db-port">' + esc(t('Port')) + '</label>'
      + '<input id="db-port" class="mono tnum" value="' + esc(access.port || '') + '"'
      + (allowed ? '' : ' disabled') + '></div>'
      + '<div class="field"><label class="field__label" for="db-user">' + esc(t('User')) + '</label>'
      + '<input id="db-user" value="' + esc(access.user || '') + '"' + (allowed ? '' : ' disabled') + '></div>'
      + '<div class="field"><label class="field__label" for="db-password">' + esc(t('Access value')) + '</label>'
      + '<input type="password" id="db-password" autocomplete="off"'
      + ' placeholder="' + esc(t('not shown; leave empty to keep it unchanged')) + '"'
      + (allowed ? '' : ' disabled') + '>'
      + '<p class="field__hint">' + (access.password_set
        ? chip(t('set ({source})', {
          source: access.source === 'environment' ? t('environment') : t('file'),
        }), 'ok')
        : chip(t('not set'), 'bad')) + '</p></div>'
      + '<div class="field"><label class="field__label" for="db-include">'
      + esc(t('Databases list')) + '</label>'
      + '<input id="db-include" class="mono" value="' + esc((access.databases_include || []).join(', ')) + '"'
      + ' placeholder="' + esc(t('empty — all except system ones')) + '"' + (allowed ? '' : ' disabled') + '></div>'
      + '<div class="field"><label class="field__label" for="db-exclude">' + esc(t('Exclude')) + '</label>'
      + '<input id="db-exclude" class="mono" value="' + esc((access.databases_exclude || []).join(', ')) + '"'
      + (allowed ? '' : ' disabled') + '></div>'
      + '</div>'
      + '<p class="field__hint mt-3">'
      + t('Access value does not have to be stored on disk at all: the environment '
        + 'variables {vars} take precedence over the file.', {
        vars: '<code>RAGRAF_DB_PASSWORD</code>, <code>RAGRAF_DB_HOST</code>, '
          + '<code>RAGRAF_DB_USER</code>, <code>RAGRAF_DB_PORT</code>, '
          + '<code>RAGRAF_DB_DIALECT</code>',
      }) + '</p>',
  });

  const limitsCard = card({
    icon: 'sliders', title: t('Limits'), desc: t('they limit the amount of collected samples'),
    body: '<div class="grid-2">'
      + [[t('Rows in sample'), 'db-rows', access.samples_max_rows],
        [t('Bytes per sample'), 'db-bytes', access.samples_max_bytes],
        [t('Max tables'), 'db-tables', access.max_tables],
        [t('Timeout seconds'), 'db-timeout', access.timeout_seconds]].map(function (row) {
        return '<div class="field"><label class="field__label" for="' + row[1] + '">' + esc(row[0]) + '</label>'
          + '<input type="number" id="' + row[1] + '" class="mono tnum" value="' + esc(row[2] || '') + '"'
          + (allowed ? '' : ' disabled') + '></div>';
      }).join('')
      + '</div>'
      + '<div class="mt-3"><label class="switch">'
      + '<input type="checkbox" id="db-samples"' + (access.samples_enabled ? ' checked' : '')
      + (allowed ? '' : ' disabled') + '><span class="switch__track"></span>'
      + '<span>' + esc(t('collect sample rows')) + ' <span class="muted">'
      + esc(t('(the only part that touches content; personal columns stay hidden)'))
      + '</span></span></label></div>'
      + '<div class="row-actions">'
      + button({
        v: 'primary', s: 'md', icon: 'check', label: t('Save settings'),
        action: 'db-save', id: 'db-save', disabled: !allowed,
      })
      + button({
        v: 'secondary', s: 'md', icon: 'zap', label: t('Check connection'),
        action: 'db-probe', id: 'db-probe',
      })
      + button({
        v: 'secondary', s: 'md', icon: 'database', label: t('Index the structure'),
        action: 'db-collect', id: 'db-collect',
      })
      + '</div>'
      + (probeBox ? '<div class="mt-3">' + probeBox + '</div>' : ''),
  });

  const summaryCard = card({
    icon: 'box', title: t('Already in the index'),
    desc: t('the actual content of the structure index'),
    body: summary.collected
      ? '<div class="metrics">'
        + metric({ label: t('databases'), value: num(summary.databases), icon: 'database' })
        + metric({ label: t('tables'), value: num(summary.tables), icon: 'rows' })
        + metric({ label: t('columns'), value: num(summary.columns_count), icon: 'hash' })
        + metric({ label: t('views'), value: num(summary.views), icon: 'file' })
        + metric({ label: t('relations'), value: num(summary.relations), icon: 'network' })
        + metric({ label: t('closed as personal data'), value: num(summary.sensitive_columns),
          tone: 'warn', icon: 'lock' })
        + '</div>'
        + '<div class="kv mt-3"><div class="kv__row"><dt>' + esc(t('Structure fingerprint')) + '</dt>'
        + '<dd>' + copyValue(String(summary.fingerprint || '')) + '</dd></div>'
        + (summary.collected_at
          ? '<div class="kv__row"><dt>' + esc(t('when')) + '</dt><dd>' + esc(stamp(summary.collected_at)) + '</dd></div>'
          : '')
        + '</div>'
        + '<p class="field__hint mt-3">'
        + t('The freshness watcher computes the structure fingerprint the same way '
          + 'as the corpus checksum: if the structure changes, the index becomes '
          + 'stale and is rebuilt. Changing rows does not affect the fingerprint.')
        + '</p>'
      : '<p class="small muted">' + esc(t('The database layer is not collected yet'))
        + (summary.note ? ': ' + esc(serverText(summary.note)) : '') + '</p>'
        + '<p class="field__hint mt-3">'
        + t('The structure is read by the operations {ops}. A relation from a '
          + 'foreign key is a fact; a relation derived from a column name is marked '
          + 'as an inference with confidence at most 0.5.', {
          ops: '<code>db_schema</code>, <code>db_table</code>, <code>db_relations</code>, '
            + '<code>db_sample</code>, <code>db_drift</code>, <code>db_cross</code>',
        }) + '</p>',
  });

  return '<section class="section" data-section="databases">'
    + pageHead({
      title: t('Databases'),
      desc: t('RAGRAF indexes the STRUCTURE of databases: databases, tables, columns, '
        + 'types, indexes, views and relations.'),
    })
    + callout({
      tone: 'info', icon: 'lock',
      title: t('Values of columns recognised as personal data or secrets are NOT collected at all'),
      desc: t('Write and schema-change operations are not supported: read only.'),
    })
    + (allowed ? '' : deniedCallout())
    + '<div class="cols-2">' + accessCard + '<div class="stack">' + limitsCard + summaryCard + '</div>' + '</div>'
    + '</section>';
}

/* --------------------------------------------------------------------------
   Каркас страницы
   --------------------------------------------------------------------------
   Сайдбар и топбар живут в разметке (PAGE_HTML) и лишь наполняются здесь:
   переключатель темы, язык и учётная запись должны существовать до первого
   ответа сервера, иначе до загрузки состояния страница выглядит пустой.
   Разделы, наоборот, целиком собираются из состояния.

   Разделы не удаляются при переключении: скрывается только неактивный. Поэтому
   выбранная в разделе строка и введённый текст не теряются при переходе.
   -------------------------------------------------------------------------- */

let activeSection = 'catalog';

function savedSection() {
  let value = '';
  try { value = window.localStorage.getItem(SECTION_KEY) || ''; } catch (e) { value = ''; }
  const known = SECTIONS.some(function (section) { return section.id === value; });
  return known ? value : 'dashboard';
}

/** Счётчики рядом с разделами: они отвечают на вопрос «где смотреть». */
function navBadges() {
  const entries = catalogEntries();
  const problems = detailNames().filter(function (name) {
    const info = qualityInfo(name);
    return !info.error && !info.pending && info.problems.length > 0;
  }).length;
  return {
    catalog: { n: entries.length, tone: 'neutral' },
    indexing: { n: staleEntries().length, tone: 'warn' },
    quality: { n: problems, tone: 'warn' },
    access: {
      n: ((currentState && currentState.access && currentState.access.log
        && currentState.access.log.entries) || []).filter(function (row) {
        return row.result === 'denied';
      }).length,
      tone: 'bad',
    },
  };
}

function navHtml() {
  const badges = navBadges();
  return NAV.map(function (group) {
    return '<div class="sidebar__group">'
      + '<p class="sidebar__group-title">' + esc(groupLabel(group.group)) + '</p>'
      + '<ul class="nav">' + group.items.map(function (section) {
        const active = section.id === activeSection;
        const badge = section.id === 'catalog'
          ? { n: badges.catalog.n, tone: 'neutral' } : badges[section.id];
        return '<li><button type="button" class="nav__item" data-action="nav" data-nav="'
          + esc(section.id) + '" aria-current="' + (active ? 'page' : 'false') + '">'
          + icon(section.icon, 'nav__icon h-[15px] w-[15px]')
          + '<span class="nav__label">' + esc(sectionLabel(section.id)) + '</span>'
          + (badge && badge.n > 0
            ? '<span class="nav__count' + (badge.tone !== 'neutral' ? ' nav__count--' + badge.tone : '')
              + '">' + esc(num(badge.n)) + '</span>'
            : '')
          + '</button></li>';
      }).join('') + '</ul></div>';
  }).join('');
}

function renderNav() {
  const html = navHtml();
  const nav = document.getElementById('nav');
  if (nav) nav.innerHTML = html;
  const drawer = document.getElementById('nav-drawer');
  if (drawer) drawer.innerHTML = html;
}

/** Состояние корпусов в подвале сайдбара: цифры, по которым видно проблему. */
function renderSidebarStats() {
  const box = document.getElementById('sidebar-stats');
  if (!box) return;
  const entries = catalogEntries();
  const stale = staleEntries().length;
  const broken = entries.filter(function (entry) { return entryState(entry) === 'bad'; }).length;
  const running = !!((currentState && currentState.indexing && currentState.indexing.job || {}).running);
  const revision = entries.length ? entries[0].rev_id : '';
  box.innerHTML = '<div class="sidebar__stat">'
    + '<div class="sidebar__stat-head">'
    + '<span class="sidebar__stat-title">' + esc(t('Indexing')) + '</span>'
    + (running ? chip(t('running'), 'info', { icon: 'refresh' })
      : chip(t('indexing is idle'), 'neutral', { icon: 'dot' }))
    + '</div>'
    + '<div class="sidebar__stat-grid">'
    + [[t('Developments'), entries.length, 'var(--foreground)'],
      [t('Out of date'), stale, stale ? 'var(--warn)' : 'var(--foreground)'],
      [t('Errors'), broken, broken ? 'var(--bad)' : 'var(--foreground)']].map(function (cell) {
      return '<div><p class="sidebar__stat-value" style="color:' + cell[2] + '">' + esc(num(cell[1]))
        + '</p><p class="sidebar__stat-label">' + esc(cell[0]) + '</p></div>';
    }).join('')
    + '</div>'
    + (revision ? '<p class="sidebar__rev">' + esc(revision + ' · ' + (entries[0].corpus_hash || '').slice(0, 12) + '…') + '</p>' : '')
    + '</div>';
}

/** Заголовок раздела в топбаре: он же отвечает «где я». */
function renderTopbar() {
  const title = document.getElementById('topbar-title');
  if (title) title.textContent = activeSection ? sectionLabel(activeSection) : '';
  const crumb = document.getElementById('topbar-crumb');
  if (crumb) crumb.textContent = 'RAGRAF · 127.0.0.1';
  const trigger = document.getElementById('palette-open');
  if (trigger) {
    trigger.innerHTML = icon('search', 'h-4 w-4')
      + '<span class="flex-1 truncate">' + esc(t('Section, project or action…')) + '</span>'
      + '<kbd class="kbd hidden sm:inline">Ctrl K</kbd>';
  }
  // Подвал: ревизия корпуса и его сумма. Это то, чем панель отвечает на вопрос
  // «о какой ревизии кода она сейчас говорит».
  const foot = document.getElementById('footer-rev');
  if (foot) {
    const entries = catalogEntries();
    const first = entries.length ? entries[0] : null;
    foot.textContent = first
      ? [first.rev_id, String(first.corpus_hash || '').slice(0, 16) + '…'].filter(Boolean).join(' · ')
      : '';
  }
}

function showSection(id) {
  document.querySelectorAll('[data-section]').forEach(function (el) {
    el.hidden = el.getAttribute('data-section') !== id;
  });
  document.querySelectorAll('[data-nav]').forEach(function (el) {
    el.setAttribute('aria-current', el.getAttribute('data-nav') === id ? 'page' : 'false');
  });
}

function go(id) {
  if (!SECTIONS.some(function (section) { return section.id === id; })) return;
  activeSection = id;
  try { window.localStorage.setItem(SECTION_KEY, id); } catch (e) { /* приватный режим */ }
  closeDrawer();
  renderTopbar();
  showSection(id);
  renderSidebarStats();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

/** Плашка о фоновом сборе: отличает «ещё считаю» от «данных нет». */
function renderCollecting() {
  const info = (currentState && currentState.collecting) || {};
  const entries = catalogEntries();
  if (!info.running || info.entries_done >= entries.length) return '';
  return '<div class="notice notice--warn a-up">'
    + esc(t('Collecting: developments {done} of {total}…',
      { done: num(info.entries_done), total: num(entries.length) }))
    + '</div>';
}

/** Запомнить фокус и позицию курсора в поле живого поиска. */
function captureFocus() {
  const el = document.activeElement;
  if (!el || !el.matches || !el.matches('[data-live]') || !el.id) return null;
  return { id: el.id, start: el.selectionStart, end: el.selectionEnd };
}

function restoreFocus(saved) {
  if (!saved) return;
  const el = document.getElementById(saved.id);
  if (!el) return;
  el.focus();
  if (saved.end !== null && saved.end !== undefined && el.setSelectionRange) {
    try { el.setSelectionRange(saved.start, saved.end); } catch (e) { /* тип поля без выделения */ }
  }
}

/**
 * Прочитать состояние и нарисовать страницу.
 *
 * Дорогие сведения (сверка сумм, размеры баз) считает сервер в фоне, поэтому
 * состояние может прийти с пометкой `refreshing`. В этом случае страница
 * рисуется сразу — с тем, что уже есть, — и перечитывается, когда сбор
 * закончится. Ждать сбор молча нельзя: именно так панель выглядела сломанной.
 */
async function refresh(force) {
  setStatus(t('loading'));
  // Явное обновление заново собирает тяжёлые сведения: полный обход идёт
  // около 100 секунд, а снимок живёт полчаса, поэтому кнопка обязана уметь
  // обновлять его досрочно.
  const data = await api(force ? '/api/state?force=1' : '/api/state');
  if (!data || !data.ok) {
    setStatus(t('load error'), 'bad');
    const app = document.getElementById('app');
    if (app) {
      app.innerHTML = '<div class="notice notice--bad">'
        + esc(t('Could not get state: {reason}',
          { reason: serverText((data && data.error) || t('unknown reason')) })) + '</div>';
    }
    return;
  }
  currentState = data.state;
  // Состояние доступа и журнал берутся отдельным запросом: они не входят
  // в общее состояние разработок, но нужны на той же странице.
  const access = await api('/api/access');
  if (access && access.ok) {
    currentState.access = access.access;
    currentState.auth = access.access.tokens || {};
  }
  const auth = await api('/api/token');
  if (auth && auth.ok && auth.auth) currentState.auth = auth.auth;
  const mode = await api('/api/mode');
  if (mode && mode.ok) currentState.mode = mode;
  const clients = await api('/api/clients');
  if (clients && clients.ok) currentState.clients = clients.clients;
  const indexing = await api('/api/indexing');
  if (indexing && indexing.ok) {
    currentState.indexing = { job: indexing.job || {}, stale: indexing.stale || [] };
  }
  render();

  const catalog = catalogEntries();
  const pending = catalog.filter(function (entry) { return entryState(entry) === 'pending'; }).length;
  const stale = staleEntries().length;
  // Повторный опрос — только пока сервер САМ сообщает, что собирает данные.
  // Раньше условие включало и `pending` (разработки без индекса): у такой
  // записи отметка остаётся неопределённой навсегда, и страница перечитывалась
  // каждые 3 секунды без конца. Вместе с полной перерисовкой это и выглядело
  // как «поля сбрасываются каждую секунду».
  if (data.refreshing) {
    setStatus(t('loading'));
    scheduleRefresh();
  } else {
    setStatus(stale
      ? t('developments need reindexing: {count}', { count: stale })
      : (pending
        ? t('developments without index: {count}', { count: pending })
        : t('All indexes are up to date')),
    stale ? 'bad' : 'ok');
  }
}

let refreshTimer = null;

/** Дождаться конца фонового сбора и перерисовать страницу. */
function scheduleRefresh(delay) {
  if (refreshTimer) return;
  refreshTimer = window.setTimeout(async function () {
    refreshTimer = null;
    const progress = await api('/api/state/progress');
    const running = !!(progress && progress.ok && progress.progress
      && progress.progress.running);
    if (!running) {
      await refresh();
      return;
    }
    await refresh();
  }, delay || 3000);
}

/** Прочитать состояние индексации: ход работы и список отставших. */
async function loadIndexing() {
  const data = await api('/api/indexing');
  if (data && data.ok && currentState) {
    currentState.indexing = { job: data.job || {}, stale: data.stale || [] };
    // Свежесть в таблице перечитывается тем же запросом: после очистки или
    // запрета она меняется, и старая отметка вводила бы в заблуждение.
    const state = await api('/api/state');
    if (state && state.ok) currentState.catalog = state.state.catalog;
    render();
  }
}

let indexingTimer = null;

/** Пока идёт индексация, страница перечитывает её ход. */
function pollIndexing() {
  if (indexingTimer) return;
  indexingTimer = window.setInterval(async function () {
    const data = await api('/api/indexing');
    if (!data || !data.ok) return;
    if (currentState) currentState.indexing = { job: data.job || {}, stale: data.stale || [] };
    render();
    if (!data.job || !data.job.running) {
      window.clearInterval(indexingTimer);
      indexingTimer = null;
      const state = await api('/api/state');
      if (state && state.ok) currentState = state.state;
      await loadIndexing();
      setStatus(t('indexing finished'), 'ok');
    }
  }, 3000);
}

/* --------------------------------------------------------------------------
   Действия
   --------------------------------------------------------------------------
   Один обработчик на всю страницу вместо привязки к каждой кнопке после каждой
   перерисовки. Причина не в краткости: перерисовка идёт при каждом опросе
   состояния, и забытая привязка делает кнопку молча нерабочей — а такую
   ошибку не видно ни в разметке, ни в консоли.
   -------------------------------------------------------------------------- */

/** Перерисовать раздел с учётом нового состояния интерфейса. */
function redraw() {
  editing = false;
  pendingRender = false;
  render();
}

async function doReindex(ids) {
  if (!writeAllowed()) {
    toast({ tone: 'warn', title: t('Write actions are unavailable'), desc: t('Sign in to continue') });
    return;
  }
  const list = (ids || []).filter(function (id) { return !!id; });
  if (!list.length) { toast({ tone: 'info', title: t('no developments need reindexing') }); return; }
  const agreed = await confirmDialog({
    tone: 'primary',
    title: t('Reindex: {count}', { count: num(list.length) }),
    desc: list.slice(0, 6).join(', ') + (list.length > 6 ? '…' : ''),
    confirm: t('Reindex'),
  });
  if (!agreed) return;
  setStatus(t('starting indexing…'));
  let started = 0;
  let lastError = '';
  // Сервер принимает одну разработку за запрос, поэтому массовое действие —
  // это последовательность тех же самых запросов, а не новая операция API.
  for (const id of list) {
    const data = await api('/api/index/reindex', {
      method: 'POST', body: JSON.stringify({ category: id }),
    });
    if (data && data.ok) {
      started += 1;
      if (currentState) currentState.indexing = { job: data.job || {}, stale: [] };
    } else {
      lastError = serverText((data && data.error) || t('could not'));
      if (data && data.clear && !data.clear.ok) {
        lastError = t('Indexing is forbidden, but the index was not deleted: {reason}',
          { reason: t((data.clear.error) || 'unknown reason') });
      }
    }
  }
  redraw();
  if (started) {
    toast({ tone: 'info', title: t('Indexing started.'), desc: t('Reindex: {count}', { count: num(started) }) });
    pollIndexing();
  } else {
    const text = lastError || t('could not');
    showMessage(text, 'bad');
    toast({ tone: 'bad', title: t('save error'), desc: text });
  }
}

async function doToggleEnabled(id, enabled) {
  if (!writeAllowed()) return;
  // Исключение разработки означает и удаление посчитанного индекса — так
  // это и названо в требовании. Предупреждение показывается ДО действия
  // и называет обе части, но не даёт оставить данные в неоднозначном
  // состоянии: исключено — значит индекса нет.
  if (!enabled) {
    const agreed = await confirmDialog({
      tone: 'bad', irreversible: true,
      title: t('Exclude {name} from indexing?', { name: id }),
      desc: t('The collected index will be deleted. The registry entry and history will remain.'),
      confirm: t('Forbid indexing'),
    });
    if (!agreed) { redraw(); return; }
  }
  const data = await api('/api/index/enabled', {
    method: 'POST', body: JSON.stringify({ category: id, enabled: enabled, clear: !enabled }),
  });
  const text = data && data.ok
    ? (enabled ? t('Indexing of {name} is allowed.', { name: id })
      : t('Indexing of {name} is forbidden.', { name: id }))
    : serverText((data && data.error) || t('could not'));
  showMessage(text, data && data.ok ? 'ok' : 'bad');
  toast({ tone: data && data.ok ? 'ok' : 'bad', title: text });
  if (data && data.clear && !data.clear.ok) {
    const note = t('Indexing is forbidden, but the index was not deleted: {reason}',
      { reason: t((data.clear.error) || 'unknown reason') });
    showMessage(note, 'bad');
    toast({ tone: 'bad', title: t('save error'), desc: note });
  }
  await loadIndexing();
}

async function doClearIndex(id) {
  if (!writeAllowed()) return;
  const agreed = await confirmDialog({
    tone: 'bad', irreversible: true,
    title: t('Delete the collected index of {name}? The registry entry and history will remain.', { name: id }),
    confirm: t('Delete index'),
  });
  if (!agreed) return;
  const data = await api('/api/index/clear', {
    method: 'POST', body: JSON.stringify({ category: id }),
  });
  const text = data && data.ok
    ? t('Index of {name} deleted.', { name: id })
    : serverText((data && data.error) || t('could not'));
  showMessage(text, data && data.ok ? 'ok' : 'bad');
  toast({ tone: data && data.ok ? 'warn' : 'bad', title: text });
  await loadIndexing();
}

async function bulkSetEnabled(enabled) {
  const ids = UI.projects.sel.slice();
  if (!ids.length) return;
  if (!writeAllowed()) return;
  if (!enabled) {
    const agreed = await confirmDialog({
      tone: 'bad', irreversible: true,
      title: t('Exclude {count} developments from indexing?', { count: num(ids.length) }),
      desc: t('The collected index will be deleted. The registry entry and history will remain.'),
      confirm: t('Forbid indexing'),
    });
    if (!agreed) return;
  }
  let done = 0;
  for (const id of ids) {
    const data = await api('/api/index/enabled', {
      method: 'POST', body: JSON.stringify({ category: id, enabled: enabled, clear: !enabled }),
    });
    if (data && data.ok) done += 1;
  }
  UI.projects.sel = [];
  await loadIndexing();
  toast({
    tone: enabled ? 'ok' : 'warn',
    title: enabled ? t('Allow indexing') : t('Forbid indexing'),
    desc: t('{count} developments updated', { count: num(done) }),
  });
  redraw();
}

async function modeApply() {
  const select = document.getElementById('mode-select');
  const mode = select ? select.value : '';
  const data = await api('/api/mode', { method: 'POST', body: JSON.stringify({ mode: mode }) });
  const text = data.ok
    ? t('mode saved: {mode}', { mode: data.mode.mode })
    : serverText(data.error || t('could not'));
  showMessage(text, data.ok ? 'ok' : 'bad');
  toast({ tone: data.ok ? 'ok' : 'bad', title: text });
  if (data.ok) {
    // Ответ присваивается ЦЕЛИКОМ, со списком режимов: прежде сюда попадала
    // строка, и выпадающий список схлопывался в один `cli`.
    currentState.mode = {
      ok: true, mode: modeValueOf({ mode: data.mode }), modes: data.modes || [],
    };
    if (data.probe && currentState) currentState.probe = data.probe;
    redraw();
    return;
  }
  await refresh();
}

async function modeProbe() {
  setStatus(t('checking channels…'));
  const data = await api('/api/probe');
  if (data.ok && currentState) currentState.probe = data.probe;
  setStatus(t('verification finished'));
  redraw();
}

async function clientGrantAccess() {
  const field = document.getElementById('client-label');
  const data = await api('/api/clients/access', {
    method: 'POST', body: JSON.stringify({ label: field ? field.value : '' }),
  });
  const out = document.getElementById('client-issued');
  if (out) {
    out.hidden = false;
    out.textContent = data.ok
      ? t('the value is issued once, copy it now: {value}', { value: data.value_once })
      : serverText(data.error || t('could not issue access'));
  }
  toast({
    tone: data.ok ? 'ok' : 'bad',
    title: data.ok ? t('Grant access') : t('could not issue access'),
    desc: data.ok ? String(data.value_once) : serverText(data.error || ''),
  });
}

async function harnessApply() {
  const select = document.getElementById('harness-transport');
  const data = await api('/api/clients/harness', {
    method: 'POST', body: JSON.stringify({ transport: select ? select.value : '' }),
  });
  const out = document.getElementById('harness-result');
  const text = data.ok
    ? t('block written, the patch is parsed by the application; restart Harness')
    : t('not applied: {reason}', {
      reason: serverText((data.result && (data.result.error || (data.result.check || {}).detail))
        || t('reason not given')),
    });
  if (out) { out.hidden = false; out.textContent = text; }
  toast({ tone: data.ok ? 'ok' : 'bad', title: text });
  await refresh();
}

/* --- один обработчик на страницу ----------------------------------------- */

function installActions() {
  document.addEventListener('click', function (event) {
    const target = event.target;
    if (!target || !target.closest) return;
    // Палитра: щелчок по подложке закрывает, щелчок внутри — нет.
    if (target.classList && target.classList.contains('overlay') && target.getAttribute('data-action') === 'palette-scrim') {
      closePalette();
      return;
    }
    const paletteItem = target.closest('[data-palette-index]');
    if (paletteItem) {
      paletteRun();
      return;
    }
    const el = target.closest('[data-action]');
    if (!el) return;
    const action = el.getAttribute('data-action');
    const id = el.getAttribute('data-id') || '';
    const scope = el.getAttribute('data-scope') || '';

    switch (action) {
      case 'nav': go(el.getAttribute('data-nav')); break;
      case 'sidebar-open': openDrawer(); break;
      case 'sidebar-close': closeDrawer(); break;
      case 'palette-open': openPalette(); break;
      case 'palette-close': closePalette(); break;
      case 'theme-menu': {
        const menu = document.getElementById('theme-menu');
        const toggle = document.getElementById('theme-toggle');
        if (!menu) break;
        const willOpen = menu.hidden;
        menu.hidden = !willOpen;
        if (toggle) toggle.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
        if (willOpen) renderThemeMenu();
        break;
      }
      case 'theme': setThemeMode(el.getAttribute('data-theme')); break;
      case 'density': toggleDensity(); break;
      case 'refresh': refresh(true); break;
      case 'account-menu': accountMenuToggle(); break;
      case 'account-out': accountSignOut(); break;
      case 'account-token-apply': accountUseToken(); break;
      case 'account-forget': accountForgetToken(); break;
      case 'login-apply': loginApply(); break;
      case 'login-out': loginSignOut(); break;
      case 'login-token-apply': loginTokenApply(); break;
      case 'login-token-clear': loginTokenClear(); break;
      case 'dialog-ok': closeDialog(true); break;
      case 'dialog-cancel': closeDialog(false); break;
      case 'copy': {
        if (copyText(el.getAttribute('data-value') || '')) {
          toast({ tone: 'ok', title: t('Copied to clipboard') });
        }
        break;
      }
      case 'reindex-stale': doReindex(staleEntries().map(function (entry) { return entry.category; })); break;
      case 'reindex-one': doReindex([id]); break;
      case 'indexing-refresh': loadIndexing(); break;
      case 'clear-index': doClearIndex(id); break;
      case 'projects-open':
        UI.projects.open = UI.projects.open === id ? null : id;
        redraw();
        break;
      case 'projects-reset':
        UI.projects.q = '';
        UI.projects.state = 'all';
        UI.projects.page = 0;
        UI.projects.sel = [];
        UI.projects.open = null;
        redraw();
        break;
      case 'bulk-reindex': doReindex(UI.projects.sel.slice()); break;
      case 'bulk-allow': bulkSetEnabled(true); break;
      case 'bulk-forbid': bulkSetEnabled(false); break;
      case 'bulk-clear': UI.projects.sel = []; redraw(); break;
      case 'sort': {
        const sort = sortStateFor(scope);
        if (!sort) break;
        if (sort.key === el.getAttribute('data-key')) {
          sort.dir = sort.dir === 'asc' ? 'desc' : 'asc';
        } else {
          sort.key = el.getAttribute('data-key');
          sort.dir = 'asc';
        }
        redraw();
        break;
      }
      case 'page': {
        const state = UI[scope];
        if (state) { state.page = Number(el.getAttribute('data-page')) || 0; redraw(); }
        break;
      }
      case 'quality-select': UI.quality.sel = id; redraw(); break;
      case 'usage-select': UI.usage.sel = id; redraw(); break;
      case 'storage-open': UI.storage.open = UI.storage.open === id ? '' : id; redraw(); break;
      case 'settings-select': UI.settings.sel = id; redraw(); break;
      case 'save-global': saveGlobal(); break;
      case 'save-project': saveProject(id); break;
      case 'mode-apply': modeApply(); break;
      case 'mode-probe': modeProbe(); break;
      case 'client-access': clientGrantAccess(); break;
      case 'harness-apply': harnessApply(); break;
      case 'pwd-set': accessPasswordSet(); break;
      case 'pwd-clear': accessPasswordClear(); break;
      case 'token-create': accessTokenCreate(); break;
      case 'token-revoke': accessTokenAction('revoke', id); break;
      case 'token-restore': accessTokenAction('restore', id); break;
      case 'token-delete': accessTokenDelete(id); break;
      case 'db-save': databaseSave(); break;
      case 'db-probe': databaseProbe(); break;
      case 'db-collect': databaseCollect(); break;
      case 'tab': {
        const group = el.getAttribute('data-group');
        const tab = el.getAttribute('data-tab');
        if (group === 'settings') UI.settings.tab = tab;
        else if (group === 'projects-state') { UI.projects.state = tab; UI.projects.page = 0; }
        redraw();
        break;
      }
      default: break;
    }
  });

  document.addEventListener('change', function (event) {
    const el = event.target;
    if (!el || !el.getAttribute) return;
    const action = el.getAttribute('data-action');
    switch (action) {
      case 'entry-enabled': doToggleEnabled(el.getAttribute('data-id'), el.checked); break;
      case 'projects-select': {
        const id = el.getAttribute('data-id');
        UI.projects.sel = el.checked
          ? UI.projects.sel.concat([id]).filter(function (v, i, all) { return all.indexOf(v) === i; })
          : UI.projects.sel.filter(function (value) { return value !== id; });
        redraw();
        break;
      }
      case 'projects-all': {
        const view = currentProjectsPage();
        UI.projects.sel = el.checked
          ? UI.projects.sel.concat(view).filter(function (v, i, all) { return all.indexOf(v) === i; })
          : UI.projects.sel.filter(function (value) { return view.indexOf(value) < 0; });
        redraw();
        break;
      }
      case 'per-page': {
        const state = UI[el.getAttribute('data-scope')];
        if (state) { state.perPage = Number(el.value) || 10; state.page = 0; redraw(); }
        break;
      }
      case 'quality-only-issues': UI.quality.onlyIssues = el.checked; redraw(); break;
      case 'access-channel': UI.access.channel = el.value; UI.access.page = 0; redraw(); break;
      case 'access-result': UI.access.result = el.value; UI.access.page = 0; redraw(); break;
      case 'mode-select': {
        const box = document.getElementById('mode-instructions');
        if (box) box.innerHTML = modeSetupHtml({ mode: el.value, clients: currentState.clients });
        break;
      }
      case 'db-enabled': break;
      default: break;
    }
  });

  document.addEventListener('input', function (event) {
    const el = event.target;
    if (!el || !el.getAttribute) return;
    const action = el.getAttribute('data-action');
    switch (action) {
      case 'projects-search': UI.projects.q = el.value; UI.projects.page = 0; render(); break;
      case 'quality-search': UI.quality.q = el.value; render(); break;
      case 'storage-search': UI.storage.q = el.value; render(); break;
      case 'settings-search': UI.settings.q = el.value; render(); break;
      case 'settings-project-search': UI.settings.q = el.value; render(); break;
      case 'access-search': UI.access.q = el.value; UI.access.page = 0; render(); break;
      default: break;
    }
  });

  document.addEventListener('keydown', function (event) {
    const meta = event.ctrlKey || event.metaKey;
    if (meta && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      if (paletteOpen) closePalette(); else openPalette();
      return;
    }
    if (meta && event.key.toLowerCase() === 'b') {
      event.preventDefault();
      const drawer = document.getElementById('drawer');
      if (drawer && !drawer.hidden) closeDrawer(); else openDrawer();
      return;
    }
    if (event.key === 'Escape') {
      if (paletteOpen) { closePalette(); return; }
      if (dialogState) { closeDialog(false); return; }
      const drawer = document.getElementById('drawer');
      if (drawer && !drawer.hidden) { closeDrawer(); return; }
      closeAccountMenu();
      const menu = document.getElementById('theme-menu');
      if (menu) menu.hidden = true;
      return;
    }
    // Слой поверх страницы удерживает фокус: Tab и Shift+Tab ходят по кругу
    // внутри него и не уводят в содержимое под слоем.
    if (event.key === 'Tab') {
      const layer = document.querySelector('.dialog[aria-modal="true"]');
      if (layer) { keepFocusInside(event, layer); return; }
    }
    // Вкладки: стрелки переводят фокус И выбор на соседнюю вкладку. Так требует
    // набор ролей tablist/tab: у невыбранной вкладки `tabindex="-1"`, и без
    // обработки стрелок она недостижима с клавиатуры вовсе.
    const tab = event.target && event.target.closest
      ? event.target.closest('[role="tab"]') : null;
    if (tab && (event.key === 'ArrowRight' || event.key === 'ArrowLeft'
      || event.key === 'Home' || event.key === 'End')) {
      const list = Array.prototype.slice.call(tab.parentElement.querySelectorAll('[role="tab"]'));
      const at = list.indexOf(tab);
      let next = 0;
      if (event.key === 'ArrowRight') next = (at + 1) % list.length;
      else if (event.key === 'ArrowLeft') next = (at - 1 + list.length) % list.length;
      else if (event.key === 'End') next = list.length - 1;
      event.preventDefault();
      // Фокус возвращается в render(): перерисовка заменяет саму кнопку.
      pendingTabFocus = {
        group: tab.getAttribute('data-group'),
        tab: list[next].getAttribute('data-tab'),
      };
      list[next].click();
      return;
    }
    if (!paletteOpen) return;
    if (event.key === 'ArrowDown') { event.preventDefault(); paletteMove(1); }
    if (event.key === 'ArrowUp') { event.preventDefault(); paletteMove(-1); }
    if (event.key === 'Home') { event.preventDefault(); paletteIndex = 0; renderPalette(); }
    if (event.key === 'End') { event.preventDefault(); paletteIndex = filteredPaletteItems().length - 1; renderPalette(); }
    if (event.key === 'Enter') { event.preventDefault(); paletteRun(); }
  });
}

/** Таблица сортировок по области: у каждого раздела своя запомненная. */
function sortStateFor(scope) {
  if (scope === 'projects') return UI.projects.sort;
  if (scope === 'usage') return UI.usage.sort;
  return null;
}

/** Разработки текущей страницы: нужны для «выбрать всё на странице». */
function currentProjectsPage() {
  const rows = projectsFiltered();
  const sorted = sortRows(rows, UI.projects.sort, {
    category: function (entry) { return String(entry.category).toLowerCase(); },
    state: function (entry) { return entryState(entry); },
    files: function (entry) { return Number(entry.files) || 0; },
    nodes: function (entry) { return Number(entry.nodes) || 0; },
    edges: function (entry) { return Number(entry.edges) || 0; },
    size: function (entry) { return entryBytes(entry); },
    calls: function (entry) {
      const info = usageInfo(String(entry.category));
      return info.error || info.pending ? -1 : info.calls;
    },
  });
  const perPage = UI.projects.perPage;
  const page = Math.min(UI.projects.page, Math.max(0, Math.ceil(sorted.length / perPage) - 1));
  return sorted.slice(page * perPage, page * perPage + perPage)
    .map(function (entry) { return String(entry.category); });
}

function openDrawer() {
  const drawer = document.getElementById('drawer');
  const scrim = document.getElementById('drawer-scrim');
  if (drawer) drawer.hidden = false;
  // Подложка ловит щелчок мимо панели: без неё на узком экране закрыть
  // навигацию можно было только кнопкой или Escape.
  if (scrim) scrim.hidden = false;
  const close = document.getElementById('drawer-close');
  if (close) close.focus();
}

function closeDrawer() {
  const drawer = document.getElementById('drawer');
  const scrim = document.getElementById('drawer-scrim');
  if (drawer) drawer.hidden = true;
  if (scrim) scrim.hidden = true;
}

async function loginTokenApply() {
  const field = document.getElementById('login-token');
  // Права проверяет сервер: раньше здесь читалась страница состояния, а она
  // открыта и без прав — успешным выглядел любой ввод.
  const accepted = await applySessionToken(field ? field.value : '');
  // Надпись в шапке перерисовывается в обоих случаях: в ней видно состояние.
  renderAccount();
  if (accepted) await refresh();
}

function loginTokenClear() {
  forgetToken();
  sessionAuthenticated = false;
  renderAccount();
  showAuthMessage(t('Token forgotten.'), 'ok');
  render();
}

/**
 * Нарисовать страницу. Контейнер заменяется целиком: разделы короткие, а
 * выбранное в них состояние живёт в `UI`, поэтому перерисовка ничего не теряет.
 * Единственное исключение — поле с фокусом: его содержимое и позиция курсора
 * возвращаются на место (иначе опрос состояния «съедал» бы ввод).
 */
function render() {
  const state = currentState;
  if (!state) return;
  // Страж редактирования: пока фокус в поле формы, страница не перерисовывается.
  if (editing) { pendingRender = true; return; }
  const app = document.getElementById('app');
  if (!app) return;
  const savedValues = snapshotFormValues();
  const focus = captureFocus();
  app.innerHTML = renderCollecting()
    + renderSession(state)
    + renderDashboard()
    + renderProjects()
    + renderIndexing()
    + renderQuality()
    + renderUsage()
    + renderStorage()
    + renderSettings()
    + renderMode()
    + renderAccess()
    + renderDatabases();
  restoreFormValues(savedValues);
  restoreFocus(focus);
  renderNav();
  renderSidebarStats();
  renderTopbar();
  updateThemeButton();
  renderAccount();
  renderThemeMenu();
  showSection(activeSection);
  // Фокус вкладки, переведённый стрелками, возвращается на место: перерисовка
  // заменила саму кнопку, и без этого фокус уходил бы в начало страницы.
  if (pendingTabFocus) {
    const wanted = document.querySelector('[data-action="tab"][data-group="' + pendingTabFocus.group
      + '"][data-tab="' + pendingTabFocus.tab + '"]');
    pendingTabFocus = null;
    if (wanted && wanted.focus) wanted.focus();
  }
}

/** Запуск панели. Порядок важен: словарь, затем права, затем состояние. */
async function start() {
  await loadLanguages();
  applyLanguage(I18N.current);
  fillLanguageSelect();
  await loadToken();
  applyTheme();
  applyDensity();
  installSystemThemeWatch();
  installEditGuard();
  installActions();
  installAccountListeners();
  activeSection = savedSection();
  renderThemeMenu();
  renderToasts();
  await refresh();
  renderAccount();
  renderTopbar();
}

start();









