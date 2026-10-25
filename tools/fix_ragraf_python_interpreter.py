"""Исправление дефекта: RAGRAF запускался не тем интерпретатором.

НАЙДЕННЫЙ ДЕФЕКТ (воспроизводится вызовом):
    loader = _load_orchestrator_function("dev_agent", "ragraf")
    loader(operation="task_stats")
    → {"ok": False, "error": "RAGRAF завершился с кодом 2",
       "detail": "ошибка конфигурации: не установлен PyYAML"}

ПРИЧИНА. Функция RAGRAF запускает дочерний процесс через
`sys.executable` — то есть интерпретатором ТОГО ПРОЦЕССА, который её
вызвал. В SagaAI это Python из `.venv` проекта (3.14.7), где нет
зависимостей RAGRAF (`PyYAML` и другие).

При вызове из командной строки я использовал интерпретатор RAGRAF,
поэтому дефект не проявлялся. Через интерфейс SagaAI он обнаруживается
сразу: команда `/RAGRAF` не заработала бы.

ЧТО НЕВЕРНО В МОЁМ ПЕРВОНАЧАЛЬНОМ РЕШЕНИИ. В комментарии к функции было
написано: «Берётся текущий (тот, в котором работает SagaAI): так исключается
ситуация, когда RAGRAF не может найти свои зависимости». Это ровно наоборот:
зависимости нужны RAGRAF, а не SagaAI.

ИСПРАВЛЕНИЕ. Интерпретатор выбирается по порядку:
  1. переменная `RAGRAF_PYTHON`, если задана (для нестандартных установок);
  2. интерпретатор из окружения самого RAGRAF (`.venv` рядом с ним);
  3. `sys.executable` — последний вариант, если ничего не найдено.
"""

from __future__ import annotations

import ast
import pathlib

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
TARGET = SAGA / "orchestrators" / "dev_agent" / "functions" / "ragraf.py"
text = TARGET.read_text(encoding="utf-8")

OLD = '''def _python() -> str:
    """Интерпретатор Python для запуска RAGRAF.

    Берётся текущий (тот, в котором работает SagaAI): так исключается
    ситуация, когда RAGRAF не может найти свои зависимости.
    """
    return sys.executable or "python"'''

NEW = '''def _python() -> str:
    """Интерпретатор Python для запуска RAGRAF.

    ВАЖНО: это НЕ обязательно тот интерпретатор, в котором работает SagaAI.

    Дефект, который здесь исправлен. Раньше возвращался `sys.executable` —
    интерпретатор вызвавшего процесса. В SagaAI это Python из `.venv`
    проекта (3.14), где нет зависимостей RAGRAF, и вызов падал с
    «ошибка конфигурации: не установлен PyYAML». Через интерфейс команда
    `/RAGRAF` не работала бы вовсе.

    Порядок выбора:
      1. `RAGRAF_PYTHON` — если задан явно (для нестандартной установки);
      2. интерпретатор из окружения RAGRAF (`.venv` рядом с ним);
      3. `sys.executable` — крайний случай, когда ничего не нашлось.

    Зависимости нужны RAGRAF, а не SagaAI, поэтому приоритет у его
    собственного окружения.
    """
    import os as _os

    explicit = (_os.environ.get("RAGRAF_PYTHON") or "").strip()
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

OLD_IMPORT = "import json\nimport os\nimport subprocess\nimport sys"
NEW_IMPORT = "import json\nimport os\nimport subprocess\nimport sys\nfrom pathlib import Path"

if OLD not in text:
    raise SystemExit("НЕ НАЙДЕНА функция _python")
text = text.replace(OLD, NEW, 1)

if "\nfrom pathlib import Path" not in text:
    if OLD_IMPORT in text:
        text = text.replace(OLD_IMPORT, NEW_IMPORT, 1)
        print("  добавлен импорт Path")

TARGET.write_text(text, encoding="utf-8", newline="\n")

after = TARGET.read_text(encoding="utf-8")
print("  функция _python исправлена")
print()
print("  проверки:")
checks = [
    ("RAGRAF_PYTHON" in after, "учитывается переменная RAGRAF_PYTHON"),
    ("from pathlib import Path" in after, "импорт Path добавлен"),
    (".venv" in after, "ищется окружение RAGRAF"),
    ('root / ".venv" / "Scripts" / "python.exe"' in after,
     "путь для Windows проверяется"),
]
for ok, label in checks:
    print(f"    [{'OK ' if ok else '--'}] {label}")

try:
    ast.parse(after)
    print("    [OK ] синтаксис верен")
except SyntaxError as exc:
    print(f"    [ПРОВАЛ] синтаксис: строка {exc.lineno}: {exc.msg}")
    raise SystemExit(1)
