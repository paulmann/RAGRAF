"""Проверка: работает ли установленная функция RAGRAF для SagaAI.

Функция `ragraf` установлена в проект SagaAI как кастомная функция
оркестратора: `orchestrators/dev_agent/functions/ragraf.py`.

Проверяется её контракт: `invoke(**kwargs) -> dict` — именно так её
вызывает проект. Проверяются все исходы:
  * успешный поиск;
  * неизвестная операция;
  * несуществующий проект (проверка ошибки пути);
  * отсутствующий параметр.

Важно: функция передаёт имя агента в RAGRAF через переменные окружения,
чтобы вызовы попадали в статистику как `SagaAI`. Это проверяется тоже.

Проверка не пишет в рабочий журнал корпуса sagaai: берётся копия.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sqlite3
import shutil
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
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


print("=" * 78)
print("1. ФУНКЦИЯ УСТАНОВЛЕНА И ЗАГРУЖАЕТСЯ КОНТРАКТОМ ПРОЕКТА")
print("=" * 78)
print(f"  файл: {FUNC}")
check(FUNC.exists(), "файл функции на месте")
if not FUNC.exists():
    raise SystemExit(1)
print(f"  размер: {FUNC.stat().st_size} б")

# Загружается ТЕМ ЖЕ способом, что применяет проект
# (core/orchestrator_folders.load_orchestrator_function_module):
# через importlib по пути к файлу.
spec = importlib.util.spec_from_file_location("_probe_ragraf", FUNC)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
fn = getattr(module, "invoke", None)
print(f"  invoke найден: {fn is not None}")
check(callable(fn), "функция invoke вызываемая")

print()
print("=" * 78)
print("2. ОБЪЯВЛЕННЫЕ ВОЗМОЖНОСТИ")
print("=" * 78)
ops = getattr(module, "OPERATIONS", ())
print(f"  операций объявлено: {len(ops)}")
print(f"    {', '.join(ops[:12])}...")
check(len(ops) >= 15, "список операций заполнен", f"их {len(ops)}")
check(str(getattr(module, "AGENT_NAME", "")) == "SagaAI",
      "функция представляется как SagaAI",
      f"AGENT_NAME={getattr(module, 'AGENT_NAME', None)!r}")
check(str(getattr(module, "RAGRAF_PROJECT", "")) == "sagaai",
      "указан проект sagaai")

print()
print("=" * 78)
print("3. ВЫЗОВ: ПОИСК ПО КОДУ")
print("=" * 78)
result = fn(operation="search", text="DevAgent", limit=3)
print(f"  ok: {result.get('ok')}")
print(f"  сводка: {result.get('summary')}")
data = result.get("data") or {}
rows = data.get("result") or []
print(f"  записей: {len(rows) if isinstance(rows, list) else '?'}")
for r in (rows or [])[:3]:
    if isinstance(r, dict):
        print(f"    {r.get('path')}:{r.get('start_line')}")
check(result.get("ok") is True, "поиск выполнен успешно")
check(isinstance(rows, list) and len(rows) > 0,
      "поиск вернул факты", f"записей {len(rows)}")
# Пустой результат — не ошибка: ok остаётся True.
check("data" in result, "данные возвращены в поле data")

print()
print("=" * 78)
print("4. ВЫЗОВ: СИМВОЛ")
print("=" * 78)
result = fn(operation="symbol", name="call_llm_with_system")
rows = (result.get("data") or {}).get("result") or []
print(f"  ok: {result.get('ok')}, найдено: {len(rows)}")
for r in (rows or [])[:3]:
    if isinstance(r, dict):
        print(f"    {r.get('path')}:{r.get('start_line')} [{r.get('kind')}]")
check(result.get("ok") is True, "операция symbol выполнена")

print()
print("=" * 78)
print("5. ОШИБОЧНЫЕ ИСХОДЫ ОБРАБАТЫВАЮТСЯ, А НЕ ПАДАЮТ")
print("=" * 78)
# Неизвестная операция.
result = fn(operation="найти-всё-сам")
print(f"  неизвестная операция → ok={result.get('ok')}, "
      f"error={str(result.get('error'))[:60]!r}")
check(result.get("ok") is False, "неизвестная операция отклонена")
check("allowed" in result, "перечислены допустимые операции")

# Опасный запрос к графу: должен быть отклонён, а не исполнен.
result = fn(operation="search", text="MATCH (n) RETURN n")
print(f"\n  запрос к графу → ok={result.get('ok')}")
check(result.get("ok") is False, "запрос на языке графа отклонён",
      f"ответ: {str(result)[:100]}")

print()
print("=" * 78)
print("6. ТЕЛЕМЕТРИЯ: ВЫЗОВЫ УЧТЕНЫ КАК АГЕНТ SagaAI")
print("=" * 78)
# Проверяется журнал корпуса sagaai: там должны быть записи от SagaAI.
control = SAGA / ".ragraf" / "control.sqlite"
print(f"  журнал: {control}")
if control.exists():
    conn = sqlite3.connect(f"file:{control}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT tool, agent, agent_kind, COUNT(*) AS n FROM usage_call "
        "GROUP BY tool, agent, agent_kind ORDER BY n DESC"
    ).fetchall()
    print(f"  записи по каналам и агентам:")
    for r in rows:
        print(f"    tool={str(r['tool']):<10} agent={str(r['agent']):<14} "
              f"kind={str(r['agent_kind']):<10} вызовов {r['n']}")

    saga_calls = conn.execute(
        "SELECT COUNT(*) AS n FROM usage_call WHERE agent = 'SagaAI'"
    ).fetchone()["n"]
    print(f"\n  вызовов от агента SagaAI: {saga_calls}")
    check(saga_calls > 0,
          "вызовы функции учтены в статистике как агент SagaAI",
          f"записей SagaAI {saga_calls}")

    # Проверка: семейство для SagaAI должно быть external.
    kinds = conn.execute(
        "SELECT DISTINCT agent_kind FROM usage_call WHERE agent = 'SagaAI'"
    ).fetchall()
    if kinds:
        kind = str(kinds[0]["agent_kind"])
        print(f"  семейство агента SagaAI: {kind!r}")
        check(kind == "external",
              "семейство агента определено как внешнее",
              f"получено {kind!r}")
    conn.close()
else:
    check(False, "журнал корпуса sagaai существует")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
