"""Числа в реестре берутся из ЗАПЕЧАТАННОЙ ревизии, а не из отчёта индексатора.

Стиль как у остальных проверок проекта: самостоятельный скрипт с функцией
`check`, запускается напрямую (`python tests/test_registry_counts.py`).

Какой дефект закрывается. Замер 06.11.2026 на корпусе `default-workspace`:

  * запись ревизии `rev-0013` — 477 файлов, 2 233 узла, 34 306 рёбер,
    и ровно столько же строк лежало в таблицах `node`/`edge` (сверено
    запросом `count(*)` к `graph.sqlite`);
  * реестр (`catalog/catalog.json`) для той же ревизии — 4 595 узла и
    125 828 рёбер, то есть числа ОТЧЁТА индексатора, посчитанные до
    схлопывания повторов.

Реестр — единственное место, откуда панель и операция `catalog` берут состояние
разработки. Расхождение читается как «в индексе втрое больше рёбер, чем есть»,
и именно оно стояло в отчёте как «не расследовано».

Проверки:
  1. `record_index_run` записывает числа ревизии, даже если отчёт называет другие;
  2. отчёт используется только тогда, когда ревизии ещё нет (первый прогон);
  3. запись реестра не теряет соседнюю разработку, записанную параллельно
     (был дефект: `save()` читал файл один раз и затирал чужую запись).

Ничего не меняет за пределами временного каталога: реестр и хранилища создаются
во временном каталоге и удаляются.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragraf import catalog as catalog_mod  # noqa: E402
from ragraf import config as config_mod  # noqa: E402
from ragraf import store as store_mod  # noqa: E402
from ragraf import util  # noqa: E402
from ragraf import watch as watch_mod  # noqa: E402

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


def make_cfg(root: Path) -> object:
    """Настоящий конфиг корпуса (не подделка): `current_hashes` читает `scan`."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "main.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
    cfg_path = root / "ragraf.yaml"
    cfg_path.write_text(
        f"""project: {root.name}
root: {util.to_posix(root)}

languages:
  - python

include:
  - "**/*.py"

exclude:
  - "**/.ragraf/**"

db:
  enabled: false

embedding:
  model: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
  dim: 384

index:
  joern_enabled: false
  budget_seconds: 0
""",
        encoding="utf-8",
        newline="\n",
    )
    return config_mod.load(cfg_path)


def seal_revision(cfg, rev_id: str, *, files: int,
                  nodes: int, edges: int) -> None:
    """Записать ЗАПЕЧАТАННУЮ ревизию с названными числами."""
    conn = store_mod.connect(cfg.graph_db)
    store_mod.init_graph(conn)
    conn.execute(
        "INSERT INTO rev (rev_id, created_at, state, capability, file_count,"
        " node_count, edge_count) VALUES (?,?,?,?,?,?,?)",
        (rev_id, "2026-11-06T00:00:00+03:00", "ready", "full", files, nodes,
         edges),
    )
    conn.commit()
    conn.close()


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        state = base / "state"
        state.mkdir()
        (state / "catalog.json").write_text(
            json.dumps({"version": 1, "entries": {}}), encoding="utf-8")
        # Реестр операции берут из paths.state_file: подменяем каталог состояния.
        import os

        os.environ["RAGRAF_CATALOG_DIR"] = str(state)

        root = base / "corpus"
        cfg = make_cfg(root)
        seal_revision(cfg, "rev-0007", files=3, nodes=11, edges=97)

        print("=== 1. Числа из ревизии, а не из отчёта ===")
        sealed = watch_mod._sealed_counts(cfg)
        check(sealed == (3, 11, 97, "full"), "числа прочитаны из ревизии", str(sealed))

        # Отчёт индексатора заведомо врёт: так он и вёл себя на живом корпусе.
        report = SimpleNamespace(rev_id="rev-0007", ok=True, files=999,
                                 nodes=4595, edges=125828, seconds=240.0,
                                 capability="full")
        watch_mod.record_index_run(cfg, report, trigger="test:counts")

        entry = catalog_mod.Catalog().get("corpus")
        check(entry is not None, "запись разработки создана")
        if entry is not None:
            check((entry.file_count, entry.node_count, entry.edge_count) == (3, 11, 97),
                  "в реестре числа ревизии (3/11/97), а не отчёта (999/4595/125828)",
                  f"файлов={entry.file_count} узлов={entry.node_count} рёбер={entry.edge_count}")
            check(entry.capability == "full", "режим взят из ревизии",
                  str(entry.capability))

        print("\n=== 2. Без ревизии используется отчёт ===")
        root2 = base / "corpus2"
        cfg2 = make_cfg(root2)
        report2 = SimpleNamespace(rev_id="rev-0001", ok=True, files=2, nodes=5,
                                  edges=13, seconds=1.0, capability="degraded")
        watch_mod.record_index_run(cfg2, report2, trigger="test:first")
        entry2 = catalog_mod.Catalog().get("corpus2")
        check(entry2 is not None and (entry2.node_count, entry2.edge_count) == (5, 13),
              "первый прогон без ревизии записан по отчёту",
              "" if entry2 is None else f"{entry2.node_count}/{entry2.edge_count}")

        print("\n=== 3. Параллельная запись не теряется ===")
        # Два экземпляра реестра: второй читает файл ДО записи первого и пишет
        # после. Раньше второй затирал первую запись своей копией файла.
        first = catalog_mod.Catalog()
        second = catalog_mod.Catalog()
        e_first = first.get("corpus")
        e_second = second.get("corpus2")
        check(e_first is not None and e_second is not None, "обе записи прочитаны")
        if e_first is not None and e_second is not None:
            e_second.project = "правка-соседа"
            second.put(e_second)                 # вторая запись поверх файла
            e_first.project = "правка-первого"
            first.put(e_first)                   # первая пишет свою (устаревшую копию)
            final = catalog_mod.Catalog().get("corpus2")
            check(final is not None and final.project == "правка-соседа",
                  "запись соседа уцелела", "" if final is None else final.project)

        os.environ.pop("RAGRAF_CATALOG_DIR", None)

    print("\n=== ИТОГ ===")
    print(f"пройдено: {PASS}, провалено: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
