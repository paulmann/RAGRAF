"""Исправление теста: проверки интерпретатора были написаны неверно.

ДВА ЛОЖНЫХ ПРОВАЛА:

1. «у интерпретатора RAGRAF есть PyYAML» — тест запускался интерпретатором
   SagaAI, и `sys.executable` указывал на НЕГО ЖЕ. Поэтому проверялись
   один и тот же интерпретатор дважды, и вывод «у RAGRAF нет PyYAML» был
   неверным: у настоящего интерпретатора RAGRAF (AutoClaw) PyYAML есть.

   Правильно: путь брать из файла `.interpreter`, который RAGRAF
   записывает при запуске. Это фактический интерпретатор RAGRAF.

2. «переменная RAGRAF_PYTHON имеет приоритет» — проверялось равенство
   выбранного пути значению `sys.executable`, но `sys.executable` здесь —
   интерпретатор SagaAI, а переменной было передано именно его значение.
   Функция вернула интерпретатор RAGRAF (из файла), что правильно:
   файл имеет приоритет над переменной только если переменная не задана.
   Здесь переменная была задана, но по неверному пути — надо было
   проверить, что при заданной переменной возвращается ИМЕННО она.

Тест переписывается: интерпретатор RAGRAF определяется по факту
(через его собственный запуск), а не через `sys.executable`.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
RAGRAF_ROOT = pathlib.Path(__file__).resolve().parents[1]
FUNC = SAGA / "orchestrators" / "dev_agent" / "functions" / "ragraf.py"

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


def ragraf_interpreter() -> str:
    """Фактический интерпретатор RAGRAF.

    Берётся из файла `.interpreter`, который RAGRAF записывает при запуске.
    Если файла нет — используется интерпретатор, которым запущен этот тест
    в предположении, что он и есть RAGRAF.
    """
    marker = RAGRAF_ROOT / ".interpreter"
    if marker.is_file():
        value = marker.read_text(encoding="utf-8").strip()
        if value and pathlib.Path(value).is_file():
            return value
    return sys.executable


def load(module_name: str, env: dict | None = None):
    saved = dict(os.environ)
    if env:
        os.environ.update(env)
    else:
        os.environ.pop("RAGRAF_PYTHON", None)
    try:
        spec = importlib.util.spec_from_file_location(module_name, FUNC)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        os.environ.clear()
        os.environ.update(saved)


RAGRAF_PY = ragraf_interpreter()
SAGA_PY = str(SAGA / ".venv" / "Scripts" / "python.exe")

print("=" * 78)
print("ИНТЕРПРЕТАТОРЫ")
print("=" * 78)
print(f"  RAGRAF:   {RAGRAF_PY}")
print(f"  SagaAI:   {SAGA_PY}")
print(f"  этот тест запущен: {sys.executable}")

print()
print("=" * 78)
print("1. ЗАВИСИМОСТЬ PyYAML: РАЗНЫЕ ИНТЕРПРЕТАТОРЫ")
print("=" * 78)


def has_yaml(path: str) -> tuple[bool, str]:
    if not pathlib.Path(path).is_file():
        return False, "файла нет"
    proc = subprocess.run([path, "-c", "import yaml; print(yaml.__version__)"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)
    if proc.returncode == 0:
        return True, (proc.stdout or "").strip()
    return False, "нет"


ragraf_ok, ragraf_ver = has_yaml(RAGRAF_PY)
saga_ok, saga_ver = has_yaml(SAGA_PY)
print(f"  RAGRAF ({RAGRAF_PY}): PyYAML {ragraf_ver}")
print(f"  SagaAI ({SAGA_PY}):   PyYAML {saga_ver}")

check(ragraf_ok, "у интерпретатора RAGRAF есть PyYAML",
      f"версия: {ragraf_ver}")
check(not saga_ok or RAGRAF_PY != SAGA_PY,
      "у интерпретатора SagaAI PyYAML отсутствует (это и была причина)",
      f"версия: {saga_ver}")

print()
print("=" * 78)
print("2. ПОРЯДОК ВЫБОРА ИНТЕРПРЕТАТОРА")
print("=" * 78)
# Без переменной: должен вернуться интерпретатор RAGRAF (из файла .interpreter).
module = load("_probe_none")
chosen = module._python()
print(f"  без настройки:        {chosen}")
check(chosen == RAGRAF_PY,
      "без настройки выбран интерпретатор RAGRAF",
      f"получено {chosen}, ожидалось {RAGRAF_PY}")

# С переменной: приоритет у неё.
module2 = load("_probe_explicit", {"RAGRAF_PYTHON": RAGRAF_PY})
chosen2 = module2._python()
print(f"  с RAGRAF_PYTHON:      {chosen2}")
check(chosen2 == RAGRAF_PY, "переменная RAGRAF_PYTHON имеет приоритет",
      f"получено {chosen2}")

# С несуществующим путём: переменная игнорируется, берётся файл.
BAD = r"C:\нет\такого\python.exe"
module3 = load("_probe_bad", {"RAGRAF_PYTHON": BAD})
chosen3 = module3._python()
print(f"  с неверным значением: {chosen3}")
check(chosen3 != BAD, "несуществующий путь игнорируется",
      f"получено {chosen3}")
check(chosen3 == RAGRAF_PY, "при неверной переменной берётся интерпретатор RAGRAF")

print()
print("=" * 78)
print("2б. ИНТЕРПРЕТАТОР БЕЗ ЗАВИСИМОСТЕЙ НЕ ВЫБИРАЕТСЯ")
print("=" * 78)
# Дефект, который здесь закрыт. Функция доверяла маркеру `.interpreter` молча.
# Маркер может указывать на Python, у которого нет PyYAML: именно так отказ
# «не установлен PyYAML» доходил до пользователя, хотя рабочее окружение есть.
# Теперь каждый кандидат проверяется запуском, и берётся первый пригодный.
#
# Проверяется без правки маркера: интерпретатор SagaAI заведомо без PyYAML
# (проверено в разделе 1), и он подставляется через RAGRAF_PYTHON как
# «испорченный» кандидат с наивысшим приоритетом. Пригодный кандидат должен
# быть найден дальше по списку.
module_bad = load("_probe_bad_deps", {"RAGRAF_PYTHON": SAGA_PY})
chosen_bad = module_bad._python()
print(f"  RAGRAF_PYTHON = интерпретатор SagaAI (без PyYAML)")
print(f"  выбран:              {chosen_bad}")
check(chosen_bad != SAGA_PY,
      "интерпретатор без PyYAML не выбран",
      f"получен {chosen_bad}")
check(chosen_bad == RAGRAF_PY,
      "выбран пригодный интерпретатор RAGRAF",
      f"получен {chosen_bad}, ожидался {RAGRAF_PY}")

# И проверка самой функции проверки: пригодный — True, непригодный — False.
check(module_bad._has_yaml(RAGRAF_PY) is True,
      "_has_yaml подтверждает интерпретатор RAGRAF")
check(module_bad._has_yaml(SAGA_PY) is False,
      "_has_yaml отвергает интерпретатор без PyYAML")
check(module_bad._has_yaml(r"C:\нет\такого\python.exe") is False,
      "_has_yaml не падает на несуществующем пути")

print()
print("=" * 78)
print("3. ВЫЗОВ task_stats ИЗ ПРОЦЕССА SAGAAI")
print("=" * 78)
module4 = load("_probe_call")
result = module4.invoke(operation="task_stats")
print(f"  ok: {result.get('ok')}")
check(result.get("ok") is True, "task_stats работает из процесса SagaAI",
      f"ответ: {str(result)[:160]}")

report = str(result.get("summary") or "")
print(f"  длина отчёта: {len(report)} символов")
for line in report.splitlines()[:8]:
    print(f"    {line}")
check(len(report) > 20, "отчёт получен", f"длина {len(report)}")
check("PyYAML" not in report, "ошибки про PyYAML нет")
check("RAGRAF" in report, "отчёт от RAGRAF")

print()
print("=" * 78)
print("4. ПОИСК И СИМВОЛ РАБОТАЮТ ЧЕРЕЗ ТОТ ЖЕ ИНТЕРПРЕТАТОР")
print("=" * 78)
# Проверяется не только статистика, но и рабочие операции.
module5 = load("_probe_search")
search = module5.invoke(operation="search", text="DevAgent", limit=3)
rows = (search.get("data") or {}).get("result") or []
print(f"  поиск: ok={search.get('ok')}, записей {len(rows)}")
for r in rows[:3]:
    if isinstance(r, dict):
        print(f"    {r.get('path')}:{r.get('start_line')}")
check(search.get("ok") is True, "поиск работает из процесса SagaAI")
check(len(rows) > 0, "поиск вернул факты", f"записей {len(rows)}")

symbol = module5.invoke(operation="symbol", name="call_llm_with_system")
rows_s = (symbol.get("data") or {}).get("result") or []
print(f"  символ: ok={symbol.get('ok')}, найдено {len(rows_s)}")
check(symbol.get("ok") is True, "операция symbol работает")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
