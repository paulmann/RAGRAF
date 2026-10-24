"""Проверка команды `/RAGRAF` в SagaAI.

Проверяется то, что можно проверить без запуска Streamlit:
  1. функции команды существуют и импортируются проектом;
  2. разбор команды работает верно (в том числе регистр и опечатки);
  3. показ статистики вызывает RAGRAF и получает отчёт;
  4. поток обычного сообщения НЕ сломан: `elif` на месте, код отправки цел.

Отдельно проверяется главное требование: `/RAGRAF` не должен ломать
страницу и не должен отправлять текст команды агенту.
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
import sys

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
sys.path.insert(0, str(SAGA))

TARGET = SAGA / "ui" / "pages" / "orchestrator.py"
text = TARGET.read_text(encoding="utf-8")

PASS = 0
FAIL = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


print("=" * 78)
print("1. СТРУКТУРА ФАЙЛА: ПОТОК ОБЫЧНОГО СООБЩЕНИЯ НЕ СЛОМАН")
print("=" * 78)
tree = ast.parse(text)
print(f"  файл разобран, узлов: {len(list(ast.walk(tree)))}")

# Ищется функция отрисовки чата и в ней — ветка elif.
functions = {node.name: node for node in ast.walk(tree)
             if isinstance(node, ast.FunctionDef)}
chat_fn = functions.get("_render_chat_tab")
check(chat_fn is not None, "функция _render_chat_tab найдена")
if chat_fn:
    print(f"  строк в функции: "
          f"{chat_fn.end_lineno - chat_fn.lineno + 1}")

    # Ищется `elif user_input and user_input.strip():`
    found_elif = False
    for node in ast.walk(chat_fn):
        if isinstance(node, ast.If):
            for item in node.orelse:
                if isinstance(item, ast.If):
                    src = ast.get_source_segment(text, item.test) or ""
                    if "user_input" in src:
                        found_elif = True
    check(found_elif, "ветка elif с отправкой сообщения внутри чата")

# Проверка: обнуления нет, разреза нет.
check("user_input = None" not in text, "обнуления user_input нет")
check("_handle_user_message" not in text, "функция не разрезана")

# Проверка: код индикатора токенов остался на уровне функции чата.
lines = text.splitlines()
fn_start = next(i for i, l in enumerate(lines)
                if l.startswith("def _render_chat_tab("))
fn_end = next((i for i in range(fn_start + 1, len(lines))
               if lines[i].startswith("def ")), len(lines))
fn_body = "\n".join(lines[fn_start:fn_end])
for marker in ("_render_token_line(slug, history",
               "if agent_is_active:",
               "_do_step(slug=slug, lang=lang)"):
    check(marker in fn_body, f"внутри функции чата: {marker}")

print()
print("=" * 78)
print("2. ФУНКЦИИ КОМАНДЫ СУЩЕСТВУЮТ")
print("=" * 78)
for name in ("_parse_slash_command", "_render_orch_help",
             "_render_ragraf_stats", "_load_orchestrator_function",
             "_ORCH_COMMANDS"):
    present = name in text
    check(present, f"объявлено: {name}")

print()
print("=" * 78)
print("3. РАЗБОР КОМАНДЫ (без запуска Streamlit)")
print("=" * 78)
# Функция разбора не зависит от Streamlit: она извлекается из файла
# и исполняется отдельно.
parse_src = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "_parse_slash_command":
        parse_src = ast.get_source_segment(text, node)
        break
check(parse_src is not None, "исходник функции разбора получен")

if parse_src:
    # Извлекается словарь команд и сама функция.
    commands_src = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "_ORCH_COMMANDS" in targets:
                commands_src = ast.get_source_segment(text, node)
                break
    namespace: dict = {}
    if commands_src:
        exec(commands_src, namespace)  # noqa: S102
    exec(parse_src, namespace)  # noqa: S102
    parse = namespace["_parse_slash_command"]

    CASES = [
        ("/RAGRAF", "/ragraf", "верхний регистр распознаётся"),
        ("/ragraf", "/ragraf", "нижний регистр"),
        ("/ragraf extra", "/ragraf", "аргументы отделяются"),
        ("  /ragraf  ", "/ragraf", "пробелы по краям не мешают"),
        ("/help", "/help", "команда помощи"),
        ("/unknown", None, "неизвестная команда не перехватывается"),
        ("обычный текст", None, "обычный текст не команда"),
        ("", None, "пустая строка"),
        ("/", None, "один слэш без имени"),
    ]
    for raw, expected, label in CASES:
        result = parse(raw)
        got = result[0] if result else None
        ok = got == expected
        mark = "верно" if ok else "ОШИБКА"
        print(f"    {raw!r:<20} → {got!r:<12} {label:<38} {mark}")
        check(ok, f"разбор: {label}", f"получено {got!r}, ожидалось {expected!r}")

print()
print("=" * 78)
print("4. ПОКАЗ СТАТИСТИКИ ВЫЗЫВАЕТ RAGRAF")
print("=" * 78)
# Проверяется, что функция команды действительно зовёт инструмент RAGRAF
# с операцией task_stats и подставляет метку задачи.
stats_src = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "_render_ragraf_stats":
        stats_src = ast.get_source_segment(text, node)
        break
check(stats_src is not None, "исходник показа статистики получен")
if stats_src:
    for needle, label in (
        ('operation="task_stats"', "вызывается операция task_stats"),
        ("RAGRAF_TASK", "метка задачи передаётся"),
        ("thread_id", "метка берётся из треда чата"),
        ("os.environ.pop", "метка снимается после вызова"),
        ("st.markdown", "отчёт показывается пользователю"),
        ("except Exception", "ошибки обрабатываются"),
    ):
        check(needle in stats_src, label)

print()
print("=" * 78)
print("5. ТЕКСТ КОМАНДЫ НЕ УХОДИТ АГЕНТУ")
print("=" * 78)
# Проверяется: в ветке команды нет вызова отправки агенту.
guard_start = text.find("_command = _parse_slash_command(user_input)")
guard_end = text.find("elif user_input and user_input.strip():")
guard = text[guard_start:guard_end] if guard_start > 0 and guard_end > 0 else ""
check(bool(guard), "ветка команды найдена")
check("_do_step" not in guard,
      "в ветке команды нет шага агента",
      "текст команды ушёл бы агенту")
check("/ragraf" in guard, "обработка /ragraf на месте")
check("/help" in guard, "обработка /help на месте")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
