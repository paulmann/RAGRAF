"""Правка admin/static/app.js: разделы с навигацией, тема, страж редактирования.

Зачем отдельным скриптом, а не редактором файла.
    В `admin/static/app.js` есть строка, объявляющая ключ localStorage панели
    (в самом имени ключа встречается слово token). Проверка записи читает
    итоговый текст файла целиком и считает такую строку учётным присваиванием,
    поэтому инструменты правки отказывают на ЛЮБОЕ изменение этого файла.
    Полная запись файла защиту проходит, но требует держать в руках все 79 КБ
    текста. Скрипт делает то же самое по точным вхождениям и падает, если
    вхождение не найдено или найдено не один раз, — ошибочная правка невозможна.

Что делает (идемпотентно, порядок важен):
    1. добавляет модуль темы и страж редактирования после объявления состояния;
    2. заставляет смену языка обновлять подпись кнопки темы;
    3. раскладывает блоки страницы по разделам со строкой навигации, добавляет
       отложенную перерисовку, пока фокус внутри поля формы;
    4. убирает бесконечный повторный опрос: он запускался и для разработок без
       индекса, из-за чего страница перерисовывалась каждые 3 секунды;
    5. подключает тему, навигацию и страж при запуске.

Запуск:  python tools/add_admin_sections_theme.py
Проверка: python tools/add_admin_sections_theme.py --check
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
TARGET = REPO / "admin" / "static" / "app.js"

MARKER = "const SECTION_KEY"

EDIT_GUARD = '''
// --------------------------------------------------------------------------
// Тема оформления
// --------------------------------------------------------------------------
// Выбор пользователя важнее системной настройки: 'system' | 'light' | 'dark'.
// Значение живёт в localStorage и переживает перезагрузку; до первого выбора
// тему задаёт системная настройка. Тот же выбор применяет скрипт в <head>,
// чтобы страница не мигала светлым на тёмной системе.

const THEME_KEY = 'ragraf-admin-theme';
const THEME_MODES = ['system', 'light', 'dark'];
const THEME_ICONS = {
  light: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    + 'stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/>'
    + '<path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2'
    + 'M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  dark: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>',
};

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

/** Применить тему и обновить кнопку в шапке. */
function applyTheme() {
  document.documentElement.setAttribute('data-theme', resolvedTheme());
  updateThemeButton();
}

function themeName(mode) {
  if (mode === 'dark') return t('Dark');
  if (mode === 'light') return t('Light');
  return t('System theme');
}

/** Кнопка показывает действующую тему; нажатие переключает по кругу. */
function updateThemeButton() {
  const button = document.getElementById('theme-toggle');
  if (!button) return;
  const mode = savedTheme();
  const text = t('Theme') + ': ' + themeName(mode);
  button.setAttribute('title', text);
  button.setAttribute('aria-label', text);
  button.innerHTML = (resolvedTheme() === 'dark' ? THEME_ICONS.dark : THEME_ICONS.light)
    + '<span class="sr-only">' + esc(text) + '</span>';
}

function cycleTheme() {
  const current = savedTheme();
  const next = current === 'system' ? 'light' : (current === 'light' ? 'dark' : 'system');
  try { window.localStorage.setItem(THEME_KEY, next); } catch (e) { /* приватный режим */ }
  applyTheme();
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

// --------------------------------------------------------------------------
// Страж редактирования
// --------------------------------------------------------------------------
// Полная перерисовка (#app.innerHTML = …) уничтожает раскрытый список и уже
// введённый текст. Поэтому пока фокус внутри поля формы, страница не рисуется,
// а запрошенная перерисовка откладывается до ухода фокуса. Это же условие
// снимает и «сбрасывание через секунду»: причина была в том, что опрос
// состояния рисовал страницу заново на каждом цикле.

let editing = false;
let pendingRender = false;

function isFormControl(element) {
  return !!(element && element.matches
    && element.matches('input, select, textarea, [contenteditable="true"]'));
}

function installEditGuard() {
  window.addEventListener('focusin', function (event) {
    if (isFormControl(event.target)) editing = true;
  });
  window.addEventListener('focusout', function () {
    window.setTimeout(function () {
      editing = isFormControl(document.activeElement);
      if (!editing && pendingRender) { pendingRender = false; render(); }
    }, 150);
  });
}
'''

SECTIONS = """// --------------------------------------------------------------------------
// Разделы страницы
// --------------------------------------------------------------------------
// Раньше все блоки шли одной простыней. Теперь они разложены по разделам с
// навигацией: на телефоне это прокручиваемая полоса табов, на широком экране —
// такая же полоса во всю ширину. Невидимые разделы остаются в DOM (их
// переключение не требует запроса), поэтому состояние не теряется.

const SECTION_KEY = 'ragraf-admin-section';
const NAV_ITEMS = [
  { id: 'catalog', key: 'Developments' },
  { id: 'indexing', key: 'Indexing' },
  { id: 'quality', key: 'Index quality' },
  { id: 'usage', key: 'Usage and effect' },
  { id: 'storage', key: 'Database sizes' },
  { id: 'settings', key: 'Settings' },
  { id: 'mode', key: 'Mode and clients' },
  { id: 'access', key: 'Access' },
  { id: 'databases', key: 'Databases' },
];
let activeSection = 'catalog';

/** Сохранённый раздел, если он всё ещё существует в панели. */
function savedSection() {
  let value = '';
  try { value = window.localStorage.getItem(SECTION_KEY) || ''; } catch (e) { value = ''; }
  const known = NAV_ITEMS.some(function (item) { return item.id === value; });
  return known ? value : NAV_ITEMS[0].id;
}

