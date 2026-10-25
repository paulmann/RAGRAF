"""Исправление моего дефекта: перехват команды попал внутрь чужого блока.

ЧТО БЫЛО СДЕЛАНО НЕВЕРНО. Скрипт вставки определил границу блока отправки
по отступу и переотступнул не тот диапазон. В результате:

    исходник:                     стало:
    [8] if uploaded_files:        [8]  if uploaded_files:
    [12]   ...вложения...         [12]   ...вложения...
    [8] if user_input:            [12]   _command = _parse_slash_command(...)
    [12]   ...отправка...         [12]   if _command is not None:
                                  [12]   elif user_input and user_input...
                                  [16]     ...отправка...

То есть разбор команды и отправка сообщения оказались ВНУТРИ ветки
`if uploaded_files:`. Последствия:
  * `/RAGRAF` работает только при приложенном файле;
  * обычная отправка сообщения ломается (тест usability_fixes это поймал);
  * воспроизводится трудно: «иногда команда не срабатывает».

КАК ИСПРАВЛЯЕТСЯ. Файл возвращается из резервной копии (состояние до
неудачной правки), затем правка делается заново с ЯВНО заданными границами:

    на строке 1549 начинается `if uploaded_files:` (отступ 8),
    на строке 1581 — `if user_input and user_input.strip():` (отступ 8).

Значит блок отправки — строки 1581..(перед строкой отступа 4). Границы
берутся по номеру строки, а не вычисляются по отступам: вычисление уже
один раз ошиблось.
"""

from __future__ import annotations

import ast
import pathlib
import shutil

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
TARGET = SAGA / "ui" / "pages" / "orchestrator.py"
BACKUP = TARGET.with_suffix(".py.bak-slash")

# --- 1. Восстановление исходного файла ---
if not BACKUP.exists():
    raise SystemExit(f"НЕТ резервной копии: {BACKUP}")
shutil.copy2(BACKUP, TARGET)
text = TARGET.read_text(encoding="utf-8")
lines = text.splitlines()
print(f"  файл восстановлен: {len(lines)} строк")
print(f"  моих функций нет: {'_parse_slash_command' not in text}")

# --- 2. Границы блока отправки определяются по НОМЕРАМ строк ---
# Ищется строка `if user_input and user_input.strip():` на отступе 8.
start = None
for i, ln in enumerate(lines):
    if ln.strip() == "if user_input and user_input.strip():":
        indent = len(ln) - len(ln.lstrip())
        if indent == 8:
            start = i
            break
if start is None:
    raise SystemExit("НЕ НАЙДЕНА строка отправки на отступе 8")

# Конец блока: первая непустая строка с отступом <= 4 (уровень функции).
end = None
for j in range(start + 1, len(lines)):
    ln = lines[j]
    if not ln.strip():
        continue
    indent = len(ln) - len(ln.lstrip())
    if indent <= 4:
        end = j
        break
if end is None:
    raise SystemExit("НЕ НАЙДЕН конец блока отправки")

# Проверка: на строке перед началом должен быть закрыт блок вложений.
prev = lines[start - 1].strip() if start > 0 else ""
print(f"\n  блок отправки: строки {start + 1}—{end}")
print(f"    строка перед началом: {prev[:70]!r}")
print(f"    первая строка после:  {lines[end].strip()[:70]!r}")

# Ключевая проверка: строка отправки НЕ должна быть внутри if uploaded_files.
upload_if = next((i for i, ln in enumerate(lines)
                  if ln.strip().startswith("if uploaded_files:")), None)
if upload_if is not None:
    upload_end = next((j for j in range(upload_if + 1, len(lines))
                       if lines[j].strip()
                       and (len(lines[j]) - len(lines[j].lstrip()))
                       <= (len(lines[upload_if]) - len(lines[upload_if].lstrip()))),
                      len(lines))
    inside = upload_if < start < upload_end
    print(f"\n  блок uploaded_files: строки {upload_if + 1}—{upload_end}")
    print(f"  отправка внутри него: {'ДА — ОШИБКА' if inside else 'НЕТ (верно)'}")
    if inside:
        raise SystemExit("ГРАНИЦЫ НЕВЕРНЫ: блок отправки внутри вложений")

# --- 3. Переотступление тела блока (сама строка if заменяется на elif) ---
body = lines[start + 1:end]
indented = [("    " + ln) if ln.strip() else ln for ln in body]

GUARD = [
    "        # ─── Команды чата ──────────────────────────────────────────",
    "        # Механизма слэш-команд в проекте не было: текст уходил прямо",
    "        # агенту. Здесь добавлен минимальный разбор.",
    "        #",
    "        # ВАЖНО: этот код стоит на уровне `with st.bottom:`",
    "        # (отступ 8), то есть ВНЕ ветки загруженных файлов. Иначе",
    "        # команда работала бы только при приложенном файле.",
    "        _command = _parse_slash_command(user_input)",
    "        if _command is not None:",
    "            _name, _args = _command",
    "            if _name == \"/ragraf\":",
    "                _render_ragraf_stats(slug)",
    "            elif _name == \"/help\":",
    "                _render_orch_help(lang)",
    "            # Команда не уходит агенту и не сохраняется в истории.",
    "        elif user_input and user_input.strip():",
]

new_lines = lines[:start] + GUARD + indented + lines[end:]
text2 = "\n".join(new_lines) + "\n"

# --- 4. Функции команд: вставляются перед отрисовкой чата ---
HELPERS = '''
# ─── Команды чата ────────────────────────────────────────────────────────────
#
# В проекте не было механизма слэш-команд: текст из поля чата уходил прямо
# агенту. Здесь добавляется минимальный разбор: строка, начинающаяся с `/`
# и совпадающая с известной командой, исполняется и не уходит агенту.
# Неизвестная команда уходит агенту обычным текстом, чтобы опечатка
# не съедала ввод пользователя.
#
# ВАЖНО про место вызова: перехват стоит в `_render_chat_tab` на уровне
# блока `with st.bottom:` — НЕ внутри ветки загруженных файлов. Иначе
# команда срабатывала бы только при приложенном файле.

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

# --- 5. Проверки ---
print()
print("  проверки:")
try:
    ast.parse(after)
    print("    [OK ] синтаксис верен")
except SyntaxError as exc:
    print(f"    [ПРОВАЛ] синтаксис: строка {exc.lineno}: {exc.msg}")
    raise SystemExit(1)

after_lines = after.splitlines()
my_line = next((i for i, ln in enumerate(after_lines, 1)
                if "_command = _parse_slash_command" in ln), None)
my_indent = len(after_lines[my_line - 1]) - len(
    after_lines[my_line - 1].lstrip())
print(f"    [{'OK ' if my_indent == 8 else '--'}] "
      f"перехват на отступе 8 (найден {my_indent})")

upload_if = next((i for i, ln in enumerate(after_lines, 1)
                  if ln.strip().startswith("if uploaded_files:")), None)
print(f"    [OK ] if uploaded_files на строке {upload_if}")
print(f"    [OK ] перехват на строке {my_line} — ПОСЛЕ блока вложений")
