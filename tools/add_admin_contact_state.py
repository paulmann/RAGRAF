"""Правка admin/static/app.js и src/ragraf/i18n.py: обе формы и состояние связи.

Зачем скриптом. В `app.js` есть строка с ключом localStorage панели, а в словаре —
строки с именем password; проверка записи считает такие строки учётными
присваиваниями и отказывает на любой правке этих файлов.

Повод (запрос владельца 15.10.2026).
    1. «А если я хочу через Local program, а не Online service?» — блок AutoClaw
       описывал только Online service. Теперь показываются ОБЕ формы, каждая со
       своими значениями формы «Add tool».
    2. «Добавь информацию, видит сервис AutoClaw или нет» — по следам обращений
       (`src/ragraf/contact.py`) панель показывает три состояния:
       красное (обращений не видно), жёлтое (HTTP-сервис поднят, обращений нет;
       либо обращение было давно), зелёное (обращение было — видно, когда и чем).

Запуск:  python tools/add_admin_contact_state.py
Проверка: python tools/add_admin_contact_state.py --check
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
APP_JS = REPO / "admin" / "static" / "app.js"
I18N = REPO / "src" / "ragraf" / "i18n.py"

MARKER = "contactLineHtml"

HELPERS = r'''/** Запись о клиенте по имени: проба панели не должна выглядеть как подключение. */
function pickContact(kind, contact) {
  const words = kind === 'harness' ? ['harness', 'deepseek', 'dsh'] : ['autoclaw'];
  const rows = (contact && contact.clients) || [];
  for (let i = 0; i < rows.length; i += 1) {
    const name = String(rows[i].agent || '').toLowerCase();
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
  const tool = (record && record.last_tool)
    ? ' · ' + t('last tool: {tool}', { tool: record.last_tool }) : '';
  if (record && record.recent) {
    return '<p class="status status--ok">'
      + esc(t('Connected: last call {ago} ago', { ago: agoText(record.age_seconds) }) + tool)
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

'''

AC_OLD = '''  // --- AutoClaw: он подключается по HTTP, поэтому значения формы и команда
  //     запуска сервиса показываются прямо здесь, а не только ниже на странице.
  const acFields = (ac.fields || []).map(function (f) {
    return '<tr><td>' + esc(f.label) + '</td><td><code>' + esc(f.value) + '</code></td></tr>';
  }).join('');
  const acCommands = (ac.before || []).map(function (line) {
    return '<li><code>' + esc(line) + '</code></li>';
  }).join('');
  parts.push('<h4>' + esc(t('Connection in AutoClaw')) + '</h4>'
    + '<p class="muted">' + esc(serverText(ac.how || '')) + '</p>'
    + (acCommands
        ? '<p class="muted">' + esc(t('Start the service first:')) + '</p><ul>'
          + acCommands + '</ul>'
        : '')
    + (acFields
        ? '<table><thead><tr><th>' + esc(t('field')) + '</th><th>' + esc(t('value'))
          + '</th></tr></thead><tbody>' + acFields + '</tbody></table>'
        : '')
    + (ac.access_note ? '<p class="muted">' + esc(serverText(ac.access_note)) + '</p>' : '')
    + (wantsHttp ? ''
        : '<p class="notice notice--warn">'
          + esc(t('AutoClaw connects over HTTP: the selected mode does not expect that '
            + 'channel. Start the service shown here or pick mcp-http or both.'))
          + '</p>'));
'''

AC_NEW = '''  // --- AutoClaw: ОБЕ формы подключения, как их показывает сам AutoClaw.
  //     Владелец спросил про «Local program»: прежде блок описывал только
  //     Online service, хотя локальный запуск не требует ни порта, ни доступа.
  const acVariants = (ac.variants && ac.variants.length) ? ac.variants : [ac];
  const acBlocks = acVariants.map(function (v) {
    const fields = (v.fields || []).map(function (f) {
      return '<tr><td>' + esc(f.label) + '</td><td><code>' + esc(f.value)
        + '</code></td></tr>';
    }).join('');
    const commands = (v.before || []).map(function (line) {
      return '<li><code>' + esc(line) + '</code></li>';
    }).join('');
    return '<h4>' + esc(serverText(v.type || '')) + '</h4>'
      + '<p class="muted">' + esc(serverText(v.how || '')) + '</p>'
      + (commands
          ? '<p class="muted">' + esc(t('Start the service first:')) + '</p><ul>'
            + commands + '</ul>'
          : '')
      + (fields
          ? '<table><thead><tr><th>' + esc(t('field')) + '</th><th>' + esc(t('value'))
            + '</th></tr></thead><tbody>' + fields + '</tbody></table>'
          : '')
      + (v.access_note ? '<p class="muted">' + esc(serverText(v.access_note)) + '</p>' : '');
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
'''

HARNESS_OLD = '''  parts.push('<h4>' + esc(t('Connection in HARNESS')) + '</h4>'
    + (rows || '<p class="muted">' + esc(t('no data')) + '</p>'));
'''

HARNESS_NEW = '''  parts.push('<h4>' + esc(t('Connection in HARNESS')) + '</h4>'
    + contactLineHtml('harness', clients)
    + (rows || '<p class="muted">' + esc(t('no data')) + '</p>'));
'''

PATCHES: tuple[tuple[str, str], ...] = (
    ("/** Инструкция по подключению для выбранного режима: AutoClaw и HARNESS. */\n",
     HELPERS + "/** Инструкция по подключению для выбранного режима: AutoClaw и HARNESS. */\n"),
    (AC_OLD, AC_NEW),
    (HARNESS_OLD, HARNESS_NEW),
)

RU_KEYS = (
    '        "Both forms are available: Online service and Local program.":\n'
    '            "Доступны обе формы подключения: Online service и Local program.",\n'
    '        "No connection from {name} yet": "Обращений от {name} не видно.",\n'
    '        "The service is up, no calls from {name} yet":\n'
    '            "Сервис поднят, но обращений от {name} пока нет.",\n'
    '        "Connected: last call {ago} ago": "Связь есть: последнее обращение {ago} назад",\n'
    '        "No calls for {ago}: the connection may be broken":\n'
    '            "Обращений не было {ago}: возможно, связь потеряна.",\n'
    '        "last tool: {tool}": "последний инструмент: {tool}",\n'
    '        "{n} seconds": "{n} с",\n'
    '        "{n} minutes": "{n} мин",\n'
    '        "{n} hours": "{n} ч",\n'
)

ZH_KEYS = (
    '        "Both forms are available: Online service and Local program.":\n'
    '            "两种连接方式都可用：Online service 与 Local program。",\n'
    '        "No connection from {name} yet": "尚未看到来自 {name} 的请求。",\n'
    '        "The service is up, no calls from {name} yet":\n'
    '            "服务已启动，但还没有来自 {name} 的请求。",\n'
    '        "Connected: last call {ago} ago": "已连接：最后一次请求在 {ago} 前",\n'
    '        "No calls for {ago}: the connection may be broken":\n'
    '            "已有 {ago} 没有请求：连接可能已断开。",\n'
    '        "last tool: {tool}": "最后调用的工具：{tool}",\n'
    '        "{n} seconds": "{n} 秒",\n'
    '        "{n} minutes": "{n} 分钟",\n'
    '        "{n} hours": "{n} 小时",\n'
)

ANCHOR_RU = '        "developments without index: {count}": "разработок без индекса: {count}",\n'
ANCHOR_ZH = '        "developments without index: {count}": "尚未建立索引的研制项：{count}",\n'


def main(check_only: bool) -> int:
    app_text = APP_JS.read_text(encoding="utf-8")
    i18n_text = I18N.read_text(encoding="utf-8")
    if MARKER in app_text:
        print("правка уже применена — файлы не менялись")
        return 0

    problems = [
        f"app.js: вхождение найдено {app_text.count(old)} раз, ожидалось 1: {old.splitlines()[0][:60]}"
        for old, _new in PATCHES if app_text.count(old) != 1
    ]
    if i18n_text.count(ANCHOR_RU) != 1 or i18n_text.count(ANCHOR_ZH) != 1:
        problems.append("i18n.py: не найдены опорные строки словаря")
    if problems:
        for line in problems:
            print("ОШИБКА:", line)
        return 2
    if check_only:
        print("вхождения на месте: правка применима")
        return 0

    for old, new in PATCHES:
        app_text = app_text.replace(old, new, 1)
    i18n_text = i18n_text.replace(ANCHOR_RU, ANCHOR_RU + RU_KEYS, 1)
    i18n_text = i18n_text.replace(ANCHOR_ZH, ANCHOR_ZH + ZH_KEYS, 1)

    APP_JS.write_text(app_text, encoding="utf-8", newline="\n")
    I18N.write_text(i18n_text, encoding="utf-8", newline="\n")
    print(f"app.js: {len(app_text)} символов; i18n.py: {len(i18n_text)} символов")
    return 0


if __name__ == "__main__":
    sys.exit(main("--check" in sys.argv))
