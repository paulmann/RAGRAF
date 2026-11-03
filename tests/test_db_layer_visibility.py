# -*- coding: utf-8 -*-
"""Проверка этапа 15: пустой живой слой баз не остаётся без причины.

Замер 01.11.2026. Ревизии rev-1193, rev-1195, rev-1204 и rev-1221 не имели
живого слоя баз. Причину узнать было НЕЛЬЗЯ: `_collect_databases` её
возвращала, но в запись ревизии она не попадала — стадия `databases` писалась
без примечания, и `index_status` показывал `items=0` без объяснения. «Слой
пуст» и «сбор выключен» выглядели одинаково, хотя это разные состояния, и
разбирать их приходилось вручную.

Найденный механизм: живой доступ берётся из каталога состояния, а если он
отведён переменной `RAGRAF_CATALOG_DIR` (песочница, второй профиль), то
`db_access.json` там нет, и доступ «выключен» не решением, а окружением.

Проверяются три свойства:
  1. прогон с отведённым каталогом состояния записывает причину в
     `stage_timing.note` — и причина называет переменную, а не молчит;
  2. причина остаётся В ЗАПИСИ ревизии (читается из хранилища после закрытия),
     а не только в отчёте прогона;
  3. собранный слой не даёт примечания и показывает число таблиц — иначе
     подсказка стала бы шумом в каждой индексации.

Стиль — самостоятельный скрипт с `check`, как остальные проверки проекта.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"

PASS = 0
FAIL = 0


def check(condition: bool, title: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK ] {title}")
    else:
        FAIL += 1
        print(f"  [FAIL] {title}" + (f"\n      {detail}" if detail else ""))


PHP = """<?php
function probe_%(i)d($x) {
    return $x + %(i)d;
}
"""


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ragraf_dblayer_"))
    # Каталог состояния отводится ДО импорта: db_access читает его на импорте,
    # ровно как в прогоне, который оставил ревизии без слоя.
    catalog = tmp / "catalog"
    catalog.mkdir()
    os.environ["RAGRAF_CATALOG_DIR"] = str(catalog)
    os.environ["RAGRAF_HOME"] = str(tmp / "home")
    os.environ["RAGRAF_CONFIG_DIR"] = str(tmp / "config")

    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))

    from ragraf.config import load as load_config  # noqa: E402
    from ragraf.indexer import Indexer, describe_db_layer  # noqa: E402
    from ragraf.init_corpus import create_config  # noqa: E402

    try:
        corpus = tmp / "corpus"
        (corpus / "src").mkdir(parents=True)
        (corpus / ".gitignore").write_text("probe\n", encoding="utf-8")
        for i in range(3):
            (corpus / "src" / f"m{i}.php").write_text(PHP % {"i": i}, encoding="utf-8")

        cfg_path, _scan = create_config(corpus, project="dblayerprobe",
                                       config_path=corpus / "corpus.yaml")
        text = cfg_path.read_text(encoding="utf-8").replace(
            "joern_enabled: true", "joern_enabled: false")
        cfg_path.write_text(text, encoding="utf-8", newline="\n")
        cfg = load_config(cfg_path)

        print("=== 1. Отведённый каталог состояния: причина названа ===")
        idx = Indexer(cfg, mode="fast")
        try:
            report = idx.run(with_vectors=False)
        finally:
            idx.close()
        db_stage = next((st for st in report.stages if st["stage"] == "databases"), None)
        print(f"  отчёт: ok={report.ok} rev={report.rev_id} "
              f"databases={report.databases}")
        check(report.ok, "прогон завершён", f"ok={report.ok}")
        check(db_stage is not None, "стадия databases записана",
              str([st["stage"] for st in report.stages]))
        note = (db_stage or {}).get("note") or ""
        print(f"  примечание стадии: {note[:160]}")
        check(bool(note), "у стадии есть примечание, а не пустое items=0")
        check("не собран" in note, "причина названа словами", note[:200])
        check("RAGRAF_CATALOG_DIR" in note,
              "причина называет отведённый каталог состояния — найденный механизм",
              note[:200])
        check(int((db_stage or {}).get("items") or 0) == 0,
              "число таблиц нулевое — слой действительно не собран",
              str((db_stage or {}).get("items")))

        print("\n=== 2. Причина осталась в записи ревизии ===")
        idx2 = Indexer(cfg, mode="fast")
        try:
            rows = idx2.graph.conn.execute(
                "SELECT note FROM stage_timing WHERE rev_id = ? AND stage = 'databases'",
                (report.rev_id,),
            ).fetchall()
        finally:
            idx2.close()
        stored = (rows[0][0] if rows else "") or ""
        check(bool(rows), "запись стадии в хранилище есть", str(len(rows)))
        check(stored == note, "в хранилище лежит то же примечание",
              f"хранилище={stored[:120]!r} отчёт={note[:120]!r}")

        print("\n=== 3. Собранный слой не даёт примечания ===")

        class _Collected(Indexer):
            def _collect_databases(self, rev_id):  # noqa: D401 — подмена стадии
                return {"collected": True, "graph": {"tables": 7}}

        idx3 = _Collected(cfg, mode="fast")
        try:
            ok_report = idx3.run(with_vectors=False)
        finally:
            idx3.close()
        db3 = next((st for st in ok_report.stages if st["stage"] == "databases"), None)
        print(f"  стадия: {db3}")
        check(db3 is not None, "стадия databases записана и при собранном слое")
        check(not (db3 or {}).get("note"), "примечания нет — подсказка не шумит",
              str((db3 or {}).get("note")))
        check(int((db3 or {}).get("items") or 0) == 7,
              "число таблиц взято из собранного слоя", str((db3 or {}).get("items")))

        print("\n=== 4. Пояснение различает причину ===")
        msg_sandbox = describe_db_layer({"collected": False,
                                        "reason": "сбор баз выключен в настройках"})
        check("RAGRAF_CATALOG_DIR" in (msg_sandbox or ""),
              "при отведённом каталоге названа переменная", str(msg_sandbox)[:160])
        saved = os.environ.pop("RAGRAF_CATALOG_DIR")
        try:
            msg_normal = describe_db_layer({"collected": False, "reason": "нет доступа"})
        finally:
            os.environ["RAGRAF_CATALOG_DIR"] = saved
        check("RAGRAF_CATALOG_DIR" not in (msg_normal or ""),
              "без отвода переменная не приписана", str(msg_normal)[:160])
        check("пересоберите" in (msg_normal or ""),
              "в обычном случае названо, что делать", str(msg_normal)[:160])
        check(describe_db_layer({"collected": True, "graph": {"tables": 3}}) is None,
              "собранный слой пояснения не требует")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("=" * 78)
    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
    print("=" * 78)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
