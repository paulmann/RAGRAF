"""Исправление дефекта: команда обнуляла user_input и ломала поток ниже.

НАЙДЕННЫЙ ДЕФЕКТ (мой, внесён предыдущей правкой).

Я добавил перехват команды так:
    if _command is not None:
        ...
        user_input = None
    if user_input and user_input.strip() and not _ss(slug, "thread_id"):

Строка `user_input = None` небезопасна. Ниже в том же блоке (строка 1754)
выполняется:
    _set_ss(slug, "user_message", user_input.strip())

При `user_input = None` это вызовет AttributeError и вся страница упадёт
с ошибкой — вместо показа статистики пользователь увидит красный текст.
Проверка `if user_input and ...` выше спасает только один из нескольких
блоков, а не все.

ПРАВКА: команда обрабатывается и СРАЗУ завершает обработку ввода,
не трогая переменную `user_input`. Используется отдельная ветка с `else`,
поэтому дальнейший код для команды просто не выполняется — без обнуления
чужой переменной.

Это чище и безопаснее: никакой код ниже не зависит от значения `user_input`
при исполнении команды.
"""

from __future__ import annotations

import ast
import pathlib

TARGET = pathlib.Path(
    r"C:\!.Tihon\sagaai_edit_github\ui\pages\orchestrator.py"
)
text = TARGET.read_text(encoding="utf-8")

OLD = '''            # Команды чата: исполняются здесь и НЕ уходят агенту.
            # Проверка стоит до создания треда: команда не должна
            # открывать задачу, которую не решает.
            _command = _parse_slash_command(user_input)
            if _command is not None:
                _name, _args = _command
                if _name == "/ragraf":
                    _render_ragraf_stats(slug)
                elif _name == "/help":
                    _render_orch_help(lang)
                # Текст команды не отправляется в агент и не сохраняется
                # как сообщение пользователя.
                user_input = None
            if user_input and user_input.strip() and not _ss(slug, "thread_id"):'''

NEW = '''            # Команды чата: исполняются здесь и НЕ уходят агенту.
            #
            # Ветка `else` обязательна. Обнулять `user_input` нельзя:
            # ниже в этом же блоке есть `_set_ss(..., user_input.strip())`,
            # и при None страница падала с AttributeError вместо показа
            # статистики. Поэтому весь дальнейший код просто не выполняется
            # для команды — без изменения чужих переменных.
            _command = _parse_slash_command(user_input)
            if _command is not None:
                _name, _args = _command
                if _name == "/ragraf":
                    _render_ragraf_stats(slug)
                elif _name == "/help":
                    _render_orch_help(lang)
                # Текст команды не отправляется в агент и не сохраняется
                # как сообщение пользователя.
            else:
                _handle_user_message(
                    slug=slug, lang=lang, user_input=user_input, orch=orch,
                    att_files=att_files,
                )

    # ─── Отправка сообщения агенту ───────────────────────────────────────────
    # Вынесено в отдельную функцию: команды чата обрабатываются в ветке
    # `else` выше, и при команде этот код не выполняется вообще.


def _handle_user_message(slug: str, lang: str, user_input: str,
                         orch: dict, att_files: list) -> None:
    """Отправить сообщение пользователя агенту.

    Раньше этот код был в `_render_chat_tab` сразу после разбора команды.
    Вынесение понадобилось, чтобы развести два потока: команда чата
    и обычное сообщение. Иначе пришлось бы обнулять `user_input`, а на нём
    ниже построена запись в состояние (и это давало падение).
    """
    if user_input and user_input.strip() and not _ss(slug, "thread_id"):'''

if OLD not in text:
    raise SystemExit("НЕ НАЙДЕН фрагмент перехвата")

text = text.replace(OLD, NEW, 1)
TARGET.write_text(text, encoding="utf-8", newline="\n")

after = TARGET.read_text(encoding="utf-8")
print("  правка внесена: команда вынесена в отдельную ветку")

try:
    ast.parse(after)
    print("  [OK ] синтаксис верен")
except SyntaxError as exc:
    print(f"  [ПРОВАЛ] синтаксис: строка {exc.lineno}: {exc.msg}")
    raise SystemExit(1)

# Проверки.
checks = [
    ("user_input = None" not in after, "обнуление user_input убрано"),
    ("_handle_user_message" in after, "обработчик сообщения вынесен"),
    ("else:" in after, "ветка else добавлена"),
    ("_render_ragraf_stats(slug)" in after, "показ статистики на месте"),
]
print()
for ok, label in checks:
    print(f"  [{'OK ' if ok else '--'}] {label}")

# Проверяется, что отступы вынесенной функции согласованы:
# тело ниже должно быть с отступом 4 пробела (внутри новой функции).
lines = after.splitlines()
start = next(i for i, l in enumerate(lines)
             if l.startswith("def _handle_user_message"))
body = lines[start + 1:start + 30]
indents = {len(l) - len(l.lstrip()) for l in body if l.strip()}
print(f"\n  отступы в новой функции: {sorted(indents)}")
