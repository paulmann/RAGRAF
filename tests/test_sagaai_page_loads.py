"""Проверка: страница SagaAI загружается после правок.

Синтаксис проверен, но это не то же самое, что успешный импорт: ошибка
может быть в имени, которого нет в области видимости, или в импорте.

Загружается модуль страницы целиком — так, как это делает приложение
(`from ui.pages.orchestrator import page_orchestrator`). Если импорт
проходит, значит добавленный код не ломает приложение.

Streamlit вне контекста выполнения может ругаться на вызовы `st.*`
верхнего уровня, но здесь проверяется ТОЛЬКО импорт модуля: определения
функций не исполняются.
"""

from __future__ import annotations

import pathlib
import sys

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
sys.path.insert(0, str(SAGA))

# Переменные окружения как у приложения.
import os  # noqa: E402

os.environ.setdefault("SAGAAI_DATA_DIR", str(SAGA))

print("=" * 78)
print("1. ИМПОРТ МОДУЛЯ СТРАНИЦЫ")
print("=" * 78)
try:
    from ui.pages import orchestrator as orch  # noqa: E402

    print("  модуль импортирован")
    print(f"  функций объявлено: "
          f"{len([n for n in dir(orch) if callable(getattr(orch, n))])}")
    for name in ("page_orchestrator", "_parse_slash_command",
                 "_render_ragraf_stats", "_render_orch_help",
                 "_load_orchestrator_function", "_ORCH_COMMANDS"):
        present = hasattr(orch, name)
        print(f"    [{'OK ' if present else '--'}] {name}")
    assert hasattr(orch, "_parse_slash_command")
    print("\n  РЕЗУЛЬТАТ: импорт успешен")
except SyntaxError as exc:
    print(f"  ОШИБКА СИНТАКСИСА: строка {exc.lineno}: {exc.msg}")
    raise SystemExit(1)
except Exception as exc:
    print(f"  ОШИБКА ИМПОРТА: {type(exc).__name__}: {exc}")
    import traceback

    traceback.print_exc()
    raise SystemExit(1)

print()
print("=" * 78)
print("2. ФУНКЦИИ КОМАНДЫ РАБОТАЮТ")
print("=" * 78)
# Разбор команды — чистая функция, её можно вызвать без Streamlit.
cases = {
    "/RAGRAF": "/ragraf",
    "/ragraf": "/ragraf",
    "/HELP": "/help",
    "/unknown": None,
    "текст": None,
    "": None,
}
for raw, expected in cases.items():
    result = orch._parse_slash_command(raw)
    got = result[0] if result else None
    status = "верно" if got == expected else f"ОШИБКА (ожидалось {expected!r})"
    print(f"  {raw!r:<12} → {got!r:<12} {status}")

print()
print("  словарь команд:")
for name, description in orch._ORCH_COMMANDS.items():
    print(f"    {name} — {description}")

print()
print("=" * 78)
print("3. ЗАГРУЗКА ФУНКЦИИ RAGRAF ЧЕРЕЗ ФУНКЦИЮ КОМАНДЫ")
print("=" * 78)
loader = orch._load_orchestrator_function("dev_agent", "ragraf")
print(f"  загружено: {loader is not None}")
if loader is not None:
    result = loader(operation="task_stats")
    print(f"  вызов task_stats: ok={result.get('ok')}")
    report = str(result.get("summary") or "")
    print(f"  отчёт: {len(report)} символов")
    for line in report.splitlines()[:6]:
        print(f"    {line}")
    assert result.get("ok") is True
    print("\n  РЕЗУЛЬТАТ: команда получает отчёт от RAGRAF")
else:
    print("  ПРОВАЛ: функция не загружена")
    raise SystemExit(1)

print()
print("=" * 78)
print("ИТОГ: страница загружается, команда работает")
print("=" * 78)
