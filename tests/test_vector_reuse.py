"""Свойства переиспользования векторов (этап 12, задача 1).

Что закрепляется — замером 31.10.2026 на корпусе ragraf:

* соединение `vec_meta` с виртуальной `vec_chunk` SQLite выполняет как полный
  скан `vec_chunk` на каждую строку `vec_meta` (план `INDEX 1:2!___`): 21 272
  чанка против 41 356 строк дали **16,98 с** на одном запросе, и это была почти
  вся стадия lexvec (16,18 с). Два скана и пересечение в Python — **0,17 с**;
* из 21 272 чанков активной ревизии **все 21 272** уже имели вектор, то есть
  прежняя безусловная запись переписывала 21 тыс. строк на каждом прогоне и не
  меняла ни одного значения;
* вектор принадлежит `chunk_id` (он адресуется содержимым), поэтому вопрос
  «есть ли вектор» не зависит от ревизии, а `rev_id` в vec0 означает «ревизию,
  где вектор ПОСЧИТАН».

Проверка идёт на отдельном временном хранилище и не трогает ни один корпус.
"""

from __future__ import annotations

import hashlib
import pathlib
import shutil
import sys
import tempfile
from types import SimpleNamespace

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.extract.php_ast import ChunkRec  # noqa: E402
from ragraf.lexvec import LexVecStore  # noqa: E402

PASS = 0
FAIL = 0
VEC = [0.25] * 384
OTHER = [0.5] * 384


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}" + (f"\n      {detail}" if detail else ""))


def scratch_dir() -> pathlib.Path:
    try:
        d = pathlib.Path(tempfile.mkdtemp(prefix="ragraf-reuse-"))
        probe = d / "probe.txt"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return d
    except OSError:
        d = REPO / ".staging" / "vector-reuse-test"
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        return d


