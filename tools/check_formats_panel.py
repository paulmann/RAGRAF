"""Проверка вкладки «Форматы» без браузера: отрисовка, счётчик, окно фильтра.

Зачем отдельная проверка
    Вкладка «Форматы» — это код на JavaScript, который никто не исполнит при
    проверке Python-частей: синтаксис (`node --check`) проходит и на функции,
    которая зовёт несуществующий помощник или падает на пустом списке. Ошибка
    тогда видна только в браузере, то есть у владельца.

    Здесь ровно эти три вещи и исполняются в настоящем DOM (jsdom из
    node_modules): `renderFormats` строит разметку, `updateFormatCounter`
    считает включённые, `applyFormatFilter` прячет несовпавшие строки.
    Проверяется ФАКТ: сколько строк нарисовано, какие галочки отмечены, что
    стало видно после ввода в окно фильтра.

    Живой сервер не поднимается: проверяется код панели, а не её порт.

Запуск: python tools/check_formats_panel.py
Код возврата 0 — проверки прошли либо окружение без node/jsdom (пропуск).
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
APP = REPO / "admin" / "static" / "app.js"

FUNCTIONS = ("formatRowHtml", "updateFormatCounter", "setAllFormats",
             "applyFormatFilter", "renderFormats")


def extract(name: str, source: str) -> str:
    """Вырезать функцию по имени с учётом вложенных фигурных скобок."""
    start = source.find(f"function {name}(")
    if start < 0:
        raise SystemExit(f"ОСТАНОВ: функция {name} не найдена в app.js")
    index = source.find("{", start)
    depth = 0
    for pos in range(index, len(source)):
        char = source[pos]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:pos + 1]
    raise SystemExit(f"ОСТАНОВ: у функции {name} не найден конец")


def main() -> int:
    if not APP.exists():
        print(f"ОСТАНОВ: нет файла {APP}")
        return 1
    node = shutil.which("node")
    if not node:
        print("ПРОПУСК: node не найден — проверка вкладки не выполнена")
        return 0
    jsdom = REPO / "node_modules" / "jsdom"
    if not jsdom.exists():
        print("ПРОПУСК: jsdom не установлен — проверка вкладки не выполнена")
        return 0

    # require разрешается относительно файла скрипта, а он лежит во временном
    # каталоге: путь к jsdom передаётся абсолютным, иначе модуль «не найден».
    jsdom_path = json.dumps(str(jsdom.resolve()))

    source = APP.read_bytes().decode("utf-8")
    pieces = "\n\n".join(extract(name, source) for name in FUNCTIONS)

    harness = """
const { JSDOM } = require(__JSDOM__);
const dom = new JSDOM('<!doctype html><html><body></body></html>');
global.window = dom.window;
global.document = dom.window.document;
global.UI = { settings: { fmtQ: '' } };

