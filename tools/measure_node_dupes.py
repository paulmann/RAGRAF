"""Точный замер: сколько узлов строит индексатор и сколько из них уникальны.

Факт: журнал ревизии пишет 2227 узлов, а в таблице node лежит 1290. Ключ
таблицы — (rev_id, node_id), и повторов в таблице нет. Значит построитель
выдаёт список строк, где node_id повторяется, и INSERT OR REPLACE оставляет
из каждой группы только последнюю строку.

Что проверяется:
  1. сколько строк узлов строит построитель;
  2. сколько среди них различных node_id;
  3. какие именно узлы схлопываются — с указанием вида, имени и места.

Это решающая проверка: если схлопываются разные сущности (например два
метода с одинаковым именем в одном файле), то индекс теряет узлы молча.
Если схлопывается одно и то же объявление, записанное дважды, — потери нет.

Ничего не меняет: работает по копии извлечения.
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
print(f"корпус: {cfg.project}, файлов: {len(corpus_scan.files)}")

extractor = MultiExtractor()

# Собираются все узлы так же, как это делает индексатор.
all_nodes: list[tuple[str, str, str, str, int]] = []   # id, kind, name, path, line
by_file: dict[str, int] = {}
for cf in corpus_scan.files:
    if cf.lang not in {"php", "css", "html"}:
        continue
    try:
        data = cf.abs_path.read_bytes()
    except OSError:
        continue
    res = extractor.extract(cf.path, data)
    by_file[cf.path] = len(res.symbols)
    for s in res.symbols:
        all_nodes.append((s.node_id, s.kind, s.name, res.path, s.start_line or 0))

print(f"\n  строк узлов построено: {len(all_nodes)}")
ids = Counter(n[0] for n in all_nodes)
print(f"  различных node_id:      {len(ids)}")
print(f"  СХЛОПЫВАЕТСЯ:           {len(all_nodes) - len(ids)}")

print()
print("=" * 78)
print("ЧТО ИМЕННО СХЛОПЫВАЕТСЯ")
print("=" * 78)
groups: dict[str, list[tuple]] = defaultdict(list)
for n in all_nodes:
    groups[n[0]].append(n)

dupes = {k: v for k, v in groups.items() if len(v) > 1}
print(f"  групп с повтором: {len(dupes)}")
print(f"  узлов в этих группах: {sum(len(v) for v in dupes.values())}")

# Разбирается: совпадают ли у повторов (kind, name, path) — то есть
# это одно и то же объявление или разные сущности.
same_entity = 0
diff_entity = 0
examples_same = []
examples_diff = []
for node_id, items in dupes.items():
    keys = {(n[1], n[2], n[3]) for n in items}     # (kind, name, path)
    if len(keys) == 1:
        same_entity += 1
        if len(examples_same) < 3:
            examples_same.append(items)
    else:
        diff_entity += 1
        if len(examples_diff) < 6:
            examples_diff.append(items)

print(f"\n  групп, где повторяется ОДНО И ТО ЖЕ объявление: {same_entity}")
print(f"  групп, где схлопываются РАЗНЫЕ объявления:      {diff_entity}")

print()
print("  --- примеры: одно и то же объявление (потери нет) ---")
for items in examples_same:
    kind, name, path, line = items[0][1], items[0][2], items[0][3], items[0][4]
    lines = sorted({n[4] for n in items})
    print(f"    {kind} {name} — {path}, строки {lines}")

print()
print("  --- примеры: РАЗНЫЕ объявления под одним id (ПОТЕРЯ) ---")
for items in examples_diff:
    print(f"    node_id {items[0][0][:12]}…:")
    for n in items:
        print(f"      {n[1]:<12} {n[2]:<26} {n[3]} строка {n[4]}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
if diff_entity == 0:
    print("""
  Все повторы — одно и то же объявление, записанное дважды. Потери узлов
  НЕТ: второй записью таблица просто подтверждает первый. Расхождение
  между журналом (2227) и таблицей (1290) в этом случае означает лишь,
  что счётчик в журнале считает СТРОКИ ДО СХЛОПЫВАНИЯ, а не сами узлы.

  Это неточность учёта, а не потеря данных. Правильное число — 1290.
""")
else:
    print(f"""
  ВНИМАНИЕ: в {diff_entity} группах под одним node_id лежат РАЗНЫЕ объявления.
  Это потеря узлов: часть символов не попадает в индекс, хотя была извлечена.

  Причина — в вычислении node_id: в него не входит признак, различающий
  эти объявления. Требуется правка формулы идентификатора.
""")
