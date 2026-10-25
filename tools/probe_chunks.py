"""Диагностика: почему в выдаче доминируют чанки source_kind='file' со строки 1.

Симптом: в гибридном поиске первые места занимают записи вида
`mail/index.php:1` и `config/config.php:1` с source_kind='file' — это крупные
чанки «весь файл», которые слабо совпадают со всем подряд и потому попадают в
оба списка (лексика и вектор), получая двойной вес в RRF.

Что проверяется:
  1. сколько чанков каждого вида (symbol/file) в индексе;
  2. какие файлы дали чанк вида file и почему (сколько у них символов);
  3. размер этих чанков против символьных.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import util  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.lexvec import LexVecStore  # noqa: E402


def main() -> int:
    cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
    rev = util.read_json(cfg.rev_file, default={}).get("rev_id")
    print(f"ревизия: {rev}")

    lv = LexVecStore(cfg)

    print("\n=== распределение чанков по видам ===")
    rows = lv.conn.execute(
        """SELECT source_kind, COUNT(*) AS cnt,
                  MIN(LENGTH(f.code_tokens)) AS min_tok,
                  MAX(LENGTH(f.code_tokens)) AS max_tok
             FROM chunk_map m
             JOIN ft_chunk f ON f.rev_id = m.rev_id AND f.chunk_id = m.chunk_id
            WHERE m.rev_id = ?
            GROUP BY source_kind ORDER BY cnt DESC""",
        (rev,),
    ).fetchall()
    for r in rows:
        print(f"  {r['source_kind']:>8}: {r['cnt']:>4} чанков, "
              f"токенов {r['min_tok']}..{r['max_tok']}")

    print("\n=== чанки вида file: какие файлы и какого размера ===")
    rows = lv.conn.execute(
        """SELECT m.path, LENGTH(f.code_tokens) AS tok, m.start_line, m.end_line
             FROM chunk_map m
             JOIN ft_chunk f ON f.rev_id = m.rev_id AND f.chunk_id = m.chunk_id
            WHERE m.rev_id = ? AND m.source_kind = 'file'
            ORDER BY LENGTH(f.code_tokens) DESC LIMIT 12""",
        (rev,),
    ).fetchall()
    for r in rows:
        print(f"  токенов {r['tok']:>6}  строки {r['start_line']}..{r['end_line']}  {r['path']}")

    print("\n=== есть ли символьные чанки у этих же файлов? ===")
    file_paths = [str(r["path"]) for r in rows][:5]
    for p in file_paths:
        cnt = lv.conn.execute(
            "SELECT COUNT(*) FROM chunk_map WHERE rev_id = ? AND path = ? AND source_kind = 'symbol'",
            (rev, p),
        ).fetchone()[0]
        allcnt = lv.conn.execute(
            "SELECT COUNT(*) FROM chunk_map WHERE rev_id = ? AND path = ?", (rev, p)
        ).fetchone()[0]
        print(f"  {p}: символьных {cnt}, всего {allcnt}")

    print("\n=== сколько файлов имеют только файловый чанк ===")
    rows = lv.conn.execute(
        """SELECT path, SUM(CASE WHEN source_kind='file' THEN 1 ELSE 0 END) AS filechunks,
                  SUM(CASE WHEN source_kind='symbol' THEN 1 ELSE 0 END) AS symchunks
             FROM chunk_map WHERE rev_id = ?
            GROUP BY path HAVING filechunks > 0 AND symchunks = 0
            ORDER BY path LIMIT 15""",
        (rev,),
    ).fetchall()
    if rows:
        for r in rows:
            print(f"  только файловый чанк: {r['path']}")
    else:
        print("  таких файлов нет")

    lv.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
