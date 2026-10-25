"""Фактическое состояние режимов class_usage после устранения дефекта CSS.

Тест `test_class_usage.py` ожидал, что в режиме `missing` (используется, но
правила нет) будут классы, и что `z-agr-card__head` — именно такой класс.
Замер показал, что у всех 44 классов z-agr правила в CSS теперь есть.

Нужно увидеть фактические ответы операции, чтобы переписать проверки
по реальности, а не по ожиданиям двухлетней давности.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.facade import Facade  # noqa: E402

cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
fac = Facade(cfg, tool="probe", telemetry=False, watch=False)


def show(title: str, env: dict) -> None:
    res = env.get("result") or {}
    print("=" * 78)
    print(title)
    print("=" * 78)
    print(f"  ключи ответа: {sorted(res)}")
    print(f"  найдено: {res.get('count')}")
    for name in ("status", "class", "defined_count", "used_count"):
        if name in res:
            print(f"  {name}: {res[name]!r}")
    classes = res.get("classes") or []
    for x in classes[:8]:
        print(f"    .{x.get('name'):<34} опр {x.get('defined_count')} "
              f"исп {x.get('used_count')}  {x.get('first_at')}")
    print()


show("1. ОДИН КЛАСС, ВСЕ ПРАВИЛА НА МЕСТЕ (z-small)",
     fac.op_class_usage("z-small"))
show("2. КЛАСС ИЗ СОГЛАШЕНИЙ (z-agr-card__head)",
     fac.op_class_usage("z-agr-card__head"))
show("3. РЕЖИМ missing", fac.op_class_usage(mode="missing", limit=500))
show("4. РЕЖИМ unused", fac.op_class_usage(mode="unused", limit=500))
show("5. РЕЖИМ all", fac.op_class_usage(mode="all", limit=500))

# Проверка: сколько всего классов и сколько из них без правил.
res = (fac.op_class_usage(mode="all", limit=2000).get("result") or {})
classes = res.get("classes") or []
no_rules = [x for x in classes if x.get("defined_count") == 0
            and x.get("used_count", 0) > 0]
with_rules = [x for x in classes if x.get("defined_count", 0) > 0]
print("=" * 78)
print("СВОДКА ПО РЕЖИМУ all")
print("=" * 78)
print(f"  всего классов:            {len(classes)}")
print(f"  с правилами в CSS:        {len(with_rules)}")
print(f"  без правил, используемых: {len(no_rules)}")
print("\n  первые 20 без правил:")
for x in no_rules[:20]:
    print(f"    .{x.get('name'):<34} использований {x.get('used_count')}  "
          f"{x.get('first_at')}")

# Ключевой вопрос: попадают ли в missing НАСТОЯЩИЕ классы без правил.
missing = (fac.op_class_usage(mode="missing", limit=2000).get("result") or {})
m_classes = missing.get("classes") or []
m_names = {x.get("name") for x in m_classes}
verified = {x.get("name") for x in no_rules}
print()
print(f"  в missing: {len(m_names)}, из них подтверждено по all: "
      f"{len(m_names & verified)}")
only_missing = sorted(m_names - verified)[:12]
if only_missing:
    print(f"  только в missing ({len(only_missing)}):")
    for n in only_missing:
        print(f"    .{n}")

fac.close()
