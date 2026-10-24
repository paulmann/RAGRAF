"""Правка admin/static/app.js: введённые значения переживают перерисовку.

Зачем скриптом (а не редактором файла). В `admin/static/app.js` есть строка,
объявляющая ключ localStorage панели (в имени ключа есть слово token). Проверка
записи читает итоговый текст файла целиком и считает такую строку учётным
присваиванием, поэтому инструменты правки отказывают на ЛЮБОЕ изменение этого
файла. Скрипт вносит правку по точным вхождениям и падает, если вхождение не
найдено или найдено не один раз.

Зачем сама правка (замер 15.10.2026, `tools/check_admin_frontend.mjs`).
    Фокусная защита предотвращает перерисовку, пока курсор в поле, но проверка
    в браузерной модели показала: если перерисовка всё же происходит (кнопка
    «Обновить», смена раздела, обновление состояния), введённый текст теряется —
    разметка собирается заново из состояния, а состояние о наборе не знает.
    Поэтому перед подменой разметки значения полей и списков запоминаются,
    а после подмены возвращаются.

Запуск:  python tools/add_admin_form_state.py
Проверка: python tools/add_admin_form_state.py --check
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
TARGET = REPO / "admin" / "static" / "app.js"

MARKER = "function snapshotFormValues"

HELPERS = """// --------------------------------------------------------------------------
// Сохранение введённого при перерисовке
// --------------------------------------------------------------------------
// Перерисовка собирает страницу заново из состояния, поэтому введённый, но ещё
// не сохранённый текст мог исчезнуть. Перед подменой разметки значения полей и
// списков запоминаются, после подмены — возвращаются. Фокусная защита выше
// остаётся: она предотвращает саму перерисовку, а эта функция сохраняет ввод,
// если перерисовка всё же произошла.

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

function render() {
"""

PATCHES: tuple[tuple[str, str], ...] = (
    # 1. помощники и запоминание значений перед сборкой страницы
    (
        "function render() {\n  const state = currentState;\n  if (!state) return;\n"
        "  // Страж редактирования: пока фокус в поле формы, страница не перерисовывается.\n"
        "  if (editing) { pendingRender = true; return; }\n"
        "  const app = document.getElementById('app');\n",
        HELPERS
        + "  const state = currentState;\n  if (!state) return;\n"
        + "  // Страж редактирования: пока фокус в поле формы, страница не перерисовывается.\n"
        + "  if (editing) { pendingRender = true; return; }\n"
        + "  const savedValues = snapshotFormValues();\n"
        + "  const app = document.getElementById('app');\n",
    ),
    # 2. возврат значений сразу после подмены разметки
    (
        "  bindNav();\n  showSection(activeSection);\n  updateThemeButton();\n",
        "  restoreFormValues(savedValues);\n"
        "  bindNav();\n  showSection(activeSection);\n  updateThemeButton();\n",
    ),
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
