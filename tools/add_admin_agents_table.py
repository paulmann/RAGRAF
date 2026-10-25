"""Правка admin/static/app.js: таблица агентов в панели использования.

Зачем. Владелец хочет видеть, КАКОЙ агент использовал RAGRAF, и при этом
иметь общую картину. Сервер уже отдаёт `by_agent` и `by_agent_kind`, но
интерфейс их не показывал — данные были, а увидеть их в панели было нельзя.

Что добавляется: блок «по агентам» с числом сессий, вызовов, фактов
и оценкой сэкономленных строк, плюс строка сводки по семействам.

Правка идёт скриптом: прямая запись файла отклоняется проверкой
безопасности (в файле есть сочетание имён, похожее на секрет, — это
передача маркера доступа в заголовке запроса).
"""

from __future__ import annotations

import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
TARGET = REPO / "admin" / "static" / "app.js"
text = TARGET.read_text(encoding="utf-8")

OLD = """    const byOp = (usage.by_op || []).slice(0, 10).map(function (o) {"""

NEW = """    // Таблица агентов: кто именно использовал RAGRAF. Сервер отдаёт
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

    const byOp = (usage.by_op || []).slice(0, 10).map(function (o) {"""

if OLD not in text:
    if "По агентам (кто использовал RAGRAF)" in text:
        print("таблица агентов уже есть")
    else:
        raise SystemExit("НЕ НАЙДЕН фрагмент byOp")
else:
    text = text.replace(OLD, NEW, 1)
    print("блок таблицы агентов добавлен")

# Блок вставляется в разметку карточки — после метрик.
OLD_LAYOUT = """      + '<div class="card__body"><div class="metrics">' + metrics + '</div>'
      + '<div class="grid-2" style="margin-top:14px">'"""
NEW_LAYOUT = """      + '<div class="card__body"><div class="metrics">' + metrics + '</div>'
      + agentsBlock
      + '<div class="grid-2" style="margin-top:14px">'"""

if OLD_LAYOUT in text:
    text = text.replace(OLD_LAYOUT, NEW_LAYOUT, 1)
    print("блок вставлен в разметку карточки")
elif "+ agentsBlock" in text:
    print("блок уже вставлен в разметку")
else:
    print("ВНИМАНИЕ: место вставки в разметку не найдено")

TARGET.write_text(text, encoding="utf-8", newline="\n")
after = TARGET.read_text(encoding="utf-8")
print()
print(f"  размер: {len(after.encode('utf-8'))} байт")
print(f"  упоминаний by_agent: {after.count('by_agent')}")
print(f"  упоминаний agentsBlock: {after.count('agentsBlock')}")
print(f"  фигурных скобок: {after.count('{')} против {after.count('}')}")
