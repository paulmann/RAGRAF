"""Замер распределения времени индексации по стадиям.

Зачем. Перед тем как переносить работы на видеокарту, надо знать, где реально
тратится время. Переносить на GPU стадию, которая занимает 2% времени, бессмысленно
даже если она ускорится в сто раз.

Источник данных: таблица покрытия в графе корпуса — там сохраняются замеры
каждой стадии индексации (scan, ast, load, resolve, schema, cpg, lexvec).
Ничего не меняет: только читает базы.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.catalog import Catalog  # noqa: E402

print("=" * 78)
print("РАСПРЕДЕЛЕНИЕ ВРЕМЕНИ ИНДЕКСАЦИИ ПО СТАДИЯМ")
print("=" * 78)

for name in ("z-batteryservice", "revoap"):
    cfg = load_config(REPO / "config" / f"{name}.yaml")
    conn = store.connect(cfg.graph_db, readonly=True)

    rev = conn.execute(
        "SELECT rev_id FROM rev ORDER BY rev_id DESC LIMIT 1"
    ).fetchone()["rev_id"]

    print(f"\n=== {cfg.project} (ревизия {rev}) ===")

    # Стадии хранятся в таблице покрытия: metric вида stage_<имя>.
    rows = conn.execute(
        "SELECT metric, value, note FROM coverage WHERE rev_id = ?", (rev,)
    ).fetchall()
    stages: dict[str, float] = {}
    for r in rows:
        metric = str(r["metric"])
        if metric.startswith("stage_"):
            stages[metric[len("stage_"):]] = float(r["value"] or 0)

    if not stages:
        # Запасной путь: стадии могут лежать в отдельной таблице.
        try:
            srows = conn.execute(
                "SELECT stage, seconds FROM stage_timing WHERE rev_id = ?", (rev,)
            ).fetchall()
            stages = {str(r["stage"]): float(r["seconds"] or 0) for r in srows}
        except Exception:
            pass

    if not stages:
        print("  замеры стадий не найдены в этой ревизии")
        conn.close()
        continue

    total = sum(stages.values())
    print(f"  суммарно по стадиям: {total:.2f} с")
    print(f"  {'стадия':<12} {'секунд':>8} {'доля':>7}   что это")
    descriptions = {
        "scan": "обход файлов и хеши",
        "ast": "разбор дерева (tree-sitter)",
        "load": "запись узлов и рёбер в базу",
        "resolve": "разрешение внешних имён",
        "schema": "схема БД из миграций",
        "cpg": "граф свойств кода (Joern)",
        "lexvec": "чанки, лексика, ВЕКТОРЫ",
    }
    for stage, seconds in sorted(stages.items(), key=lambda kv: -kv[1]):
        share = seconds / total * 100 if total else 0
        print(f"  {stage:<12} {seconds:>8.2f} {share:>6.1f}%   "
              f"{descriptions.get(stage, '')}")

    # Отдельно: сколько времени внутри lexvec уходит на кодирование векторов.
    for r in rows:
        if str(r["metric"]) in ("vectors", "vectors_reused", "chunks") and r["note"]:
            print(f"  {r['metric']}: {int(r['value'])} ({r['note']})")

    conn.close()

print("\n" + "=" * 78)
print("ЧТО ЭТО ЗНАЧИТ")
print("=" * 78)
print("""
Чтобы решить про GPU, нужно сопоставить два факта:
  1. долю стадии в общем времени (таблица выше);
  2. ускоряется ли эта стадия на видеокарте В ПРИНЦИПЕ.

Стадии, которые на GPU не ускоряются вообще (проверяется отдельно):
  scan   — это операции файловой системы, видеокарта не участвует;
  ast    — разбор текста tree-sitter, это последовательный алгоритм на CPU;
  load   — запись в SQLite, видеокарта не участвует;
  resolve— SQL-запросы и сопоставление имён, видеокарта не участвует;
  schema — разбор SQL-миграций, видеокарта не участвует;
  cpg    — Java-программа Joern, видеокарта не участвует.

Стадия, которая на GPU ускоряется:
  lexvec — внутри неё кодирование текста в векторы (матричные операции нейросети).
           Именно эта часть и есть единственный настоящий кандидат на перенос.
""")
