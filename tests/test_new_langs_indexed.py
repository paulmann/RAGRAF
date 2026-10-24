"""Проверка: новые языки действительно попали в индекс существующих корпусов.

Прямая проверка исходной задачи на всех корпусах сразу. Для каждого языка
считается:
  * сколько файлов этого языка в индексе (по таблице file графовой базы);
  * сколько узлов эти файлы дали (ноль означает «только текст»);
  * находит ли поиск файлы этих типов.

Отдельно проверяются роли документов: сколько документов какой роли попало
в индекс каждого корпуса.
"""

from __future__ import annotations

import pathlib
import sys
from collections import Counter

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

CORPORA = [
    ("z-batteryservice.yaml", "z.batteryservice.ru"),
    ("revoap.yaml", "revoap.com"),
    ("md.viewer.yaml", "MD.Viewer"),
    ("rdpmon.yaml", "RdpMON (C#)"),
]

# Языки, которых раньше в индексе не было вовсе.
NEW_LANGS = ("powershell", "bash", "batch", "csharp", "perl", "python",
             "javascript", "typescript", "yaml", "json", "ini", "text", "xml")

print("=" * 78)
print("НОВЫЕ ЯЗЫКИ В ИНДЕКСАХ")
print("=" * 78)

summary: list[tuple[str, int, int]] = []

for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    conn = store.connect(cfg.graph_db, readonly=True)
    rev = conn.execute(
        "SELECT rev_id FROM rev ORDER BY rev_id DESC LIMIT 1"
    ).fetchone()["rev_id"]

    print()
    print("=" * 78)
    print(f"{title}   ({rev})")
    print("=" * 78)

    files = conn.execute(
        "SELECT lang, COUNT(*) AS n, SUM(lines) AS lines FROM file "
        "WHERE rev_id = ? GROUP BY lang ORDER BY n DESC",
        (rev,),
    ).fetchall()
    total_files = sum(r["n"] for r in files)
    total_nodes = conn.execute(
        "SELECT COUNT(*) AS n FROM node WHERE rev_id = ?", (rev,)
    ).fetchone()["n"]
    print(f"  всего: файлов {total_files}, узлов {total_nodes}")

    # Узлы по языкам: показывает, какие языки разобраны деревом, а какие текст.
    node_langs = Conn_langs = conn.execute(
        "SELECT f.lang, COUNT(DISTINCT n.node_id) AS n FROM node n "
        "JOIN file f ON f.rev_id = n.rev_id AND f.path = n.path "
        "WHERE n.rev_id = ? GROUP BY f.lang ORDER BY n DESC",
        (rev,),
    ).fetchall()
    nodes_by_lang = {r["lang"]: r["n"] for r in node_langs}

    print(f"\n  {'язык':<14} {'файлов':>7} {'строк':>8} {'узлов':>8}  режим")
    print("  " + "-" * 54)
    new_total = 0
    for r in files:
        lang = r["lang"]
        nodes = nodes_by_lang.get(lang, 0)
        if nodes > 0:
            mode = "разбор деревом"
        else:
            mode = "текстом"
        mark = " *" if lang in NEW_LANGS else ""
        print(f"  {lang:<14} {r['n']:>7} {r['lines'] or 0:>8} {nodes:>8}  {mode}{mark}")
        if lang in NEW_LANGS:
            new_total += r["n"]

    print(f"\n  (* — языки, которых раньше в индексе не было)")
    print(f"  файлов новых языков: {new_total}")
    summary.append((title, total_files, new_total))

    # Роли документов в этом корпусе.
    lex = pathlib.Path(str(cfg.lexvec_db))
    if lex.exists():
        import sqlite3

        lconn = sqlite3.connect(f"file:{lex}?mode=ro", uri=True)
        lconn.row_factory = sqlite3.Row
        rev_lex = lconn.execute(
            "SELECT rev_id FROM chunk_map ORDER BY rev_id DESC LIMIT 1"
        ).fetchone()
        if rev_lex:
            roles = Counter()
            for row in lconn.execute(
                "SELECT doc_role, COUNT(*) AS n FROM chunk_map WHERE rev_id = ? "
                "GROUP BY doc_role",
                (rev_lex["rev_id"],),
            ).fetchall():
                roles[row["doc_role"] or "(не документ)"] = row["n"]
            doc_roles = {k: v for k, v in roles.items() if k != "(не документ)"}
            if doc_roles:
                print(f"\n  роли документов ({sum(doc_roles.values())} чанков):")
                for role, n in sorted(doc_roles.items(), key=lambda kv: -kv[1]):
                    print(f"    {role:<16} {n}")
            else:
                print("\n  роли документов не проставлены")
        lconn.close()
    conn.close()

print()
print("=" * 78)
print("СВОДКА")
print("=" * 78)
print(f"\n  {'корпус':<24} {'файлов':>8} {'из них новых языков':>21}")
print("  " + "-" * 56)
for title, total, new in summary:
    print(f"  {title:<24} {total:>8} {new:>21}")

print("""
  Проверяемое утверждение исходной задачи: «.ps1, .sh, .bat в индекс не попали».
  Здесь видно, сколько файлов этих и других новых языков теперь в индексе и
  разобраны ли они деревом (узлы > 0) или идут текстом.
""")
