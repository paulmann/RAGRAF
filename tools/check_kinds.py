"""Обзор типов узлов и рёбер, которые понимает система.

Нужно перед добавлением новых языков: извлекатель обязан выдавать те же виды
узлов и рёбер, иначе операции фасада (callers, impact, slice) их не увидят.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
conn = store.connect(cfg.graph_db, readonly=True)
rev = conn.execute(
    "SELECT rev_id FROM rev ORDER BY rev_id DESC LIMIT 1"
).fetchone()["rev_id"]
print(f"корпус: {cfg.project}, ревизия: {rev}\n")

print("=== виды узлов ===")
for r in conn.execute(
    "SELECT kind, COUNT(*) AS n FROM node WHERE rev_id = ?"
    " GROUP BY kind ORDER BY n DESC",
    (rev,),
).fetchall():
    print(f"  {r['kind']:16} {r['n']:>6}")

print("\n=== виды рёбер ===")
for r in conn.execute(
    "SELECT kind, COUNT(*) AS n FROM edge WHERE rev_id = ?"
    " GROUP BY kind ORDER BY n DESC",
    (rev,),
).fetchall():
    print(f"  {r['kind']:20} {r['n']:>6}")

print("\n=== какие виды рёбер понимает обход (_bfs) ===")
facade_src = (REPO / "src" / "ragraf" / "facade.py").read_text(encoding="utf-8")
import re

# Ищется список видов рёбер в обходе графа.
match = re.search(r"AND kind IN \(([^)]+)\)", facade_src, re.DOTALL)
if match:
    kinds = re.findall(r"'([^']+)'", match.group(1))
    print(f"  {len(kinds)} видов:")
    for k in kinds:
        print(f"    {k}")

print("\n=== какие виды рёбер в графе размечает построитель запросов ===")
indexer_src = (REPO / "src" / "ragraf" / "indexer.py").read_text(encoding="utf-8")
for line in indexer_src.splitlines():
    if "PARSE_LANGS" in line or "TEXT_LANGS" in line:
        print(f"  {line.strip()}")

conn.close()
