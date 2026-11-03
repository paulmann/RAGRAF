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
проверяется догоняющая миграция: прежние записи не теряются.

ОТКУДА БЕРУТСЯ ОЖИДАНИЯ. Свои строки строятся в песочнице (agent IS NULL
и agent = ''), и ожидание считается по числу СОЗДАННЫХ строк. Живой журнал
корпуса z — только образец формы: прежняя редакция требовала, чтобы все его
2 5xx вызовов были без агента, и 28.10.2026 покраснела, когда часть вызовов
уже назвали себя (106 `DeepSeek Harness` + 62 `ragraf-cli`): свойство было
цело, а ожидание устарело. Теперь ожидание по живому журналу считается по
нему же, поэтому его изменения проверку не краснят.
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
print("5. ПРЕЖНИЕ ЗАПИСИ БЕЗ АГЕНТА: СВОИ СТРОКИ В ПЕСОЧНИЦЕ")
print("=" * 78)
# Прежняя редакция брала ЖИВУЮ копию журнала корпуса z и требовала, чтобы ВСЕ
# её вызовы выглядели как unknown. 28.10.2026 проверка покраснела по чужой
# причине: часть её вызовов уже названа (172 записи: 106 `DeepSeek Harness`
# и 62 `ragraf-cli`), и ожидание «все вызовы без агента» перестало быть верным,
# хотя свойство миграции было цело. Поэтому строки строятся СВОИ, а ожидание
# считается по числу созданных строк, а не по числу из живого журнала.
LEGACY_DB = STAGING / "telemetry_legacy_probe.sqlite"
if LEGACY_DB.exists():
    LEGACY_DB.unlink()

ctrl3 = ControlStore.__new__(ControlStore)
ctrl3.cfg = cfg
ctrl3.conn = sqlite3.connect(str(LEGACY_DB))
ctrl3.conn.row_factory = sqlite3.Row
tel3 = Telemetry(ctrl3, tool="cli", agent="AutoClaw", agent_kind="autoclaw")


def add_calls(target: Telemetry, count: int, op: str = "search") -> None:
    """Записать вызовы так, как это делает рабочий код."""
    for _ in range(count):
        target.record(CallRecord(op=op, rev_id="legacy", capability="full",
                                 duration_ms=1.0, found=True, fact_count=1,
                                 facts_with_span=1, result_chars=10,
                                 error=None, tool="cli", arg_hash="",
                                 arg_digest=""))


# Строки без агента — две РАЗНЫЕ формы одного и того же состояния:
# NULL (так выглядит запись после `ALTER TABLE ... ADD COLUMN`) и ''
# (так пишут старые пути). Обе обязаны учитываться как unknown.
NULL_ROWS = 3
EMPTY_ROWS = 2
add_calls(tel3, NULL_ROWS + EMPTY_ROWS)
# Названные вызовы — приманки: их миграция не имеет права переименовать.
NAMED = {"DeepSeek Harness": 2, "ragraf-cli": 1}
for name, count in NAMED.items():
    named_tel = Telemetry(ctrl3, tool="cli", agent=name, agent_kind="external")
    add_calls(named_tel, count, op="symbol")

with_null = [r["id"] for r in ctrl3.conn.execute(
    "SELECT id FROM usage_call ORDER BY id")][:NULL_ROWS]
with_empty = [r["id"] for r in ctrl3.conn.execute(
    "SELECT id FROM usage_call ORDER BY id")][NULL_ROWS:NULL_ROWS + EMPTY_ROWS]
for call_id in with_null:
    ctrl3.conn.execute(
        "UPDATE usage_call SET agent = NULL, agent_kind = NULL WHERE id = ?",
        (call_id,))
for call_id in with_empty:
    ctrl3.conn.execute(
        "UPDATE usage_call SET agent = '', agent_kind = '' WHERE id = ?",
        (call_id,))
ctrl3.conn.commit()

EXPECTED_UNKNOWN = NULL_ROWS + EMPTY_ROWS
EXPECTED_TOTAL = EXPECTED_UNKNOWN + sum(NAMED.values())
print(f"  создано строк: с NULL {NULL_ROWS}, с пустым {EMPTY_ROWS}, "
      f"названных {sum(NAMED.values())} — всего {EXPECTED_TOTAL}")
print(f"  ожидание взято из созданных строк: unknown {EXPECTED_UNKNOWN}, "
      f"названные {NAMED}")


def audit_legacy(conn: sqlite3.Connection, expected_unknown: int,
                 expected_named: dict[str, int]) -> list[str]:
    """Свойства догоняющей миграции — одним списком находок.

    Вынесено в функцию НАМЕРЕННО: её прогоняют и на испорченной копии базы
    (раздел 6). Проверка, которую нельзя покраснеть подсаженным нарушением,
    ничего не проверяет.

    Про неизвестного агента. `unknown` — это ТРИ формы одной записи:
    NULL (следствие `ALTER TABLE ... ADD COLUMN`), пустая строка и буквальная
    строка `unknown` (её пишет `Telemetry`, когда имя не передали). Считать их
    через `GROUP BY` с `COALESCE` нельзя: две из трёх форм дают две группы с
    ОДНИМ именем и одна затирает другую (замер 28.10.2026 на живом журнале
    корпуса z: 138 NULL и 191 буквальных `unknown`, сумма посчиталась как 191).
    Поэтому формы складываются явно.
    """
    problems: list[str] = []
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM usage_call").fetchone()["n"]
    unknown = conn.execute(
        "SELECT COUNT(*) AS n FROM usage_call WHERE agent IS NULL "
        "OR agent = '' OR agent = 'unknown'").fetchone()["n"]
    named = {
        r["agent"]: r["n"] for r in conn.execute(
            "SELECT agent, COUNT(*) AS n FROM usage_call "
            "WHERE agent IS NOT NULL AND agent <> '' AND agent <> 'unknown' "
            "GROUP BY agent").fetchall()
    }
    expected_total = expected_unknown + sum(expected_named.values())
    if unknown != expected_unknown:
        problems.append(
            f"без агента: ожидалось {expected_unknown}, в базе {unknown}")
    for name, count in expected_named.items():
        if named.get(name, 0) != count:
            problems.append(
                f"{name}: ожидалось {count}, в базе {named.get(name, 0)}")
    extra = sorted(set(named) - set(expected_named))
    if extra:
        problems.append(f"лишние агенты (миграция придумала имена): {extra}")
    if total != expected_total:
        problems.append(
            f"всего вызовов: ожидалось {expected_total}, в базе {total}")
    sessions = conn.execute("SELECT COUNT(*) AS n FROM usage_session").fetchone()["n"]
    if sessions < 1:
        problems.append("сессии не записаны")
    return problems


