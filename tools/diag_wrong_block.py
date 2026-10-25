"""Точная причина моего дефекта: код вставлен не в тот блок.

УСТАНОВЛЕНО. Мой перехват команды стоит на отступе 12 внутри блока
`if uploaded_files:` — он начинается на отступе 8. А исходный код отправки
сообщения (который я превратил в `elif`) лежал на отступе 8, на уровне
`with st.bottom:`.

ПОСЛЕДСТВИЕ. Разбор команды исполняется ТОЛЬКО когда есть загруженные
файлы. При обычной отправке сообщения (файлов нет) блок не исполняется
вовсе, поэтому:
  * команда `/RAGRAF` без вложений НЕ РАБОТАЕТ;
  * `elif` с отправкой сообщения стал частью другого условия;
  * тест usability_fixes падает, потому что отправка не происходит.

Это подтверждает и сам тест: он передаёт вложения (`orch_o1_attached`),
но поток всё равно расходится.

Проверяется гипотеза фактически: смотрится, где начинается `if uploaded_files`
и `with st.bottom`, и определяется правильный уровень для вставки.
"""

from __future__ import annotations

import pathlib
import re

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
TARGET = SAGA / "ui" / "pages" / "orchestrator.py"
lines = TARGET.read_text(encoding="utf-8", errors="replace").splitlines()

print("=" * 78)
print("СТРУКТУРА БЛОКОВ ВОКРУГ МОЕГО КОДА")
print("=" * 78)
# Ищутся границы: with st.bottom, if uploaded_files, if user_input.
MARKERS = [
    (r"^\s*with st\.bottom:", "НАЧАЛО: with st.bottom"),
    (r"^\s*if uploaded_files:", "if uploaded_files"),
    (r"^\s*if added:", "if added"),
    (r"^\s*_command = _parse_slash_command", "МОЙ КОД"),
    (r"^\s*elif user_input and user_input\.strip\(\):",
     "МОЙ elif отправки"),
    (r"^\s*_set_ss\(slug, \"user_message\"", "_set_ss user_message"),
    (r"^\s*_render_token_line", "конец: _render_token_line"),
]
positions = []
for pattern, label in MARKERS:
    for i, ln in enumerate(lines, 1):
        if re.search(pattern, ln):
            indent = len(ln) - len(ln.lstrip())
            positions.append((i, indent, label))
positions.sort()
print(f"\n  {'строка':>7} {'отступ':>7}  что")
for line_no, indent, label in positions:
    print(f"  {line_no:>7} {indent:>7}  {label}")

print()
print("=" * 78)
print("ГДЕ НАХОДИТСЯ КОД ОТПРАВКИ ОТНОСИТЕЛЬНО ВЛОЖЕНИЙ")
print("=" * 78)
upload_if = next((i for i, ln in enumerate(lines, 1)
                  if re.match(r"^\s*if uploaded_files:", ln)), None)
my_code = next((i for i, ln in enumerate(lines, 1)
                if "_command = _parse_slash_command" in ln), None)
target = next((i for i, ln in enumerate(lines, 1)
               if re.match(r"^\s*elif user_input and user_input\.strip\(\):",
                           ln)), None)
bottom = next((i for i, ln in enumerate(lines, 1)
               if re.match(r"^\s*with st\.bottom:", ln)), None)

print(f"  with st.bottom:        строка {bottom}, "
      f"отступ {len(lines[bottom - 1]) - len(lines[bottom - 1].lstrip())}")
print(f"  if uploaded_files:     строка {upload_if}, "
      f"отступ {len(lines[upload_if - 1]) - len(lines[upload_if - 1].lstrip())}")
print(f"  мой код разбора:       строка {my_code}")
print(f"  мой elif отправки:     строка {target}")

# Проверяется, внутри ли блок вложений: отступ больше и он между
# началом и концом этого блока.
if upload_if and my_code:
    upload_end = next((i for i in range(upload_if + 1, len(lines) + 1)
                       if i <= len(lines)
                       and lines[i - 1].strip()
                       and (len(lines[i - 1]) - len(lines[i - 1].lstrip()))
                       <= (len(lines[upload_if - 1])
                           - len(lines[upload_if - 1].lstrip()))),
                      len(lines) + 1)
    inside = upload_if < my_code < upload_end
    print(f"\n  блок uploaded_files: строки {upload_if}—{upload_end - 1}")
    print(f"  мой код ВНУТРИ этого блока: {'ДА — ЭТО ДЕФЕКТ' if inside else 'нет'}")

print()
print("=" * 78)
print("ПОСЛЕДСТВИЯ ДЕФЕКТА")
print("=" * 78)
print("""
  1. Команда /RAGRAF работает ТОЛЬКО когда приложен хотя бы один файл.
     Без вложений до разбора команды дело не доходит — блок не исполняется.

  2. Отправка обычного сообщения: тест падает, потому что ветка отправки
     оказалась частью блока вложений.

  3. В интерфейсе это выглядело бы как «иногда команда не срабатывает» —
     состояние, которое трудно воспроизвести и легко принять за случайность.

ПРАВКА: перехват надо перенести на уровень блока `with st.bottom:`
(отступ 8), туда, где изначально стоял `if user_input and user_input.strip():`.
""")
