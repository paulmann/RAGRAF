"""Политика хранения `.ragraf`: свойства, а не описание (этап 9).

Что проверяется — шесть свойств, каждое против независимого источника:

  1. `--plan` (функция `storage.plan`) ничего не пишет: размеры, время правки
     и число строк в хранилищах до и после совпадают;
  2. политика удаляет ТОЛЬКО хвост: строки ревизии, которой нет в истории,
     исчезают, а строки хранимых ревизий остаются поштучно теми же;
  3. активная ревизия цела И по записи, И по строкам: числа файлов/узлов/рёбер
     и количество строк по каждой таблице совпадают до и после;
  4. повторный прогон идемпотентен: удалять нечего, освобождено 0 байт,
     числа не изменились;
  5. удалённая ревизия не находится поиском: до очистки лексический поиск по ней
     находит маркер, после — не находит;
  6. подсадки ловятся: (а) если строки активной ревизии пропали, очистка
     отказывает `StoragePolicyError`; (б) если хвост попал в список хранения,
     отказ происходит ДО первого удаления.

Отдельный раздел — артефакты CPG: удаляются только артефакты ревизий вне
хранения, артефакт активной ревизии и имя, не разобранное как ревизия,
остаются.

Стиль — самостоятельный скрипт с именами `check`, как остальные проверки
проекта: `python tests/test_storage_policy.py`, код 1 при провале.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import storage as storage_mod  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.indexer import Indexer  # noqa: E402
from ragraf.init_corpus import create_config  # noqa: E402

PASS = 0
FAIL = 0

MARKER = "zzzorphanmarker"
#: Идентификаторы хвоста взяты ВНЕ диапазона песочницы: здесь первая же ревизия
#: называется rev-0001, и подсев под этим именем проверял бы не хвост, а
#: хранимую ревизию — тест был бы ложным (измерено при первом прогоне).
ORPHAN = "rev-9001"
ORPHAN_CPG = "rev-9002"
ORPHAN_SECOND = "rev-9003"

PHP = """<?php
function probe_%(i)d($x) {
    return $x + %(i)d;
}
"""


def check(condition: bool, title: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK ] {title}")
    else:
        FAIL += 1
        print(f"  [FAIL] {title}" + (f"\n      {detail}" if detail else ""))


def build_corpus(tmp: Path) -> object:
    """Песочный корпус: четыре файла, Joern выключен, векторы не считаются."""
    src = tmp / "src"
    src.mkdir()
    for i in range(4):
        (src / f"mod{i}.php").write_text(PHP % {"i": i}, encoding="utf-8", newline="\n")
    (tmp / ".gitignore").write_text("probe\n", encoding="utf-8", newline="\n")
    cfg_path, _scan = create_config(tmp, project="storeprobe",
                                    config_path=tmp / "store.yaml")
    text = cfg_path.read_text(encoding="utf-8").replace(
        "joern_enabled: true", "joern_enabled: false")
    cfg_path.write_text(text, encoding="utf-8", newline="\n")
    return load_config(cfg_path)


def index(cfg) -> object:
    indexer = Indexer(cfg)
    try:
        return indexer.run(with_vectors=False, budget_seconds=300)
    finally:
        indexer.close()


def files_state(paths: list[Path]) -> dict:
    """Размер и время правки — то, что меняется при любой записи."""
    state = {}
    for path in paths:
        try:
            stat = path.stat()
            state[str(path.name)] = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            state[str(path.name)] = None
    return state


def row_counts(cfg) -> dict:
    """Число строк в таблицах с `rev_id`, по хранилищам (включая WAL-состояние)."""
    out: dict = {}
    for label, path_value in (("graph", cfg.graph_db), ("lexvec", cfg.lexvec_db)):
        path = Path(path_value)
        if not path.is_file():
            continue
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            for table in storage_mod.tables_with_rev(conn):
                try:
                    out[f"{label}.{table}"] = int(
                        conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                except sqlite3.Error:
                    continue
        finally:
            conn.close()
    return out


def inject_orphans(cfg, rev_id: str, source_rev: str) -> None:
    """Подсеять строки ревизии, которой нет в истории, — состояние дефекта.

    Копируются ВСЕ строки исходной ревизии из каждой таблицы с `rev_id`,
    включая FTS-таблицу: иначе «поиск не находит» проверялся бы на данных,
    которых поиск и не касается.
    """
    for path_value in (cfg.graph_db, cfg.lexvec_db):
        path = Path(path_value)
        if not path.is_file():
            continue
        conn = sqlite3.connect(str(path))
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            for table in storage_mod.tables_with_rev(conn):
                columns = [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")]
                select = ", ".join(
                    "?" if name == "rev_id" else name for name in columns)
                try:
                    conn.execute(
                        f"INSERT INTO {table} ({', '.join(columns)}) "
                        f"SELECT {select} FROM {table} WHERE rev_id = ?",
                        (rev_id, source_rev),
                    )
                except sqlite3.Error:
                    continue
            for table, column in (("chunk_text", "text"), ("document_chunk", "text")):
                try:
                    conn.execute(
                        f"UPDATE {table} SET {column} = {column} || ' {MARKER}' WHERE rev_id = ?",
                        (rev_id,))
                except sqlite3.Error:
                    continue
            try:
                conn.execute(
                    "UPDATE ft_chunk SET text_tokens = ? WHERE rev_id = ?", (MARKER, rev_id))
            except sqlite3.Error:
                pass
            conn.commit()
        finally:
            conn.close()


def lexical_hits(cfg, rev_id: str) -> int:
    """Сколько находит поиск по ревизии — тем же предикатом, что у продукта."""
    path = Path(cfg.lexvec_db)
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM ft_chunk WHERE rev_id = ? AND ft_chunk MATCH ?",
            (rev_id, MARKER)).fetchone()
        return int(row[0])
    except sqlite3.Error:
        return -1
    finally:
        conn.close()


def make_artifacts(cfg, orphan_rev: str, keep_rev: str) -> dict:
    """Создать артефакты CPG: хвост, активная ревизия, неопознанное имя."""
    workdir = Path(cfg.ragraf_dir) / "cpg"
    (workdir / "workspace").mkdir(parents=True, exist_ok=True)
    (workdir / f"{orphan_rev}.cpg.bin").write_bytes(b"x" * 4096)
    packed = workdir / "workspace" / f"{orphan_rev}.cpg.bin"
    packed.mkdir(parents=True, exist_ok=True)
    (packed / "cpg.bin").write_bytes(b"y" * 8192)
    export = workdir / f"export_{orphan_rev}_neo4jcsv"
    export.mkdir(parents=True, exist_ok=True)
    (export / "nodes.csv").write_text("id\n1\n", encoding="utf-8", newline="\n")
    (workdir / f"{keep_rev}.cpg.bin").write_bytes(b"z" * 1024)
    (workdir / "probe-test.cpg.bin").write_bytes(b"")
    return {
        "orphan_bin": workdir / f"{orphan_rev}.cpg.bin",
        "orphan_packed": packed,
        "orphan_export": export,
        "keep_bin": workdir / f"{keep_rev}.cpg.bin",
        "unknown": workdir / "probe-test.cpg.bin",
    }


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ragraf_storage_"))
    try:
        cfg = build_corpus(tmp)
        first = index(cfg)
        check(first.ok, "корпус проиндексирован", str(first.notes))
        rev = str(first.rev_id)
        (tmp / "src" / "mod0.php").write_text(
            PHP % {"i": 100} + "// правка\n", encoding="utf-8", newline="\n")
        second = index(cfg)
        check(second.ok, "корпус переиндексирован", str(second.notes))
        active = str(second.rev_id)

        print("\n=== 1. `--plan` ничего не пишет ===")
        watch = [Path(cfg.graph_db), Path(cfg.lexvec_db), Path(cfg.control_db)]
        before_state = files_state(watch)
        before_rows = row_counts(cfg)
        planned = storage_mod.plan(cfg, retain=2)
        check(files_state(watch) == before_state,
              "файлы хранилищ не изменились (размер и время правки)")
        check(row_counts(cfg) == before_rows, "число строк не изменилось")
        check(planned["orphans"]["rows_total"] == 0,
              "в чистом хранилище хвоста нет", str(planned["orphans"]["rows"]))

        print("\n=== 2. Политика удаляет ТОЛЬКО хвост ===")
        inject_orphans(cfg, ORPHAN, active)
        planned = storage_mod.plan(cfg, retain=2)
        check(planned["orphans"]["revisions"] == 1,
              "подсеянная ревизия названа хвостом", str(planned["orphans"]["revision_ids"]))
        check(planned["orphans"]["rows_total"] > 0,
              "строки хвоста посчитаны", str(planned["orphans"]["rows_total"]))
        kept_before = {key: value for key, value in row_counts(cfg).items()}
        active_store_before = storage_mod.rev_store_counts(cfg, active)
        older_store_before = storage_mod.rev_store_counts(cfg, rev)
        hits_before = lexical_hits(cfg, ORPHAN)
        check(hits_before > 0, "до очистки хвост находится поиском", str(hits_before))

        result = storage_mod.apply(cfg, retain=2)
        check(bool(result.get("ok")), "очистка выполнена", str(result.get("error")))
        check(result.get("orphans_left") == [], "хвоста не осталось",
              str(result.get("orphans_left")))
        leftovers = 0
        for label, path_value in (("graph", cfg.graph_db), ("lexvec", cfg.lexvec_db)):
            conn = sqlite3.connect(f"file:{Path(path_value).as_posix()}?mode=ro", uri=True)
            try:
                for table in storage_mod.tables_with_rev(conn):
                    leftovers += int(conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE rev_id = '{ORPHAN}'").fetchone()[0])
            finally:
                conn.close()
        check(leftovers == 0, "строк подсеянной ревизии не осталось", str(leftovers))

        kept_after = row_counts(cfg)
        older_after = storage_mod.rev_store_counts(cfg, rev)
        check(older_after == older_store_before,
              "строки хранимой (не активной) ревизии не изменились поштучно",
              str({k: (older_store_before.get(k), older_after.get(k))
                   for k in older_store_before if older_store_before.get(k) != older_after.get(k)})[:240])
        check(kept_after.get("lexvec.chunk_map", 0) > 0, "поисковый слой не опустел")

        print("\n=== 3. Активная ревизия цела по числам и по строкам ===")
        check(result["active"]["before"] == result["active"]["after"],
              "числа ревизии до и после совпадают", str(result["active"]))
        check(result["active_store"]["before"] == result["active_store"]["after"],
              "количество строк активной ревизии по таблицам совпадает")

        print("\n=== 4. Повторный прогон идемпотентен ===")
        again = storage_mod.apply(cfg, retain=2)
        check(again.get("removed_rows") == {}, "удалять нечего", str(again.get("removed_rows")))
        check(float(again.get("freed_total_mb") or 0) == 0.0,
              "освобождено 0 МБ", str(again.get("freed_total_mb")))
        check(row_counts(cfg) == kept_after, "числа после повтора те же")

        print("\n=== 5. Удалённая ревизия не находится поиском ===")
        check(lexical_hits(cfg, ORPHAN) == 0,
              "поиск по удалённой ревизии пуст", str(lexical_hits(cfg, ORPHAN)))

        print("\n=== 6. Артефакты CPG: удаляется только хвост ===")
        paths = make_artifacts(cfg, ORPHAN_CPG, active)
        planned = storage_mod.plan(cfg, retain=2)
        check(planned["cpg"]["remove_revs"] == 1,
              "к удалению названа одна ревизия", str(planned["cpg"]["remove_revs"]))
        check(active in planned["cpg"]["keep_revs"],
              "артефакт активной ревизии в списке хранения", str(planned["cpg"]["keep_revs"]))
        storage_mod.apply(cfg, retain=2)
        check(not paths["orphan_bin"].exists(), "файл CPG хвоста удалён")
        check(not paths["orphan_packed"].exists(), "распакованный каталог Joern удалён")
        check(not paths["orphan_export"].exists(), "выгрузка neo4j удалена")
        check(paths["keep_bin"].exists(), "файл CPG активной ревизии на месте")
        check(paths["unknown"].exists(),
              "неопознанное имя (probe-test.cpg.bin) не удалено")

        print("\n=== 7. Подсадки ловятся ===")
        # (а) очистка удаляет строку ХРАНИМОЙ ревизии — обязана отказать.
        inject_orphans(cfg, ORPHAN_SECOND, active)
        real_purge = storage_mod._purge_revisions

        def damaging_purge(cfg_arg, revs, **kwargs):
            """Подсадка «удалили лишнее»: к хвосту добавляются строки активной ревизии."""
            result = real_purge(cfg_arg, revs, **kwargs)
            conn = sqlite3.connect(str(cfg_arg.graph_db))
            try:
                conn.execute(
                    "DELETE FROM node WHERE rev_id = ? AND rowid IN "
                    "(SELECT rowid FROM node WHERE rev_id = ? LIMIT 1)", (active, active))
                conn.commit()
            finally:
                conn.close()
            return result

        storage_mod._purge_revisions = damaging_purge  # type: ignore[assignment]
        try:
            refused = False
            message = ""
            try:
                storage_mod.apply(cfg, retain=2, vacuum=False)
            except storage_mod.StoragePolicyError as error:
                refused = True
                message = str(error)
            check(refused, "удаление строк живой ревизии поймано", message[:200])
            check("строки активной ревизии" in message,
                  "отказ называет причину словами", message[:200])
        finally:
            storage_mod._purge_revisions = real_purge  # type: ignore[assignment]

        # (б) хвост попал в список хранения — отказ ДО удаления.
        inject_orphans(cfg, ORPHAN_SECOND, active)
        real_plan = storage_mod.plan

        def broken_plan(*args, **kwargs):
            data = real_plan(*args, **kwargs)
            tail = data["orphans"]["revision_ids"]
            if tail:
                data["revisions_kept"] = list(data["revisions_kept"]) + [tail[0]]
            return data
        storage_mod.plan = broken_plan  # type: ignore[assignment]
        try:
            refused = False
            message = ""
            try:
                storage_mod.apply(cfg, retain=2, vacuum=False)
            except storage_mod.StoragePolicyError as error:
                refused = True
                message = str(error)
            check(refused, "хвост в списке хранения пойман до удаления", message[:200])
            conn = sqlite3.connect(f"file:{Path(cfg.lexvec_db).as_posix()}?mode=ro", uri=True)
            try:
                rows = int(conn.execute(
                    "SELECT COUNT(*) FROM ft_chunk WHERE rev_id = ?",
                    (ORPHAN_SECOND,)).fetchone()[0])
            finally:
                conn.close()
            check(rows > 0, "при отказе ничего не удалено", str(rows))
        finally:
            storage_mod.plan = real_plan  # type: ignore[assignment]

        # --- Отпечаток снимка статуса: все хранилища и их WAL ---
        #
        # Измеренный дефект (31.10.2026): в отпечатке было только два
        # хранилища, а третье (`control.sqlite`, этап 9) отсутствовало, и WAL
        # не учитывался. Ответ `index_status` называл размер control
        # 6 541 312 байт при фактических 6 586 368 — расхождение нашёл
        # `tools/check_storage_disclosure.py`. Свойство проверяется здесь,
        # потому что это политика хранения, а не граф.
        from ragraf.facade import Facade as _Facade

        snapshot_facade = _Facade(cfg, tool="selftest", telemetry=False, watch=False)
        try:
            stamp = snapshot_facade._status_storages_stamp()
        finally:
            snapshot_facade.close()
        names = set(stamp)
        check(any(name.startswith("control.sqlite") for name in names),
              "отпечаток снимка включает третье хранилище (control)", str(sorted(names)))
        check(any(name.endswith("-wal") for name in names),
              "отпечаток снимка включает WAL: правка в WAL иначе незаметна",
              str(sorted(names)))
        check(all(isinstance(value, list) and len(value) == 2
                  for value in stamp.values() if value),
              "отпечаток сравнивает и время, и размер, а не одно из них",
              str(stamp))

        print(f"\nИТОГ: пройдено {PASS}, провалено {FAIL}")
        return 0 if FAIL == 0 else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
