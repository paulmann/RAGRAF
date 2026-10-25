"""Команда `/RAGRAF` в чате SagaAI: статистика использования индекса.

ЧТО ДЕЛАЕТ. Пользователь вводит `/RAGRAF` в поле чата — вместо отправки
текста агенту показывается отчёт о работе RAGRAF по текущей задаче:
сколько было обращений, сколько фактов получено, сколько строк кода
не пришлось читать.

ПОЧЕМУ ЭТО РАЗРАБОТКА С НУЛЯ. Разведка показала: механизма слэш-команд
в проекте НЕТ вообще — ни реестра команд, ни разбора префикса. Ввод идёт
напрямую в агент (`ui/pages/orchestrator.py:1580`). Поэтому перехват
добавляется явно, ДО отправки в агент.

ПОЧЕМУ СТАТИСТИКА ИМЕННО ПО ЗАДАЧЕ. Каждый вызов инструмента запускает
ОТДЕЛЬНЫЙ процесс RAGRAF, поэтому «сессия RAGRAF» (один запуск процесса)
с задачей пользователя не совпадает. Собрать вклад задачи можно только
по общей метке, которая передаётся через `RAGRAF_TASK`.

ЧТО ИМЕННО ДОБАВЛЯЕТСЯ:
  1. словарь команд и функция разбора (`_parse_slash_command`);
  2. функция показа отчёта (`_render_ragraf_stats`);
  3. перехват в месте ввода: если введена команда — она исполняется,
     и текст НЕ уходит агенту.

Изменения минимальны и обратимы: правка в одном файле, перед строкой 1580.
"""

from __future__ import annotations

import pathlib
import shutil

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
TARGET = SAGA / "ui" / "pages" / "orchestrator.py"
text = TARGET.read_text(encoding="utf-8")
print(f"  файл: {TARGET}")
print(f"  строк до правки: {len(text.splitlines())}")

backup = TARGET.with_suffix(".py.bak-slash")
shutil.copy2(TARGET, backup)
print(f"  копия: {backup.name}")

changes: list[str] = []

# --- 1. Функции команды: вставляются перед определением страницы ---
HELPERS = '''
# ─── Команды чата (/RAGRAF и другие) ─────────────────────────────────────────
#
# Механизма слэш-команд в проекте не было: текст из поля чата уходил прямо
# агенту. Здесь добавляется минимальный разбор: если строка начинается
# с `/`, она исполняется как команда и НЕ отправляется агенту.
#
# Список команд намеренно короткий: добавлять в него надо только то, что
# отвечает мгновенно и не требует работы модели.

_ORCH_COMMANDS = {
    "/ragraf": "Показать работу RAGRAF по текущей задаче",
    "/help": "Показать список команд",
}


def _parse_slash_command(text: str) -> tuple[str, list[str]] | None:
    """Разобрать команду из строки ввода.

    Возвращает `(имя, аргументы)` или None, если строка не команда.
    Имя приводится к нижнему регистру: `/RAGRAF` и `/ragraf` равнозначны.
    Строка, начинающаяся с `/` но не совпадающая с известной командой,
    командой не считается — она уходит агенту как обычный текст, чтобы
    пользователь не терял ввод из-за опечатки.
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
    """Показать список доступных команд."""
    st.markdown("#### Команды")
    for name, description in sorted(_ORCH_COMMANDS.items()):
        st.markdown(f"- `{name}` — {description}")


def _render_ragraf_stats(slug: str) -> None:
    """Показать отчёт о работе RAGRAF по текущей задаче.

    Отчёт берётся у самого RAGRAF через инструмент `task_stats`: он читает
    журнал обращений и возвращает готовый markdown. Здесь ничего
    не подсчитывается заново — иначе цифры разошлись бы с журналом.

    Метка задачи = идентификатор треда чата. Так все вызовы инструмента
    внутри одной задачи попадают в одну строку статистики, хотя каждый
    вызов запускает отдельный процесс RAGRAF.
    """
    thread_id = str(_ss(slug, "thread_id") or "").strip()
    if not thread_id:
        st.info("Задача ещё не начата: RAGRAF по ней ничего не делал. "
                "Задайте вопрос по коду проекта.")
        return

    # Метка задачи передаётся через окружение: функция RAGRAF читает её
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
        # Метка снимается: иначе она попала бы и в последующие вызовы
        # других задач в этом же процессе.
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
            "фактов": data.get("facts_total"),
            "со ссылкой файл:строка": data.get("facts_with_span"),
            "объём ответов": data.get("result_chars_total"),
            "оценка токенов": data.get("est_tokens_total"),
            "строк не читалось": data.get("est_lines_not_read"),
            "по операциям": data.get("by_op"),
        })


def _load_orchestrator_function(slug: str, name: str):
    """Получить вызываемую функцию оркестратора по имени.

    Используется штатный загрузчик проекта: он же применяется при исполнении
    инструментов агентом, поэтому поведение команды и агента совпадает.
    """
    try:
        from core.orchestrator_folders import load_orchestrator_function_module

        return load_orchestrator_function_module(slug, name)
    except Exception:
        return None

'''

