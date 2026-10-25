"""Правка перехвата команд: точное определение границ блока.

ЧТО ПОШЛО НЕ ТАК в предыдущей попытке. Скрипт искал границу блока по
первой строке с отступом `<= 4`, но внутри функции отступы вложенные:
строка `if user_input and user_input.strip():` имеет отступ 8, а строки
уровня `if att_files:` — тоже 8. Поиск остановился на комментарии
и переотступил не тот диапазон, из-за чего `elif` остался без тела.

ПРАВИЛЬНЫЙ ПОДХОД. Исходный блок начинается на строке `if user_input...`
(отступ 8) и заканчивается ПЕРЕД строкой, где отступ снова равен 8
и которая не является продолжением блока. Границы проверяются печатью
до правки, а не вслепую.

Порядок:
  1. восстановление файла из копии (правки предыдущей попытки убираются);
  2. печать候选 границ блока для проверки;
  3. переотступление ровно этого блока;
  4. вставка перехвата с `elif`.
"""

from __future__ import annotations

import ast
import pathlib
import shutil

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
TARGET = SAGA / "ui" / "pages" / "orchestrator.py"
BACKUP = TARGET.with_suffix(".py.bak-slash")

# --- 1. Восстановление ---
shutil.copy2(BACKUP, TARGET)
text = TARGET.read_text(encoding="utf-8")
lines = text.splitlines()
print(f"  файл восстановлен: {len(lines)} строк")
print(f"  правок нет: {'_parse_slash_command' not in text}")

# --- 2. Точные границы блока ---
# Ищется строка `if user_input and user_input.strip():` внутри _render_chat_tab.
start = None
for i, ln in enumerate(lines):
    if ln.strip() == "if user_input and user_input.strip():" and i > 1400:
        start = i
        break
if start is None:
    raise SystemExit("НЕ НАЙДЕНА строка отправки сообщения")

base_indent = len(lines[start]) - len(lines[start].lstrip())
print(f"\n  строка отправки: {start + 1}, отступ {base_indent}")

# Конец блока: первая непустая строка ПОСЛЕ начала, у которой отступ
# МЕНЬШЕ ИЛИ РАВЕН base_indent. Именно так заканчивается блок
# в Python: следующий оператор того же уровня.
end = None
for j in range(start + 1, len(lines)):
    ln = lines[j]
    if not ln.strip():
        continue
    indent = len(ln) - len(ln.lstrip())
    if indent <= base_indent:
        end = j
        break
if end is None:
    raise SystemExit("НЕ НАЙДЕН конец блока")

print(f"  конец блока: строка {end} (перед ней — последняя строка блока)")
print(f"    последняя строка блока {end}:    {lines[end - 1].strip()[:72]}")
print(f"    следующая строка {end + 1}:      {lines[end].strip()[:72]}")

# Проверка границ: блок должен содержать ключевые строки отправки.
block = lines[start:end]
block_text = "\n".join(block)
REQUIRED = [
    "_do_step(slug=slug, lang=lang)",
    "_set_ss(slug, \"user_message\"",
    "create_devagent_thread(",
]
print(f"\n  проверка границ (блок {len(block)} строк):")
all_ok = True
for marker in REQUIRED:
    ok = marker in block_text
    if not ok:
        all_ok = False
    print(f"    [{'OK ' if ok else '--'}] содержит: {marker}")
if not all_ok:
    raise SystemExit("ГРАНИЦЫ НЕВЕРНЫ: блок не содержит нужных строк")

# Проверка: блок НЕ должен захватить код индикатора токенов.
FORBIDDEN = ["_render_token_line(slug, history", "if agent_is_active:"]
for marker in FORBIDDEN:
    if marker in block_text:
        raise SystemExit(f"ГРАНИЦЫ НЕВЕРНЫ: блок захватил {marker}")
print("    [OK ] блок не захватил код индикатора токенов")

