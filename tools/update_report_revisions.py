"""Обновление номеров ревизий в отчёте.

Причина: корпуса переиндексировались во время проверок (сторож свежести
запускает переиндексацию сам, когда сумма файлов не совпадает). Номера
ревизий сдвинулись, а числа (файлы, узлы, рёбра) при этом совпали —
индексация детерминирована.

Отчёт обязан называть АКТУАЛЬНУЮ ревизию: иначе читатель сверит его
с индексом и увидит расхождение, которого нет.

Скрипт читает текущие ревизии из индексов и заменяет их в тексте отчёта.
"""

from __future__ import annotations

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

REPORT = REPO / "docs" / "RAGRAF-REPORT.md"
text = REPORT.read_text(encoding="utf-8")

CORPORA = [
    ("z-batteryservice.yaml", "z.batteryservice.ru"),
    ("revoap.yaml", "revoap.com"),
    ("md.viewer.yaml", "MD.Viewer"),
    ("rdpmon.yaml", "RdpMON"),
]

# Собираются актуальные ревизии и числа.
actual: dict[str, tuple[str, int, int, int]] = {}
for config_name, title in CORPORA:
    cfg = load_config(REPO / "config" / config_name)
    conn = store.connect(cfg.graph_db, readonly=True)
    row = conn.execute(
        "SELECT rev_id, file_count, node_count, edge_count FROM rev "
        "WHERE state = 'ready' ORDER BY rev_id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if row:
        actual[title] = (row["rev_id"], row["file_count"], row["node_count"],
                         row["edge_count"])
        print(f"  {title:<22} {row['rev_id']}  файлов {row['file_count']}, "
              f"узлов {row['node_count']}, рёбер {row['edge_count']}")

print()
# Находятся все упоминания ревизий вида rev-NNNN и заменяются на актуальные.
# Соответствие определяется по порядку: в отчёте таблица идёт в том же
# порядке, что список CORPORA.
found = re.findall(r"rev-\d{4}", text)
print(f"  ревизий в отчёте: {len(found)} — {sorted(set(found))}")

changed = 0
for title, (rev, files, nodes, edges) in actual.items():
    # В отчёте каждая ревизия встречается в строке таблицы рядом с названием
    # корпуса. Заменяется та, что стоит в строке с этим названием.
    pattern = re.compile(rf"(\| {re.escape(title)} \| )rev-\d{{4}}")
    new_text, count = pattern.subn(rf"\g<1>{rev}", text)
    if count:
        text = new_text
        changed += count
        print(f"  {title}: заменено {count} раз на {rev}")

# Отдельно: раздел 3 отчёта перечисляет ревизии вне таблицы.
for old in sorted(set(found)):
    if old in text:
        print(f"  осталось упоминание {old}")

REPORT.write_text(text, encoding="utf-8", newline="\n")
after = REPORT.read_text(encoding="utf-8")
print()
print(f"  заменено вхождений: {changed}")
print(f"  размер отчёта: {len(after.encode('utf-8'))} байт")
remaining = sorted(set(re.findall(r"rev-\d{4}", after)))
print(f"  ревизии в отчёте после правки: {remaining}")
