"""Диагностика сопоставления имён: почему вызовы не привязались к символам.

Установлено окончательно:
  * исключения РАБОТАЮТ: `.venv` (5734 .py) и `.dev_agent` не попали в граф;
  * граф содержит все исходники проекта: 172 184 вызова из core, ui,
    dev_agent, tests, storage — то есть из настоящего кода;
  * каталога `src` в проекте НЕТ (моё прежнее предположение было неверным);
  * но подтверждено в графе 0 из 40 806 — сопоставление имён не работает.

Надо сравнить имена точно: что отдаёт Joern и что лежит в индексе RAGRAF
для ОДНОГО И ТОГО ЖЕ файла. Это покажет расхождение без догадок.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf import store  # noqa: E402
from ragraf import cpg as cpg_mod  # noqa: E402

cfg = load_config(REPO / "config" / "sagaai.yaml")
runner = cpg_mod.JoernRunner(cfg)
files = sorted(runner.workdir.glob("rev-*.cpg.bin"),
               key=lambda p: -p.stat().st_size)
rev_id = files[0].stem.replace(".cpg", "")
ok, note, rows = runner.query(rev_id, cpg_mod.QUERY_CALLS, timeout=1800)
print(f"  записей Joern: {len(rows)}")

# Берётся один файл, который точно есть и в графе, и в индексе.
target_file = None
for r in rows:
    posix = str(r.get("srcFile") or "").replace("\\", "/")
    if posix.startswith("core/") and posix.endswith(".py"):
        target_file = posix
        break
print(f"  выбранный файл: {target_file}")

print()
print("=" * 78)
print("1. ЧТО ОТДАЁТ JOERN ДЛЯ ЭТОГО ФАЙЛА")
print("=" * 78)
joern_entries = [r for r in rows
                 if str(r.get("srcFile") or "").replace("\\", "/") == target_file]
print(f"  записей: {len(joern_entries)}")
for r in joern_entries[:5]:
    print(f"    srcMethod={str(r.get('srcMethod'))!r}")
    print(f"      callee={str(r.get('callee'))!r} line={r.get('line')}")

print()
print("=" * 78)
print("2. ЧТО ЛЕЖИТ В ИНДЕКСЕ ДЛЯ ЭТОГО ЖЕ ФАЙЛА")
print("=" * 78)
conn = store.connect(cfg.graph_db, readonly=True)
rev = conn.execute(
    "SELECT rev_id FROM rev WHERE state = 'ready' ORDER BY rev_id DESC LIMIT 1"
).fetchone()["rev_id"]
print(f"  ревизия индекса: {rev}")

nodes = conn.execute(
    "SELECT kind, name, fqn, start_line FROM node "
    "WHERE rev_id = ? AND path = ? AND kind IN ('function','method') "
    "ORDER BY start_line LIMIT 8",
    (rev, target_file),
).fetchall()
print(f"  узлов: {len(nodes)}")
for n in nodes:
    print(f"    {n['kind']:<8} name={n['name']!r} строка {n['start_line']}")
    print(f"      fqn={n['fqn']!r}")

# Проверка: совпадает ли путь узлов с путём Joern.
same_path = conn.execute(
    "SELECT COUNT(*) AS n FROM node WHERE rev_id = ? AND path = ?",
    (rev, target_file),
).fetchone()["n"]
back_path = conn.execute(
    "SELECT COUNT(*) AS n FROM node WHERE rev_id = ? AND path = ?",
    (rev, target_file.replace("/", "\\")),
).fetchone()["n"]
print(f"\n  узлов по пути как у Joern:        {same_path}")
print(f"  узлов по пути с обратными слешами: {back_path}")

print()
print("=" * 78)
print("3. СОПОСТАВЛЕНИЕ ПО ИМЕНИ: РУЧНАЯ ПРОВЕРКА")
print("=" * 78)
# Из записи Joern извлекается имя владельца и ищется в индексе.
for r in joern_entries[:5]:
    src_method = str(r.get("srcMethod") or "")
    # Имя владельца — часть после последнего двоеточия.
    owner = src_method.rsplit(":", 1)[-1] if ":" in src_method else src_method
    found = conn.execute(
        "SELECT node_id, name, fqn FROM node WHERE rev_id = ? AND path = ? "
        "AND name = ? LIMIT 2",
        (rev, target_file, owner),
    ).fetchall()
    print(f"\n  Joern: srcMethod={src_method[:70]!r}")
    print(f"    извлечённое имя владельца: {owner!r}")
    print(f"    найдено в индексе по (path, name): {len(found)}")
    for f in found:
        print(f"      fqn={f['fqn']!r}")
    # Проверяется сопоставление по полному имени.
    fqn_variant = f"{target_file}:{owner}"
    by_fqn = conn.execute(
        "SELECT node_id FROM node WHERE rev_id = ? AND fqn = ?",
        (rev, fqn_variant),
    ).fetchall()
    print(f"    по fqn={fqn_variant!r}: {len(by_fqn)}")

conn.close()

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Здесь видно расхождение точно. Варианты, которые проверяются:
    * совпадает ли имя владельца после последнего двоеточия;
    * совпадает ли путь (прямые против обратных слешей);
    * совпадает ли полное имя fqn.

  По результату видно, какой именно шаг сопоставления надо поправить.
""")