# --- 3. Переотступление ---
#
# ВАЖНО: сама строка `if user_input and user_input.strip():` в блок НЕ входит.
# Она заменяется на `elif ...` в перехвате, а её ТЕЛО (строки после неё)
# становится телом этой ветки. Если оставить её внутри, она встанет
# на один уровень с `elif` и тот останется без тела — синтаксическая
# ошибка (именно так и случилось в прошлой попытке).
body_lines = lines[start + 1:end]
indented = [("    " + ln) if ln.strip() else ln for ln in body_lines]
print(f"\n  тело ветки: {len(indented)} строк")

# --- 4. Подстановка перехвата ---
GUARD = [
    "            # ─── Команды чата ──────────────────────────────────────",
    "            # Механизма слэш-команд в проекте не было: текст уходил",
    "            # прямо агенту. Здесь добавлен минимальный разбор.",
    "            #",
    "            # Ветка `elif` обязательна: без неё пришлось бы обнулять",
    "            # user_input, а на нём ниже построена запись в состояние",
    "            # (обнуление давало AttributeError вместо статистики).",
    "            _command = _parse_slash_command(user_input)",
    "            if _command is not None:",
    "                _name, _args = _command",
    "                if _name == \"/ragraf\":",
    "                    _render_ragraf_stats(slug)",
    "                elif _name == \"/help\":",
    "                    _render_orch_help(lang)",
    "            elif user_input and user_input.strip():",
]

new_lines = lines[:start] + GUARD + indented + lines[end:]
text2 = "\n".join(new_lines) + "\n"

# --- 5. Функции команд ---
HELPERS = '''
# ─── Команды чата ────────────────────────────────────────────────────────────
#
# В проекте не было механизма слэш-команд: текст из поля чата уходил прямо
# агенту. Здесь добавляется минимальный разбор: строка, начинающаяся с `/`
# и совпадающая с известной командой, исполняется и не уходит агенту.
# Неизвестная команда уходит агенту обычным текстом: опечатка не должна
# съедать ввод пользователя.

_ORCH_COMMANDS = {
    "/ragraf": "Показать работу RAGRAF по текущей задаче",
    "/help": "Показать список команд",
}


def _parse_slash_command(text: str) -> tuple[str, list[str]] | None:
    """Разобрать команду из строки ввода.

    Возвращает `(имя, аргументы)` либо None, если это не команда.
    Имя приводится к нижнему регистру: `/RAGRAF` и `/ragraf` равнозначны.
    """
    if not text:
        return None
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    parts = stripped.split()
    name = parts[0].lower()
    if name not in _ORCH_COMMANDS:
        return None
    return name, parts[1:]


def _render_orch_help(lang: str = "ru") -> None:
    """Показать список доступных команд чата."""
    st.markdown("#### Команды")
    for name, description in sorted(_ORCH_COMMANDS.items()):
        st.markdown(f"- `{name}` — {description}")


def _load_orchestrator_function(slug: str, name: str):
    """Получить вызываемую функцию оркестратора по имени.

    Используется штатный загрузчик проекта — тот же, которым пользуется
    агент при исполнении инструментов. Поэтому поведение команды
    и агента совпадает.
    """
    try:
        from core.orchestrator_folders import load_orchestrator_function_module

        return load_orchestrator_function_module(slug, name)
    except Exception:
        return None


def _render_ragraf_stats(slug: str) -> None:
    """Показать отчёт о работе RAGRAF по текущей задаче.

    Отчёт берётся у самого RAGRAF через операцию `task_stats`: она читает
    журнал обращений и возвращает готовый markdown. Здесь ничего
    не подсчитывается заново — иначе цифры разошлись бы с журналом.

    Метка задачи = идентификатор треда чата. Так вызовы инструмента
    внутри одной задачи собираются вместе, хотя каждый вызов запускает
    отдельный процесс RAGRAF.
    """
    thread_id = str(_ss(slug, "thread_id") or "").strip()
    if not thread_id:
        st.info("Задача ещё не начата: RAGRAF по ней ничего не делал. "
                "Задайте вопрос по коду проекта.")
        return

    # Метка передаётся через окружение: функция RAGRAF читает её
    # и подставляет в каждый вызов RAGRAF.
    os.environ["RAGRAF_TASK"] = thread_id
    try:
        loader = _load_orchestrator_function(slug, "ragraf")
        if loader is None:
            st.warning("Инструмент RAGRAF не подключён к этому оркестратору.")
            return
        result = loader(operation="task_stats")
    except Exception as exc:
        st.error(f"Не удалось получить статистику RAGRAF: "
                 f"{type(exc).__name__}: {exc}")
        return
    finally:
        # Метка снимается: иначе она попала бы в вызовы других задач
        # в этом же процессе.
        os.environ.pop("RAGRAF_TASK", None)

    if not isinstance(result, dict) or not result.get("ok"):
        error = (result or {}).get("error") if isinstance(result, dict) else None
        st.warning(f"RAGRAF не ответил: {error or 'неизвестная ошибка'}")
        return

    st.markdown(str(result.get("summary") or "Нет данных."))
    data = result.get("data") or {}
    with st.expander("Подробно (данные журнала)"):
        st.json({
            "задача": thread_id,
            "обращений": data.get("calls_total"),
            "ошибок": data.get("errors"),
            "фактов": data.get("fact_count"),
            "объём ответов": data.get("result_chars_total"),
            "оценка токенов": data.get("est_tokens_total"),
            "строк не читалось": data.get("est_lines_not_read"),
        })


'''

