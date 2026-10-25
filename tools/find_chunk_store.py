"""Разведка: где хранятся чанки и какие у них поля.

Прошлый запрос упал: в графовой базе таблиц чанков нет. Чанки лежат
в отдельном хранилище (lexvec). Нужно найти его и посмотреть схему —
только там можно разместить пометку документации.
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402

cfg = load_config(REPO / "config" / "rdpmon.yaml")
print(f"  graph_db:  {cfg.graph_db}")
print(f"  lex_db:    {getattr(cfg, 'lex_db', '—')}")

# Все файлы баз в служебном каталоге корпуса.
lex_dir = None
for attr in ("lex_db", "lexvec_db", "index_dir"):
    value = getattr(cfg, attr, None)
    if value:
        print(f"  cfg.{attr} = {value}")
        if attr == "lex_db":
            lex_dir = pathlib.Path(str(value))

if lex_dir:
    print(f"\n  каталог lexvec существует: {lex_dir.exists()}")
    if lex_dir.exists():
        for f in sorted(lex_dir.rglob("*")):
            if f.is_file():
                print(f"    {f.name} ({f.stat().st_size / 1024:.0f} КБ)")

# Ищутся базы по всему служебному каталогу корпуса.
index_dir = pathlib.Path(str(getattr(cfg, "index_dir", cfg.root)))
if not index_dir.is_absolute():
    index_dir = pathlib.Path(cfg.root) / index_dir
print(f"\n  поиск баз в: {index_dir}")
if index_dir.exists():
    for f in sorted(index_dir.rglob("*.db")):
        size_mb = f.stat().st_size / 1024 / 1024
        print(f"    {f.relative_to(index_dir)} ({size_mb:.1f} МБ)")
        try:
            conn = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()]
            print(f"      таблиц: {len(tables)}")
            for t in tables:
                cols = [c["name"] for c in conn.execute(f"PRAGMA table_info({t})")]
                n = conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
                print(f"        {t:<16} строк {n:>7}  поля: {', '.join(cols)}")
            conn.close()
        except Exception as exc:
            print(f"      ошибка чтения: {type(exc).__name__}: {exc}")

# Фактические значения полей, похожих на вид чанка.
print()
print("=" * 78)
print("ЗНАЧЕНИЯ ПОЛЕЙ ВИДА ЧАНКА")
print("=" * 78)
for f in sorted(index_dir.rglob("*.db")) if index_dir.exists() else []:
    try:
        conn = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        for t in tables:
            cols = [c["name"] for c in conn.execute(f"PRAGMA table_info({t})")]
            for col in ("source_kind", "text_kind", "label", "kind"):
                if col not in cols:
                    continue
                rows = conn.execute(
                    f"SELECT {col} AS v, COUNT(*) AS n FROM {t} "
                    f"GROUP BY {col} ORDER BY n DESC LIMIT 8"
                ).fetchall()
                print(f"\n  {f.name} → {t}.{col}:")
                for r in rows:
                    print(f"    {r['v']!r}: {r['n']}")
        conn.close()
    except Exception as exc:
        print(f"  {f.name}: {type(exc).__name__}: {exc}")
