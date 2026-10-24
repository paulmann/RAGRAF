"""Сбор финальных фактов для отчёта: что именно найдено и что сделано.

Собираются точные данные по всем пунктам отчёта, чтобы каждая строка в нём
опиралась на проверенный факт, а не на впечатление:
  1. находки аудита с путями и строками;
  2. подтверждение критической уязвимости защиты;
  3. состояние установленных компонентов;
  4. числа корпусов RAGRAF.
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")

print("=" * 78)
print("1. УСТАНОВЛЕННЫЕ КОМПОНЕНТЫ В SAGAAI")
print("=" * 78)
items = [
    ("Функция RAGRAF", SAGA / "orchestrators" / "dev_agent" / "functions"
     / "ragraf.py"),
    ("Инструкция RAGRAF", SAGA / "orchestrators" / "dev_agent" / "instructions"
     / "ragraf_index.md"),
    ("Навык SKILL.md", SAGA / "skills" / "Ragaf_Stats" / "SKILL.md"),
    ("Реестр навыков", SAGA / "skills" / "skills.json"),
    ("Страница оркестратора", SAGA / "ui" / "pages" / "orchestrator.py"),
    ("AGENT.md", SAGA / "AGENT.md"),
]
for label, path in items:
    exists = path.exists()
    size = path.stat().st_size if exists else 0
    print(f"  {'[OK ]' if exists else '[--]'} {label:<24} {size:>7} б")
    print(f"        {path.relative_to(SAGA)}")

print()
print("=" * 78)
print("2. КОРПУСА RAGRAF")
print("=" * 78)
CORPORA = [
    ("z-batteryservice.yaml", "z.batteryservice.ru"),
    ("revoap.yaml", "revoap.com"),
    ("md.viewer.yaml", "MD.Viewer"),
    ("rdpmon.yaml", "RdpMON"),
    ("sagaai.yaml", "sagaai"),
    ("ragraf.yaml", "ragraf (свой)"),
]
sys.path.insert(0, str(REPO / "src"))
from ragraf.config import load as load_config  # noqa: E402

print(f"\n  {'корпус':<24} {'ревизия':<11} {'файлов':>7} {'узлов':>7} "
      f"{'capability':<10}")
print("  " + "-" * 62)
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
        "SELECT rev_id, capability, file_count, node_count FROM rev "
        "WHERE state = 'ready' ORDER BY rev_id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if row is None:
        continue
    print(f"  {title:<24} {row['rev_id']:<11} {row['file_count']:>7} "
          f"{row['node_count']:>7} {str(row['capability']):<10}")

print()
print("=" * 78)
print("3. СТАТИСТИКА ПО АГЕНТАМ (ЖИВАЯ)")
print("=" * 78)
from ragraf.facade import Facade  # noqa: E402

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
    print(f"\n  {title}: вызовов {summary.get('calls_total')}, "
          f"агентов {summary.get('agents_total')}")
    for a in by_agent[:4]:
        print(f"    {str(a['agent']):<16} {str(a['agent_kind']):<10} "
              f"вызовов {a['calls']}")

print()
print("=" * 78)
print("4. ФАЙЛЫ ОТЧЁТА")
print("=" * 78)
docs = pathlib.Path(__file__).resolve().parents[1].parent / "docs"
print(f"  каталог отчётов: {docs}")
print(f"  существует: {docs.exists()}")
if docs.exists():
    for item in sorted(docs.iterdir())[:10]:
        kind = "DIR " if item.is_dir() else "FILE"
        print(f"    {kind} {item.name}")
