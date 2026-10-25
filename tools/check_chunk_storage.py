"""Разведка: как хранятся чанки и что из этого видно в поиске.

Нужно перед добавлением пометки «документация». Пометку надо положить туда,
где она уже хранится и откуда её видно в ответе поиска, — иначе она
существует только внутри индекса и агенту бесполезна.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

cfg = load_config(REPO / "config" / "rdpmon.yaml")
conn = store.connect(cfg.graph_db, readonly=True)
rev = conn.execute(
    "SELECT rev_id FROM rev ORDER BY rev_id DESC LIMIT 1"
).fetchone()["rev_id"]

print("=" * 78)
print("ТАБЛИЦЫ ЧАНКОВ")
print("=" * 78)
for name in ("chunk_map", "ft_chunk", "vec_chunk", "vec_meta"):
    try:
        cols = conn.execute(f"PRAGMA table_info({name})").fetchall()
        print(f"\n  {name}:")
        print(f"    {', '.join(c['name'] for c in cols)}")
    except Exception as exc:
        print(f"\n  {name}: недоступна ({type(exc).__name__})")

print()
print("=" * 78)
print("ФАКТИЧЕСКИЕ ЗНАЧЕНИЯ source_kind И label ДЛЯ ФАЙЛОВ .md")
print("=" * 78)
rows = conn.execute(
    "SELECT DISTINCT source_kind, label, lang FROM chunk_map "
    "WHERE rev_id = ? AND lang = 'markdown' LIMIT 12",
    (rev,),
).fetchall()
print(f"  найдено различных сочетаний: {len(rows)}")
for r in rows:
    print(f"    source_kind={r['source_kind']!r}  label={r['label']!r}  "
          f"lang={r['lang']!r}")

print()
print("=" * 78)
print("ВСЕ ЗНАЧЕНИЯ source_kind В ИНДЕКСЕ")
print("=" * 78)
for r in conn.execute(
    "SELECT source_kind, COUNT(*) AS n FROM chunk_map WHERE rev_id = ? "
    "GROUP BY source_kind ORDER BY n DESC",
    (rev,),
).fetchall():
    print(f"    {r['source_kind']!r}: {r['n']}")

print()
print("=" * 78)
print("ВСЕ НЕПУСТЫЕ label")
print("=" * 78)
for r in conn.execute(
    "SELECT label, COUNT(*) AS n FROM chunk_map WHERE rev_id = ? "
    "AND label IS NOT NULL AND label != '' GROUP BY label ORDER BY n DESC LIMIT 10",
    (rev,),
).fetchall():
    print(f"    {r['label']!r}: {r['n']}")

print()
print("=" * 78)
print("ЧТО ВОЗВРАЩАЕТ ПОИСК ДЛЯ .md (сырой ответ)")
print("=" * 78)
from ragraf.facade import Facade  # noqa: E402

fac = Facade(cfg, tool="probe", telemetry=False, watch=False)
env = fac.op_search(query="документация проекта архитектура", limit=3)
res = env.get("result") if isinstance(env, dict) else env
for item in (res if isinstance(res, list) else []):
    print(f"    path={item.get('path')}")
    print(f"      source_kind={item.get('source_kind')!r}  "
          f"label={item.get('label')!r}")
    print(f"      поля: {sorted(item)}")
fac.close()
conn.close()