# Вставляется перед отрисовкой вкладки чата: там же находятся все функции
# интерфейса чата, и новые функции будут им доступны.
anchor = "def _render_chat_tab(slug: str, lang: str) -> None:"
if anchor in text:
    text = text.replace(anchor, HELPERS + "\n" + anchor, 1)
    changes.append(f"добавлены функции команд (перед {anchor.strip()})")
else:
    raise SystemExit("НЕ НАЙДЕНА функция отрисовки чата")

# --- 2. Проверка наличия импорта os ---
if not any(line.strip() == "import os" for line in text.splitlines()):
    # Импорт добавляется после первого import.
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("import ") or line.startswith("from "):
            lines.insert(i, "import os")
            break
    text = "\n".join(lines)
    changes.append("добавлен импорт os")

# --- 3. Перехват команды в месте ввода ---
OLD_INPUT = """        if user_input and user_input.strip():
            if not _ss(slug, "thread_id"):"""
NEW_INPUT = """        if user_input and user_input.strip():
            # Команды чата: исполняются здесь и НЕ уходят агенту.
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
            if user_input and user_input.strip() and not _ss(slug, "thread_id"):"""
if OLD_INPUT in text:
    text = text.replace(OLD_INPUT, NEW_INPUT, 1)
    changes.append("добавлен перехват команды в месте ввода")
else:
    raise SystemExit("НЕ НАЙДЕНО место ввода для перехвата")

TARGET.write_text(text, encoding="utf-8", newline="\n")
after = TARGET.read_text(encoding="utf-8")
print(f"  строк после правки: {len(after.splitlines())}")
print()
print("  изменения:")
for c in changes:
    print(f"    - {c}")

print()
print("  проверки:")
checks = [
    ("_ORCH_COMMANDS" in after, "словарь команд объявлен"),
    ("_parse_slash_command" in after, "разбор команды добавлен"),
    ("_render_ragraf_stats" in after, "показ статистики добавлен"),
    ("_load_orchestrator_function" in after, "загрузка функции добавлена"),
    ('if _name == "/ragraf"' in after, "обработка /ragraf есть"),
    ("RAGRAF_TASK" in after, "метка задачи передаётся"),
    (after.count("def _render_ragraf_stats") == 1, "функция ровно одна"),
]
for ok, label in checks:
    print(f"    [{'OK ' if ok else '--'}] {label}")

import ast  # noqa: E402

try:
    ast.parse(after)
    print("    [OK ] синтаксис верен")
except SyntaxError as exc:
    print(f"    [ПРОВАЛ] синтаксис: строка {exc.lineno}: {exc.msg}")
    raise SystemExit(1)
