"""Пошаговая проверка обхода: где теряются вызовы.

Обход возвращает None, и в логе нет моего примечания — значит сработал
молчаливый выход `if not calls: return None` после фильтрации.

Проверяется каждый шаг в точности так, как это делает индексер:
  1. сколько записей отдаёт Joern;
  2. сколько остаётся после parse_calls_export;
  3. сколько из них привязывается к символам индекса.

Это покажет, где именно теряются данные: на фильтрации или на привязке.

Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import sys
from collections import Counter

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf import cpg as cpg_mod  # noqa: E402

cfg = load_config(REPO / "config" / "sagaai.yaml")
runner = cpg_mod.JoernRunner(cfg)
files = sorted(runner.workdir.glob("rev-*.cpg.bin"),
               key=lambda p: -p.stat().st_size)
rev_id = files[0].stem.replace(".cpg", "")
print(f"  CPG: {files[0].name}, ревизия {rev_id}")

print()
print("=" * 78)
print("ШАГ 1: ЗАПИСИ ОТ JOERN")
print("=" * 78)
ok, note, rows = runner.query(rev_id, cpg_mod.QUERY_CALLS, timeout=1800)
print(f"  ok={ok}, записей {len(rows) if rows else 0}")
if not rows:
    print(f"  примечание: {note}")
    raise SystemExit(1)

# Сколько записей имеют владельца-модуль и сколько — функцию.
module_owners = 0
func_owners = 0
for r in rows:
    src = str(r.get("srcMethod") or "")
    if "<module>" in src or "<global>" in src:
        module_owners += 1
    else:
        func_owners += 1
print(f"  с владельцем-модулем: {module_owners}")
print(f"  с владельцем-функцией: {func_owners}")

print()
print("=" * 78)
print("ШАГ 2: ФИЛЬТРАЦИЯ parse_calls_export")
print("=" * 78)
# Записи приводятся к тому же виду, что делает индексер.
raw_calls = [
    {
        "owner_method": str(r.get("srcMethod") or ""),
        "owner_file": str(r.get("srcFile") or ""),
        "callee": str(r.get("callee") or ""),
        "callee_name": str(r.get("calleeName") or ""),
        "line": int(r.get("line") or -1),
        "dispatch_type": str(r.get("dispatchType") or ""),
        "code": str(r.get("code") or ""),
    }
    for r in rows
]
calls = cpg_mod.parse_calls_export(raw_calls)
print(f"  после фильтрации: {len(calls)} из {len(raw_calls)}")

if not calls:
    print("\n  ВСЁ ОТФИЛЬТРОВАНО — вот причина пустого обхода.")
    # Ищется, что именно отсеивает.
    from ragraf.joern_export import is_global_owner, is_noise_file

    reasons = Counter()
    for r in raw_calls:
        if is_global_owner(r["owner_method"]):
            reasons["владелец-модуль"] += 1
        elif is_noise_file(r["owner_file"]):
            reasons["служебный файл"] += 1
        elif cpg_mod._is_noise_call(r["callee"], r["callee_name"]):
            reasons["оператор языка"] += 1
        else:
            reasons["другое"] += 1
    for reason, n in reasons.most_common():
        print(f"    {reason}: {n}")
    raise SystemExit(0)

print(f"\n  примеры оставшихся вызовов:")
for c in calls[:8]:
    print(f"    owner={c['owner_method'][:60]!r}")
    print(f"      callee={c['callee'][:40]!r} файл={c['owner_file'][:40]!r}")

print()
print("=" * 78)
print("ШАГ 3: ПРИВЯЗКА К СИМВОЛАМ ИНДЕКСА")
print("=" * 78)
# Проверяется тем же нормализующим кодом, что использует индексер.
from ragraf.joern_export import normalize_joern_name, normalize_joern_path  # noqa: E402

# Резолвер создаётся так же, как в индексере.
import sqlite3  # noqa: E402

conn = sqlite3.connect(f"file:{cfg.graph_db}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
rev = conn.execute(
    "SELECT rev_id FROM rev WHERE state = 'ready' ORDER BY rev_id DESC LIMIT 1"
).fetchone()["rev_id"]
all_fqns = {
    str(r["fqn"])
    for r in conn.execute("SELECT fqn FROM node WHERE rev_id = ?", (rev,))
}
conn.close()
print(f"  fqn в индексе: {len(all_fqns)}")

matched = 0
samples_unmatched: list[str] = []
for c in calls[:400]:
    owner = normalize_joern_name(c["owner_method"])
    if owner in all_fqns:
        matched += 1
    elif len(samples_unmatched) < 6:
        samples_unmatched.append(owner)

print(f"  из первых 400 вызовов владелец найден в индексе: {matched}")
if samples_unmatched:
    print(f"\n  примеры НЕ найденных владельцев:")
    for s in samples_unmatched:
        print(f"    {s!r}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print(f"""
  Записей от Joern:        {len(rows)}
  После фильтрации:        {len(calls)}
  Владелец найден в индексе: {matched} из первых 400

  Если после фильтрации пусто — надо править фильтр.
  Если владелец не находится — надо править нормализацию имени.
""")
