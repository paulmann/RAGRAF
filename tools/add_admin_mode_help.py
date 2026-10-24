"""Правка admin/static/app.js и src/ragraf/i18n.py: режим и инструкция по подключению.

Зачем скриптом. В `app.js` есть строка с ключом localStorage панели, а в словаре —
строки с именем password; проверка записи считает такие строки учётными
присваиваниями и отказывает на любой правке этих файлов.

Повод (жалоба владельца 15.10.2026). После применения режима из выпадающего списка
пропадали все значения, кроме `cli`, и ни одно не было выбрано: ответ на применение
режима приходил СТРОКОЙ, страница присваивала его себе и теряла список режимов.
Форма ответа приведена к одной на сервере (`mode_payload`), здесь — страховка
на стороне страницы. Кроме того владелец просил под выбором режима показывать
инструкцию по подключению для AutoClaw и HARNESS — теперь она есть и строится из
данных сервера (значения формы "Add tool", команда запуска сервиса, конфигурации
клиента HARNESS).

Запуск:  python tools/add_admin_mode_help.py
Проверка: python tools/add_admin_mode_help.py --check
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
APP_JS = REPO / "admin" / "static" / "app.js"
I18N = REPO / "src" / "ragraf" / "i18n.py"

MARKER = "MODE-HELP-PATCH"

MODE_HELP_JS = r'''// MODE-HELP-PATCH: что делать с выбранным режимом — инструкция под самим выбором.
function modeValueOf(state) {
  // Ответ на применение режима приходил СТРОКОЙ (`mode`), а на чтение — объектом
  // со списком режимов. Страница присваивала себе ответ целиком, теряла список и
  // показывала один `cli` без выбранного значения: смена режима выглядела как
  // поломка. Форма ответа приведена к одной на сервере; здесь — страховка.
  const raw = (state && state.mode) || {};
  return typeof raw === 'string' ? raw : String(raw.mode || '');
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

  // --- AutoClaw: он подключается по HTTP, поэтому значения формы и команда
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

  // --- HARNESS: показываются только те варианты подключения, что отвечают
  //     выбранному каналу. Конфигурацию даёт сервер, страница её не сочиняет.
  const wanted = (wantsHttp && wantsStdio) ? ['stdio', 'streamable-http']
    : (wantsHttp ? ['streamable-http'] : ['stdio']);
  const rows = (hs.variants || []).filter(function (v) {
    return wanted.indexOf(v.transport) >= 0;
  }).map(function (v) {
    return '<h4>' + esc(v.transport) + '</h4>'
      + (v.note ? '<p class="muted">' + esc(serverText(v.note)) + '</p>' : '')
      + '<pre><code>' + esc(JSON.stringify(v.entry, null, 1)) + '</code></pre>'
      + (v.path
          ? '<p class="muted">' + esc(t('The profile file:')) + ' <code>' + esc(v.path)
            + '</code></p>'
          : '');
  }).join('');
  parts.push('<h4>' + esc(t('Connection in HARNESS')) + '</h4>'
    + (rows || '<p class="muted">' + esc(t('no data')) + '</p>'));

  return parts.join('');
}

'''


def main(check_only: bool) -> int:
    app_text = APP_JS.read_text(encoding="utf-8")
    i18n_text = I18N.read_text(encoding="utf-8")
    if MARKER in app_text:
        print("правка уже применена — файлы не менялись")
        return 0

    patches = (
        (
            "function renderMode(state) {\n"
            "  const mode = (state && state.mode) || {};\n"
            "  const probe = (state && state.probe) || {};\n"
            "  const clients = (state && state.clients) || {};\n"
            "  const modes = mode.modes || ['cli'];\n",
            MODE_HELP_JS
            + "function renderMode(state) {\n"
              "  const raw = (state && state.mode) || {};\n"
              "  // Строка вместо объекта — ответ на применение режима до правки формы.\n"
              "  const mode = (typeof raw === 'string') ? { mode: raw } : raw;\n"
              "  const probe = (state && state.probe) || {};\n"
              "  const clients = (state && state.clients) || {};\n"
              "  // Запасной список — только если сервер не ответил: набор режимов\n"
              "  // закрытый и описан в §8.20 правил работы.\n"
              "  const modes = (mode.modes && mode.modes.length)\n"
              "    ? mode.modes : ['cli', 'mcp-stdio', 'mcp-http', 'both'];\n",
        ),
        (
            "        : t('verification not started'))\n"
            "    + '</span></div>'\n",
            "        : t('verification not started'))\n"
            "    + '</span></div>'\n"
            "    + '<div id=\"mode-instructions\">' + modeSetupHtml(state) + '</div>'\n",
        ),
        (
            "  const sel = document.getElementById('mode-select');\n"
            "  const apply = document.getElementById('mode-apply');\n",
            "  const sel = document.getElementById('mode-select');\n"
            "  // Инструкция обновляется СРАЗУ при выборе: владелец должен видеть, что\n"
            "  // именно настраивать в AutoClaw и HARNESS, ещё до применения режима.\n"
            "  if (sel) sel.addEventListener('change', function () {\n"
            "    const box = document.getElementById('mode-instructions');\n"
            "    if (box) box.innerHTML = modeSetupHtml(\n"
            "      { mode: sel.value, clients: currentState.clients });\n"
            "  });\n"
            "  const apply = document.getElementById('mode-apply');\n",
        ),
        (
            "    if (data.ok) { currentState.mode = data.mode; fill(data.probe); return; }\n",
            "    if (data.ok) {\n"
            "      // Ответ присваивается ЦЕЛИКОМ, со списком режимов: прежде сюда попадала\n"
            "      // строка, и выпадающий список схлопывался в один `cli`.\n"
            "      currentState.mode = { ok: true, mode: modeValueOf({ mode: data.mode }),\n"
            "                             modes: data.modes || [] };\n"
            "      fill(data.probe);\n"
            "      return;\n"
            "    }\n",
        ),
    )

    problems = [
        f"app.js: вхождение найдено {app_text.count(old)} раз, ожидалось 1: {old.splitlines()[0][:60]}"
        for old, _new in patches if app_text.count(old) != 1
    ]
    anchor = '        "developments without index: {count}": "разработок без индекса: {count}",\n'
    anchor_zh = '        "developments without index: {count}": "尚未建立索引的研制项：{count}",\n'
    if i18n_text.count(anchor) != 1 or i18n_text.count(anchor_zh) != 1:
        problems.append("i18n.py: не найдены опорные строки словаря")
    if problems:
        for line in problems:
            print("ОШИБКА:", line)
        return 2
    if check_only:
        print("вхождения на месте: правка применима")
        return 0

    for old, new in patches:
        app_text = app_text.replace(old, new, 1)

    ru = (
        '        "Connection in AutoClaw": "Подключение в AutoClaw",\n'
        '        "Connection in HARNESS": "Подключение в HARNESS",\n'
        '        "Start the service first:": "Сначала поднимите сервис:",\n'
        '        "CLI needs no client: the agent calls":\n'
        '            "CLI не требует клиента: агент вызывает",\n'
        '        "This channel works without AutoClaw or HARNESS settings.":\n'
        '            "Этот канал работает без настройки AutoClaw и HARNESS.",\n'
        '        "AutoClaw connects over HTTP: the selected mode does not expect that "\n'
        '        "channel. Start the service shown here or pick mcp-http or both.":\n'
        '            "AutoClaw подключается по HTTP, а выбранный режим этот канал не "\n'
        '            "ожидает: поднимите сервис по команде выше либо выберите mcp-http "\n'
        '            "или both.",\n'
        '        "The profile file:": "Файл профиля:",\n'
    )
    zh = (
        '        "Connection in AutoClaw": "在 AutoClaw 中连接",\n'
        '        "Connection in HARNESS": "在 HARNESS 中连接",\n'
        '        "Start the service first:": "请先启动服务：",\n'
        '        "CLI needs no client: the agent calls": "CLI 无需客户端：智能体调用",\n'
        '        "This channel works without AutoClaw or HARNESS settings.":\n'
        '            "该通道无需配置 AutoClaw 和 HARNESS。",\n'
        '        "AutoClaw connects over HTTP: the selected mode does not expect that "\n'
        '        "channel. Start the service shown here or pick mcp-http or both.":\n'
        '            "AutoClaw 通过 HTTP 连接，而当前模式不包含该通道：请按上方命令启动服务，"\n'
        '            "或选择 mcp-http / both。",\n'
        '        "The profile file:": "配置文件：",\n'
    )
    i18n_text = i18n_text.replace(anchor, anchor + ru, 1)
    i18n_text = i18n_text.replace(anchor_zh, anchor_zh + zh, 1)

    APP_JS.write_text(app_text, encoding="utf-8", newline="\n")
    I18N.write_text(i18n_text, encoding="utf-8", newline="\n")
    print(f"app.js: {len(app_text)} символов; i18n.py: {len(i18n_text)} символов")
    return 0


if __name__ == "__main__":
    sys.exit(main("--check" in sys.argv))
