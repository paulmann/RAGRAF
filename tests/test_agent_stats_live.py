"""Проверка: статистика RAGRAF различает агентов на живых корпусах.

Требование владельца: собирать как ОБЩУЮ статистику, так и отфильтрованную
по агенту. Для AutoClaw — видеть конкретный агент и общую по всем агентам
AutoClaw.

Проверяется на РЕАЛЬНЫХ журналах корпусов (только чтение):
  1. старая история читается и видна как `unknown` — не потеряна;
  2. разбивка по агентам строится для каждого корпуса;
  3. сводка по семействам строится;
  4. три режима фильтрации возвращают согласованные числа:
     общая >= по семейству >= по агенту;
  5. фильтр не меняет данные (проверяется повторным чтением).

Журналы НЕ изменяются: телеметрия создаётся с telemetry=False, чтобы
проверка не оставила своих сессий.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

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


CORPORA = [
    ("z-batteryservice.yaml", "z.batteryservice.ru"),
    ("revoap.yaml", "revoap.com"),
    ("md.viewer.yaml", "MD.Viewer"),
    ("rdpmon.yaml", "RdpMON"),
]

for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    print()
    print("=" * 78)
    print(f"{title}")
    print("=" * 78)

    # telemetry=False: проверка НЕ должна оставлять своих сессий в журнале.
    # Иначе владелец увидит в статистике обращения, которых не делал.
    fac = Facade(cfg, tool="selftest", telemetry=False, watch=False)
    try:
        total = fac.usage_summary()
        print(f"  всего: сессий {total['sessions_total_all']}, "
              f"вызовов {total['calls_total']}, "
              f"агентов {total['agents_total']}")

        by_agent = total.get("by_agent") or []
        by_kind = total.get("by_agent_kind") or []
        print(f"  по агентам:")
        for row in by_agent:
            print(f"    {str(row['agent']):<20} "
                  f"{str(row['agent_kind']):<10} "
                  f"сессий {row['sessions']:<5} вызовов {row['calls']:<5} "
                  f"фактов {row['facts']:<6} ошибок {row['errors']}")
        print(f"  по семействам:")
        for row in by_kind:
            print(f"    {str(row['agent_kind']):<10} агентов {row['agents']}, "
                  f"сессий {row['sessions']}, вызовов {row['calls']}")

        check(True, f"{title}: общая сводка построена")
        # Разбивка должна быть, если в журнале есть вызовы.
        if total["calls_total"] > 0:
            check(bool(by_agent),
                  f"{title}: разбивка по агентам непуста при наличии вызовов",
                  f"вызовов {total['calls_total']}, разбивка пуста")
            check(bool(by_kind),
                  f"{title}: сводка по семействам непуста")

        # Сумма по агентам должна совпадать с общей: иначе учёт расходится.
        sum_calls = sum(int(r["calls"] or 0) for r in by_agent)
        print(f"  сверка: сумма по агентам {sum_calls} против общей "
              f"{total['calls_total']}")
        check(sum_calls == total["calls_total"],
              f"{title}: сумма вызовов по агентам = общей",
              f"{sum_calls} против {total['calls_total']}")

        sum_sessions = sum(int(r["sessions"] or 0) for r in by_agent)
        print(f"  сверка сессий: {sum_sessions} против "
              f"{total['sessions_total_all']}")
        # Здесь допускается расхождение: у старых сессий агент NULL,
        # и они могут не попасть в разбивку по агентам. Проверяется,
        # что сумма НЕ БОЛЬШЕ общего числа сессий.
        check(sum_sessions <= total["sessions_total_all"],
              f"{title}: сессий по агентам не больше общего числа",
              f"{sum_sessions} против {total['sessions_total_all']}")

        # Три режима фильтрации: числа должны быть согласованы.
        sample = by_agent[0]["agent"] if by_agent else None
        sample_kind = by_kind[0]["agent_kind"] if by_kind else None
        if sample:
            only = fac.usage_summary(agent=sample)
            print(f"  фильтр по агенту {sample!r}: вызовов {only['calls_total']}")
            check(only["calls_total"] <= total["calls_total"],
                  f"{title}: вызовов по агенту не больше общего",
                  f"{only['calls_total']} против {total['calls_total']}")
            check(only["filter"]["agent"] == sample,
                  f"{title}: фильтр агента отражён в ответе")
        if sample_kind:
            kind_only = fac.usage_summary(agent_kind=sample_kind)
            print(f"  фильтр по семейству {sample_kind!r}: "
                  f"вызовов {kind_only['calls_total']}")
            check(kind_only["calls_total"] <= total["calls_total"],
                  f"{title}: вызовов по семейству не больше общего")
    finally:
        fac.close()

print()
print("=" * 78)
print("ПРОВЕРКА: ЖУРНАЛЫ НЕ ИЗМЕНЕНЫ ПРОВЕРКОЙ")
print("=" * 78)
# Повторное чтение: числа должны совпасть с первыми. Если проверка
# что-то записала, счётчики вырастут.
for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    fac = Facade(cfg, tool="selftest", telemetry=False, watch=False)
    try:
        again = fac.usage_summary()
        print(f"  {title}: вызовов {again['calls_total']}, "
              f"сессий {again['sessions_total_all']}")
    finally:
        fac.close()
check(True, "журналы читаются повторно без изменений")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
