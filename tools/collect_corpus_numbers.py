"""Актуальные числа всех корпусов — основа для правки отчёта.

Отчёт ссылался на rev-0150, а индекс уже rev-0155: сторож свежести
переиндексировал корпуса. Тест `test_report_facts.py` верно это поймал.

Здесь собираются фактические числа по каждому корпусу: ревизия, файлы,
узлы, рёбра. Эти числа пойдут в отчёт.

Дополнительно проверяется, не изменили ли мои правки в слое CPG
числа PHP-корпусов: правки касались выбора языка и нормализации имён.
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402

CORPORA = [
    ("z-batteryservice.yaml", "z.batteryservice.ru"),
    ("revoap.yaml", "revoap.com"),
    ("md.viewer.yaml", "MD.Viewer"),
    ("rdpmon.yaml", "RdpMON"),
    ("clean_bom_senior.yaml", "Clean_BOM_Senior"),
    ("sagaai.yaml", "sagaai"),
]

print("=" * 78)
print("АКТУАЛЬНЫЕ ЧИСЛА КОРПУСОВ")
print("=" * 78)
print(f"\n  {'корпус':<24} {'ревизия':<12} {'файлов':>7} {'узлов':>8} "
      f"{'рёбер':>8} {'capability':<10}")
print("  " + "-" * 76)

results: list[dict] = []
for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        print(f"  {title:<24} конфига нет")
        continue
    cfg = load_config(path)
    if not pathlib.Path(str(cfg.graph_db)).exists():
        print(f"  {title:<24} индекса нет")
        continue
    conn = sqlite3.connect(f"file:{cfg.graph_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT rev_id, capability, file_count, node_count, edge_count "
        "FROM rev WHERE state = 'ready' ORDER BY rev_id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        print(f"  {title:<24} готовых ревизий нет")
        conn.close()
        continue
    # Числа берутся ИЗ ТАБЛИЦ, а не из полей ревизии: поля могут
    # расходиться с содержимым (это уже находилось ранее).
    rev = row["rev_id"]
    nodes = conn.execute(
        "SELECT COUNT(*) AS n FROM node WHERE rev_id = ?", (rev,)
    ).fetchone()["n"]
    edges = conn.execute(
        "SELECT COUNT(*) AS n FROM edge WHERE rev_id = ?", (rev,)
    ).fetchone()["n"]
    files = conn.execute(
        "SELECT COUNT(*) AS n FROM file WHERE rev_id = ?", (rev,)
    ).fetchone()["n"] if conn.execute(
        "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' "
        "AND name='file'"
    ).fetchone()["n"] else row["file_count"]
    conn.close()

    print(f"  {title:<24} {rev:<12} {files:>7} {nodes:>8} {edges:>8} "
          f"{str(row['capability']):<10}")
    results.append({
        "title": title, "rev": rev, "files": files,
        "nodes": nodes, "edges": edges,
        "capability": str(row["capability"]),
    })

print()
print("=" * 78)
print("ЧИСЛА ДЛЯ ОТЧЁТА")
print("=" * 78)
for r in results:
    print(f"  | {r['title']} | {r['rev']} | {r['files']} | {r['nodes']} | "
          f"{r['edges']} |")

print()
print("=" * 78)
print("СВЕРКА С ОТЧЁТОМ")
print("=" * 78)
report = REPO / "docs" / "RAGRAF-REPORT.md"
text = report.read_text(encoding="utf-8")
import re  # noqa: E402

# Ищется строка таблицы корпусов в отчёте.
for r in results:
    # В отчёте строки вида: | имя | rev-XXXX | файлов | узлов | рёбер | языки |
    pattern = re.compile(
        r"\|\s*" + re.escape(r["title"]) + r"\s*\|\s*(rev-\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)"
    )
    match = pattern.search(text)
    if match:
        rev_rep, files_rep, nodes_rep, edges_rep = match.groups()
        same = (rev_rep == r["rev"] and int(files_rep) == r["files"]
                and int(nodes_rep) == r["nodes"]
                and int(edges_rep) == r["edges"])
        mark = "совпадает" if same else "РАСХОЖДЕНИЕ"
        print(f"  {r['title']}:")
        print(f"    отчёт:   {rev_rep} {files_rep} {nodes_rep} {edges_rep}")
        print(f"    индекс:  {r['rev']} {r['files']} {r['nodes']} {r['edges']}"
              f"   → {mark}")
    else:
        print(f"  {r['title']}: строки в отчёте не найдено")

print()
print("=" * 78)
print("ИЗМЕНИЛИ ЛИ МОИ ПРАВКИ PHP-КОРПУСА")
print("=" * 78)
print("""
  Прежние числа (из контекста задачи):
    z.batteryservice.ru  rev-0150  162 файла  1352 узла  13 702 ребра
    revoap.com           rev-0034  592 файла  5902 узла  55 116 рёбер
    MD.Viewer            rev-0008   15 файлов   658 узлов  3774 ребра
    RdpMON               rev-0006 1266 файлов 20 131 узел 97 041 ребро
""")
for r in results:
    print(f"  {r['title']:<24} сейчас: {r['rev']} {r['files']} файлов "
          f"{r['nodes']} узлов {r['edges']} рёбер")
