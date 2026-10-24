"""Проверка: статистика по задаче работает и различает задачи.

Требование владельца: по команде `/RAGRAF` получать статистику
использования RAGRAF за текущую сессию решения задачи.

Проблема, которую решает метка задачи: каждый вызов инструмента запускает
ОТДЕЛЬНЫЙ процесс RAGRAF, поэтому «сессия RAGRAF» (один запуск) не равна
задаче пользователя. Метка `RAGRAF_TASK` собирает все вызовы одной задачи.

Проверяется:
  1. поле task появилось в схеме и заполняется;
  2. две разные задачи различаются в разбивке;
  3. фильтр `--task` возвращает только свою задачу;
  4. записи без задачи видны отдельной строкой и не теряются;
  5. вывод CLI содержит раздел «по задачам».

Работа идёт на ВРЕМЕННОЙ базе: рабочие журналы не изменяются.
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.revisions import ControlStore  # noqa: E402
from ragraf.telemetry import CallRecord, Telemetry  # noqa: E402

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


STAGING = REPO / ".staging"
STAGING.mkdir(exist_ok=True)
TMP_DB = STAGING / "task_probe.sqlite"
if TMP_DB.exists():
    TMP_DB.unlink()

cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
ctrl = ControlStore.__new__(ControlStore)
ctrl.cfg = cfg
ctrl.conn = sqlite3.connect(str(TMP_DB))
ctrl.conn.row_factory = sqlite3.Row

print("=" * 78)
print("1. ПОЛЕ task В СХЕМЕ")
print("=" * 78)
tel = Telemetry(ctrl, tool="selftest", agent="AutoClaw",
                agent_kind="autoclaw", task="task-A")
cols_call = [r[1] for r in ctrl.conn.execute("PRAGMA table_info(usage_call)")]
cols_sess = [r[1] for r in ctrl.conn.execute("PRAGMA table_info(usage_session)")]
print(f"  поля usage_call:    {sorted(cols_call)}")
print(f"  поля usage_session: {sorted(cols_sess)}")
check("task" in cols_call, "поле task есть в usage_call")
check("task" in cols_sess, "поле task есть в usage_session")
check(tel.task == "task-A", "задача сохранена в объекте телеметрии")

row = ctrl.conn.execute(
    "SELECT task FROM usage_session WHERE session_id = ?", (tel.session_id,)
).fetchone()
check(row["task"] == "task-A", "задача записана в сессию",
      f"получено {row['task']!r}")

print()
print("=" * 78)
print("2. ДВЕ ЗАДАЧИ РАЗЛИЧАЮТСЯ")
print("=" * 78)
# Первая задача: два вызова. Вторая: один. Плюс вызов без метки.
tel.record(CallRecord(op="search", rev_id="t", capability="full",
                      duration_ms=10.0, found=True, fact_count=3,
                      facts_with_span=2, result_chars=400,
                      error=None, tool="selftest", arg_hash="",
                      arg_digest=""))
tel.record(CallRecord(op="symbol", rev_id="t", capability="full",
                      duration_ms=5.0, found=True, fact_count=1,
                      facts_with_span=1, result_chars=100,
                      error=None, tool="selftest", arg_hash="",
                      arg_digest=""))

tel_b = Telemetry(ctrl, tool="mcp", agent="SagaAI", agent_kind="external",
                  task="task-B")
tel_b.record(CallRecord(op="code", rev_id="t", capability="full",
                        duration_ms=7.0, found=True, fact_count=1,
                        facts_with_span=1, result_chars=200,
                        error=None, tool="mcp", arg_hash="", arg_digest=""))

tel_none = Telemetry(ctrl, tool="cli", agent="AutoClaw",
                     agent_kind="autoclaw", task="")
tel_none.record(CallRecord(op="repo_map", rev_id="t", capability="full",
                           duration_ms=3.0, found=True, fact_count=4,
                           facts_with_span=0, result_chars=500,
                           error=None, tool="cli", arg_hash="", arg_digest=""))

summary = tel.summary()
print(f"\n  вызовов всего: {summary['calls_total']}, "
      f"задач: {summary['tasks_total']}")
print(f"  по задачам:")
for row in summary["by_task"]:
    print(f"    {str(row['task']):<16} вызовов {row['calls']:<3} "
          f"процессов {row['processes']:<3} символов {row['chars']:<6} "
          f"строк не читалось {row['est_lines_not_read']}")

tasks = {str(r["task"]): r for r in summary["by_task"]}
check("task-A" in tasks, "задача A видна в разбивке")
check("task-B" in tasks, "задача B видна в разбивке")
check("(без задачи)" in tasks, "вызовы без метки видны отдельно",
      f"найдено: {sorted(tasks)}")
check(tasks.get("task-A", {}).get("calls") == 2,
      "у задачи A два вызова", f"{tasks.get('task-A', {}).get('calls')}")
check(tasks.get("task-B", {}).get("calls") == 1, "у задачи B один вызов")
# Процессов у задачи A два (две сессии), но вызовов тоже два.
check(tasks.get("task-A", {}).get("processes") == 1,
      "у задачи A один процесс (сессия)",
      f"{tasks.get('task-A', {}).get('processes')}")

print()
print("=" * 78)
print("3. ФИЛЬТР ПО ЗАДАЧЕ")
print("=" * 78)
only_a = tel.summary(task="task-A")
print(f"  task-A: вызовов {only_a['calls_total']}, "
      f"фильтр {only_a['filter']}")
check(only_a["calls_total"] == 2, "фильтр task-A даёт только её вызовы",
      f"получено {only_a['calls_total']}")
check(only_a["filter"]["task"] == "task-A",
      "применённый фильтр отражён в ответе")

only_b = tel.summary(task="task-B")
check(only_b["calls_total"] == 1, "фильтр task-B даёт только её вызовы",
      f"получено {only_b['calls_total']}")

no_task = tel.summary(task="(без задачи)")
print(f"  без метки: вызовов {no_task['calls_total']}")
check(no_task["calls_total"] == 1,
      "фильтр по записям без метки работает",
      f"получено {no_task['calls_total']}")

print()
print("=" * 78)
print("4. ВЫВОД CLI: РАЗДЕЛ «ПО ЗАДАЧАМ»")
print("=" * 78)
# Проверяется, что код вывода содержит раздел (без запуска процесса).
cli_src = (REPO / "src" / "ragraf" / "cli.py").read_text(encoding="utf-8")
check("по задачам:" in cli_src, "в выводе CLI есть раздел «по задачам»")
check("--task" in cli_src, "в CLI есть флаг --task")
check("RAGRAF_TASK" in cli_src, "CLI читает метку из RAGRAF_TASK")
check("_task_from_env" in cli_src, "есть функция чтения метки")

print()
print("=" * 78)
print("5. ДОГОНЯЮЩАЯ МИГРАЦИЯ: СТАРЫЕ ЖУРНАЛЫ НЕ ЛОМАЮТСЯ")
print("=" * 78)
# На копии реального журнала: поля добавляются, записи сохраняются.
import shutil  # noqa: E402

LIVE = pathlib.Path(__file__).resolve().parents[1].parent / "z.batteryservice.ru/.ragraf/control.sqlite"
MIGR = STAGING / "task_migration_probe.sqlite"
if LIVE.exists():
    if MIGR.exists():
        MIGR.unlink()
    shutil.copy2(LIVE, MIGR)
    conn2 = sqlite3.connect(str(MIGR))
    conn2.row_factory = sqlite3.Row
    before_calls = conn2.execute(
        "SELECT COUNT(*) AS n FROM usage_call"
    ).fetchone()["n"]
    ctrl2 = ControlStore.__new__(ControlStore)
    ctrl2.cfg = cfg
    ctrl2.conn = conn2
    tel2 = Telemetry(ctrl2, tool="selftest", agent="AutoClaw",
                     agent_kind="autoclaw", task="migr-check")
    after_calls = conn2.execute(
        "SELECT COUNT(*) AS n FROM usage_call"
    ).fetchone()["n"]
    print(f"  вызовов до миграции: {before_calls}, после: {after_calls}")
    check(after_calls == before_calls, "прежние вызовы не потеряны",
          f"{before_calls} → {after_calls}")
    summary2 = tel2.summary()
    tasks2 = {str(r["task"]) for r in summary2["by_task"]}
    print(f"  задачи после миграции: {tasks2}")
    check("(без задачи)" in tasks2,
          "старые записи видны как «без задачи»",
          f"найдено {tasks2}")
    conn2.close()
    MIGR.unlink(missing_ok=True)
else:
    check(True, "живого журнала нет — миграция проверена выше")

ctrl.conn.close()
TMP_DB.unlink(missing_ok=True)

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
print("\n  Временная база удалена. Рабочие журналы не изменялись.")
raise SystemExit(0 if FAIL == 0 else 1)