def make_chunk(cid: str, text: str, idx: int = 0) -> ChunkRec:
    return ChunkRec(
        chunk_id=cid,
        source_kind="symbol",
        path=f"src/sample_{idx}.py",
        node_id=None,
        start_line=1,
        end_line=3,
        lang="python",
        text=text,
        text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def main() -> int:
    db_dir = scratch_dir()
    db = db_dir / "lexvec.sqlite"
    cfg = SimpleNamespace(
        lexvec_db=db,
        index=SimpleNamespace(batch_size=64),
        embedding=SimpleNamespace(model="test-model"),
    )
    try:
        lv = LexVecStore(cfg)
        if not lv.vec_ok:
            print(f"  ПРОПУСК: векторный слой недоступен — {lv.vec_unavailable_reason()}")
            lv.close()
            return 0

        chunks = [make_chunk(f"c{i}", f"текст {i}", i) for i in range(5)]
        vectors = {c.text_hash: VEC for c in chunks}
        lv.add_chunks("rev-0001", chunks, vectors)

        # 1. Карта переиспользования: два скана, без соединения с vec0.
        hash_to_chunk, present = lv.reuse_index("test-model")
        check(len(present) == 5, "reuse_index нашёл 5 чанков с вектором", f"{len(present)}")
        check(len(hash_to_chunk) == 5, "reuse_index вернул 5 хэшей текста",
              f"{len(hash_to_chunk)}")
        check(all(cid in present for cid in hash_to_chunk.values()),
              "каждый хэш указывает на chunk_id, у которого вектор есть")

        # 2. Вектор чужой модели не переиспользуется: иначе чужой вектор попал бы
        #    в новый индекс молча.
        other_map, other_live = lv.reuse_index("другая-модель")
        check(not other_map and not other_live,
              "векторы другой модели не считаются переиспользуемыми")

        # 3. Вторая ревизия с теми же чанками И с векторами (именно так работал
        #    прежний код: он читал 21 тыс. векторов в Python и писал их обратно).
        #    Свойство: при `present` строки vec0 НЕ переписываются.
        lv.add_chunks("rev-0002", chunks, vectors, present=present)
        check(getattr(lv, "vectors_copied", 0) == 0, "копировать было нечего",
              str(getattr(lv, "vectors_copied", 0)))
        rev_marks = {
            str(r[0]) for r in lv.conn.execute(
                "SELECT rev_id FROM vec_chunk WHERE chunk_id = 'c0'")
        }
        check(rev_marks == {"rev-0001"},
              "строка vec0 сохранила ревизию, в которой вектор ПОСЧИТАН", str(rev_marks))
        total = lv.conn.execute("SELECT COUNT(*) FROM vec_chunk").fetchone()[0]
        check(total == 5, "строк в vec0 по-прежнему 5", f"{total}")

        # 4. Покрытие второй ревизии при этом полное: считается пересечением
        #    идентификаторов, а не меткой rev_id в vec0.
        counts = lv.counts("rev-0002")
        check(counts.get("vec_chunk") == 5,
              "counts второй ревизии сообщает 5 векторов", str(counts.get("vec_chunk")))
        check(counts.get("vec_chunk_total") == 5,
              "counts сообщает 5 строк vec0 всего", str(counts.get("vec_chunk_total")))

        # 5. Сдвинутый chunk_id при том же тексте: вектор переносится запросом
        #    INSERT…SELECT (vec0 его поддерживает — проверено пробой на песочнице).
        shifted = make_chunk("c0-shifted", chunks[0].text, 0)
        lv.add_chunks("rev-0003", [shifted], {}, copies={"c0-shifted": "c0"})
        check(getattr(lv, "vectors_copied", 0) == 1,
              "перенесён ровно один вектор", str(getattr(lv, "vectors_copied", 0)))
        row = lv.conn.execute(
            "SELECT embedding FROM vec_chunk WHERE chunk_id = 'c0-shifted'").fetchone()
        check(row is not None, "у сдвинутого чанка появился вектор")
        if row is not None:
            source = lv.conn.execute(
                "SELECT embedding FROM vec_chunk WHERE chunk_id = 'c0'").fetchone()
            check(bytes(row[0]) == bytes(source[0]),
                  "перенесённый вектор совпадает с исходным побайтово")

        # 6. Ротация: вектор, на который ссылается ДРУГАЯ ревизия, не удаляется.
        lv.add_chunks("rev-0004", chunks[:2], {}, present=present)
        lv.purge_rev("rev-0002")
        left = lv.conn.execute("SELECT COUNT(*) FROM vec_chunk").fetchone()[0]
        check(left >= 5, "после уборки ревизии векторы живых ревизий целы", f"{left}")

        # 7. Сирота (вектор без единой ссылки в vec_meta) убирается.
        lv.conn.execute(
            "INSERT INTO vec_chunk(chunk_id, rev_id, embedding) VALUES(?,?,?)",
            ("orphan", "rev-0001", __import__("struct").pack("<384f", *OTHER)),
        )
        lv.conn.commit()
        lv.purge_rev("rev-0004")
        orphan = lv.conn.execute(
            "SELECT COUNT(*) FROM vec_chunk WHERE chunk_id = 'orphan'").fetchone()[0]
        check(orphan == 0, "вектор без ссылок убран уборкой")

        # 8. Защита от возврата медленного плана: соединения vec_meta с vec0 в
        #    горячем пути быть не должно (оно стоит 16,98 с против 0,17 с).
        source = (REPO / "src" / "ragraf" / "lexvec.py").read_text(encoding="utf-8")
        check("JOIN vec_chunk v ON v.chunk_id = vm.chunk_id" not in source,
              "в lexvec.py нет соединения vec_meta с vec0")
        check("SELECT chunk_id FROM vec_chunk" in source,
              "наличие вектора читается сканом vec0")

        lv.close()
    finally:
        shutil.rmtree(db_dir, ignore_errors=True)

    print()
    print(f"итог: пройдено {PASS}, провалено {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())