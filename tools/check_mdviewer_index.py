"""Проверка: попал ли JavaScript в индекс корпуса MD.Viewer.

Зачем отдельная проверка. Язык `javascript` добавлен в карту расширений,
и его надо проверить на реальном корпусе: попал ли в индекс, разбирается ли
как текстовый (без PHP-грамматики), находятся ли по нему ответы.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.facade import Facade  # noqa: E402

cfg = load_config(REPO / "config" / "md.viewer.yaml")
print(f"корпус: {cfg.project}\n")

conn = store.connect(cfg.lexvec_db, readonly=True)
rev = conn.execute(
    "SELECT rev_id FROM chunk_map ORDER BY rev_id DESC LIMIT 1"
).fetchone()["rev_id"]

print("=== 1. Чанки по языкам ===")
for r in conn.execute(
    "SELECT lang, COUNT(*) AS n FROM chunk_map WHERE rev_id = ?"
    " GROUP BY lang ORDER BY n DESC",
    (rev,),
).fetchall():
    print(f"  {r['lang']}: {r['n']}")

print("\n=== 2. Файлы JavaScript в индексе ===")
js_rows = conn.execute(
    "SELECT DISTINCT path FROM chunk_map WHERE rev_id = ? AND lang = 'javascript'"
    " ORDER BY path LIMIT 10",
    (rev,),
).fetchall()
print(f"  файлов: {len(js_rows)}")
for r in js_rows:
    print(f"    {r['path']}")

print("\n=== 3. Узлы: есть ли мусор от PHP-разбора JS ===")
# Узлы лежат в базе ГРАФА, а чанки — в лексической. Это разные файлы,
# и запрос к неверной базе даёт «no such table: node».
graph = store.connect(cfg.graph_db, readonly=True)
node_rows = graph.execute(
    "SELECT kind, COUNT(*) AS n FROM node WHERE rev_id = ? AND path LIKE '%.js'"
    " GROUP BY kind",
    (rev,),
).fetchall()
graph.close()
if node_rows:
    print("  ВНИМАНИЕ: по .js появились узлы — значит шёл разбор деревом:")
    for r in node_rows:
        print(f"    {r['kind']}: {r['n']}")
else:
    print("  узлов по .js нет — верно: JS идёт как текстовые чанки, без разбора")
conn.close()

print("\n=== 4. Поиск находит JS-файлы ===")
facade = Facade(cfg, tool="test", telemetry=False, watch=False)
try:
    for q in ("upload файл", "tooltips подсказки", "settings настройки"):
        d = facade.op_search(q, limit=5)
        hits = d.get("result") or []
        js_hits = [h for h in hits if str(h.get("path", "")).endswith(".js")]
        print(f"  «{q}»: всего {len(hits)}, из них .js: {len(js_hits)}")
        for h in js_hits[:2]:
            print(f"    {h['path']}:{h['start_line']}-{h['end_line']} "
                  f"(оценка {h['score']:.1f})")
finally:
    facade.close()

print("\n=== 5. ИТОГ ===")
has_js = bool(js_rows)
print(f"  JavaScript в индексе: {'да' if has_js else 'НЕТ'}")
print(f"  разбор деревом не применялся: {not node_rows}")
