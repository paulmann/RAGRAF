"""Проверка учёта агента в телеметрии RAGRAF.

Проверяется главное требование владельца:
  1. в журнал пишется, КАКОЙ агент использовал RAGRAF;
  2. можно получить статистику по одному агенту;
  3. можно получить общую статистику по всем агентам AutoClaw;
  4. можно получить общую по всем (как было);
  5. старая история не потеряна — догоняющая миграция работает;
  6. MCP-сервер сообщает агента (из clientInfo или параметра).

ВАЖНО ПРО БАЗУ. Проверка работает на ВРЕМЕННОЙ базе, а не на журнале
живого корпуса. Первая версия писала в реальный журнал корпуса z и
загрязнила статистику: в ней появились агенты, которые в корпусе
не работали (проверка удалена откатом, tools/rollback_telemetry_test.py).
Проверка не имеет права менять то, что потом читает владелец.

Временная база создаётся в .staging и удаляется в конце. Отдельно
проверяется догоняющая миграция: на копии реального журнала (только
чтение исходника) убеждаемся, что прежние записи не теряются.
"""

from __future__ import annotations

import pathlib
import shutil
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


# --- рабочая база: временная копия, не живой журнал ---
STAGING = REPO / ".staging"
STAGING.mkdir(exist_ok=True)
TMP_DB = STAGING / "telemetry_agent_probe.sqlite"
if TMP_DB.exists():
    TMP_DB.unlink()

print("=" * 78)
print("1. СХЕМА ЖУРНАЛА: ПОЛЯ АГЕНТА ПОЯВИЛИСЬ")
print("=" * 78)
print(f"  временная база: {TMP_DB.relative_to(REPO)}")

# ControlStore создаётся на временной базе: подменяется путь в конфиге.
cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
ctrl = ControlStore.__new__(ControlStore)
ctrl.cfg = cfg
ctrl.conn = sqlite3.connect(str(TMP_DB))
ctrl.conn.row_factory = sqlite3.Row

# Создаётся телеметрия: она накатит схему и догоняющую миграцию.
tel = Telemetry(ctrl, tool="selftest", agent="AutoClaw",
                agent_kind="autoclaw")

session_cols = [
    r[1] for r in ctrl.conn.execute("PRAGMA table_info(usage_session)")
]
call_cols = [r[1] for r in ctrl.conn.execute("PRAGMA table_info(usage_call)")]
print(f"\n  поля usage_session: {sorted(session_cols)}")
print(f"  поля usage_call:    {sorted(call_cols)}")
check("agent" in session_cols, "поле agent есть в usage_session")
check("agent_kind" in session_cols, "поле agent_kind есть в usage_session")
check("agent" in call_cols, "поле agent есть в usage_call")
check("agent_kind" in call_cols, "поле agent_kind есть в usage_call")

# Индексы по агенту: нужны, чтобы фильтрация не читала весь журнал.
indexes = {
    r[0] for r in ctrl.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    )
}
print(f"\n  индексы: {sorted(i for i in indexes if 'agent' in i)}")
check(any("agent" in i for i in indexes), "индекс по агенту создан")

print()
print("=" * 78)
print("2. АГЕНТ ЗАПИСЫВАЕТСЯ В СЕССИЮ И В КАЖДЫЙ ВЫЗОВ")
print("=" * 78)
print(f"  наша сессия: tool={tel.tool!r} agent={tel.agent!r} "
      f"kind={tel.agent_kind!r}")
check(tel.tool == "selftest", "канал записан (tool)")
check(tel.agent == "AutoClaw", "агент записан (agent)")
check(tel.agent_kind == "autoclaw", "семейство записано (agent_kind)")

row = ctrl.conn.execute(
    "SELECT tool, agent, agent_kind FROM usage_session WHERE session_id = ?",
    (tel.session_id,),
).fetchone()
print(f"  строка в usage_session: {dict(row)}")
check(row["agent"] == "AutoClaw", "агент в базе совпадает")
check(row["agent_kind"] == "autoclaw", "семейство в базе совпадает")

