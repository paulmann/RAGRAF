"""Проверка расхождения: отчёт ссылается на ревизию, которой нет в индексе.

Тест `test_report_facts.py` сообщает:
    z.batteryservice.ru: ревизия rev-0150 из отчёта — такой ревизии в индексе нет

Надо понять причину: отчёт устарел или индекс переиндексирован под другим
номером ревизии. Это не связано с моими правками (я работал с корпусом
sagaai), но проверить надо: возможно, корпус z переиндексировался сам.

Проверяется фактическое состояние индекса и текст отчёта.
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402

print("=" * 78)
print("РЕВИЗИИ В ИНДЕКСЕ Z.BATTERYSERVICE.RU")
print("=" * 78)
cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
conn = sqlite3.connect(f"file:{cfg.graph_db}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT rev_id, state, capability, created_at, file_count, node_count "
    "FROM rev ORDER BY rev_id"
).fetchall()
print(f"  всего ревизий: {len(rows)}")
for r in rows:
    print(f"    {r['rev_id']:<12} {str(r['state']):<8} "
          f"{str(r['capability']):<10} файлов {r['file_count']:<5} "
          f"{str(r['created_at'])[:19]}")
latest = rows[-1]["rev_id"] if rows else None
print(f"\n  последняя ревизия: {latest}")
conn.close()

print()
print("=" * 78)
print("ЧТО СКАЗАНО В ОТЧЁТЕ")
print("=" * 78)
report = REPO / "docs" / "RAGRAF-REPORT.md"
text = report.read_text(encoding="utf-8")
import re  # noqa: E402

revs_in_report = sorted(set(re.findall(r"rev-\d{4}", text)))
print(f"  упомянутых ревизий: {len(revs_in_report)}")
print(f"    {revs_in_report}")
for line_no, line in enumerate(text.splitlines(), 1):
    if "rev-0150" in line:
        print(f"\n  строка {line_no}:")
        print(f"    {line.strip()[:150]}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print(f"""
  Последняя ревизия в индексе: {latest}
  В отчёте упомянута:         rev-0150

  Если они различаются — отчёт описывает прежнее состояние корпуса.
  Это ожидаемо после переиндексации: номер ревизии растёт при каждом
  изменении кода корпуса.

  Правка нужна одна: обновить номер ревизии в отчёте (и связанные числа,
  если они изменились). Данные индекса при этом не трогаются.
""")
