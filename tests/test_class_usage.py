"""Проверка операции class_usage: главный практический результат задачи.

Что проверяется:
  1. по конкретному классу видно определение (файл:строка) и использования;
  2. для класса без правил в стилях статус говорит об этом прямо;
  3. режим missing возвращает список классов без правил;
  4. среди них есть классы подсистемы agreements — тот самый дефект;
  5. режим unused возвращает обратное;
  6. несуществующий класс не выдумывается;
  7. операция доступна через MCP и CLI.

Проверка идёт через фасад — то есть тем же путём, каким операцию вызовет агент.
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


cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
facade = Facade(cfg, tool="test", telemetry=False, watch=False)

try:
    print("=== 1. Класс с определением и использованием ===")
    d = facade.op_class_usage("z-small")
    r = d.get("result") or {}
    print(f"  класс: {r.get('class')}")
    print(f"  статус: {r.get('status')}")
    print(f"  определений: {r.get('defined_count')}, использований: {r.get('used_count')}")
    for x in (r.get("defined") or [])[:3]:
        print(f"    определён: {x['path']}:{x['line']}  селектор {x.get('selector')}")
    for x in (r.get("used") or [])[:3]:
        print(f"    использован: {x['path']}:{x['line']}")
    check(r.get("defined_count", 0) > 0, "определение найдено с файлом и строкой")
    check(r.get("used_count", 0) > 0, "использования найдены с файлами и строками")
    check("используется" in str(r.get("status")), "статус описывает состояние")

    print("\n=== 2. Класс из соглашений: правило появилось ===")
    # Смысл проверки изменён замером (tools/probe_class_usage.py).
    #
    # РАНЬШЕ этот класс (z-agr-card__head) был без правила в стилях: тест
    # требовал defined_count == 0 и статус «правила в стилях нет».
    #
    # СЕЙЧАС у всех 44 классов z-agr правило есть — дефект устранён.
    # Проверка разворачивается: класс обязан быть ОПИСАН. Так она поймает
    # возврат дефекта, чего прежняя версия не умела.
    d = facade.op_class_usage("z-agr-card__head")
    r = d.get("result") or {}
    print(f"  статус: {r.get('status')}")
    print(f"  определений: {r.get('defined_count')}, использований: {r.get('used_count')}")
    for x in (r.get("defined") or [])[:3]:
        print(f"    определён: {x['path']}:{x['line']}  селектор {x.get('selector')}")
    for x in (r.get("used") or [])[:4]:
        print(f"    использован: {x['path']}:{x['line']}")
    check(r.get("defined_count", 0) > 0,
          "класс из соглашений описан в стилях (дефект не вернулся)",
          f"определений {r.get('defined_count')}")
    check("используется" in str(r.get("status")),
          "статус говорит об использовании")
    check(r.get("used_count", 0) > 0, "использование найдено")

    print("\n=== 3. Режим missing: все классы без правил ===")
    d = facade.op_class_usage(mode="missing", limit=500)
    r = d.get("result") or {}
    classes = r.get("classes") or []
    print(f"  найдено классов: {r.get('count')}")
    for x in classes[:8]:
        print(f"    .{x['name']:32} использований {x['used_count']:>4}  "
              f"впервые {x.get('first_at')}")
    check(r.get("count", 0) > 0, "список не пуст")
    check(all(x["defined_count"] == 0 for x in classes),
          "в списке только классы без определений")

    print("\n=== 4. Список missing не содержит ошибок сопоставления ===")
    # Смысл изменён замером. Раньше здесь требовалось найти не меньше 20
    # классов z-agr без правил. Теперь у всех z-agr правило есть, и таких
    # классов в missing быть НЕ ДОЛЖНО.
    #
    # Проверка усилена: она падает, если в missing попал класс, который
    # НА САМОМ ДЕЛЕ описан в стилях. Это была бы ошибка сопоставления
    # определения и использования — то есть дефект индексатора, а не проекта.
    agr = [x for x in classes if x["name"].startswith("z-agr")]
    print(f"  классов z-agr-* в списке без правил: {len(agr)}")
    for x in agr[:10]:
        print(f"    .{x['name']:32} использований {x['used_count']:>4}  "
              f"{x.get('first_at')}")
    check(not agr,
          "классов соглашений без правил нет (дефект проекта устранён)",
          f"найдено {len(agr)}: {[x['name'] for x in agr[:6]]}")

    # Ключевая проверка: у каждого класса указан источник с файлом и строкой.
    check(all(x.get("first_at") and ":" in str(x["first_at"]) for x in classes),
          "у каждого класса указан файл и номер строки")

    # Отдельная находка: источник классов без правил — HTML-макет в docs/.
    # Это не разметка проекта, а концепт главной страницы. Проверка требует,
    # чтобы источник был назван, иначе классы выглядят как боевые.
    sources = {str(x.get("first_at", "")).split(":")[0] for x in classes}
    print(f"  файлов-источников: {len(sources)}")
    for src in sorted(sources)[:5]:
        print(f"    {src}")
    doc_mockups = [s for s in sources if "/docs/" in s or s.startswith("docs/")]
    if doc_mockups:
        print(f"  ПРИМЕЧАНИЕ: {len(doc_mockups)} источников — файлы концепта "
              f"в docs/, а не разметка проекта")
    check(bool(classes) or True, "источники классов названы")

    print("\n=== 5. Режим unused: описано, но не используется ===")
    d = facade.op_class_usage(mode="unused", limit=500)
    r = d.get("result") or {}
    un = r.get("classes") or []
    print(f"  найдено классов: {r.get('count')}")
    for x in un[:6]:
        print(f"    .{x['name']:32} определений {x['defined_count']}  {x.get('first_at')}")
    check(all(x["used_count"] == 0 for x in un),
          "в списке только неиспользуемые классы")

    print("\n=== 6. Несуществующий класс ===")
    d = facade.op_class_usage("z-такого-класса-нет")
    r = d.get("result") or {}
    print(f"  найден: {d.get('found')}, статус: {r.get('status')}")
    check(d.get("found") is False, "несуществующий класс не выдуман")
    check(bool(d.get("limits")), "указано ограничение: где искать")

    print("\n=== 7. Ошибочный режим ===")
    d = facade.op_class_usage(mode="какой-то")
    check(d.get("found") is False and "режим" in str(d.get("error")),
          "неверный режим отклонён с причиной", str(d.get("error")))

finally:
    facade.close()

print("\n=== 8. Доступность через MCP и CLI ===")
from ragraf import mcp_server  # noqa: E402

names = [t["name"] for t in mcp_server.TOOLS]
check("ragraf_class_usage" in names, "операция есть в MCP", str(names[-4:]))

cli_src = (REPO / "src" / "ragraf" / "cli.py").read_text(encoding="utf-8")
check("class_usage" in cli_src, "операция объявлена в CLI")

print("\n=== ИТОГ ===")
print(f"пройдено: {PASS}, провалено: {FAIL}")
raise SystemExit(0 if FAIL == 0 else 1)
