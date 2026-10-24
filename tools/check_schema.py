"""Схема хранения: какие поля есть у файлов, чанков и узлов.

Нужно перед добавлением пометки «документация». Пометка должна лечь в
существующую схему, а не в выдуманную: если поля нет, его надо добавить
миграцией — и это уже изменение схемы, которое делается осознанно.

Также проверяется, лежит ли где-нибудь хэш содержимого файла: без него
схлопнуть дубликаты документации по содержанию не получится.
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

print("=" * 78)
print("СХЕМА: ТАБЛИЦЫ И ПОЛЯ")
print("=" * 78)

tables = [
    r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
]
print(f"\n  таблиц: {len(tables)}")
for name in tables:
    cols = conn.execute(f"PRAGMA table_info({name})").fetchall()
    fields = ", ".join(f"{c['name']}:{c['type']}" for c in cols)
    print(f"\n  {name}")
    print(f"    {fields}")

print()
print("=" * 78)
print("ЧТО МОЖЕТ СЛУЖИТЬ ПОМЕТКОЙ ДОКУМЕНТАЦИИ")
print("=" * 78)
print("""
  Ищем три вещи:
    1. есть ли у файла хэш содержимого (для схлопывания дублей);
    2. есть ли у файла поле роли/вида, куда положить пометку;
    3. есть ли у узла поле props (там можно хранить без изменения схемы).
""")
for table, wanted in (
    ("file", ["sha256", "hash", "kind", "role", "lang", "path"]),
    ("node", ["props", "kind", "name"]),
    ("chunk_map", ["path", "source_kind", "tokens", "node_id"]),
):
    try:
        cols = {c["name"] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        continue
    present = [w for w in wanted if w in cols]
    missing = [w for w in wanted if w not in cols]
    print(f"  {table}:")
    print(f"    есть:  {present}")
    print(f"    нет:   {missing}")

conn.close()
