"""Диагностика: почему вызов ragraf_usage не попал в журнал.

Симптом: сервер отвечает на initialize и tools/list, но после вызова
ragraf_usage в журнале нет ни одной новой записи с агентом.

Возможные причины:
  1. вызов упал (в stderr раньше была ошибка), и телеметрия не записалась;
  2. запись идёт в другой файл, а проверка читает не тот;
  3. клиент MCP не вызвал инструмент (ответ есть, но это ошибка).

Проверяется по факту: запускается сервер, подаётся вызов, и печатается
ПОЛНЫЙ ответ и полный stderr.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
STAGING = REPO / ".staging"

LIVE = pathlib.Path(__file__).resolve().parents[1].parent / "revoap.com/.ragraf/control.sqlite"
work_db = STAGING / "mcp_diag.sqlite"
if work_db.exists():
    work_db.unlink()
shutil.copy2(LIVE, work_db)

cfg_tmp = STAGING / "mcp_diag.yaml"
shutil.copy2(REPO / "config" / "revoap.yaml", cfg_tmp)

runner = STAGING / "run_mcp_diag.py"
runner.write_text(
    "import sys, pathlib\n"
    f"sys.path.insert(0, r'{REPO / 'src'}')\n"
    "from ragraf.config import ProjectConfig\n"
    f"_probe_db = pathlib.Path(r'{work_db}')\n"
    "ProjectConfig.control_db = property(lambda self: _probe_db)\n"
    "from ragraf.mcp_server import main\n"
    "raise SystemExit(main())\n",
    encoding="utf-8", newline="\n",
)

print("=" * 78)
print("ЗАПУСК СЕРВЕРА С ВЫЗОВОМ ragraf_usage")
print("=" * 78)
print(f"  журнал (копия): {work_db}")
print(f"  размер копии:   {work_db.stat().st_size / 1024:.0f} КБ")

requests = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2024-11-05",
                "clientInfo": {"name": "SagaAI", "version": "1.0"}}},
    {"jsonrpc": "2.0", "id": 2, "method": "notifications/initialized",
     "params": {}},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
     "params": {"name": "ragraf_search",
                "arguments": {"query": "pipeline", "limit": 3}}},
]
stdin_text = "\n".join(json.dumps(r, ensure_ascii=False) for r in requests)

proc = subprocess.run(
    [sys.executable, str(runner), "--project", str(cfg_tmp),
     "--agent", "SagaAIDiag", "--agent-kind", "external"],
    input=stdin_text, capture_output=True, text=True,
    encoding="utf-8", errors="replace", timeout=300,
)
print(f"\n  код возврата: {proc.returncode}")
print(f"\n  --- ПОЛНЫЙ stderr ---")
for line in (proc.stderr or "").splitlines()[:20]:
    print(f"    {line}")

print(f"\n  --- stdout (первые 3 ответа, сокращённо) ---")
for line in (proc.stdout or "").splitlines()[:3]:
    line = line.strip()
    if not line:
        continue
    try:
        obj = json.loads(line)
        rid = obj.get("id")
        if "error" in obj:
            print(f"    id={rid}: ОШИБКА {obj['error']}")
        else:
            keys = sorted((obj.get("result") or {}))
            print(f"    id={rid}: ключи результата {keys}")
    except json.JSONDecodeError:
        print(f"    {line[:100]}")

print()
print("=" * 78)
print("ЧТО В ЖУРНАЛЕ-КОПИИ")
print("=" * 78)
import sqlite3  # noqa: E402

conn = sqlite3.connect(str(work_db))
conn.row_factory = sqlite3.Row
print(f"\n  сессии:")
for r in conn.execute(
    "SELECT session_id, tool, agent, agent_kind FROM usage_session "
    "ORDER BY started_at DESC LIMIT 5"
).fetchall():
    print(f"    tool={r['tool']!r} agent={r['agent']!r} "
          f"kind={r['agent_kind']!r} id={r['session_id']}")

print(f"\n  вызовы (последние 5):")
for r in conn.execute(
    "SELECT op, tool, agent, agent_kind FROM usage_call "
    "ORDER BY id DESC LIMIT 5"
).fetchall():
    print(f"    op={r['op']!r} tool={r['tool']!r} agent={r['agent']!r} "
          f"kind={r['agent_kind']!r}")

total = conn.execute("SELECT COUNT(*) AS n FROM usage_call").fetchone()["n"]
with_agent = conn.execute(
    "SELECT COUNT(*) AS n FROM usage_call WHERE agent IS NOT NULL"
).fetchone()["n"]
print(f"\n  всего вызовов: {total}, из них с агентом: {with_agent}")
conn.close()

# Уборка.
work_db.unlink(missing_ok=True)
cfg_tmp.unlink(missing_ok=True)
runner.unlink(missing_ok=True)
print("\n  временные файлы удалены")
