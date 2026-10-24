"""Финальная сверка: состояние корпусов и итог работы.

Собираются факты для отчёта владельцу:
  1. состав корпусов (ревизия, файлы, узлы, рёбра, capability);
  2. статистика по агентам (сколько агентов, кто именно);
  3. что работает на новом корпусе sagaai;
  4. состояние процессов и порта.
"""

from __future__ import annotations

import pathlib
import socket
import sqlite3
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.facade import Facade  # noqa: E402

print("=" * 78)
print("1. КОРПУСА")
print("=" * 78)
CORPORA = [
    ("z-batteryservice.yaml", "z.batteryservice.ru"),
    ("revoap.yaml", "revoap.com"),
    ("md.viewer.yaml", "MD.Viewer"),
    ("rdpmon.yaml", "RdpMON"),
    ("clean_bom_senior.yaml", "Clean_BOM_Senior"),
    ("sagaai.yaml", "sagaai"),
]
print(f"\n  {'корпус':<24} {'ревизия':<11} {'файлов':>7} {'узлов':>7} "
      f"{'рёбер':>7} {'capability':<10}")
print("  " + "-" * 72)
for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    if not pathlib.Path(str(cfg.graph_db)).exists():
        continue
    conn = sqlite3.connect(f"file:{cfg.graph_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT rev_id, capability, file_count FROM rev "
        "WHERE state = 'ready' ORDER BY rev_id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        conn.close()
        continue
    rev = row["rev_id"]
    nodes = conn.execute("SELECT COUNT(*) AS n FROM node WHERE rev_id = ?",
                         (rev,)).fetchone()["n"]
    edges = conn.execute("SELECT COUNT(*) AS n FROM edge WHERE rev_id = ?",
                         (rev,)).fetchone()["n"]
    conn.close()
    print(f"  {title:<24} {rev:<11} {row['file_count']:>7} {nodes:>7} "
          f"{edges:>7} {str(row['capability']):<10}")

print()
print("=" * 78)
print("2. СТАТИСТИКА ПО АГЕНТАМ")
print("=" * 78)
for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    if not pathlib.Path(str(cfg.graph_db)).exists():
        continue
    fac = Facade(cfg, tool="selftest", telemetry=False, watch=False)
    try:
        summary = fac.usage_summary()
    except Exception:
        continue
    finally:
        fac.close()
    by_agent = summary.get("by_agent") or []
    by_kind = summary.get("by_agent_kind") or []
    print(f"\n  {title}: вызовов {summary.get('calls_total')}, "
          f"сессий {summary.get('sessions_total_all')}, "
          f"агентов {summary.get('agents_total')}")
    for a in by_agent[:5]:
        print(f"    {str(a['agent']):<16} {str(a['agent_kind']):<10} "
              f"сессий {a['sessions']:<4} вызовов {a['calls']:<5} "
              f"фактов {a['facts']}")
    if by_kind:
        kinds = ", ".join(f"{k['agent_kind']}({k['calls']})" for k in by_kind)
        print(f"    по семействам: {kinds}")

print()
print("=" * 78)
print("3. КОРПУС SAGA AI: РАБОТОСПОСОБНОСТЬ")
print("=" * 78)
cfg = load_config(REPO / "config" / "sagaai.yaml")
fac = Facade(cfg, tool="selftest", telemetry=False, watch=False)
try:
    for op_name, call in (
        ("поиск", lambda: fac.op_search("DevAgent", limit=3)),
        ("символ", lambda: fac.op_symbol(name="call_llm_with_system")),
    ):
        try:
            result = call()
            rows = result.get("result") or []
            print(f"  {op_name:<10} найдено {len(rows)}, "
                  f"capability={result.get('capability')}")
            for r in rows[:2]:
                if isinstance(r, dict):
                    print(f"    {r.get('path')}:{r.get('start_line')}")
        except Exception as exc:
            print(f"  {op_name:<10} ошибка {type(exc).__name__}")
finally:
    fac.close()

print()
print("=" * 78)
print("4. ПРОЦЕССЫ И ПОРТ")
print("=" * 78)
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(2)
busy = sock.connect_ex(("127.0.0.1", 8777)) == 0
sock.close()
print(f"  порт 8777 (панель): {'занят' if busy else 'свободен'}")
import subprocess  # noqa: E402

out = subprocess.run(
    ["powershell", "-NoProfile", "-Command",
     "(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "
     "'*RAGRAF*' -and ($_.Name -eq 'java.exe' -or $_.Name -eq 'python.exe') })"
     ".Count"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    timeout=120,
)
print(f"  процессов RAGRAF: {(out.stdout or '').strip()}")