anchor = "def _render_chat_tab(slug: str, lang: str) -> None:"
if anchor not in text2:
    raise SystemExit("НЕ НАЙДЕНА функция отрисовки чата")
text2 = text2.replace(anchor, HELPERS + "\n" + anchor, 1)

TARGET.write_text(text2, encoding="utf-8", newline="\n")
after = TARGET.read_text(encoding="utf-8")
print(f"\n  файл записан: {len(after.splitlines())} строк")

# --- 6. Проверки ---
print()
print("  проверки:")
try:
    ast.parse(after)
    print("    [OK ] синтаксис верен")
    syntax_ok = True
except SyntaxError as exc:
    print(f"    [ПРОВАЛ] синтаксис: строка {exc.lineno}: {exc.msg}")
    syntax_ok = False

checks = [
    ("user_input = None" not in after, "обнуления user_input нет"),
    ("_handle_user_message" not in after, "разреза функции нет"),
    ("elif user_input and user_input.strip():" in after, "ветка elif есть"),
    ("_parse_slash_command" in after, "разбор команды добавлен"),
    ("_render_ragraf_stats" in after, "показ статистики добавлен"),
    (after.count("def _render_chat_tab") == 1, "чат определён один раз"),
    (after.count("def _parse_slash_command") == 1, "разбор определён один раз"),
    (after.count("elif user_input and user_input.strip():") == 1,
     "ветка elif ровно одна"),
]
for ok, label in checks:
    print(f"    [{'OK ' if ok else '--'}] {label}")

# Структура: код индикатора токенов должен остаться снаружи блока.
if syntax_ok:
    func_start = after.find("def _render_chat_tab(")
    func_block = after[func_start:]
    next_def = func_block.find("\ndef ", 10)
    func_body = func_block[:next_def if next_def > 0 else len(func_block)]
    print(f"\n  тело _render_chat_tab: {len(func_body.splitlines())} строк")
    for marker, label in (
        ("_render_token_line(slug, history", "индикатор токенов внутри функции"),
        ("if agent_is_active:", "блок активности агента внутри функции"),
        ("_do_step(slug=slug, lang=lang)", "шаг агента внутри функции"),
    ):
        present = marker in func_body
        print(f"    [{'OK ' if present else '--'}] {label}")

    # Проверка отступов в новой ветке.
    lines_after = after.splitlines()
    for i, ln in enumerate(lines_after):
        if ln.strip() == "elif user_input and user_input.strip():":
            print(f"\n  ветка elif на строке {i + 1}:")
            for j in range(i, min(i + 14, len(lines_after))):
                print(f"    {j + 1:>5}: {lines_after[j][:88]}")
            break