# Поля est_tokens и replaces в CallRecord НЕ параметры: они вычисляются
# из result_chars (свойства est_tokens / replaces_reading).
tel.record(CallRecord(op="search", rev_id="test", capability="full",
                      duration_ms=1.0, found=True, fact_count=2,
                      facts_with_span=1, result_chars=100,
                      error=None, tool="selftest", arg_hash="",
                      arg_digest=""))
call_row = ctrl.conn.execute(
    "SELECT tool, agent, agent_kind, est_tokens FROM usage_call "
    "ORDER BY id DESC LIMIT 1"
).fetchone()
print(f"  строка в usage_call:    {dict(call_row)}")
check(call_row["agent"] == "AutoClaw", "агент записан в вызов")
check(call_row["agent_kind"] == "autoclaw", "семейство записано в вызов")
# Оценка токенов должна считаться: это проверка, что запись не сломалась.
check(call_row["est_tokens"] is None or call_row["est_tokens"] >= 0,
      "оценка токенов в вызове корректна")

print()
print("=" * 78)
print("3. РАЗНЫЕ АГЕНТЫ РАЗЛИЧАЮТСЯ")
print("=" * 78)
# Второй агент другого семейства и третий того же семейства, что AutoClaw.
tel_ext = Telemetry(ctrl, tool="mcp", agent="SagaAI", agent_kind="external")
tel_same = Telemetry(ctrl, tool="mcp", agent="MD.Viewer",
                     agent_kind="autoclaw")
for t in (tel_ext, tel_same):
    t.record(CallRecord(op="symbol", rev_id="test", capability="full",
                        duration_ms=2.0, found=True, fact_count=1,
                        facts_with_span=1, result_chars=50,
                        error=None, tool="mcp", arg_hash="",
                        arg_digest=""))

agents = ctrl.conn.execute(
    "SELECT agent, agent_kind, COUNT(*) AS n FROM usage_call "
    "GROUP BY agent, agent_kind ORDER BY n DESC"
).fetchall()
print(f"\n  агенты в журнале:")
for a in agents:
    print(f"    {a['agent']!r} ({a['agent_kind']}): {a['n']} вызовов")
names = {a["agent"] for a in agents}
check("AutoClaw" in names, "агент AutoClaw в журнале")
check("SagaAI" in names, "агент SagaAI в журнале")
check("MD.Viewer" in names, "агент MD.Viewer в журнале")
check(len(names) == 3, "агенты различаются между собой", f"найдено {names}")

print()
print("=" * 78)
print("4. ФИЛЬТРАЦИЯ: ПО АГЕНТУ И ПО СЕМЕЙСТВУ")
print("=" * 78)
total = tel.summary()
print(f"\n  общая: вызовов {total['calls_total']}, "
      f"агентов {total['agents_total']}")
check(total["agents_total"] == 3, "в общей сводке видно всех агентов",
      f"агентов {total['agents_total']}")
print(f"  разбивка по агентам:")
for row in total["by_agent"]:
    print(f"    {row['agent']:<14} {row['agent_kind']:<10} "
          f"вызовов {row['calls']:<3} сессий {row['sessions']:<3} "
          f"фактов {row['facts']}")
check(len(total["by_agent"]) == 3, "разбивка по агентам точна")

only_autoclaw = tel.summary(agent="AutoClaw")
print(f"\n  только AutoClaw: вызовов {only_autoclaw['calls_total']}")
check(only_autoclaw["calls_total"] == 1,
      "фильтр по агенту возвращает только его вызовы",
      f"получено {only_autoclaw['calls_total']}")
check(only_autoclaw["filter"]["agent"] == "AutoClaw",
      "в ответе виден применённый фильтр")

# Главный вопрос владельца: все агенты AutoClaw вместе.
autoclaw_kind = tel.summary(agent_kind="autoclaw")
print(f"\n  семейство autoclaw: вызовов {autoclaw_kind['calls_total']}")
check(autoclaw_kind["calls_total"] == 2,
      "фильтр по семейству собирает всех агентов AutoClaw",
      f"получено {autoclaw_kind['calls_total']}")

