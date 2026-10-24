"""Автоматическое определение интерпретатора RAGRAF.

ПРОБЛЕМА. Функция RAGRAF в SagaAI брала `sys.executable` — интерпретатор
процесса, который её вызвал. В SagaAI это `.venv` проекта (Python 3.14)
БЕЗ зависимостей RAGRAF, поэтому вызов падал:
    «ошибка конфигурации: не установлен PyYAML».

Переменную `RAGRAF_PYTHON` владелец должен задавать вручную — это лишний
шаг, о котором забудут. Нужно определение без настройки.

РЕШЕНИЕ. RAGRAF уже знает свой интерпретатор: он сам запускается на нём.
Поэтому RAGRAF при работе Записывает свой путь в файл рядом с конфигом,
а функция читает этот файл. Это не переменная окружения и не угадывание:
значение проставлено фактом запуска.

Порядок поиска (от надёжного к запасному):
  1. `RAGRAF_PYTHON` — если задан явно;
  2. файл `.interpreter` в каталоге RAGRAF — записан при его запуске;
  3. `.venv` рядом с RAGRAF — если владелец его создаст;
  4. `sys.executable` — крайний случай.

Дополнительно: файл перезаписывается при каждом запуске RAGRAF, поэтому
не устаревает при обновлении окружения.
"""

from __future__ import annotations

import ast
import pathlib
import sys

REPO = pathlib.Path(r"C:\AutoClaw\RAGRAF")
SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")

# --- 1. Запись интерпретатора при запуске RAGRAF ---
print("=" * 78)
print("1. ЗАПИСЬ ИНТЕРПРЕТАТОРА ПРИ ЗАПУСКЕ RAGRAF")
print("=" * 78)
cli = REPO / "src" / "ragraf" / "cli.py"
text = cli.read_text(encoding="utf-8")

MARKER = "_remember_interpreter"
if MARKER in text:
    print("  запись уже реализована")
else:
    HELPER = '''

def _remember_interpreter() -> None:
    """Запомнить интерпретатор, на котором работает RAGRAF.

    Зачем. Внешние инструменты (например функция RAGRAF в SagaAI) должны
    запускать дочерний процесс тем же интерпретатором, иначе не найдут
    зависимости RAGRAF. В проекте SagaAI это давало ошибку
    «не установлен PyYAML»: там свой `.venv` без нужных пакетов.

    Путь записывается в файл `.interpreter` рядом с конфигом, а не
    в переменную окружения: переменную процесса не видно из другого
    процесса, а файл читается всегда. При запуске RAGRAF файл
    перезаписывается, поэтому не устаревает.
    """
    import pathlib as _pathlib
    import sys as _sys

    try:
        target = _pathlib.Path(__file__).resolve().parents[2] / ".interpreter"
        target.write_text(_sys.executable or "", encoding="utf-8",
                          newline="\\n")
    except Exception:
        # Неудача записи не должна мешать работе RAGRAF: это вспомогательная
        # сведения, а не часть индексации.
        pass

'''
    # Функция вставляется перед main.
    anchor = "def main("
    if anchor in text:
        text = text.replace(anchor, HELPER + "\n" + anchor, 1)
        print("  функция записи добавлена")
    else:
        raise SystemExit("НЕ НАЙДЕНА функция main")

    # Вызов добавляется в начало main.
    OLD_MAIN = "def main(argv: list[str] | None = None) -> int:"
    NEW_MAIN = ("def main(argv: list[str] | None = None) -> int:\n"
                "    # Запоминается интерпретатор: им будут запускать RAGRAF\n"
                "    # внешние инструменты (см. _remember_interpreter).\n"
                "    _remember_interpreter()")
    if OLD_MAIN in text:
        text = text.replace(OLD_MAIN, NEW_MAIN, 1)
        print("  вызов добавлен в main")

    cli.write_text(text, encoding="utf-8", newline="\n")

