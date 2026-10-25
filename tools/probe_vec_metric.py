"""Диагностика метрики векторного поиска: почему все оценки равны нулю.

Симптом (найден на полигоне): vec0 отдаёт distance, при котором формула
score = 1.0 - distance даёт 0.000 для ВСЕХ результатов. Значит порядок от vec0
не отражается в оценке, а гибрид RRF получает шумный список и поднимает его
выше точных лексических совпадений.

Что проверяется:
  1. диапазон фактических расстояний от vec0 (min/max/среднее);
  2. норма векторов эмбеддингов (если модель нормализует, L2-расстояние лежит
     в [0, 2], и корректная близость = 1 - dist^2 / 2, а не 1 - dist);
  3. сравнение порядков: по возрастанию distance и по убыванию косинусной близости.
"""

from __future__ import annotations

import math
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store, util  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.embeddings import Embedder  # noqa: E402
from ragraf.lexvec import _vec_to_blob  # noqa: E402

QUERY = "реестр настроек шифрование секретов sodium"


def blob_to_vec(blob) -> list[float]:
    if isinstance(blob, memoryview):
        blob = blob.tobytes()
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob[: n * 4]))


def main() -> int:
    cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
    rev = util.read_json(cfg.rev_file, default={}).get("rev_id")
    print(f"ревизия: {rev}")

    emb = Embedder(cfg, allow_download=False)
    if not emb.available():
        print("эмбеддер недоступен")
        return 1
    q = emb.embed_one(QUERY)

    def open_vec():
        """Открыть lexvec с ЗАГРУЖЕННЫМ расширением vec0.

        Без загрузки расширения таблица vec_chunk невидима: sqlite сообщает
        'no such module: vec0'. Это тот же дефект, что был найден в indexer/
        lexvec при работе над пилотом, и здесь он повторился в диагностическом
        скрипте.
        """
        conn = store.connect(cfg.lexvec_db, readonly=True)
        store.try_load_extension(conn, "vec0")
        return conn

    norm = math.sqrt(sum(x * x for x in q))
    print(f"\n=== норма вектора запроса ===\n  |q| = {norm:.6f}  (единица означает нормализованный вектор)")

    conn = open_vec()
    rows = conn.execute(
        "SELECT chunk_id, embedding FROM vec_chunk LIMIT 200"
    ).fetchall()
    conn.close()
    if not rows:
        print("в vec_chunk нет данных")
        return 1

    norms = []
    for r in rows:
        v = blob_to_vec(r["embedding"])
        norms.append(math.sqrt(sum(x * x for x in v)))
    print(f"  норма векторов чанков: min={min(norms):.6f} max={max(norms):.6f} "
          f"avg={sum(norms) / len(norms):.6f} (выборка {len(norms)})")

    # Прямой KNN с выводом расстояний.
    conn = open_vec()
    blob = _vec_to_blob(q)
    res = conn.execute(
        "SELECT chunk_id, distance FROM vec_chunk WHERE embedding MATCH ? "
        "ORDER BY distance LIMIT 12",
        (blob,),
    ).fetchall()
    print("\n=== расстояния от vec0 (KNN) ===")
    dists = [float(r["distance"]) for r in res]
    for r in res:
        d = float(r["distance"])
        cos = 1.0 - (d * d) / 2.0 if d <= 2.0 else float("nan")
        old = max(0.0, 1.0 - d)
        print(f"  dist={d:.4f}  cos≈{cos:+.4f}  было score={old:.4f}  {r['chunk_id'][:12]}")

    print(f"\n  min={min(dists):.4f} max={max(dists):.4f} avg={sum(dists) / len(dists):.4f}")
    print("  вывод: если min близко к 1.0, формула 1-dist даёт почти нули,")
    print("         и оценка перестаёт различать результаты.")

    # Сверка: расстояние против реальной косинусной близости, посчитанной вручную.
    print("\n=== ручная сверка (пересчёт косинуса по векторам) ===")
    conn = open_vec()
    rows = conn.execute(
        "SELECT chunk_id, embedding FROM vec_chunk LIMIT 200"
    ).fetchall()
    qn = math.sqrt(sum(x * x for x in q))
    scored = []
    for r in rows:
        v = blob_to_vec(r["embedding"])
        vn = math.sqrt(sum(x * x for x in v)) or 1.0
        dot = sum(a * b for a, b in zip(q, v))
        scored.append((dot / (qn * vn), str(r["chunk_id"])))
    scored.sort(reverse=True)
    conn.close()
    for cos, cid in scored[:5]:
        print(f"  cos={cos:+.4f}  {cid[:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
