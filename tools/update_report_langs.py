"""Обновление списка языков в таблице отчёта.

В строке revoap остался список языков от состояния ДО схлопывания
(`text 201`, `md 59` и так далее). После отбрасывания 107 копий документов
числа по языкам изменились, и таблица врёт.

Скрипт читает состав языков из индекса и подставляет его в таблицу.
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

# Языки, которых в индексе не было до этой сессии: отмечаются жирным.
NEW_LANGS = {"powershell", "bash", "batch", "csharp", "perl", "python",
             "javascript", "typescript", "yaml", "json", "ini", "text", "xml"}

for config_name, title in CORPORA:
    cfg = load_config(REPO / "config" / config_name)
    conn = store.connect(cfg.graph_db, readonly=True)
    rev = conn.execute(
        "SELECT rev_id FROM rev WHERE state = 'ready' ORDER BY rev_id DESC LIMIT 1"
    ).fetchone()["rev_id"]
    rows = conn.execute(
        "SELECT lang, COUNT(*) AS n FROM file WHERE rev_id = ? "
        "GROUP BY lang ORDER BY n DESC",
        (rev,),
    ).fetchall()
    conn.close()

    # Строка вида: php 323, **text 201**, md 59, ...
    parts = []
    for r in rows:
        lang, n = r["lang"], r["n"]
        parts.append(f"**{lang} {n}**" if lang in NEW_LANGS else f"{lang} {n}")
    listing = ", ".join(parts)

    # Заменяется последняя колонка строки таблицы этого корпуса.
    pattern = re.compile(
        rf"(\| \*{{0,2}}{re.escape(title)}\*{{0,2}} \| rev-\d{{4}} \| "
        rf"\d+ \| \d+ \| \d+ \| )[^|]*(\|)"
    )
    text, count = pattern.subn(rf"\g<1>{listing} \g<2>", text)
    print(f"  {title}: языков {len(rows)}, {'обновлено' if count else 'НЕ найдено'}")

REPORT.write_text(text, encoding="utf-8", newline="\n")

print()
print("=" * 78)
print("ПРОВЕРКА: ЧИСЛА В ТАБЛИЦЕ СОВПАДАЮТ С ИНДЕКСОМ")
print("=" * 78)
after = REPORT.read_text(encoding="utf-8")
for config_name, title in CORPORA:
    cfg = load_config(REPO / "config" / config_name)
    conn = store.connect(cfg.graph_db, readonly=True)
    row = conn.execute(
        "SELECT rev_id, file_count, node_count, edge_count FROM rev "
        "WHERE state = 'ready' ORDER BY rev_id DESC LIMIT 1"
    ).fetchone()
    langs = conn.execute(
        "SELECT lang, COUNT(*) AS n FROM file WHERE rev_id = ? "
        "GROUP BY lang ORDER BY n DESC LIMIT 3",
        (row["rev_id"],),
    ).fetchall()
    conn.close()
    line = next((ln for ln in after.splitlines()
                 if re.match(rf"\| \*{{0,2}}{re.escape(title)}\*{{0,2}} \| rev-",
                             ln)), "")
    ok_rev = row["rev_id"] in line
    ok_files = f"| {row['file_count']} |" in line
    ok_nodes = f"| {row['node_count']} |" in line
    ok_edges = f"| {row['edge_count']} |" in line
    # Первый язык из индекса должен быть и в строке.
    top_lang = langs[0]["lang"] if langs else ""
    ok_lang = f"{top_lang} {langs[0]['n']}" in line if langs else False
    print(f"  {title:<22} ревизия {'да' if ok_rev else 'НЕТ'}, "
          f"файлы {'да' if ok_files else 'НЕТ'}, "
          f"узлы {'да' if ok_nodes else 'НЕТ'}, "
          f"рёбра {'да' if ok_edges else 'НЕТ'}, "
          f"ведущий язык {'да' if ok_lang else 'НЕТ'}")
    if not all((ok_rev, ok_files, ok_nodes, ok_edges, ok_lang)):
        print(f"    строка: {line[:150]}")

print()
print("=" * 78)
print("ВЫБОРОЧНЫЕ СТРОКИ ТАБЛИЦЫ")
print("=" * 78)
start = after.find("| корпус | ревизия")
if start >= 0:
    block = after[start:start + 1200]
    for ln in block.splitlines()[:8]:
        print(f"  {ln[:150]}")
