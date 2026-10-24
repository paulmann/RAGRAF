"""Диагностика лексико-векторного слоя: vec0, переиспользование, гибридный поиск."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import util  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.embeddings import Embedder  # noqa: E402
from ragraf.lexvec import LexVecStore, tokenize_for_fts  # noqa: E402


def main() -> int:
    cfg = load_config(REPO / "config" / "revoap.yaml")
    rev = util.read_json(cfg.rev_file, default={}).get("rev_id")
    print(f"ревизия: {rev}")

    lv = LexVecStore(cfg)
    print(f"расширение загружено: {lv.extension_loaded}")
    print(f"vec_ok: {lv.vec_ok}")
    counts = lv.counts(rev)
    print(f"счётчики: {counts}")

    # Токенизация запроса
    for q in ("PipelineEngine processStage", "настройки", "перезапуск задачи воркер"):
        print(f"  токены {q!r}: {tokenize_for_fts(q)[:120]!r}")

    lex = lv.search_lexical(rev, "настройки", limit=5)
    print(f"лексический поиск 'настройки': {len(lex)}")

    emb = Embedder(cfg, allow_download=False)
    st = emb.status()
    print(f"эмбеддер: available={st.available} reason={st.reason}")
    if st.available:
        vec = emb.embed_one("настройки")
        print(f"вектор получен, размерность {len(vec)}")
        hits = lv.search_vector(rev, vec, limit=5)
        print(f"векторный поиск: {len(hits)}")
        for h in hits[:3]:
            print(f"  {h.score:.4f} {h.path}:{h.start_line}")
        hyb = lv.search_hybrid(rev, "настройки", vec, limit=5)
        print(f"гибридный поиск: {len(hyb)}")
        for h in hyb[:5]:
            print(f"  {h.score:.2f} {h.path}:{h.start_line} — {h.why}")
    lv.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
