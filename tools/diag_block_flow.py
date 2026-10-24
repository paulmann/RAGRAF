"""Что находится между входом в `elif` и записью user_message.

Поток должен идти так: `elif user_input...` → запись thread_id → запись
user_message. Но тест не видит записи.

Проверяется, нет ли между ними раннего выхода (`return`, `continue`,
исключения) — то есть не прерывается ли поток до записи.

Печатается весь блок с указанием уровня отступа: по нему видно структуру.
"""

from __future__ import annotations

import pathlib

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
TARGET = SAGA / "ui" / "pages" / "orchestrator.py"
lines = TARGET.read_text(encoding="utf-8", errors="replace").splitlines()

# Границы: от перехвата до конца функции чата.
cmd = next(i for i, ln in enumerate(lines, 1)
           if "_command = _parse_slash_command" in ln)
end = next((i for i in range(cmd, len(lines) + 1)
            if i <= len(lines) and lines[i - 1].startswith("def ")),
           len(lines) + 1)

print("=" * 78)
print(f"БЛОК ОТ ПЕРЕХВАТА ДО КОНЦА ФУНКЦИИ (строки {cmd}—{end - 1})")
print("=" * 78)
print("  формат: [отступ] строка\n")
for i in range(cmd, end):
    ln = lines[i - 1]
    if not ln.strip():
        continue
    indent = len(ln) - len(ln.lstrip())
    text = ln.strip()
    # Помечаются значимые точки потока.
    marks = []
    if text.startswith(("return", "continue", "break")):
        marks.append("ВЫХОД")
    if "user_message" in text:
        marks.append("ЗАПИСЬ В СОСТОЯНИЕ")
    if "_do_step" in text:
        marks.append("ШАГ АГЕНТА")
    if "set_chat_css" in text or "st.chat_input" in text:
        marks.append("ВВОД")
    mark = ("  <-- " + ", ".join(marks)) if marks else ""
    print(f"  [{indent:>2}] {text[:84]}{mark}")

print()
print("=" * 78)
print("ПОИСК РАННИХ ВЫХОДОВ ВНУТРИ БЛОКА")
print("=" * 78)
for i in range(cmd, end):
    ln = lines[i - 1]
    stripped = ln.strip()
    if stripped.startswith(("return", "continue", "break")):
        indent = len(ln) - len(ln.lstrip())
        print(f"  строка {i}: [{indent}] {stripped[:80]}")

print()
print("=" * 78)
print("ГДЕ ЗАПИСЬ user_message ОТНОСИТЕЛЬНО ВЫХОДОВ")
print("=" * 78)
um = next(i for i in range(cmd, end)
          if '_set_ss(slug, "user_message"' in lines[i - 1])
print(f"  запись в состоянии: строка {um}")
for i in range(cmd, um):
    ln = lines[i - 1]
    stripped = ln.strip()
    if stripped.startswith(("return", "continue", "break")):
        indent = len(ln) - len(ln.lstrip())
        um_indent = len(lines[um - 1]) - len(lines[um - 1].lstrip())
        inside = indent > um_indent
        print(f"  выход на строке {i}: [{indent}] {stripped[:60]}")
        print(f"    {'ВНУТРИ блока (прервёт)' if inside else 'снаружи'}"
              f" — запись на отступе {um_indent}")
