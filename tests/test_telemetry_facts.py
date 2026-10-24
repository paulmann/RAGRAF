"""Проверка учёта фактов в телеметрии: новые операции тоже считаются.

Зачем проверка. Метрика `usage` отвечает на вопрос «окупается ли индекс»:
сколько фактов дал вызов и сколько из них проверяемы (`path:line`). Пока
сборщик знал только старые ключи ответа, `search_multi` с 19 семействами и
ссылками показывал НОЛЬ фактов (измерено). Ноль у операции, которая отдаёт
ссылки, делает метрику недостоверной — а по ней принимают решения.

Проверяется и обратное: один и тот же словарь не должен считаться дважды.
Ответ вложен (`families → members → evidence`), и наивный обход посчитал бы
вложенные ссылки повторно, завышая эффект.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.facade import summarize_result  # noqa: E402

PASS = 0
FAIL = 0


def check(condition: bool, title: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK ] {title}")
    else:
        FAIL += 1
        print(f"  [FAIL] {title}" + (f"\n      {detail}" if detail else ""))


print("=== 1. Пустой ответ ничего не считает ===")
check(summarize_result(None) == (0, 0, 0), "None → (0, 0, 0)")
check(summarize_result({}) == (0, 0, 0), "пустой словарь → (0, 0, 0)")

print("\n=== 2. Ответы старых операций считаются как раньше ===")
legacy = {"result": [{"path": "a.php", "line": 3}, {"path": "b.php"}]}
facts, spans, _chars = summarize_result(legacy)
check((facts, spans) == (2, 1), "список фактов: 2 факта, 1 со строкой",
      f"{facts}/{spans}")
schema = {"result": [{"table_name": "t", "columns": [{"column_name": "c"}]}]}
facts, spans, _chars = summarize_result(schema)
check(facts == 1, "ответ без ссылок: один факт верхнего уровня", str(facts))

print("\n=== 3. Вложенный ответ search_multi: факты и ссылки видны ===")
nested = {"result": {"families": [{
    "family": "php/Files.php",
    "members": [
        {"path": "php/Files.php", "evidence": []},
        {"path": "php/Files7.php",
         "evidence": [{"path": "templates/Files.html", "line": 100}]},
    ],
    "referenced_outside_hits": [
        {"path": "php/Files8.php",
         "evidence": [{"path": "db/Files.html", "line": 17}]},
    ],
}]}}
facts, spans, _chars = summarize_result(nested)
# Считаются: семейство (1), члены (2), ссылка внутри члена (1),
# подключённый вне найденных (1), ссылка внутри него (1) = 6.
check(facts == 6, "вложенные факты посчитаны без пропусков", str(facts))
check(spans == 2, "ссылки path:line посчитаны", str(spans))

print("\n=== 4. Повторного счёта нет ===")
# Тот же путь и строка в двух разных местах — это два РАЗНЫХ объекта, и оба
# считаются; а один и тот же объект, встреченный дважды, невозможен в JSON.
twice = {"result": {"families": [
    {"family": "f", "members": [{"path": "a.php",
                                 "evidence": [{"path": "x.html", "line": 1}]}]},
    {"family": "g", "members": [{"path": "b.php",
                                 "evidence": [{"path": "y.html", "line": 2}]}]},
]}}
facts, spans, _chars = summarize_result(twice)
check((facts, spans) == (6, 2), "две семьи: 6 фактов, 2 ссылки", f"{facts}/{spans}")

print("\n=== 5. Объём ответа считается по result, а не по конверту ===")
_f, _s, chars = summarize_result({"result": {"x": "y"}, "stages": [1, 2, 3]})
check(chars == len('{"x": "y"}'), "символы — только полезная нагрузка", str(chars))

print("\n=== 6. Метрики эффекта (P2-4): stale_served, misses, hit_rate, avg_ms ===")
import sqlite3  # noqa: E402

from ragraf.revisions import ControlStore  # noqa: E402
from ragraf.telemetry import CallRecord, Telemetry  # noqa: E402

STAGING = REPO / ".staging"
STAGING.mkdir(exist_ok=True)
tmp_db = STAGING / "p24_probe.sqlite"
if tmp_db.exists():
    tmp_db.unlink()
ctrl = ControlStore.__new__(ControlStore)
ctrl.cfg = load_config(REPO / "config" / "ragraf.yaml")
ctrl.conn = sqlite3.connect(str(tmp_db))
ctrl.conn.row_factory = sqlite3.Row
tel = Telemetry(ctrl, tool="selftest", agent="Probe", task="p24")
tel.record(CallRecord(op="search", rev_id="r1", capability="full", duration_ms=100.0,
                      found=True, fact_count=4, facts_with_span=4, result_chars=400,
                      error=None, stale=False, missed=False))
tel.record(CallRecord(op="search", rev_id="r1", capability="full", duration_ms=200.0,
                      found=True, fact_count=2, facts_with_span=2, result_chars=40,
                      error=None, stale=True, missed=False))
tel.record(CallRecord(op="symbol", rev_id="r1", capability="full", duration_ms=3.0,
                      found=False, fact_count=0, facts_with_span=0, result_chars=10,
                      error=None, stale=False, missed=True))
s = tel.summary()
print(f"  calls={s['calls_total']} hit_rate={s['hit_rate']} misses={s['misses']} "
      f"stale_served={s['stale_served']} avg_ms={s['avg_ms']}")
check(s["misses"] == 1, "misses считает честные «не найдено»", str(s["misses"]))
check(s["stale_served"] == 1, "stale_served считает ответы с пометкой устаревания",
      str(s["stale_served"]))
check(abs(float(s["hit_rate"]) - 2 / 3) < 1e-3,
      "hit_rate — доля ВЫЗОВОВ с фактами (2 из 3)", str(s["hit_rate"]))
check(abs(float(s["avg_ms"]) - 101.0) < 0.5,
      "avg_ms — среднее время вызова", str(s["avg_ms"]))
by_op = {r["op"]: r for r in s["by_op"]}
check(by_op["symbol"]["misses"] == 1, "misses видны и по операции")
check(by_op["search"]["stale_served"] == 1, "stale_served видны и по операции")
ctrl.conn.close()
tmp_db.unlink(missing_ok=True)

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
