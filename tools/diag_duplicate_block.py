"""Разбор моего дефекта: в файле появились ДВЕ копии блока отправки.

ПРИЗНАК: поиск `_set_ss(slug, "user_message")` находит ДВЕ строки — 1379
и 1751. В исходном файле она была одна.

ПРИЧИНА (предположение, которое надо проверить). При правке скрипт
вставлял перехват команды, и блок отправки сообщения попал в файл дважды:
один раз — переотступленный внутри `elif`, второй — исходный на прежнем
месте. Из-за этого код отправки исполняется не там, где ожидает тест,
либо исполняется дважды.

Проверяется:
  1. сколько раз встречается каждый ключевой фрагмент блока;
  2. какие это строки и в каком они окружении (внутри elif или снаружи);
  3. почему тест не видит запись в состояние.
"""

from __future__ import annotations

import pathlib
import re

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
TARGET = SAGA / "ui" / "pages" / "orchestrator.py"
lines = TARGET.read_text(encoding="utf-8", errors="replace").splitlines()

print("=" * 78)
print("1. СКОЛЬКО РАЗ ВСТРЕЧАЮТСЯ КЛЮЧЕВЫЕ ФРАГМЕНТЫ")
print("=" * 78)
MARKERS = [
    'if user_input and user_input.strip():',
    'elif user_input and user_input.strip():',
    '_command = _parse_slash_command(user_input)',
    '_set_ss(slug, "user_message"',
    '_set_ss(slug, "file_ctx"',
    'create_devagent_thread(',
    '_do_step(slug=slug, lang=lang)',
    'ctx_text, name = "", ""',
    'tid = _ss(slug, "thread_id")',
]
for marker in MARKERS:
    hits = [i for i, ln in enumerate(lines, 1) if marker in ln]
    flag = "  <-- ДУБЛЬ" if len(hits) > 1 and marker not in (
        '_do_step(slug=slug, lang=lang)',) else ""
    print(f"  {marker[:52]:<54} строки {hits}{flag}")

print()
print("=" * 78)
print("2. ОКРУЖЕНИЕ КАЖДОЙ КОПИИ БЛОКА")
print("=" * 78)
# Для каждой строки с user_message определяется, внутри ли она elif
# или в отдельном блоке `if`.
for line_no in [i for i, ln in enumerate(lines, 1)
                if '_set_ss(slug, "user_message"' in ln]:
    print(f"\n  строка {line_no}:")
    # Ищется ближайшая выше строка с `if ... user_input` или `elif`.
    owner = None
    owner_line = None
    for j in range(line_no - 1, max(0, line_no - 120), -1):
        s = lines[j - 1]
        if re.match(r"\s*(if|elif) user_input and user_input\.strip\(\):",
                    s):
            owner = s.strip()
            owner_line = j
            break
        if re.match(r"\s*if user_input and user_input\.strip\(\):", s):
            owner = s.strip()
            owner_line = j
            break
    indent = len(lines[line_no - 1]) - len(lines[line_no - 1].lstrip())
    print(f"    отступ: {indent}")
    if owner_line:
        print(f"    владелец: строка {owner_line}: {owner}")
    else:
        print(f"    владелец не найден в 120 строках выше")

print()
print("=" * 78)
print("3. ГРАНИЦЫ ФУНКЦИИ ЧАТА")
print("=" * 78)
fn_start = next(i for i, ln in enumerate(lines, 1)
                if ln.startswith("def _render_chat_tab("))
fn_end = next((i for i in range(fn_start + 1, len(lines) + 1)
               if i <= len(lines) and lines[i - 1].startswith("def ")),
              len(lines) + 1)
print(f"  _render_chat_tab: строки {fn_start}—{fn_end - 1}")
for line_no in [i for i, ln in enumerate(lines, 1)
                if '_set_ss(slug, "user_message"' in ln]:
    inside = fn_start <= line_no < fn_end
    print(f"    user_message на строке {line_no}: "
          f"{'ВНУТРИ' if inside else 'СНАРУЖИ'} функции чата")

print()
print("=" * 78)
print("4. ГДЕ НАЧАЛСЯ МОЙ ПЕРЕХВАТ И ЧТО ИДЁТ ПОСЛЕ НЕГО")
print("=" * 78)
cmd_line = next((i for i, ln in enumerate(lines, 1)
                 if "_command = _parse_slash_command" in ln), None)
if cmd_line:
    print(f"  перехват на строке {cmd_line}")
    for j in range(cmd_line - 2, min(cmd_line + 4, len(lines) + 1)):
        print(f"    {j:>5}: {lines[j - 1][:92]}")
    # Ищется, где заканчивается блок elif: первый `if` того же уровня.
    print(f"\n  далее (первые 40 строк после перехвата, только отступы "
          f"<= 16):")
    base_indent = len(lines[cmd_line - 1]) - len(
        lines[cmd_line - 1].lstrip())
    for j in range(cmd_line, min(cmd_line + 80, len(lines) + 1)):
        ln = lines[j - 1]
        if not ln.strip():
            continue
        indent = len(ln) - len(ln.lstrip())
        if indent <= base_indent:
            print(f"    {j:>5}: [{indent}] {ln[:88]}")
