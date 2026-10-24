"""Синхронизировать реестр разработок с фактическим состоянием индексов.

Зачем: команда register заводит запись, но не читает счётчики узлов и рёбер —
они остаются нулевыми, и реестр показывает «0 узлов» для проиндексированного
корпуса. Здесь показатели подтягиваются из индекса каждой разработки.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import catalog as cm  # noqa: E402
from ragraf import store  # noqa: E402
from ragraf.config import load  # noqa: E402

for cfg_name in ("z-batteryservice", "revoap"):
    cfg_path = REPO / "config" / f"{cfg_name}.yaml"
    if not cfg_path.exists():
        continue
    cfg = load(cfg_path)
    cat = cm.Catalog()
    name = cat.category_for_root(cfg.root) or cfg.project
    entry = cat.get(name)
    if entry is None:
        print(f"{name}: не зарегистрирована")
        continue

    # Показатели читаются из индекса ЭТОЙ разработки: у каждой свой каталог .ragraf,
    # поэтому перекрёстного влияния нет.
    try:
        conn = store.connect(cfg.graph_db, readonly=True)
        row = conn.execute(
            "SELECT rev_id, file_count, node_count, edge_count, capability,"
            " corpus_hash, config_hash FROM rev ORDER BY rev_id DESC LIMIT 1"
        ).fetchone()
        conn.close()
    except Exception as exc:
        print(f"{name}: индекс недоступен ({type(exc).__name__})")
        continue

    if row is None:
        print(f"{name}: ревизий нет")
        continue

    entry.rev_id = str(row["rev_id"])
    entry.file_count = int(row["file_count"] or 0)
    entry.node_count = int(row["node_count"] or 0)
    entry.edge_count = int(row["edge_count"] or 0)
    entry.capability = str(row["capability"] or "")
    if row["corpus_hash"]:
        entry.corpus_hash = str(row["corpus_hash"])
    if row["config_hash"]:
        entry.config_hash = str(row["config_hash"])
    cat.put(entry)

    print(f"{name}")
    print(f"  ревизия:  {entry.rev_id}  capability={entry.capability or '—'}")
    print(f"  размер:   файлов {entry.file_count}, узлов {entry.node_count}, рёбер {entry.edge_count}")