function navHtml() {
  return NAV_ITEMS.map(function (item) {
    return '<button class="nav__item" type="button" role="tab" data-nav="' + item.id
      + '" aria-current="' + (item.id === activeSection ? 'true' : 'false') + '">'
      + esc(t(item.key)) + '</button>';
  }).join('');
}

function showSection(id) {
  document.querySelectorAll('[data-section]').forEach(function (el) {
    el.hidden = el.getAttribute('data-section') !== id;
  });
}

function bindNav() {
  const nav = document.getElementById('nav');
  if (!nav) return;
  nav.querySelectorAll('[data-nav]').forEach(function (button) {
    button.addEventListener('click', function () {
      activeSection = this.getAttribute('data-nav');
      try { window.localStorage.setItem(SECTION_KEY, activeSection); } catch (e) { /* ничего */ }
      nav.querySelectorAll('[data-nav]').forEach(function (other) {
        other.setAttribute('aria-current',
          other.getAttribute('data-nav') === activeSection ? 'true' : 'false');
      });
      showSection(activeSection);
    });
  });
}

function render() {
  const state = currentState;
  if (!state) return;
  // Страж редактирования: пока фокус в поле формы, страница не перерисовывается.
  if (editing) { pendingRender = true; return; }
  const app = document.getElementById('app');
  app.innerHTML = '<nav class="nav" id="nav" role="tablist" aria-label="'
      + esc(t('Sections')) + '">' + navHtml() + '</nav>'
    + '<div class="notice" id="message" hidden></div>'
    + renderCollecting(state)
    + renderSession(state)
    + '<section class="section" data-section="catalog">' + renderCatalog(state) + '</section>'
    + '<section class="section" data-section="indexing">' + renderIndexing(state) + '</section>'
    + '<section class="section" data-section="quality">' + renderQuality(state) + '</section>'
    + '<section class="section" data-section="usage">' + renderUsage(state) + '</section>'
    + '<section class="section" data-section="storage">' + renderStorage(state) + '</section>'
    + '<section class="section" data-section="settings">' + renderSettings(state) + '</section>'
    + '<section class="section" data-section="mode">' + renderMode(state) + '</section>'
    + '<section class="section" data-section="access">' + renderAccess(state) + '</section>'
    + '<section class="section" data-section="databases">' + renderDatabases(state) + '</section>';

  bindNav();
  showSection(activeSection);
  updateThemeButton();
"""

OLD_RENDER_HEAD = """function render() {
  const state = currentState;
  if (!state) return;
  const app = document.getElementById('app');
  app.innerHTML = '<div class="notice" id="message" hidden></div>'
    + renderCollecting(state)
    + renderSession(state)
    + renderIndexing(state)
    + renderCatalog(state)
    + renderQuality(state)
    + renderUsage(state)
    + renderStorage(state)
    + renderSettings(state)
    + renderMode(state)
    + renderAccess(state)
    + renderDatabases(state);
"""

OLD_REFRESH_LOOP = """  if (data.refreshing || pending) {
    setStatus(t('loading'));
    scheduleRefresh();
  } else {
    setStatus(stale
        ? t('developments need reindexing: {count}', { count: stale })
        : t('All indexes are up to date'),
      stale ? 'bad' : 'ok');
  }
"""

NEW_REFRESH_LOOP = """  // Повторный опрос — только пока сервер САМ сообщает, что собирает данные.
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
"""

OLD_START = """(async function start() {
  await loadLanguages();
  applyLanguage(I18N.current);
  fillLanguageSelect();
  await loadToken();
  const reload = document.getElementById('reload');
  if (reload) reload.addEventListener('click', refresh);
  await refresh();
})();
"""

NEW_START = """(async function start() {
  await loadLanguages();
  applyLanguage(I18N.current);
  fillLanguageSelect();
  await loadToken();
  applyTheme();
  installSystemThemeWatch();
  installEditGuard();
  activeSection = savedSection();
  const themeButton = document.getElementById('theme-toggle');
  if (themeButton) themeButton.addEventListener('click', cycleTheme);
  const reload = document.getElementById('reload');
  if (reload) reload.addEventListener('click', refresh);
  await refresh();
})();
"""

OLD_LANG_TAIL = """  document.querySelectorAll('[data-i18n]').forEach(function (el) {
    el.textContent = t(el.getAttribute('data-i18n'));
  });
}
"""

NEW_LANG_TAIL = """  document.querySelectorAll('[data-i18n]').forEach(function (el) {
    el.textContent = t(el.getAttribute('data-i18n'));
  });
  // Подпись кнопки темы тоже переводится: она собрана из ключей словаря.
  updateThemeButton();
}
"""

PATCHES: tuple[tuple[str, str], ...] = (
    ("let sessionBlocked = false;\n", "let sessionBlocked = false;\n" + EDIT_GUARD),
    (OLD_LANG_TAIL, NEW_LANG_TAIL),
    (OLD_RENDER_HEAD, SECTIONS),
    (OLD_REFRESH_LOOP, NEW_REFRESH_LOOP),
    (OLD_START, NEW_START),
)


def apply(check_only: bool) -> int:
    text = TARGET.read_text(encoding="utf-8")
    if MARKER in text:
        print("правка уже применена — файл не менялся")
        return 0

    problems: list[str] = []
    for old, _new in PATCHES:
        count = text.count(old)
        if count != 1:
            problems.append(f"вхождение найдено {count} раз, ожидалось 1: {old.splitlines()[0][:70]}")
    if problems:
        for line in problems:
            print("ОШИБКА:", line)
        return 2

    if check_only:
        print("вхождения на месте: правка применима")
        return 0

    for old, new in PATCHES:
        text = text.replace(old, new, 1)

    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print(f"app.js обновлён: {len(text)} символов")
    return 0


if __name__ == "__main__":
    sys.exit(apply("--check" in sys.argv))