# Накатывается миграция: новая телеметрия на той же базе.
ctrl3.cfg = cfg
tel3b = Telemetry(ctrl3, tool="cli", agent="AutoClaw", agent_kind="autoclaw")
problems = audit_legacy(ctrl3.conn, EXPECTED_UNKNOWN, NAMED)
print(f"  после миграции: находки {problems or 'нет'}")
check(not problems,
      "прежние записи целы: unknown учтены, названные не переименованы",
      str(problems))
summary_legacy = tel3b.summary(agent="unknown")
check(summary_legacy["calls_total"] == EXPECTED_UNKNOWN,
      "фильтр unknown находит и NULL, и пустую строку",
      f"найдено {summary_legacy['calls_total']}, ожидалось {EXPECTED_UNKNOWN}")
ctrl3.conn.close()

print()
print("=" * 78)
print("6. ПОТЕРЯ СТАРЫХ СТРОК: ПРОВЕРКА ОБЯЗАНА ЗАМЕТИТЬ")
print("=" * 78)
# Та же функция проверки прогоняется на испорченной копии: если бы потеря
# записей осталась незамеченной, раздел 5 был бы зелёным всегда.
SABOTAGE_DB = STAGING / "telemetry_sabotage_probe.sqlite"
if SABOTAGE_DB.exists():
    SABOTAGE_DB.unlink()
shutil.copy2(LEGACY_DB, SABOTAGE_DB)
conn = sqlite3.connect(str(SABOTAGE_DB))
conn.row_factory = sqlite3.Row
conn.execute("DELETE FROM usage_call WHERE agent IS NULL OR agent = ''")
conn.commit()
sabotaged = audit_legacy(conn, EXPECTED_UNKNOWN, NAMED)
conn.close()
print(f"  подсажено: удалены записи без агента; находки {sabotaged}")
check(bool(sabotaged),
      "потеря старых строк замечена (проверка умеет краснеть)",
      "испорченная копия прошла проверку")
SABOTAGE_DB.unlink(missing_ok=True)
LEGACY_DB.unlink(missing_ok=True)

print()
print("=" * 78)
print("7. ЖИВОЙ ЖУРНАЛ: ОБРАЗЕЦ ФОРМЫ, А НЕ ОЖИДАНИЕ")
print("=" * 78)
# Копия живого журнала по-прежнему проверяется, но ожидание считается
# ПО НЕЙ САМОЙ: сколько в ней записей названо — столько и должно остаться.
# Поэтому живые изменения (агенты назвали свои вызовы) проверку не краснят.
LIVE = pathlib.Path(__file__).resolve().parents[1].parent / "z.batteryservice.ru/.ragraf/control.sqlite"
MIGR = STAGING / "telemetry_migration_probe.sqlite"
if LIVE.exists():
    if MIGR.exists():
        MIGR.unlink()
    shutil.copy2(LIVE, MIGR)
    conn = sqlite3.connect(str(MIGR))
    conn.row_factory = sqlite3.Row
    live_cols = [r[1] for r in conn.execute("PRAGMA table_info(usage_call)")]
    before_s = conn.execute(
        "SELECT COUNT(*) AS n FROM usage_session"
    ).fetchone()["n"]
    before_c = conn.execute(
        "SELECT COUNT(*) AS n FROM usage_call"
    ).fetchone()["n"]
    named_counts: dict[str, int] = {}
    if "agent" in live_cols:
        # Три формы «агент не назван» разделяются явно: NULL, пустая строка и
        # буквальное `unknown`. Каждая из них — старый вызов, а не новый агент.
        named_counts = {
            r["agent"]: r["n"] for r in conn.execute(
                "SELECT agent, COUNT(*) AS n FROM usage_call "
                "WHERE agent IS NOT NULL AND agent <> '' "
                "AND agent <> 'unknown' GROUP BY agent").fetchall()
        }
    unknown_before = before_c - sum(named_counts.values())
    print(f"  копия живого журнала: сессий {before_s}, вызовов {before_c}, "
          f"из них названо {sum(named_counts.values())}")
    print(f"  ожидание по самой копии: unknown {unknown_before}, "
          f"названные {named_counts}")

    # Накатывается миграция: колонки агента могли отсутствовать.
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

    live_problems = audit_legacy(conn, unknown_before, named_counts)
    print(f"  находки по копии живого журнала: {live_problems or 'нет'}")
    check(not live_problems,
          "на копии живого журнала старые записи не потеряны",
          str(live_problems))
    conn.close()
    MIGR.unlink(missing_ok=True)
else:
    print(f"  пропущено, причина: живого журнала нет ({LIVE}); "
          "свойства миграции проверены на своих строках в разделах 5-6")

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
