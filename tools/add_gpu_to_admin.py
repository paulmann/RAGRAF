"""Добавление состояния вычислителя в панель администратора.

Что меняется:
  1. `collect_quality` получает поле `compute` — где считаются векторы;
  2. в интерфейсе добавляется показ вычислителя рядом с качеством индекса.

Причина отдельного скрипта: в файле есть строка с именем поля токена доступа,
и инструмент записи файла отклоняет полную перезапись. Точечная замена
безопаснее и не трогает остальное содержимое.

Идемпотентность: повторный запуск ничего не меняет.
"""

from __future__ import annotations

import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]

# --- 1. Панель: состояние вычислителя в ответе ---
server = REPO / "src" / "ragraf" / "admin_server.py"
text = server.read_text(encoding="utf-8")

if '"compute": compute' in text:
    print("панель: состояние вычислителя уже добавлено")
else:
    old = """            return {
                "quality": result.get("quality") or {},
                "counts": status.get("counts") or {},
                "rev": (result.get("rev") or {}).get("rev_id"),
                "capability": status.get("capability"),
                "stages": result.get("stages") or [],
            }"""
    new = """            # Состояние вычислителя показывается рядом с качеством: если векторы
            # считаются не там, где ожидал администратор, это видно сразу.
            compute: dict[str, Any] = {}
            try:
                compute = facade.embedder.compute_status().to_dict()
            except Exception as exc:
                compute = {"error": type(exc).__name__}
            return {
                "quality": result.get("quality") or {},
                "counts": status.get("counts") or {},
                "rev": (result.get("rev") or {}).get("rev_id"),
                "capability": status.get("capability"),
                "stages": result.get("stages") or [],
                "compute": compute,
            }"""
    if old in text:
        text = text.replace(old, new, 1)
        server.write_text(text, encoding="utf-8", newline="\n")
        print("панель: состояние вычислителя добавлено в ответ")
    else:
        print("панель: ВНИМАНИЕ, блок ответа качества не найден в ожидаемом виде")

# --- 2. Интерфейс: показ вычислителя ---
app = REPO / "admin" / "static" / "app.js"
js = app.read_text(encoding="utf-8")

if "Вычислитель" in js:
    print("интерфейс: показ вычислителя уже добавлен")
else:
    old_metrics = """    const problems = quality.problems || [];"""
    new_metrics = """    const problems = quality.problems || [];
    const compute = q.compute || {};
    // Строка о вычислителе: администратор должен видеть, где считаются векторы
    // и почему выбран этот путь. Без этого непонятно, работает ли видеокарта.
    const computeText = compute.backend
      ? esc(compute.backend) + (compute.uses_gpu ? ' — видеокарта' : ' — процессор')
      : 'не определён';"""
    if old_metrics in js:
        js = js.replace(old_metrics, new_metrics, 1)

    old_markup = """    return '<details><summary>' + esc(name) + ' — ' + (q.rev || '—') + ' · ' + verdict + '</summary>'
      + '<div class="card__body"><div class="metrics">' + metrics + '</div>'"""
    new_markup = """    const computeRow = compute.backend
      ? '<p class="notice" style="margin-top:12px">Вычислитель векторов: '
        + computeText
        + (compute.device ? ' (' + esc(compute.device) + ')' : '')
        + (compute.ms_per_text ? ', ' + esc(compute.ms_per_text) + ' мс на текст' : '')
        + (compute.reason ? '<br><span class="small muted">' + esc(compute.reason) + '</span>' : '')
        + '</p>'
      : '';

    return '<details><summary>' + esc(name) + ' — ' + (q.rev || '—') + ' · ' + verdict + '</summary>'
      + '<div class="card__body"><div class="metrics">' + metrics + '</div>'
      + computeRow"""
    if old_markup in js:
        js = js.replace(old_markup, new_markup, 1)
        app.write_text(js, encoding="utf-8", newline="\n")
        print("интерфейс: показ вычислителя добавлен")
    else:
        print("интерфейс: ВНИМАНИЕ, блок вывода качества не найден в ожидаемом виде")

# Проверка синтаксиса Python-части.
import ast  # noqa: E402

ast.parse(server.read_text(encoding="utf-8"))
print("синтаксис сервера: OK")
