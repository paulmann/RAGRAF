"""Проверка экспорта и удаления аналитики.

Свойства:
  1. экспорт соответствует схеме `ragraf.usage.v1` и не содержит путей;
  2. удаление сначала пишет резервную копию, и только потом чистит журнал;
  3. если копию создать нельзя — удаление НЕ выполняется;
  4. удаление возвращает измеренные числа, а не «примерно».
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import analytics  # noqa: E402

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


def make_db(path: pathlib.Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE usage_call (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, ts REAL,
            op TEXT, rev_id TEXT, capability TEXT, duration_ms REAL, found INTEGER,
            fact_count INTEGER, facts_with_span INTEGER, result_chars INTEGER,
            est_tokens INTEGER, replaces INTEGER, error TEXT, tool TEXT,
            arg_hash TEXT, arg_digest TEXT
        );
        CREATE TABLE usage_session (
            session_id TEXT PRIMARY KEY, started_at REAL, tool TEXT,
            agent TEXT, agent_kind TEXT, note TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO usage_call(session_id, ts, op, duration_ms, found, fact_count,"
        " facts_with_span, result_chars, est_tokens, replaces, error)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        [("s1", 1.0, "search", 10.0, 1, 3, 3, 400, 100, 1, None),
         ("s1", 2.0, "symbol", 2.0, 1, 1, 1, 100, 25, 1, None),
         ("s2", 3.0, "catalog", 1.0, 1, 0, 0, 50, 12, 0, "boom")],
    )
    conn.executemany(
        "INSERT INTO usage_session(session_id, started_at, tool, agent, agent_kind, note)"
        " VALUES(?,?,?,?,?,?)",
        [("s1", 1.0, "cli", "AutoClaw", "autoclaw", ""),
         ("s2", 3.0, "cli", "AutoClaw", "autoclaw", "")],
    )
    conn.commit()
    return conn


print("=" * 78)
print("1. ЭКСПОРТ: СХЕМА И ГРАНИЦЫ")
print("=" * 78)
payload = {
    "generated_at": "2026-10-19T12:00:00+03:00",
    "app_version": "0.1.0", "os": "Windows 11", "arch": "AMD64",
    "period_days": 30, "sessions": 2, "calls": 3, "errors": 1,
    "facts": 4, "facts_with_span": 4, "verifiable_ratio": 1.0,
    "chars": 550, "est_tokens": 137, "replacing_chars": 500,
    "est_lines_not_read": 11, "hit_rate": 0.67, "saved_calls": 2, "avg_ms": 4.3,
    "by_op": [{"op": "search", "calls": 1, "facts": 3, "avg_ms": 10.0}],
    "excluded": ["пути", "имена проектов", "запросы", "фрагменты кода"],
}
out = analytics.export_payload(payload)
check(out["schema"] == "ragraf.usage.v1", "схема объявлена", out["schema"])
check(out["queries"]["total"] == 3, "вызовы перенесены")
check(out["value"]["estimated_tokens_saved"] == 137, "токены перенесены")
check(out["value"]["estimated_lines_not_read"] == 11, "строки перенесены")
check(out["excluded"] == payload["excluded"], "границы перечислены")
text = json.dumps(out, ensure_ascii=False)
check("C:\\" not in text and "/home/" not in text, "путей в экспорте нет")

print()
print("=" * 78)
print("2. УДАЛЕНИЕ С РЕЗЕРВНОЙ КОПИЕЙ")
print("=" * 78)
with tempfile.TemporaryDirectory(prefix="ragraf-analytics-") as tmp:
    base = pathlib.Path(tmp)
    conn = make_db(base / "control.sqlite")
    result = analytics.delete_rows(conn, backup_dir=base / "backup")
    check(result["deleted_calls"] == 3, "удалено вызовов ровно 3", str(result["deleted_calls"]))
    check(result["deleted_sessions"] == 2, "удалено сессий ровно 2", str(result["deleted_sessions"]))
    backup = pathlib.Path(result["backup"])
    check(backup.exists(), "резервная копия существует", str(backup))
    saved = json.loads(backup.read_text(encoding="utf-8"))
    check(len(saved["usage_call"]) == 3, "в копии все строки журнала")
    check(saved["schema"] == "ragraf.analytics.backup.v1", "схема копии объявлена")
    check(conn.execute("SELECT COUNT(*) FROM usage_call").fetchone()[0] == 0,
          "журнал вызовов пуст после удаления")
    check(conn.execute("SELECT COUNT(*) FROM usage_session").fetchone()[0] == 0,
          "журнал сессий пуст после удаления")
    conn.close()

print()
print("=" * 78)
print("3. НЕТ КОПИИ — НЕТ УДАЛЕНИЯ")
print("=" * 78)
with tempfile.TemporaryDirectory(prefix="ragraf-analytics-") as tmp:
    base = pathlib.Path(tmp)
    conn = make_db(base / "control.sqlite")
    blocker = base / "backup"
    blocker.write_text("это файл, а не каталог", encoding="utf-8")
    failed = False
    try:
        analytics.delete_rows(conn, backup_dir=blocker)
    except Exception:
        failed = True
    check(failed, "отказ при невозможности создать копию")
    left = conn.execute("SELECT COUNT(*) FROM usage_call").fetchone()[0]
    check(left == 3, "журнал НЕ очищен при отказе", str(left))
    conn.close()

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
