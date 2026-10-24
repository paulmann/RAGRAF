"""Обновление таблицы корпусов в отчёте после включения схлопывания.

Числа revoap изменились: 699 → 592 файла, и узел с рёбрами тоже. Отчёт обязан
показывать актуальные числа — иначе он разойдётся с индексом, и это будет
выглядеть как ошибка инструмента.

Скрипт читает числа из индексов и подставляет их в таблицу отчёта.
Также обновляет номера ревизий: они сдвинулись.
"""

from __future__ import annotations

import pathlib
import re
import sys

REPO = pathlib.Path(r"C:\AutoClaw\RAGRAF")
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

print("=" * 78)
print("ФАКТИЧЕСКИЕ ЧИСЛА КОРПУСОВ")
print("=" * 78)
actual: dict[str, tuple] = {}
for config_name, title in CORPORA:
    cfg = load_config(REPO / "config" / config_name)
    conn = store.connect(cfg.graph_db, readonly=True)
    row = conn.execute(
        "SELECT rev_id, file_count, node_count, edge_count FROM rev "
        "WHERE state = 'ready' ORDER BY rev_id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        continue
    actual[title] = (row["rev_id"], row["file_count"], row["node_count"],
                     row["edge_count"])
    print(f"  {title:<22} {row['rev_id']}  файлов {row['file_count']}, "
          f"узлов {row['node_count']}, рёбер {row['edge_count']}")

print()
print("=" * 78)
print("ОБНОВЛЕНИЕ ТАБЛИЦЫ В ОТЧЁТЕ")
print("=" * 78)

for title, (rev, files, nodes, edges) in actual.items():
    # Строка таблицы: | название | rev-NNNN | файлов | узлов | рёбер | языки |
    # Название может быть выделено жирным.
    pattern = re.compile(
        rf"(\| \*{{0,2}}{re.escape(title)}\*{{0,2}} \| )rev-\d{{4}}"
        rf"( \| )\d+( \| )\d+( \| )\d+( \|)"
    )
    new_line = rf"\g<1>{rev}\g<2>{files}\g<3>{nodes}\g<4>{edges}\g<5>"
    text, count = pattern.subn(new_line, text)
    if count:
        print(f"  {title}: обновлено ({rev}, файлов {files}, узлов {nodes}, "
              f"рёбер {edges})")
    else:
        print(f"  {title}: строка таблицы НЕ найдена")

REPORT.write_text(text, encoding="utf-8", newline="\n")
after = REPORT.read_text(encoding="utf-8")
print()
print(f"  размер отчёта: {len(after.encode('utf-8'))} байт")
print(f"  ревизии в отчёте: {sorted(set(re.findall(r'rev-[0-9]{4}', after)))}")

# Проверка: числа revoap в тексте отчёта совпадают с фактом.
rev_line = [ln for ln in after.splitlines() if "revoap.com | rev-" in ln]
for ln in rev_line:
    print(f"\n  строка revoap: {ln[:110]}")
