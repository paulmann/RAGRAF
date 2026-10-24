"""Разделение схлопываний: какие намеренные, а какие — потеря.

Прошлый замер показал 304 группы, где под одним node_id лежат записи из разных
файлов. Но для css_class это НАМЕРЕННОЕ решение: имена классов — общий ключ
без файла (class_node_id(name)), иначе определение в стилях и использование
в разметке не сопоставлялись бы. Это записано в журнале решений проекта.

Настоящая потеря — если схлопываются узлы кода: функции, методы, классы.
Для них путь и строка обязаны входить в идентификатор.

Проверяется раздельно по видам узлов.
"""

from __future__ import annotations

import pathlib
import sys
from collections import Counter, defaultdict

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.corpus import scan  # noqa: E402
from ragraf.extract.css_html import MultiExtractor  # noqa: E402

cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
corpus_scan = scan(cfg)
extractor = MultiExtractor()

rows: list[tuple[str, str, str, str, int]] = []
for cf in corpus_scan.files:
    if cf.lang not in {"php", "css", "html"}:
        continue
    try:
        data = cf.abs_path.read_bytes()
    except OSError:
        continue
    res = extractor.extract(cf.path, data)
    for s in res.symbols:
        rows.append((s.node_id, s.kind, s.name, res.path, s.start_line or 0))

print("=" * 78)
print(f"ВСЕГО строк узлов: {len(rows)}")
print("=" * 78)

by_kind = Counter(r[1] for r in rows)
print(f"\n  {'вид узла':<14} {'строк':>8} {'различных id':>14} {'схлопнуто':>11}")
print("  " + "-" * 50)
for kind in sorted(by_kind, key=lambda k: -by_kind[k]):
    subset = [r for r in rows if r[1] == kind]
    ids = len({r[0] for r in subset})
    collapsed = len(subset) - ids
    print(f"  {kind:<14} {len(subset):>8} {ids:>14} {collapsed:>11}")

print()
print("=" * 78)
print("СХЛОПЫВАНИЯ ПО КОДУ (функции, методы, классы) — здесь потерь быть не должно")
print("=" * 78)

code_kinds = {"function", "method", "class", "interface", "enum", "const",
              "constant", "property", "namespace", "module", "struct"}
groups: dict[str, list[tuple]] = defaultdict(list)
for r in rows:
    groups[r[0]].append(r)

problems = 0
for node_id, items in groups.items():
    if len(items) < 2:
        continue
    kinds = {n[1] for n in items}
    if not (kinds & code_kinds):
        continue          # схлопывание в css_class/css_var: это намеренно
    # Для кода разные (path, line) означают разные объявления.
    keys = {(n[3], n[4]) for n in items}
    if len(keys) > 1:
        problems += 1
        if problems <= 10:
            print(f"\n  node_id {node_id[:14]}… ({', '.join(kinds)}):")
            for n in items:
                print(f"    {n[1]:<12} {n[2]:<26} {n[3]} строка {n[4]}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print(f"""
  Схлопываний в узлах КОДА: {problems}

  Схлопывания классов CSS ({by_kind.get('css_class', 0)} строк → "
  {len({r[0] for r in rows if r[1] == 'css_class'})} узлов) — это задумано:
  имя класса является общим ключом без пути, иначе определение в стилях
  и использование в разметке не сопоставлялись бы. Именно так были найдены
  32 класса z-agr-*, используемых в разметке без правил в CSS.

  Если в узлах кода схлопываний нет — индекс не теряет объявления,
  а расхождение между журналом ревизии и таблицей объясняется только
  разным способом подсчёта (строки против узлов).
""")
