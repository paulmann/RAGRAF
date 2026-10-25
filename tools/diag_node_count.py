"""Почему журнал ревизии сообщает больше узлов, чем лежит в таблице.

Установлено: rev-0131 в журнале — 2227 узлов, в таблице node — 1290.
Расхождение 937 (42%). Вставка идёт через INSERT OR REPLACE, значит строки
с совпадающим ключом перезаписываются друг другом. Нужно понять:
  1. какой ключ у таблицы node;
  2. какие именно node_id повторяются внутри одной ревизии;
  3. откуда берётся совпадение — это дефект вычисления идентификатора.

Это диагностика. Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import sys
from collections import Counter

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
conn = store.connect(cfg.graph_db, readonly=True)

print("=" * 78)
print("1. КЛЮЧ ТАБЛИЦЫ node")
print("=" * 78)
sql = conn.execute(
    "SELECT sql FROM sqlite_master WHERE type='table' AND name='node'"
).fetchone()
print(sql["sql"] if sql else "  определения нет")

rev = conn.execute(
    "SELECT rev_id FROM rev WHERE state = 'ready' ORDER BY rev_id DESC LIMIT 1"
).fetchone()["rev_id"]
print(f"\n  ревизия для разбора: {rev}")
print(f"  записано в журнале узлов:      "
      f"{conn.execute('SELECT node_count AS n FROM rev WHERE rev_id = ?', (rev,)).fetchone()['n']}")
print(f"  фактически строк в node:       "
      f"{conn.execute('SELECT COUNT(*) AS n FROM node WHERE rev_id = ?', (rev,)).fetchone()['n']}")
print(f"  различных node_id:             "
      f"{conn.execute('SELECT COUNT(DISTINCT node_id) AS n FROM node WHERE rev_id = ?', (rev,)).fetchone()['n']}")

print()
print("=" * 78)
print("2. ЧТО ЛЕЖИТ ПОД ПОВТОРЯЮЩИМИСЯ КЛЮЧАМИ (следы перезаписи)")
print("=" * 78)
# В таблице остаётся по одной строке на node_id, поэтому «исчезнувшие» узлы
# видны иначе: как ключи, под которыми лежит НЕ то, что ожидалось.
# Проверяется обратное: сколько узлов имеют одинаковые (kind, name, path),
# но разные node_id — это нормально. А вот одинаковый node_id при разных
# значениях — это уже перезапись.
rows = conn.execute(
    "SELECT node_id, COUNT(*) AS n FROM node WHERE rev_id = ? "
    "GROUP BY node_id HAVING n > 1 LIMIT 5",
    (rev,),
).fetchall()
print(f"  ключей с более чем одной строкой в таблице: {len(rows)}")

# Главная проверка: совпадают ли node_id у узлов, которые должны быть разными.
print()
print("  Узлы с одинаковым (path, name), но разным kind:")
dupes = conn.execute(
    """SELECT path, name, COUNT(DISTINCT node_id) AS ids,
              GROUP_CONCAT(DISTINCT kind) AS kinds
       FROM node WHERE rev_id = ?
       GROUP BY path, name HAVING ids > 1 LIMIT 12""",
    (rev,),
).fetchall()
for d in dupes:
    print(f"    {d['path']}:{d['name']} — id {d['ids']}, виды: {d['kinds']}")

print()
print("=" * 78)
print("3. ГЛАВНОЕ: КАК СТРОИТСЯ node_id И МОЖЕТ ЛИ ОН СОВПАСТЬ")
print("=" * 78)
print("""
  Если node_id = stable_id(kind, name, path, строка), совпадение возможно
  только при полном совпадении всех четырёх частей. Тогда два разных
  объявления с одним именем на одной строке — это одно и то же объявление,
  и перезапись безвредна.

  Но расхождение 42% слишком велико для такого случая. Проверяется гипотеза:
  идентификатор считается БЕЗ пути или БЕЗ строки, и тогда одноимённые
  сущности из разных файлов схлопываются в одну — а это уже потеря данных.
""")

# Проверка: сколько пар узлов имеют одинаковые (kind, name) в разных файлах.
pairs = conn.execute(
    """SELECT kind, name, COUNT(DISTINCT path) AS paths
       FROM node WHERE rev_id = ?
       GROUP BY kind, name HAVING paths > 1 ORDER BY paths DESC LIMIT 10""",
    (rev,),
).fetchall()
print("  Одноимённые узлы в разных файлах (должны быть РАЗНЫМИ узлами):")
for p in pairs:
    print(f"    {p['kind']:<12} {p['name']:<28} в {p['paths']} файлах")

total_multi = conn.execute(
    """SELECT COUNT(*) AS n FROM (
         SELECT kind, name FROM node WHERE rev_id = ?
         GROUP BY kind, name HAVING COUNT(DISTINCT path) > 1)""",
    (rev,),
).fetchone()["n"]
print(f"\n  всего таких имён: {total_multi}")

# Проверяется, что у них РАЗНЫЕ node_id: если да — потери нет.
sample = conn.execute(
    """SELECT node_id, kind, name, path FROM node WHERE rev_id = ?
       AND name = ? AND kind = ? """,
    (rev, pairs[0]["name"] if pairs else "", pairs[0]["kind"] if pairs else ""),
).fetchall()
if sample:
    print(f"\n  Пример «{sample[0]['name']}» ({sample[0]['kind']}):")
    ids = set()
    for s in sample:
        print(f"    {s['node_id'][:16]}… {s['path']}")
        ids.add(s["node_id"])
    print(f"    различных id: {len(ids)} из {len(sample)} записей")

conn.close()