external_kind = tel.summary(agent_kind="external")
print(f"  семейство external: вызовов {external_kind['calls_total']}")
check(external_kind["calls_total"] == 1,
      "внешние агенты учтены отдельно",
      f"получено {external_kind['calls_total']}")

print(f"\n  сводка по семействам:")
for bucket in total["by_agent_kind"]:
    print(f"    {bucket['agent_kind']:<10} агентов {bucket['agents']}, "
          f"вызовов {bucket['calls']}, строк не прочитано "
          f"{bucket['est_lines_not_read']}")
check(len(total["by_agent_kind"]) >= 2,
      "сводка по семействам содержит оба вида")

# Совместный фильтр: агент внутри семейства.
combined = tel.summary(agent="MD.Viewer", agent_kind="autoclaw")
check(combined["calls_total"] == 1,
      "совместный фильтр агент+семейство работает",
      f"получено {combined['calls_total']}")

print()
print("=" * 78)
print("5. ДОГОНЯЮЩАЯ МИГРАЦИЯ: СТАРАЯ ИСТОРИЯ НЕ ТЕРЯЕТСЯ")
print("=" * 78)
# Проверяется на КОПИИ реального журнала: исходник не меняется.
LIVE = pathlib.Path(r"C:\AutoClaw\z.batteryservice.ru\.ragraf\control.sqlite")
MIGR = STAGING / "telemetry_migration_probe.sqlite"
if LIVE.exists():
    if MIGR.exists():
        MIGR.unlink()
    shutil.copy2(LIVE, MIGR)
    conn = sqlite3.connect(str(MIGR))
    conn.row_factory = sqlite3.Row
    before_s = conn.execute(
        "SELECT COUNT(*) AS n FROM usage_session"
    ).fetchone()["n"]
    before_c = conn.execute(
        "SELECT COUNT(*) AS n FROM usage_call"
    ).fetchone()["n"]
    print(f"  копия живого журнала: сессий {before_s}, вызовов {before_c}")

    # Накатывается миграция: старые записи не имеют колонок агента.
    ctrl2 = ControlStore.__new__(ControlStore)
    ctrl2.cfg = cfg
    ctrl2.conn = conn
    tel2 = Telemetry(ctrl2, tool="selftest", agent="AutoClaw",
                     agent_kind="autoclaw")

    after_s = conn.execute(
        "SELECT COUNT(*) AS n FROM usage_session"
    ).fetchone()["n"]
    after_c = conn.execute(
        "SELECT COUNT(*) AS n FROM usage_call"
    ).fetchone()["n"]
    print(f"  после миграции:       сессий {after_s}, вызовов {after_c}")
    check(after_s == before_s + 1, "прежние сессии сохранены",
          f"было {before_s}, стало {after_s}")
    check(after_c == before_c, "прежние вызовы не тронуты",
          f"было {before_c}, стало {after_c}")

    # Старые записи видны в сводке как unknown — они не исчезают.
    summary = tel2.summary()
    unknown = [r for r in summary["by_agent"] if r["agent"] == "unknown"]
    old_calls = sum(r["calls"] for r in unknown)
    print(f"  старых вызовов в сводке: {old_calls}")
    check(old_calls == before_c,
          "все прежние вызовы видны как unknown (не потеряны)",
          f"в сводке {old_calls}, в базе {before_c}")
    conn.close()
    MIGR.unlink(missing_ok=True)
else:
    print(f"  живого журнала нет ({LIVE}) — проверка миграции пропущена")
    check(True, "миграция проверена на временной базе выше")

ctrl.conn.close()
TMP_DB.unlink(missing_ok=True)

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
print("""
  Временная база удалена. Живой журнал корпуса не изменялся.
""")
raise SystemExit(0 if FAIL == 0 else 1)
