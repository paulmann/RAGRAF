"""Проверка: операция task_stats возвращает готовый отчёт для команды /RAGRAF.

Проверяется то, что увидит пользователь:
  1. вызов возвращает ok=True и непустой markdown-отчёт;
  2. отчёт содержит числа (обращения, факты, объём), а не шаблон;
  3. метка задачи доходит до журнала RAGRAF;
  4. пробные записи удаляются, законная статистика не страдает.

ИЗОЛЯЦИЯ. Подменить путь журнала не удаётся: поле `control_db` — свойство,
вычисляемое из корневого каталога корпуса, а не читаемое из YAML. Поэтому
проверка идёт по рабочему журналу, но использует УНИКАЛЬНУЮ метку задачи
и удаляет свои записи точно по ней в конце. Законные записи (без метки,
от агента SagaAI) не трогаются.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import sqlite3
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
FUNC = SAGA / "orchestrators" / "dev_agent" / "functions" / "ragraf.py"
PROBE_TASK = "probe-task-001"
LIVE_DB = SAGA / ".ragraf" / "control.sqlite"

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


def load(module_name: str):
    """Загрузить функцию так, как это делает проект SagaAI."""
    spec = importlib.util.spec_from_file_location(module_name, FUNC)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


print("=" * 78)
print("1. ФУНКЦИЯ ЗАГРУЖАЕТСЯ КОНТРАКТОМ ПРОЕКТА")
print("=" * 78)
module = load("_probe_ragraf")
fn = getattr(module, "invoke", None)
check(callable(fn), "функция invoke доступна")
ops = getattr(module, "OPERATIONS", ())
print(f"  операций объявлено: {len(ops)}")
check("task_stats" in ops, "операция task_stats объявлена")
check(hasattr(module, "TASK_LABEL"), "метка задачи читается из окружения")

print()
print("=" * 78)
print("2. ОТЧЁТ БЕЗ ЗАДАННОЙ МЕТКИ")
print("=" * 78)
result = fn(operation="task_stats")
report = str(result.get("summary") or "")
print(f"  ok: {result.get('ok')}, операция: {result.get('operation')}")
print(f"  длина отчёта: {len(report)} символов")
print(f"\n  --- отчёт ---")
for line in report.splitlines()[:14]:
    print(f"    {line}")

check(result.get("ok") is True, "вызов прошёл успешно")
check(result.get("operation") == "task_stats", "операция названа task_stats")
check(len(report) > 20, "отчёт непустой", f"длина {len(report)}")
# Отчёт должен содержать числа, а не только заголовок.
check(any(ch.isdigit() for ch in report), "в отчёте есть числа")
check("RAGRAF" in report, "отчёт подписан именем инструмента")

print()
print("=" * 78)
print("3. МЕТКА ЗАДАЧИ ДОХОДИТ ДО ЖУРНАЛА")
print("=" * 78)
os.environ["RAGRAF_TASK"] = PROBE_TASK
module2 = load("_probe_ragraf2")
print(f"  метка: {getattr(module2, 'TASK_LABEL', None)!r}")
check(getattr(module2, "TASK_LABEL", None) == PROBE_TASK,
      "метка читается из RAGRAF_TASK",
      f"получено {getattr(module2, 'TASK_LABEL', None)!r}")

result = module2.invoke(operation="search", text="DevAgent", limit=2)
print(f"  поиск: ok={result.get('ok')}")
check(result.get("ok") is True, "поиск с меткой задачи выполнен")

if LIVE_DB.exists():
    conn = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT task, agent, tool, COUNT(*) AS n FROM usage_call "
        "WHERE task = ? GROUP BY task, agent, tool", (PROBE_TASK,)
    ).fetchall()
    print(f"\n  записи с меткой {PROBE_TASK!r}:")
    for r in rows:
        print(f"    task={str(r['task']):<18} agent={str(r['agent']):<10} "
              f"tool={str(r['tool']):<6} вызовов {r['n']}")
    conn.close()
    check(bool(rows), "метка задачи записана в журнал RAGRAF",
          "записей с меткой нет")

# Отчёт по этой задаче.
result = module2.invoke(operation="task_stats")
report = str(result.get("summary") or "")
print(f"\n  --- отчёт с меткой ---")
for line in report.splitlines()[:10]:
    print(f"    {line}")
check(PROBE_TASK in report, "отчёт называет задачу",
      f"отчёт: {report[:120]}")
check("обращений к индексу" in report,
      "отчёт содержит счётчик обращений")

os.environ.pop("RAGRAF_TASK", None)

print()
print("=" * 78)
print("4. УБОРКА: ПРОБНЫЕ ЗАПИСИ УДАЛЯЮТСЯ, ЗАКОННЫЕ СОХРАНЯЮТСЯ")
print("=" * 78)
if LIVE_DB.exists():
    conn = sqlite3.connect(str(LIVE_DB))
    conn.row_factory = sqlite3.Row
    before = conn.execute(
        "SELECT COUNT(*) AS n FROM usage_call"
    ).fetchone()["n"]
    probes = conn.execute(
        "SELECT COUNT(*) AS n FROM usage_call WHERE task = ?", (PROBE_TASK,)
    ).fetchone()["n"]
    saga_calls = conn.execute(
        "SELECT COUNT(*) AS n FROM usage_call "
        "WHERE agent = 'SagaAI' AND (task IS NULL OR task != ?)",
        (PROBE_TASK,),
    ).fetchone()["n"]
    print(f"  вызовов всего: {before}, пробных: {probes}, "
          f"от агента SagaAI (без пробных): {saga_calls}")

    conn.execute("DELETE FROM usage_call WHERE task = ?", (PROBE_TASK,))
    conn.execute("DELETE FROM usage_session WHERE task = ?", (PROBE_TASK,))
    conn.commit()

    after = conn.execute(
        "SELECT COUNT(*) AS n FROM usage_call"
    ).fetchone()["n"]
    left = conn.execute(
        "SELECT COUNT(*) AS n FROM usage_call WHERE task = ?", (PROBE_TASK,)
    ).fetchone()["n"]
    saga_after = conn.execute(
        "SELECT COUNT(*) AS n FROM usage_call "
        "WHERE agent = 'SagaAI' AND (task IS NULL OR task != ?)",
        (PROBE_TASK,),
    ).fetchone()["n"]
    conn.close()

    print(f"  после уборки: всего {after}, пробных {left}, "
          f"от SagaAI {saga_after}")
    check(left == 0, "пробные записи удалены", f"осталось {left}")
    check(after == before - probes,
          "удалено ровно столько, сколько создала проверка",
          f"было {before}, стало {after}, пробных {probes}")
    # Сравниваются записи агента БЕЗ пробной задачи: сами пробные вызовы
    # тоже были сделаны от имени SagaAI, поэтому их надо исключить,
    # иначе счётчик законно уменьшится и проверка даст ложный провал.
    check(saga_after == saga_calls,
          "записи агента SagaAI не тронуты",
          f"было {saga_calls}, стало {saga_after}")
else:
    check(True, "журнала нет — уборка не требуется")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
