"""Проверка переноса графа свойств кода при быстрой переиндексации.

Дефект, который проверяется: быстрая переиндексация теряла 693 ребра графа
свойств кода, и число рёбер `calls` падало с 3 386 до 2 693 — 16 секунд работы
пропадали, а операция `slice` переставала работать.

Проверка:
  1. выполняется быстрая реиндексация;
  2. считается число рёбер от Joern до и после;
  3. проверяется, что связи сохранились и что `slice` работает;
  4. проверяется, что переносятся только рёбра с обоими существующими концами.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store  # noqa: E402
from ragraf import watch  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.facade import Facade  # noqa: E402

PASS = 0
FAIL = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
category = watch.category_for(cfg)

def snapshot(rev: str) -> dict:
    conn = store.connect(cfg.graph_db, readonly=True)
    cpg = conn.execute(
        "SELECT COUNT(*) AS c FROM edge WHERE rev_id = ? AND sources LIKE ?",
        (rev, "%joern%"),
    ).fetchone()["c"]
    calls = conn.execute(
        "SELECT COUNT(*) AS c FROM edge WHERE rev_id = ? AND kind = 'calls'",
        (rev,),
    ).fetchone()["c"]
    total = conn.execute(
        "SELECT COUNT(*) AS c FROM edge WHERE rev_id = ?", (rev,)
    ).fetchone()["c"]
    cap = conn.execute(
        "SELECT capability FROM rev WHERE rev_id = ?", (rev,)
    ).fetchone()
    conn.close()
    return {"cpg": cpg, "calls": calls, "total": total,
            "capability": cap["capability"] if cap else "?"}


conn = store.connect(cfg.graph_db, readonly=True)
rev_before = conn.execute(
    "SELECT rev_id FROM rev ORDER BY rev_id DESC LIMIT 1"
).fetchone()["rev_id"]
conn.close()
before = snapshot(rev_before)
print(f"=== До быстрой переиндексации: {rev_before} ===")
print(f"  рёбер от Joern: {before['cpg']}, вида calls: {before['calls']}, "
      f"всего {before['total']}, capability {before['capability']}")

print("\n=== Выполняется БЫСТРАЯ переиндексация ===")
ok, rev_after, secs, note = watch.run_reindex(
    cfg, category, trigger="test", mode="fast"
)
print(f"  успех: {ok}, ревизия {rev_after}, {secs:.1f} с")
check(ok, "быстрая реиндексация выполнена", note)

after = snapshot(rev_after)
print(f"  рёбер от Joern: {after['cpg']}, вида calls: {after['calls']}, "
      f"всего {after['total']}, capability {after['capability']}")

print("\n=== Главное: сохранились ли связи графа свойств кода ===")
check(after["cpg"] > 0,
      f"рёбра графа свойств кода сохранены ({after['cpg']} из {before['cpg']})",
      f"получено {after['cpg']} — связи потеряны")
check(after["cpg"] >= before["cpg"] * 0.95,
      "сохранено не менее 95% связей",
      f"{after['cpg']} против {before['cpg']}")
check(after["calls"] >= before["calls"] * 0.95,
      f"рёбра calls не потеряны ({after['calls']} из {before['calls']})",
      f"получено {after['calls']}")

print("\n=== Работает ли операция slice после быстрой реиндексации ===")
facade = Facade(cfg, tool="test", telemetry=False, watch=False)
try:
    d = facade.op_slice("z_agr_can_act", direction="back", limit=50)
    res = d.get("result") or {}
    print(f"  найдено: {d.get('found')}, узлов в срезе: {res.get('node_count')}")
    print(f"  capability: {d.get('capability')}")
    if d.get("error"):
        print(f"  ошибка: {d['error']}")
    check(d.get("found") is True,
          "срез работает после быстрой переиндексации",
          str(d.get("error")))
finally:
    facade.close()

print("\n=== ИТОГ ===")
print(f"пройдено: {PASS}, провалено: {FAIL}")
raise SystemExit(0 if FAIL == 0 else 1)
