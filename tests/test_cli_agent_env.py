"""Проверка: консольный вызов передаёт агента в телеметрию.

Внешние агенты (SagaAI, скрипты-обёртки) вызывают RAGRAF консольной
командой. У такой команды нет параметра «кто меня вызвал», поэтому агент
передаётся через переменные окружения RAGRAF_AGENT и RAGRAF_AGENT_KIND.

Проверяется по факту: запускается CLI с заданными переменными, и результат
читается из журнала. Журнал при этом НЕ рабочий, а копия в .staging:
проверка не должна попадать в статистику владельца.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]

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
print("ПРОВЕРКА: АГЕНТ ИЗ ОКРУЖЕНИЯ ДОХОДИТ ДО ЖУРНАЛА")
print("=" * 78)

# Рабочий журнал корпуса копируется во временный: проверка пишет в копию.
LIVE = pathlib.Path(__file__).resolve().parents[1].parent / "revoap.com/.ragraf/control.sqlite"
STAGING = REPO / ".staging"
STAGING.mkdir(exist_ok=True)
PROBE_DB = STAGING / "cli_agent_probe.sqlite"
if PROBE_DB.exists():
    PROBE_DB.unlink()
shutil.copy2(LIVE, PROBE_DB)

before = sqlite3.connect(f"file:{PROBE_DB}?mode=ro", uri=True).execute(
    "SELECT COUNT(*) AS n FROM usage_call"
).fetchone()[0]
# Отдельно считывается число вызовов в РАБОЧЕМ журнале: оно нужно, чтобы
# доказать, что проверка его не тронула. Сравнивать копию и рабочий журнал
# нельзя — первая версия делала именно так и получала ложный провал
# (в копии было больше вызовов, потому что в неё писали проверки).
live_conn0 = sqlite3.connect(f"file:{LIVE}?mode=ro", uri=True)
live_before = live_conn0.execute(
    "SELECT COUNT(*) AS n FROM usage_call"
).fetchone()[0]
live_conn0.close()
print(f"  журнал-копия: {PROBE_DB.name}, вызовов до проверки: {before}")
print(f"  рабочий журнал: вызовов {live_before}")

# Обёртка подменяет путь журнала у конфигурации: так проверка изолирована.
runner = STAGING / "run_cli_agent_probe.py"
runner.write_text(
    "import sys, pathlib\n"
    f"sys.path.insert(0, r'{REPO / 'src'}')\n"
    "from ragraf.config import ProjectConfig\n"
    f"_probe = pathlib.Path(r'{PROBE_DB}')\n"
    "ProjectConfig.control_db = property(lambda self: _probe)\n"
    "from ragraf.cli import main\n"
    "raise SystemExit(main())\n",
    encoding="utf-8", newline="\n",
)
print(f"  запускается: {runner.name}")

CASES = [
    ("SagaAI", "external"),
    ("AutoClaw", "autoclaw"),
    ("AnotherTool", ""),           # семейство выводится из имени
    ("", ""),                      # имя не задано вовсе
]

for agent, kind in CASES:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if agent:
        env["RAGRAF_AGENT"] = agent
    else:
        env.pop("RAGRAF_AGENT", None)
    if kind:
        env["RAGRAF_AGENT_KIND"] = kind
    else:
        env.pop("RAGRAF_AGENT_KIND", None)

    proc = subprocess.run(
        [sys.executable, str(runner), "--json", "-p", str(REPO / "config" / "revoap.yaml"),
         "query", "search", "pipeline", "--limit", "1"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=str(REPO), timeout=300,
    )
    label = f"agent={agent or '(не задан)'} kind={kind or '(выводится)'}"
    print(f"\n  {label}: код {proc.returncode}")
    if proc.returncode != 0:
        print(f"    stderr: {(proc.stderr or '')[:200]}")
    check(proc.returncode == 0, f"вызов выполнен ({label})",
          f"код {proc.returncode}")

print()
print("=" * 78)
print("ЧТО ЗАПИСАНО В ЖУРНАЛ-КОПИЮ")
print("=" * 78)
conn = sqlite3.connect(str(PROBE_DB))
conn.row_factory = sqlite3.Row

rows = conn.execute(
    "SELECT tool, agent, agent_kind, COUNT(*) AS n FROM usage_call "
    "GROUP BY tool, agent, agent_kind ORDER BY n DESC"
).fetchall()
print(f"\n  {'канал':<8} {'агент':<16} {'семейство':<12} вызовов")
print("  " + "-" * 50)
for r in rows:
    print(f"  {str(r['tool']):<8} {str(r['agent']):<16} "
          f"{str(r['agent_kind']):<12} {r['n']}")

new_rows = [r for r in rows if r["tool"] == "cli" and r["agent"] != "unknown"]
print(f"\n  новых записей от CLI с агентом: {len(new_rows)}")
check(bool(new_rows), "агент из окружения записан в журнал",
      f"записей CLI с агентом нет")

# Проверяются конкретные имена: они должны совпасть с переданными.
agents = {str(r["agent"]) for r in rows}
print(f"  агенты в журнале: {sorted(agents)}")
for expected in ("SagaAI", "AutoClaw", "AnotherTool"):
    mark = "найден" if expected in agents else "НЕТ"
    print(f"    {expected}: {mark}")
    check(expected in agents, f"агент {expected} записан")

# Проверка вывода семейства из имени для AnotherTool (kind не задавался).
row_tool = next((r for r in rows if str(r["agent"]) == "AnotherTool"), None)
if row_tool:
    print(f"  AnotherTool: семейство {row_tool['agent_kind']!r} "
          f"(задано не было — выведено)")
    check(str(row_tool["agent_kind"]) == "external",
          "семейство выведено как external для неизвестного имени",
          f"получено {row_tool['agent_kind']!r}")

# Проверка AutoClaw: семейство задано явно.
row_auto = next((r for r in rows if str(r["agent"]) == "AutoClaw"), None)
if row_auto:
    print(f"  AutoClaw: семейство {row_auto['agent_kind']!r}")
    check(str(row_auto["agent_kind"]) == "autoclaw",
          "семейство autoclaw записано для AutoClaw",
          f"получено {row_auto['agent_kind']!r}")

after = conn.execute("SELECT COUNT(*) AS n FROM usage_call").fetchone()["n"]
print(f"\n  вызовов после проверки: {after} (было {before})")
check(after > before, "новые вызовы записаны", f"{before} → {after}")
conn.close()

# Проверка, что рабочий журнал не изменился.
#
# Считаются ВЫЗОВЫ, а не записи с агентом: в рабочем журнале законно
# есть сессии панели администратора (`RAGRAF-Admin`), и требовать
# «ноль записей с агентом» значило бы требовать стирания законной работы.
# Пробная запись `TestAgentEnv` из рабочего журнала убрана отдельно
# (tools/clean_test_agent_record.py).
live_conn = sqlite3.connect(f"file:{LIVE}?mode=ro", uri=True)
live_calls = live_conn.execute(
    "SELECT COUNT(*) AS n FROM usage_call"
).fetchone()[0]
live_test_agent = live_conn.execute(
    "SELECT COUNT(*) AS n FROM usage_call WHERE agent = 'TestAgentEnv'"
).fetchone()[0]
live_conn.close()
print(f"\n  рабочий журнал: вызовов {live_calls}, "
      f"пробных записей TestAgentEnv {live_test_agent}")
check(live_test_agent == 0,
      "рабочий журнал не содержит пробных записей",
      f"найдено {live_test_agent}")
check(live_calls == live_before,
      "число вызовов в рабочем журнале не изменилось проверкой",
      f"было {live_before} при начале проверки, стало {live_calls}")

# Уборка. Файл копии мог остаться занятым другим процессом — это не ошибка
# проверки, поэтому удаление повторяется с задержкой, а не падает.
import time  # noqa: E402

for attempt in range(3):
    try:
        PROBE_DB.unlink(missing_ok=True)
        break
    except PermissionError:
        time.sleep(1)
runner.unlink(missing_ok=True)
print("  временные файлы удалены")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
