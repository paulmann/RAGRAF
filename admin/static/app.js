/* ==========================================================================
   RAGRAF — панель управления: клиентская логика
   ==========================================================================
   Принципы:
     * никаких внешних библиотек: панель работает без сети и без сборки;
     * все данные приходят из /api/state одним запросом: одна точка правды;
     * изменяющие запросы несут токен доступа (получен при загрузке);
     * результат изменения показывается явно: «сохранено» и что именно.
   ========================================================================== */

'use strict';

const TOKEN_KEY = 'ragraf-admin-token';
let accessToken = '';
let currentState = null;

/** Экранирование: значения приходят из данных, а не из разметки. */
function esc(value) {
  return String(value === null || value === undefined ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/** Человекочитаемый размер. */
function humanBytes(bytes) {
  const n = Number(bytes || 0);
  if (n < 1024) return n + ' Б';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' КБ';
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' МБ';
  return (n / 1024 / 1024 / 1024).toFixed(2) + ' ГБ';
}

function num(value) {
  return Number(value || 0).toLocaleString('ru-RU');
}

function setStatus(text, kind) {
  const el = document.getElementById('status');
  el.textContent = text;
  el.className = 'status' + (kind ? ' status--' + kind : '');
}

/** Обращение к API с токеном для изменяющих запросов. */
async function api(path, options) {
  const opts = Object.assign({}, options || {});
  opts.headers = Object.assign({ 'Accept': 'application/json' }, opts.headers || {});
  if (opts.body) {
    opts.headers['Content-Type'] = 'application/json';
    opts.headers['X-RAGRAF-Token'] = accessToken;
  }
  const response = await fetch(path, opts);
  let data = null;
  try { data = await response.json(); } catch (e) { data = { ok: false, error: 'ответ не разобран' }; }
  if (!response.ok && data && !data.error) {
    data.error = 'код ' + response.status;
  }
  return data;
}

async function loadToken() {
  // Токен сохраняется между обновлениями страницы: сервер слушает только
  // localhost, и повторно печатать его администратору не нужно.
  accessToken = window.localStorage.getItem(TOKEN_KEY) || '';
  const data = await api('/api/token');
  if (data && data.ok && data.token) {
    accessToken = data.token;
    window.localStorage.setItem(TOKEN_KEY, accessToken);
  }
}

function chip(text, kind) {
  return '<span class="chip' + (kind ? ' chip--' + kind : '') + '">' + esc(text) + '</span>';
}

// --------------------------------------------------------------------------
// Разделы страницы
// --------------------------------------------------------------------------

function renderCatalog(state) {
  const catalog = state.catalog || {};
  const entries = catalog.entries || [];
  if (!entries.length) {
    return '<section class="card"><div class="card__body">'
      + '<p>В реестре нет разработок. Зарегистрируйте корпус командой '
      + '<span class="mono">ragraf register</span>.</p></div></section>';
  }

  const rows = entries.map(function (e) {
    let freshness;
    if (e.hash_matches === true) {
      freshness = chip('индекс актуален', 'ok');
    } else if (e.hash_matches === false) {
      freshness = chip('требуется реиндексация', 'warn');
    } else if (e.check_error) {
      freshness = chip('проверить не удалось: ' + e.check_error, 'bad');
    } else {
      freshness = chip('не индексировалась');
    }
    const sizes = (e.size && e.size.files || [])
      .map(function (f) { return esc(f.title) + ': ' + humanBytes(f.bytes); })
      .join(' · ');

    return '<tr>'
      + '<td><b>' + esc(e.category) + '</b><div class="small muted mono">' + esc(e.root) + '</div></td>'
      + '<td>' + esc(e.rev_id || '—') + '<div class="small muted">' + esc(e.capability || '') + '</div></td>'
      + '<td>' + freshness + '</td>'
      + '<td class="mono small">' + num(e.files) + ' / ' + num(e.nodes) + ' / ' + num(e.edges) + '</td>'
      + '<td class="small">' + (sizes || '<span class="muted">—</span>') + '</td>'
      + '<td class="mono small">' + esc((e.corpus_hash || '').slice(0, 16)) + '…</td>'
      + '</tr>';
  }).join('');

  return '<section class="card">'
    + '<div class="card__head"><h2 class="card__title">Разработки</h2>'
    + '<span class="small muted">файлов / узлов / рёбер · контрольная сумма корпуса</span></div>'
    + '<div class="card__body card__body--flush"><table>'
    + '<thead><tr><th>Разработка</th><th>Ревизия</th><th>Состояние индекса</th>'
    + '<th>Состав</th><th>Размер баз</th><th>Сумма корпуса</th></tr></thead>'
    + '<tbody>' + rows + '</tbody></table></div></section>';
}

function renderStorage(state) {
  const entries = (state.catalog && state.catalog.entries) || [];
  if (!entries.length) return '';

  const blocks = entries.map(function (e) {
    const size = e.size || {};
    const files = (size.files || []).map(function (f) {
      return '<tr><td>' + esc(f.title) + '</td>'
        + '<td class="mono small">' + esc(f.path) + '</td>'
        + '<td>' + humanBytes(f.bytes)
        + (f.wal_bytes ? '<div class="small muted">из них журнал: ' + humanBytes(f.wal_bytes) + '</div>' : '')
        + (f.missing ? '<div class="small">файл не найден</div>' : '')
        + '</td></tr>';
    }).join('');

    const tables = (size.tables || []).slice(0, 24).map(function (t) {
      return '<tr><td class="mono">' + esc(t.name) + '</td><td>' + num(t.rows) + '</td></tr>';
    }).join('');

    return '<details><summary>' + esc(e.category)
      + ' — всего ' + humanBytes(size.total_bytes) + '</summary>'
      + '<div class="card__body"><div class="grid-2">'
      + '<div><table><thead><tr><th>База</th><th>Файл</th><th>Размер</th></tr></thead>'
      + '<tbody>' + files + '</tbody></table></div>'
      + '<div><table><thead><tr><th>Таблица</th><th>Строк</th></tr></thead>'
      + '<tbody>' + (tables || '<tr><td colspan="2" class="muted">нет данных</td></tr>') + '</tbody></table></div>'
      + '</div></div></details>';
  }).join('');

  return '<section class="card">'
    + '<div class="card__head"><h2 class="card__title">Размеры баз</h2>'
    + '<span class="small muted">показаны первые 24 таблицы каждой базы</span></div>'
    + '<div class="card__body card__body--flush">' + blocks + '</div></section>';
}

function renderUsage(state) {
  const detail = state.detail || {};
  const names = Object.keys(detail);
  if (!names.length) return '';

  const cards = names.map(function (name) {
    const usage = detail[name].usage || {};
    if (usage.error) {
      return '<div class="card__body"><p class="muted">Статистика недоступна: '
        + esc(usage.error) + '</p></div>';
    }
    const metrics = [
      ['Обращений', num(usage.calls_total), 'к операциям фасада'],
      ['Сессий', num(usage.sessions_total), 'запусков инструмента'],
      ['Фактов', num(usage.facts_total), 'получено из индекса'],
      ['Проверяемость', (Number(usage.verifiable_ratio || 0) * 100).toFixed(1) + '%', 'со ссылкой файл:строка'],
      ['Токенов (оценка)', num(usage.est_tokens_total), 'по объёму ответов'],
      ['Не читано файлами', num(usage.est_lines_not_read), 'строк кода (оценка)'],
    ].map(function (m) {
      return '<div class="metric"><div class="metric__label">' + esc(m[0]) + '</div>'
        + '<div class="metric__value">' + esc(m[1]) + '</div>'
        + '<div class="metric__note">' + esc(m[2]) + '</div></div>';
    }).join('');

    // Таблица агентов: кто именно использовал RAGRAF. Сервер отдаёт
    // by_agent (по каждому агенту) и by_agent_kind (по семействам).
    // Без этого блока данные были, но увидеть их в панели было нельзя.
    const agentRows = (usage.by_agent || []).map(function (a) {
      const kind = String(a.agent_kind || '');
      const kindTitle = kind === 'autoclaw' ? 'AutoClaw'
        : kind === 'external' ? 'внешний'
        : kind === 'internal' ? 'служебный' : kind;
      return '<tr><td class="mono">' + esc(a.agent) + '</td>'
        + '<td class="small muted">' + esc(kindTitle) + '</td>'
        + '<td>' + num(a.sessions) + '</td>'
        + '<td>' + num(a.calls) + '</td>'
        + '<td>' + num(a.facts) + '</td>'
        + '<td>' + num(Math.round((a.replacing_chars || 0) / 45)) + '</td>'
        + '<td>' + num(a.errors) + '</td></tr>';
    }).join('');

    const kindRows = (usage.by_agent_kind || []).map(function (k) {
      return '<tr><td class="mono">' + esc(k.agent_kind) + '</td>'
        + '<td>' + num(k.agents) + '</td>'
        + '<td>' + num(k.sessions) + '</td>'
        + '<td>' + num(k.calls) + '</td>'
        + '<td>' + num(k.est_lines_not_read) + '</td></tr>';
    }).join('');

    const agentsBlock = (usage.by_agent || []).length
      ? '<div style="margin-top:14px"><h3 class="small muted">'
        + 'По агентам (кто использовал RAGRAF)</h3>'
        + '<table><thead><tr><th>Агент</th><th>Семейство</th>'
        + '<th>Сессий</th><th>Вызовов</th><th>Фактов</th>'
        + '<th>Строк не читалось</th><th>Ошибок</th></tr></thead><tbody>'
        + agentRows + '</tbody></table>'
        + '<table style="margin-top:10px"><thead><tr><th>Семейство</th>'
        + '<th>Агентов</th><th>Сессий</th><th>Вызовов</th>'
        + '<th>Строк не читалось</th></tr></thead><tbody>' + kindRows
        + '</tbody></table></div>'
      : '';

    const byOp = (usage.by_op || []).slice(0, 10).map(function (o) {
      return '<tr><td class="mono">' + esc(o.op) + '</td><td>' + num(o.calls) + '</td>'
        + '<td>' + num(o.found_calls) + '</td><td>' + num(o.facts) + '</td>'
        + '<td>' + num(o.with_span) + '</td>'
        + '<td>' + Number(o.avg_ms || 0).toFixed(1) + ' мс</td></tr>';
    }).join('');

    const byArg = (usage.by_arg || []).filter(function (a) { return a.arg_digest; }).slice(0, 10)
      .map(function (a) {
        return '<tr><td class="mono">' + esc(a.op) + '</td><td class="mono small">'
          + esc(a.arg_digest) + '</td><td>' + num(a.calls) + '</td></tr>';
      }).join('');

    return '<details><summary>' + esc(name) + ' — обращений ' + num(usage.calls_total) + '</summary>'
      + '<div class="card__body"><div class="metrics">' + metrics + '</div>'
      + agentsBlock
      + '<div class="grid-2" style="margin-top:14px">'
      + '<div><table><thead><tr><th>Операция</th><th>Вызовов</th><th>Найдено</th>'
      + '<th>Фактов</th><th>Со строкой</th><th>Среднее</th></tr></thead><tbody>' + byOp + '</tbody></table></div>'
      + '<div><table><thead><tr><th>Операция</th><th>Объект</th><th>Вызовов</th></tr></thead>'
      + '<tbody>' + (byArg || '<tr><td colspan="3" class="muted">нет данных</td></tr>') + '</tbody></table></div>'
      + '</div></div></details>';
  }).join('');

  return '<section class="card">'
    + '<div class="card__head"><h2 class="card__title">Использование и эффект</h2>'
    + '<span class="small muted">токены и строки — оценка, обращения и факты — измерение</span></div>'
    + '<div class="card__body card__body--flush">' + cards + '</div></section>';
}

function renderQuality(state) {
  const detail = state.detail || {};
  const names = Object.keys(detail);
  if (!names.length) return '';

  const blocks = names.map(function (name) {
    const q = detail[name].quality || {};
    if (q.error) {
      return '<details><summary>' + esc(name) + ' — нет данных</summary>'
        + '<div class="card__body"><p class="muted">' + esc(q.error) + '</p></div></details>';
    }
    const quality = q.quality || {};
    const vectors = quality.vectors || {};
    const chunks = quality.chunks || {};
    const sigs = quality.signatures || {};
    const problems = quality.problems || [];
    const compute = q.compute || {};
    // Строка о вычислителе: администратор должен видеть, где считаются векторы
    // и почему выбран этот путь. Без этого непонятно, работает ли видеокарта.
    const computeText = compute.backend
      ? esc(compute.backend) + (compute.uses_gpu ? ' — видеокарта' : ' — процессор')
      : 'не определён';

    const verdict = quality.healthy
      ? chip('проблем не обнаружено', 'ok')
      : chip('проблемы: ' + problems.length, 'warn');

    const metrics = [
      ['Векторы', vectors.available ? (vectors.normalized ? 'нормированы' : 'не нормированы') : 'нет',
        'норма p50 ' + (vectors.norm_p50 === null || vectors.norm_p50 === undefined ? '—' : vectors.norm_p50)],
      ['Чанки', num(chunks.total), 'максимум ' + num(chunks.max_tokens) + ' токенов'],
      ['Сверх лимита', num(chunks.over_budget), 'чанков длиннее ' + num(chunks.budget_tokens)],
      ['Файлов без символов', num(chunks.files_without_symbols), 'обёрнуты в окна'],
      ['Функций', num(sigs.functions), 'с параметрами разобрано'],
      ['Обрезанных сигнатур', num(sigs.truncated), 'параметры теряются в тексте'],
      ['Docblock\'ов', num(sigs.docblocks), 'контракты параметров'],
    ].map(function (m) {
      return '<div class="metric"><div class="metric__label">' + esc(m[0]) + '</div>'
        + '<div class="metric__value">' + esc(m[1]) + '</div>'
        + '<div class="metric__note">' + esc(m[2]) + '</div></div>';
    }).join('');

    const stages = (q.stages || []).map(function (s) {
      return '<tr><td class="mono">' + esc(s.stage) + '</td>'
        + '<td>' + Number(s.seconds || 0).toFixed(3) + ' с</td>'
        + '<td>' + num(s.items) + '</td></tr>';
    }).join('');

    const computeRow = compute.backend
      ? '<p class="notice" style="margin-top:12px">Вычислитель векторов: '
        + computeText
        + (compute.device ? ' (' + esc(compute.device) + ')' : '')
        + (compute.ms_per_text ? ', ' + esc(compute.ms_per_text) + ' мс на текст' : '')
        + (compute.reason ? '<br><span class="small muted">' + esc(compute.reason) + '</span>' : '')
        + '</p>'
      : '';

    return '<details><summary>' + esc(name) + ' — ' + (q.rev || '—') + ' · ' + verdict + '</summary>'
      + '<div class="card__body"><div class="metrics">' + metrics + '</div>'
      + computeRow
      + (problems.length
          ? '<p class="notice notice--warn" style="margin-top:12px">Обнаружено: '
            + esc(problems.join('; ')) + '</p>'
          : '')
      + '<table style="margin-top:12px"><thead><tr><th>Стадия</th><th>Время</th><th>Элементов</th></tr></thead>'
      + '<tbody>' + stages + '</tbody></table>'
      + '</div></details>';
  }).join('');

  return '<section class="card">'
    + '<div class="card__head"><h2 class="card__title">Качество индекса</h2>'
    + '<span class="small muted">нормы векторов, размеры чанков, полнота сигнатур</span></div>'
    + '<div class="card__body card__body--flush">' + blocks + '</div></section>';
}

function field(key, value, meta) {
  const desc = meta.descriptions[key] || '';
  const bounds = meta.bounds[key];
  const allowed = (meta.enums || {})[key];
  if (typeof value === 'boolean') {
    return '<div class="field"><label for="g-' + esc(key) + '">' + esc(key) + '</label>'
      + '<select id="g-' + esc(key) + '" data-global="' + esc(key) + '">'
      + '<option value="true"' + (value ? ' selected' : '') + '>включено</option>'
      + '<option value="false"' + (!value ? ' selected' : '') + '>выключено</option>'
      + '</select>'
      + (desc ? '<p class="hint">' + esc(desc) + '</p>' : '') + '</div>';
  }
  // Настройка-перечисление: список допустимых значений.
  // Без этой ветки строковое значение (например 'docs') подставлялось бы
  // в поле ввода числа, и администратор видел бы пустое поле вместо режима.
  if (allowed && allowed.length) {
    const items = allowed.map(function (candidate) {
      return '<option value="' + esc(candidate) + '"'
        + (String(value) === String(candidate) ? ' selected' : '') + '>'
        + esc(candidate) + '</option>';
    }).join('');
    return '<div class="field"><label for="g-' + esc(key) + '">' + esc(key) + '</label>'
      + '<select id="g-' + esc(key) + '" data-global="' + esc(key) + '">'
      + items + '</select>'
      + (desc ? '<p class="hint">' + esc(desc) + '</p>' : '') + '</div>';
  }
  const range = bounds ? ' от ' + bounds[0] + ' до ' + bounds[1] : '';
  return '<div class="field"><label for="g-' + esc(key) + '">' + esc(key) + '</label>'
    + '<input type="number" id="g-' + esc(key) + '" data-global="' + esc(key) + '"'
    + ' value="' + esc(value) + '">'
    + (desc ? '<p class="hint">' + esc(desc) + (range ? ' Допустимо' + range + '.' : '') + '</p>' : '')
    + '</div>';
}

function renderSettings(state) {
  const meta = state.settings || {};
  const glob = meta.global_settings || {};
  const perProject = meta.per_project || {};
  const editable = meta.editable || [];

  const globalFields = editable.filter(function (k) {
    return Object.prototype.hasOwnProperty.call(glob, k);
  }).map(function (k) { return field(k, glob[k], meta); }).join('');

  const projects = Object.keys(perProject).map(function (name) {
    const p = perProject[name];
    const rows = editable.map(function (k) {
      const source = p[k + '__source'] || '—';
      const bounds = (meta.bounds || {})[k];
      const allowed = (meta.enums || {})[k];
      let control;
      if (typeof p[k] === 'boolean') {
        control = '<select data-project="' + esc(name) + '" data-key="' + esc(k) + '">'
          + '<option value="true"' + (p[k] ? ' selected' : '') + '>включено</option>'
          + '<option value="false"' + (!p[k] ? ' selected' : '') + '>выключено</option></select>';
      } else if (allowed && allowed.length) {
        // Перечисление: список значений вместо поля ввода числа.
        control = '<select data-project="' + esc(name) + '" data-key="' + esc(k) + '">'
          + allowed.map(function (candidate) {
            return '<option value="' + esc(candidate) + '"'
              + (String(p[k]) === String(candidate) ? ' selected' : '') + '>'
              + esc(candidate) + '</option>';
          }).join('') + '</select>';
      } else {
        control = '<input type="number" data-project="' + esc(name) + '" data-key="' + esc(k)
          + '" value="' + esc(p[k]) + '">';
      }
      return '<tr><td>' + esc(k) + '<div class="small muted">' + esc(source === 'проект' ? 'задано в конфиге корпуса' : 'унаследовано глобально') + '</div></td>'
        + '<td>' + control + '</td>'
        + '<td class="small muted">' + esc((meta.descriptions || {})[k] ? (meta.descriptions[k].slice(0, 90) + '…') : '') + '</td></tr>';
    }).join('');

    return '<details><summary>' + esc(name) + ' — '
      + (p.config_exists ? 'конфиг найден' : 'конфиг не найден') + '</summary>'
      + '<div class="card__body">'
      + '<p class="small muted mono">' + esc(p.config_path) + '</p>'
      + '<table><thead><tr><th>Настройка</th><th>Значение</th><th>Пояснение</th></tr></thead>'
      + '<tbody>' + rows + '</tbody></table>'
      + '<div class="row-actions">'
      + '<button class="btn btn--primary" data-save-project="' + esc(name) + '" type="button">Сохранить в конфиг корпуса</button>'
      + '<span class="small muted">правка затрагивает только будущие проверки; '
      + 'текущая ревизия не меняется</span></div></div></details>';
  }).join('');

  return '<section class="card">'
    + '<div class="card__head"><h2 class="card__title">Настройки</h2>'
    + '<span class="small muted">пороги авто-реиндексации и параметры индексации</span></div>'
    + '<div class="card__body">'
    + '<h3 class="card__title" style="margin-bottom:10px">Значения по умолчанию</h3>'
    + '<div class="grid-2">' + globalFields + '</div>'
    + '<div class="row-actions">'
    + '<button class="btn btn--primary" id="save-global" type="button">Сохранить общие настройки</button>'
    + '<span class="small muted">действуют для разработок, где ключ не задан отдельно</span>'
    + '</div></div>'
    + '<div class="card__body card__body--flush" style="border-top:1px solid var(--border)">'
    + (projects || '<p class="muted" style="padding:16px 18px">Разработок нет</p>')
    + '</div></section>';
}

// --------------------------------------------------------------------------
// Действия
// --------------------------------------------------------------------------

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

function showMessage(text, kind) {
  const box = document.getElementById('message');
  if (!box) return;
  box.className = 'notice' + (kind ? ' notice--' + kind : '');
  box.textContent = text;
  box.hidden = false;
}

async function saveGlobal(button) {
  button.disabled = true;
  setStatus('сохраняю…');
  const data = await api('/api/settings/global', {
    method: 'POST', body: JSON.stringify({ values: collectGlobal() }),
  });
  button.disabled = false;
  if (data && data.ok) {
    setStatus('настройки сохранены', 'ok');
    showMessage('Общие настройки сохранены. ' + (data.note || ''), 'ok');
    await refresh();
  } else {
    setStatus('ошибка сохранения', 'bad');
    showMessage('Не удалось сохранить: ' + ((data && data.error) || 'неизвестная причина'), 'bad');
  }
}

async function saveProject(name, button) {
  button.disabled = true;
  setStatus('сохраняю…');
  const data = await api('/api/settings/project', {
    method: 'POST',
    body: JSON.stringify({ category: name, values: collectProject(name) }),
  });
  button.disabled = false;
  if (data && data.ok) {
    setStatus('настройки разработки сохранены', 'ok');
    showMessage('Записано в ' + data.config + ': '
      + Object.keys(data.written || {}).join(', ')
      + '. Рядом создана резервная копия.', 'ok');
    await refresh();
  } else {
    setStatus('ошибка сохранения', 'bad');
    showMessage('Не удалось сохранить: ' + ((data && data.error) || 'неизвестная причина'), 'bad');
  }
}

function render() {
  const state = currentState;
  if (!state) return;
  const app = document.getElementById('app');
  app.innerHTML = '<div class="notice" id="message" hidden></div>'
    + renderCatalog(state)
    + renderQuality(state)
    + renderUsage(state)
    + renderStorage(state)
    + renderSettings(state);

  document.getElementById('save-global').addEventListener('click', function () {
    saveGlobal(this);
  });
  document.querySelectorAll('[data-save-project]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      saveProject(this.getAttribute('data-save-project'), this);
    });
  });
}

async function refresh() {
  setStatus('обновляю…');
  const data = await api('/api/state');
  if (!data || !data.ok) {
    setStatus('ошибка загрузки', 'bad');
    document.getElementById('app').innerHTML =
      '<div class="notice notice--bad">Не удалось получить состояние: '
      + esc((data && data.error) || 'неизвестная причина') + '</div>';
    return;
  }
  currentState = data.state;
  render();
  const catalog = (currentState.catalog && currentState.catalog.entries) || [];
  const stale = catalog.filter(function (e) { return e.hash_matches === false; }).length;
  setStatus(stale ? ('разработок требуют реиндексации: ' + stale) : 'все индексы актуальны',
    stale ? 'bad' : 'ok');
}

(async function start() {
  await loadToken();
  document.getElementById('reload').addEventListener('click', refresh);
  await refresh();
})();
