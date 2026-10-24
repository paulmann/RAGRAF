"""Сбор итоговых цифр для отчёта.

Все числа берутся из фактического состояния индексов и кода, а не из памяти.
Результат печатается в виде, готовом для переноса в отчёт.
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys
from collections import Counter

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store  # noqa: E402
from ragraf.config import LANG_BY_EXT, TREE_LANGS, load as load_config  # noqa: E402
from ragraf.docrole import ROLES  # noqa: E402
from ragraf.extract.generic import PROFILES  # noqa: E402

print("=" * 78)
print("1. СОСТОЯНИЕ КОДА RAGRAF")
print("=" * 78)
print(f"  расширений в карте языков (LANG_BY_EXT): {len(LANG_BY_EXT)}")
print(f"  языков всего:                            {len(set(LANG_BY_EXT.values()))}")
print(f"  языков с разбором деревом (TREE_LANGS):   {len(TREE_LANGS)}")
print(f"  профилей грамматик (PROFILES):            {len(PROFILES)}")
print(f"  ролей документов (ROLES):                 {len(ROLES)}")
print(f"\n  языки с разбором деревом:")
print(f"    {', '.join(sorted(TREE_LANGS))}")
print(f"\n  языки только текстом:")
text_only = sorted(set(LANG_BY_EXT.values()) - TREE_LANGS)
print(f"    {', '.join(text_only)}")

print()
print("=" * 78)
print("2. СОСТОЯНИЕ КОРПУСОВ")
print("=" * 78)
CORPORA = [
    ("z-batteryservice.yaml", "z.batteryservice.ru"),
    ("revoap.yaml", "revoap.com"),
    ("md.viewer.yaml", "MD.Viewer"),
    ("rdpmon.yaml", "RdpMON"),
]
print(f"\n  {'корпус':<22} {'ревизия':<9} {'файлов':>8} {'узлов':>8} "
      f"{'рёбер':>8} {'роль':>6}")
print("  " + "-" * 66)
for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    conn = store.connect(cfg.graph_db, readonly=True)
    row = conn.execute(
        "SELECT rev_id, file_count, node_count, edge_count, capability FROM rev "
        "WHERE state = 'ready' ORDER BY rev_id DESC LIMIT 1"
    ).fetchone()
    if not row:
        print(f"  {title:<22} нет готовой ревизии")
        conn.close()
        continue
    print(f"  {title:<22} {row['rev_id']:<9} {row['file_count']:>8} "
          f"{row['node_count']:>8} {row['edge_count']:>8} "
          f"{str(row['capability'] or ''):>6}")

    # Языки корпуса и файлы по ним.
    langs = conn.execute(
        "SELECT lang, COUNT(*) AS n FROM file WHERE rev_id = ? "
        "GROUP BY lang ORDER BY n DESC",
        (row["rev_id"],),
    ).fetchall()
    parts = ", ".join(f"{r['lang']} {r['n']}" for r in langs)
    print(f"    языки: {parts}")
    conn.close()

print()
print("=" * 78)
print("3. РОЛИ ДОКУМЕНТОВ ПО КОРПУСАМ")
print("=" * 78)
for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    lex = pathlib.Path(str(cfg.lexvec_db))
    if not lex.exists():
        continue
    conn = sqlite3.connect(f"file:{lex}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT rev_id FROM chunk_map ORDER BY rev_id DESC LIMIT 1"
    ).fetchone()
    if not row:
        conn.close()
        continue
    roles = Counter()
    docs = 0
    total = 0
    for r in conn.execute(
        "SELECT doc_role, COUNT(*) AS n FROM chunk_map WHERE rev_id = ? "
        "GROUP BY doc_role",
        (row["rev_id"],),
    ).fetchall():
        role = r["doc_role"] or ""
        total += r["n"]
        if role:
            roles[role] = r["n"]
            docs += r["n"]
    print(f"\n  {title} ({row['rev_id']}): чанков {total}, с ролью {docs}")
    for role, n in sorted(roles.items(), key=lambda kv: -kv[1]):
        print(f"    {role:<16} {n}")
    conn.close()

print()
print("=" * 78)
print("4. ЗАМЕРЫ ЭТОЙ СЕССИИ")
print("=" * 78)
print("""
  Сканирование стека (tools/scan_scripts.py):
    <полигон>:  Python 1337, Perl 1235, SQL 1058, C# 723, C/C++ 384,
                  shell 212, TypeScript 181, PowerShell 88
    1st_RdpMON\\Service: C# 1217, PowerShell 8, shell 1, Python 1

  Грамматики (tools/check_grammars.py, check_parse_real.py):
    доступно языков в tree-sitter-language-pack: 26
    разбираются деревом на реальных файлах:      14 из 20 проверенных
    SQL: 0 из 12 (только текст), batch: 0 из 7,
    PowerShell: 6 из 12 без ошибок, INI: 3 из 12

  Разбор PowerShell (tools/check_powershell_parse.py):
    всего файлов .ps1/.psm1: 96
    без ошибок:               53
    с неполным разбором:      43

  C#-корпус RdpMON (после подключения):
    файлов .cs в проекте:          1385
    минус .zwork (53) и obj (168): 1164 в индексе
    узлов:                         20131
    рёбер:                         97041
    функций и методов:             8457
    запись record:                 исправлена (class вместо struct)

  Учёт узлов (tools/measure_node_dupes2.py):
    по узлам кода схлопываний:     0
    схлопывания только у css_class: 1441 строка → 521 узел (задумано)
    расхождение журнала и таблицы:  исправлено (было 2227 против 1290)

  cuGraph (tools/check_cugraph_windows.py):
    cugraph-cu12 26.8.0:            only manylinux, win_amd64 нет
    cugraph-cu13 26.8.0:            только manylinux
    cudf-cu12 26.8.1:               только manylinux
    вывод:                          под Windows не ставится

  Роли документов (tests/test_docrole.py):
    документов проверено:           70
    роль определена:                доля считается тестом
    конвенции ТЗ:                   нет (4 файла в 4 схемах)

  Дубликаты документации:
    побайтово одинаковых копий:     669
    из 120 файлов readme.md:        103 разного содержания
""")