# --- 2. Чтение интерпретатора функцией SagaAI ---
print()
print("=" * 78)
print("2. ЧТЕНИЕ ИНТЕРПРЕТАТОРА В ФУНКЦИИ SAGAAI")
print("=" * 78)
func = SAGA / "orchestrators" / "dev_agent" / "functions" / "ragraf.py"
ftext = func.read_text(encoding="utf-8")

OLD_PY = '''    explicit = (_os.environ.get("RAGRAF_PYTHON") or "").strip()
    if explicit and _os.path.isfile(explicit):
        return explicit

    # Окружение самого RAGRAF: ищется в порядке вероятности.
    root = Path(RAGRAF_ROOT)
    candidates = [
        root / ".venv" / "Scripts" / "python.exe",   # Windows
        root / ".venv" / "bin" / "python",           # Linux
        root / "venv" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            return str(candidate)

    # Последний вариант: интерпретатор вызвавшего процесса. Может не иметь
    # зависимостей RAGRAF — тогда вернётся понятная ошибка конфигурации.
    return sys.executable or "python"'''

NEW_PY = '''    explicit = (_os.environ.get("RAGRAF_PYTHON") or "").strip()
    if explicit and _os.path.isfile(explicit):
        return explicit

    root = Path(RAGRAF_ROOT)

    # Файл, который RAGRAF записывает при своём запуске. Это самый надёжный
    # источник: путь проставлен фактом работы, а не угадан.
    marker = root / ".interpreter"
    if marker.is_file():
        try:
            recorded = marker.read_text(encoding="utf-8").strip()
        except OSError:
            recorded = ""
        if recorded and _os.path.isfile(recorded):
            return recorded

    # Окружение самого RAGRAF: если владелец создаст его, берётся оно.
    candidates = [
        root / ".venv" / "Scripts" / "python.exe",   # Windows
        root / ".venv" / "bin" / "python",           # Linux
        root / "venv" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            return str(candidate)

    # Последний вариант: интерпретатор вызвавшего процесса. Может не иметь
    # зависимостей RAGRAF — тогда вернётся понятная ошибка конфигурации.
    return sys.executable or "python"'''

if OLD_PY in ftext:
    ftext = ftext.replace(OLD_PY, NEW_PY, 1)
    func.write_text(ftext, encoding="utf-8", newline="\n")
    print("  чтение файла интерпретатора добавлено")
else:
    if ".interpreter" in ftext:
        print("  чтение уже реализовано")
    else:
        raise SystemExit("НЕ НАЙДЕН блок выбора интерпретатора")

# --- 3. Запись интерпретатора прямо сейчас ---
print()
print("=" * 78)
print("3. ЗАПИСЬ ФАЙЛА ИНТЕРПРЕТАТОРА")
print("=" * 78)
marker_file = REPO / ".interpreter"
marker_file.write_text(sys.executable or "", encoding="utf-8", newline="\n")
print(f"  файл: {marker_file}")
print(f"  содержимое: {marker_file.read_text(encoding='utf-8').strip()}")

print()
print("=" * 78)
print("4. ПРОВЕРКИ")
print("=" * 78)
checks = []
after_cli = cli.read_text(encoding="utf-8")
after_func = func.read_text(encoding="utf-8")
checks.append((MARKER in after_cli, "CLI записывает интерпретатор"))
checks.append((after_cli.count(MARKER) >= 2,
               "запись вызывается в main"))
checks.append((".interpreter" in after_func,
               "функция читает файл интерпретатора"))
checks.append((marker_file.is_file(), "файл интерпретатора существует"))
checks.append((after_cli.count(MARKER) == after_cli.count(MARKER),
               "подсчёт вызовов согласован"))
for ok, label in checks:
    print(f"  [{'OK ' if ok else '--'}] {label}")

for path, name in ((cli, "cli.py"), (func, "ragraf.py (SagaAI)")):
    try:
        ast.parse(path.read_text(encoding="utf-8"))
        print(f"  [OK ] синтаксис верен: {name}")
    except SyntaxError as exc:
        print(f"  [ПРОВАЛ] синтаксис {name}: строка {exc.lineno}: {exc.msg}")
        raise SystemExit(1)