function esc(value) {
  return String(value === null || value === undefined ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function t(key) { return key; }
function num(value) { return String(value); }
function button(o) {
  const id = o.id ? ' id="' + o.id + '"' : '';
  const dis = o.disabled ? ' disabled' : '';
  const act = o.action ? ' data-action="' + o.action + '"' : '';
  return '<button' + id + act + dis + '>' + esc(o.label || '') + '</button>';
}
function card(o) {
  return '<div class="card">'
    + '<div class="card__head">' + (o.actions || '') + '</div>'
    + '<div class="card__body">' + (o.body || '') + '</div>'
    + '<div class="card__foot">' + (o.foot || '') + '</div></div>';
}
function searchField(o) {
  return '<input data-action="' + o.action + '" value="' + esc(o.value || '') + '">';
}
function emptyHtml(o) { return '<div class="empty">' + esc(o.title) + '</div>'; }
function writeAllowed() { return true; }
function deniedCallout() { return ''; }

__PIECES__

const list = [
  { id: 'python', title: 'Python', group: 'code', group_title: 'Языки программирования',
    exts: ['.py'], names: [], structure_title: 'разбор деревом', note: '', enabled: true },
  { id: 'kotlin', title: 'Kotlin', group: 'code', group_title: 'Языки программирования',
    exts: ['.kt'], names: [], structure_title: 'разбор деревом (tags-запрос)',
    note: '', enabled: true },
  { id: 'word', title: 'Word', group: 'document', group_title: 'Документы',
    exts: ['.docx', '.doc'], names: [], structure_title: 'извлечение текста библиотекой',
    note: 'старый .doc — собственный разбор', enabled: false }
];
const meta = { formats: list, formats_summary: { total: 3, enabled: 2 } };
const html = renderFormats(meta, '<header></header>', '<div>', '</div>');
document.body.innerHTML = html;

const result = {};
result.rendered_ids = Array.prototype.slice
  .call(document.querySelectorAll('[data-format]'))
  .map(function (box) { return box.getAttribute('data-format'); });
result.checked = Array.prototype.slice
  .call(document.querySelectorAll('[data-format]'))
  .filter(function (box) { return box.checked; })
  .map(function (box) { return box.getAttribute('data-format'); });
result.rows = document.querySelectorAll('[data-format-row]').length;
result.groups = document.querySelectorAll('[data-format-group]').length;
result.has_group_titles = html.indexOf('Документы') >= 0
  && html.indexOf('Языки программирования') >= 0;
result.has_structures = html.indexOf('tags-запрос') >= 0
  && html.indexOf('извлечение текста библиотекой') >= 0;
result.has_filter = html.indexOf('data-action="formats-filter"') >= 0;
result.has_buttons = html.indexOf('data-action="formats-all"') >= 0
  && html.indexOf('data-action="formats-none"') >= 0
  && html.indexOf('id="save-formats"') >= 0;

updateFormatCounter();
result.counter = (document.getElementById('formats-count') || {}).textContent || '';

applyFormatFilter('kotlin');
result.after_filter_visible = Array.prototype.slice
  .call(document.querySelectorAll('[data-format-row]'))
  .filter(function (row) { return !row.hidden; })
  .map(function (row) { return row.getAttribute('data-hay').split(' ')[0]; });
result.after_filter_groups = Array.prototype.slice
  .call(document.querySelectorAll('[data-format-group]'))
  .filter(function (group) { return !group.hidden; }).length;
result.after_filter_found = (document.getElementById('formats-found') || {}).textContent || '';

applyFormatFilter('.doc');
result.after_ext_visible = Array.prototype.slice
  .call(document.querySelectorAll('[data-format-row]'))
  .filter(function (row) { return !row.hidden; })
  .map(function (row) { return row.getAttribute('data-hay').split(' ')[0]; });

applyFormatFilter('');
result.after_reset_visible = Array.prototype.slice
  .call(document.querySelectorAll('[data-format-row]'))
  .filter(function (row) { return !row.hidden; }).length;

setAllFormats(true);
result.after_all_checked = Array.prototype.slice
  .call(document.querySelectorAll('[data-format]'))
  .filter(function (box) { return box.checked; }).length;
setAllFormats(false);
result.after_none_checked = Array.prototype.slice
  .call(document.querySelectorAll('[data-format]'))
  .filter(function (box) { return box.checked; }).length;

console.log(JSON.stringify(result));
""".replace("__PIECES__", pieces).replace("__JSDOM__", jsdom_path)

    with tempfile.TemporaryDirectory() as tmp:
        script = pathlib.Path(tmp) / "panel_check.cjs"
        script.write_text(harness, encoding="utf-8")
        proc = subprocess.run(
            [node, str(script)],
            cwd=str(REPO), capture_output=True, text=True, encoding="utf-8",
            timeout=120,
        )
    if proc.returncode != 0:
        print("ПРОВАЛ: harness не выполнился")
        print(proc.stdout[-2000:])
        print(proc.stderr[-3000:])
        return 1
    line = [row for row in proc.stdout.splitlines() if row.strip().startswith("{")]
    if not line:
        print("ПРОВАЛ: нет JSON-ответа от harness")
        print(proc.stdout[-2000:])
        return 1
    got = json.loads(line[-1])

    checks = [
        (got["rows"] == 3, "нарисованы все три строки форматов", got["rows"]),
        (got["groups"] == 2, "группы разделены", got["groups"]),
        (got["rendered_ids"] == ["python", "kotlin", "word"],
         "идентификаторы форматов попали в разметку", got["rendered_ids"]),
        (got["checked"] == ["python", "kotlin"],
         "отмечены только включённые форматы", got["checked"]),
        (got["has_group_titles"], "названия групп на месте", got["has_group_titles"]),
        (got["has_structures"], "способ разбора показан у формата",
         got["has_structures"]),
        (got["has_filter"], "окно фильтра есть", got["has_filter"]),
        (got["has_buttons"], "кнопки «все», «ничего» и сохранение есть",
         got["has_buttons"]),
        ("2 / 3" in got["counter"], "счётчик показывает включённые из всех",
         got["counter"]),
        (got["after_filter_visible"] == ["kotlin"],
         "фильтр по имени оставляет одну строку", got["after_filter_visible"]),
        (got["after_filter_groups"] == 1,
         "пустая группа скрыта вместе со строками", got["after_filter_groups"]),
        (got["after_filter_found"] == "1", "счётчик найденного обновился",
         got["after_filter_found"]),
        (got["after_ext_visible"] == ["word"],
         "фильтр по расширению находит формат", got["after_ext_visible"]),
        (got["after_reset_visible"] == 3, "сброс фильтра возвращает все строки",
         got["after_reset_visible"]),
        (got["after_all_checked"] == 3, "кнопка «все» отмечает всё",
         got["after_all_checked"]),
        (got["after_none_checked"] == 0, "кнопка «ничего» снимает всё",
         got["after_none_checked"]),
    ]

    failed = 0
    for ok, label, detail in checks:
        if ok:
            print(f"  [OK ] {label}")
        else:
            failed += 1
            print(f"  [FAIL] {label} — получено: {detail!r}")

    print()
    print(f"ИТОГ: пройдено {len(checks) - failed} из {len(checks)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
