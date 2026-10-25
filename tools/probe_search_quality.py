"""Диагностика: почему search систематически выдаёт одни и те же файлы.

Симптом (найден замером на корпусе z.batteryservice.ru): для восьми разных
запросов первым результатом идёт mail/index.php, и почти всегда в выдаче есть
config/config.local.example.php. Это либо дефект ранжирования, либо особенность
маленького корпуса.

Что проверяется:
  1. размеры файлов и число чанков на файл — нет ли аномально крупных чанков;
  2. что именно лежит в чанке mail/index.php (по метаданным, без выгрузки кода);
  3. как распределены оценки лексики и вектора на конкретном запросе;
  4. сравнение: чисто лексический поиск, чисто векторный, гибридный.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store, util  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.embeddings import Embedder  # noqa: E402
from ragraf.lexvec import LexVecStore  # noqa: E402

QUERY = "реестр настроек шифрование секретов sodium"


def main() -> int:
    cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
    rev = util.read_json(cfg.rev_file, default={}).get("rev_id")
    print(f"ревизия: {rev}")

    conn = store.connect(cfg.graph_db, readonly=True)
    # В text_chunk текст лежит в колонке raw (проверено схемой store.py).
    rows = conn.execute(
        """SELECT path, COUNT(*) AS chunks, SUM(LENGTH(raw)) AS chars
             FROM text_chunk WHERE rev_id = ?
             GROUP BY path ORDER BY chunks DESC LIMIT 5""",
        (rev,),
    ).fetchall()
    print("\n=== топ-5 файлов по числу текстовых чанков (комментарии) ===")
    for r in rows:
        print(f"  {r['chunks']:>4} чанков, {r['chars']:>7} символов  {r['path']}")
    conn.close()

    lv = LexVecStore(cfg)
    counts = lv.counts(rev)
    print(f"\n=== объём лексико-векторного индекса ===\n  {counts}")

    print("\n=== размеры чанков (символы) ===")
    rows = lv.conn.execute(
        """SELECT m.path, m.tokens, LENGTH(f.code_tokens) AS toks
             FROM chunk_map m JOIN ft_chunk f
               ON f.rev_id = m.rev_id AND f.chunk_id = m.chunk_id
            WHERE m.rev_id = ?
            ORDER BY LENGTH(f.code_tokens) DESC LIMIT 5""",
        (rev,),
    ).fetchall()
    for r in rows:
        print(f"  токенов≈{r['toks']:>6}  {r['path']}")

    print(f"\n=== запрос: {QUERY!r} ===")
    lex = lv.search_lexical(rev, QUERY, limit=5)
    print("  лексика (только FTS5/BM25):")
    for h in lex:
        print(f"    {h.score:>8.3f}  {h.path}:{h.start_line}")

    emb = Embedder(cfg, allow_download=False)
    vec = emb.embed_one(QUERY) if emb.available() else None
    if vec:
        vh = lv.search_vector(rev, vec, limit=5)
        print("  вектор (только эмбеддинги):")
        for h in vh:
            print(f"    {h.score:>8.3f}  {h.path}:{h.start_line}")
        hyb = lv.search_hybrid(rev, QUERY, vec, limit=5)
        print("  гибрид (RRF):")
        for h in hyb:
            print(f"    {h.score:>8.3f}  {h.path}:{h.start_line}  [{h.why}]")
    lv.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
