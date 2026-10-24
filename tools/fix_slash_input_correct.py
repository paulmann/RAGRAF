"""Корректная правка перехвата команд: переотступление, а не вынесение.

ПЕРВАЯ ПОПЫТКА БЫЛА НЕВЕРНОЙ. Я попытался вынести обработку сообщения
в отдельную функцию, вставив `def` в середину `_render_chat_tab`. Это
разрезало бы функцию: весь код ниже (`_render_token_line`, блок
`agent_is_active`) оказался бы внутри новой функции и перестал выполняться
при обычной отправке сообщения — интерфейс сломался бы молча.

ПРАВИЛЬНЫЙ ПОДХОД: оставить код на месте и переотступнуть исходный блок
на 4 пробела, поставив его в ветку `elif`. Тогда:

    _command = _parse_slash_command(user_input)
    if _command is not None:
        ...исполнить команду...
    elif user_input and user_input.strip():
        ...исходный код без изменений, но с отступом +4...

Никакие переменные не обнуляются, ничего не выносится, порядок исполнения
сохранён. Изменение чисто механическое и обратимое.

Скрипт начинается с восстановления из резервной копии: первая попытка
оставила файл в промежуточном состоянии.
"""

from __future__ import annotations

import ast
import pathlib
import shutil

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
TARGET = SAGA / "ui" / "pages" / "orchestrator.py"
BACKUP = TARGET.with_suffix(".py.bak-slash")

# --- Шаг 1. Восстановление исходного файла ---
if not BACKUP.exists():
    raise SystemExit(f"НЕТ резервной копии: {BACKUP}")
shutil.copy2(BACKUP, TARGET)
text = TARGET.read_text(encoding="utf-8")
print(f"  файл восстановлен из копии: {len(text.splitlines())} строк")
print(f"  моих правок в нём нет: {'_parse_slash_command' not in text}")

# --- Шаг 2. Помощник: переотступление диапазона строк ---
lines = text.splitlines()

# Находится первая строка исходного блока.
start = None
for i, line in enumerate(lines):
    if line.strip() == 'if user_input and user_input.strip():':
        start = i
        break
if start is None:
    raise SystemExit("НЕ НАЙДЕНА строка отправки сообщения")

# Находится конец блока: первая строка с отступом 4 пробела после start,
# которая не пустая и не комментарий на том же уровне.
end = None
for j in range(start + 1, len(lines)):
    line = lines[j]
    if not line.strip():
        continue
    indent = len(line) - len(line.lstrip())
    if indent <= 4:
        end = j
        break
if end is None:
    raise SystemExit("НЕ НАЙДЕН конец блока отправки")

print(f"\n  блок отправки сообщения: строки {start + 1}—{end}")
print(f"    первая: {lines[start].strip()[:70]}")
print(f"    конец:  {lines[end].strip()[:70]}")

# --- Шаг 3. Переотступление блока на 4 пробела ---
block = lines[start:end]
indented = [("    " + ln) if ln.strip() else ln for ln in block]

# --- Шаг 4. Подстановка перехвата команды + ветка elif ---
GUARD = [
    "",
    "            # ─── Команды чата ────────────────────────────────────────",
    "            # Механизма слэш-команд в проекте не было: текст уходил",
    "            # прямо агенту. Здесь добавлен минимальный разбор.",
    "            #",
    "            # Ветка `elif` обязательна: без неё пришлось бы обнулять",
    "            # user_input, а на нём ниже построена запись в состояние",
    "            # (это давало AttributeError вместо показа статистики).",
    "            _command = _parse_slash_command(user_input)",
    "            if _command is not None:",
    "                _name, _args = _command",
    "                if _name == \"/ragraf\":",
    "                    _render_ragraf_stats(slug)",
    "                elif _name == \"/help\":",
    "                    _render_orch_help(lang)",
    "                # Команда не уходит агенту и не сохраняется в истории.",
    "            elif user_input and user_input.strip():",
]

new_lines = lines[:start] + GUARD + indented + lines[end:]
text2 = "\n".join(new_lines) + ("\n" if text.endswith("\n") else "")

# --- Шаг 5. Функции команд: вставляются перед отрисовкой чата ---
HELPERS = '''
# ─── Команды чата ────────────────────────────────────────────────────────────
#
# В проекте не было механизма слэш-команд: текст из поля чата уходил прямо
# агенту. Здесь добавляется минимальный разбор: строка, начинающаяся с `/`
# и совпадающая с известной командой, исполняется и не уходит агенту.
# Неизвестная команда уходит агенту обычным текстом, чтобы опечатка
# не съедала ввод пользователя.

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

    Метка задачи = идентификатор треда чата. Так все вызовы инструмента
    внутри одной задачи собираются вместе, хотя каждый вызов запускает
    отдельный процесс RAGRAF.
    """
    thread_id = str(_ss(slug, "thread_id") or "").strip()
    if not thread_id:
        st.info("Задача ещё не начата: RAGRAF по ней ничего не делал. "
                "Задайте вопрос по коду проекта.")
        return

    # Метка передаётся через окружение: функция RAGRAF читает её и подставляет
    # в каждый вызов RAGRAF.
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

# --- Шаг 6. Проверки ---
print()
print("  проверки:")
checks = [
    ("user_input = None" not in after, "обнуления user_input нет"),
    ("_handle_user_message" not in after, "разреза функции нет"),
    ("elif user_input and user_input.strip():" in after,
     "ветка elif добавлена"),
    ("_parse_slash_command" in after, "разбор команды добавлен"),
    ("_render_ragraf_stats" in after, "показ статистики добавлен"),
    ('if _name == "/ragraf"' in after, "обработка /ragraf есть"),
    ("RAGRAF_TASK" in after, "метка задачи передаётся"),
    (after.count("def _render_ragraf_stats") == 1, "функция ровно одна"),
    (after.count("def _render_chat_tab") == 1,
     "_render_chat_tab определена один раз"),
    (after.count("def _parse_slash_command") == 1, "разбор определён один раз"),
]
for ok, label in checks:
    print(f"    [{'OK ' if ok else '--'}] {label}")

# Синтаксис.
try:
    tree = ast.parse(after)
    print("    [OK ] синтаксис верен")
except SyntaxError as exc:
    print(f"    [ПРОВАЛ] синтаксис: строка {exc.lineno}: {exc.msg}")
    raise SystemExit(1)

# Проверка структуры: `_render_chat_tab` должна остаться целой,
# а код ниже (token line, agent_is_active) — внутри неё.
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
