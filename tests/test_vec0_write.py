"""Проверка записи векторов в vec0: замена существующего ключа и честный отказ.

Дефект, который закрепляет проверка (замер 16.10.2026, корпус RAGRAF):

  1. vec0 НЕ умеет `INSERT OR REPLACE`: повторная вставка существующего
     chunk_id падает с «UNIQUE constraint failed on vec_chunk primary key».
     Запись векторов обрывалась, код молча ставил `vec_ok=False`, и покрытие
     сообщало НЕВЕРНУЮ причину — «sqlite-vec не загрузился» — при полностью
     рабочем расширении. Следствие: 772 вектора на 3952 чанка, векторный канал
     поиска фактически выключен.

  2. Дубль chunk_id ВНУТРИ одного списка чанков (разбор может выдать один
     идентификатор дважды: ft_chunk 3953 строки против 3952 в chunk_map на
     rev-0152) — после починки п.1 он оборвал бы запись на середине.
     Идентификаторы обязаны дедуплицироваться перед записью.

  3. Отказ записи обязан называть себя (`vec_error` и `vec_unavailable_reason`),
     а не выглядеть как «расширение не загрузилось».

Проверка идёт на отдельном временном хранилище и не трогает ни один корпус.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.extract.php_ast import ChunkRec  # noqa: E402
from ragraf.lexvec import LexVecStore  # noqa: E402

PASS = 0
FAIL = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


def scratch_dir() -> Path:
    """Каталог для временного хранилища.

    Сначала системный временный каталог; если он недоступен на запись (процесс
    с низкой целостностью не может писать «вверх» в каталоги более высокого
    уровня), берётся каталог внутри проекта: он доступен процессу, запущенному
    из дерева, в обоих случаях.
    """
    import tempfile

    try:
        d = Path(tempfile.mkdtemp(prefix="ragraf-vec0-"))
        probe = d / "probe.txt"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return d
    except OSError:
        d = REPO / ".staging" / "vec0-write-test"
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        return d


def make_chunk(cid: str, text: str, idx: int) -> ChunkRec:
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
            # Среда без sqlite-vec: проверять нечего, но об этом говорится прямо,
            # а не выдаётся зелёный результат (НФТ: векторный слой опционален).
            print(f"  ПРОПУСК: векторный слой недоступен — {lv.vec_unavailable_reason()}")
            lv.close()
            return 0

        vec = [0.01] * 384
        chunks = [make_chunk(f"c{i}", f"текст {i}", i) for i in range(5)]
        vectors = {c.text_hash: vec for c in chunks}

        n1 = lv.add_chunks("rev-0001", chunks, vectors)
        check(n1 == 5, "первая запись чанков", f"записано {n1}")
        check(lv.vec_ok, "после первой записи vec_ok остался True",
              lv.vec_unavailable_reason())
        v1 = lv.conn.execute("SELECT COUNT(*) FROM vec_chunk").fetchone()[0]
        check(v1 == 5, "векторов записано 5", f"получено {v1}")

        # Повторная запись тех же chunk_id: здесь падал INSERT OR REPLACE —
        # vec0 не поддерживает разрешение конфликта первичного ключа.
        n2 = lv.add_chunks("rev-0002", chunks, vectors)
        check(n2 == 5, "повторная запись тех же chunk_id принята", f"записано {n2}")
        check(lv.vec_ok and not lv.vec_error,
              "замена ключа прошла без отказа записи", lv.vec_error)
        v2 = lv.conn.execute("SELECT COUNT(*) FROM vec_chunk").fetchone()[0]
        check(v2 == 5, "дублей не появилось: 5 ключей", f"получено {v2}")
        rev2 = lv.conn.execute(
            "SELECT COUNT(*) FROM vec_chunk WHERE rev_id='rev-0002'"
        ).fetchone()[0]
        check(rev2 == 5, "строки обновлены на новую ревизию: 5", f"получено {rev2}")

        # Дубль chunk_id внутри одного списка: раньше вторая вставка того же
        # ключа обрывала запись на середине.
        dup = chunks + [chunks[0]]
        n3 = lv.add_chunks("rev-0003", dup, vectors)
        check(n3 == 6, "список с дублем принят целиком (возврат — число чанков)",
              f"получено {n3}")
        check(lv.vec_ok and not lv.vec_error,
              "дубль не оборвал запись векторов", lv.vec_error)
        v3 = lv.conn.execute("SELECT COUNT(*) FROM vec_chunk").fetchone()[0]
        check(v3 == 5, "в хранилище по-прежнему 5 уникальных ключей",
              f"получено {v3}")

        # Отказ записи обязан быть назван причиной, а не смолчать.
        bad = {chunks[4].text_hash: [0.01] * 5}
        lv.add_chunks("rev-0004", [chunks[4]], bad)
        check(not lv.vec_ok, "отказ записи переводит vec_ok в False")
        reason = lv.vec_unavailable_reason()
        check("запись в vec0" in reason,
              "причина отказа названа, а не выдана за «не загрузилось»",
              reason)

        lv.close()
    finally:
        shutil.rmtree(db_dir, ignore_errors=True)

    print(f"\nитог: {PASS} ok, {FAIL} fail")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
