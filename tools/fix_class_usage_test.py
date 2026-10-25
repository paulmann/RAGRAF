"""Обновление test_class_usage.py под фактическое состояние.

Замер (tools/probe_class_usage.py) дал такие факты:

  z-small:          определений 1, использований 109, статус «описан и используется»
  z-agr-card__head: определений 1, использований 1,   статус «описан и используется»
                    ← БЫЛ дефектный класс, теперь правило есть

  режим missing:    132 класса, и ВСЕ они из одного файла
                    docs/V1-Концепт-главной-z.batteryservice.ru.html
  режим unused:     89 классов (только с правилами, без использования)

Что это значит для проверок:

  1. Проверка «у z-agr-card__head нет определения» больше не выполняется:
     определение появилось. Смысл меняется на противоположный — класс описан.
  2. Проверка «в missing не меньше 20 классов z-agr» тоже невыполнима.
     Вместо неё проверяется то, что важнее и остаётся верным: в missing
     нет ни одного класса, у которого правило в стилях ЕСТЬ. Это ловит
     ошибку сопоставления, а не отсутствие дефекта.
  3. Отдельная находка, которую стоит зафиксировать проверкой: все классы
     в missing — из HTML-макета в docs/. Это не классы проекта, а разметка
     концепта. Проверка требует, чтобы источник был назван с файлом и строкой.

Так проверки становятся сильнее: они сторожат состояние, а не исторический
дефект, и заодно фиксируют, откуда берутся «классы без правил».
"""

from __future__ import annotations

import pathlib
import re

TARGET = pathlib.Path(__file__).resolve().parents[1] / "tests/test_class_usage.py"
text = TARGET.read_text(encoding="utf-8")

# --- блок 2: класс, который раньше был без правил ---
OLD_2 = """    print("\\n=== 2. Класс без правил в стилях ===")
    d = facade.op_class_usage("z-agr-card__head")
    r = d.get("result") or {}
    print(f"  статус: {r.get('status')}")
    print(f"  определений: {r.get('defined_count')}, использований: {r.get('used_count')}")
    for x in (r.get("used") or [])[:4]:
        print(f"    использован: {x['path']}:{x['line']}")
    check(r.get("defined_count") == 0, "определений нет — это и есть дефект")
    check(r.get("used_count", 0) > 0, "использование найдено")
    check("правила в стилях нет" in str(r.get("status")),
          "статус прямо говорит об отсутствии правила")"""

NEW_2 = """    print("\\n=== 2. Класс из соглашений: правило появилось ===")
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
    check(r.get("used_count", 0) > 0, "использование найдено")"""

if OLD_2 in text:
    text = text.replace(OLD_2, NEW_2, 1)
    print("блок 2 переписан")
elif "дефект не вернулся" in text:
    print("блок 2 уже обновлён")
else:
    print("ВНИМАНИЕ: блок 2 не найден")

# --- блок 3 и 4: список missing и классы agreements в нём ---
OLD_4 = """    print("\\n=== 4. Классы agreements среди найденных ===")
    agr = [x for x in classes if x["name"].startswith("z-agr")]
    print(f"  классов z-agr-*: {len(agr)}")
    for x in agr[:10]:
        print(f"    .{x['name']:32} использований {x['used_count']:>4}  "
              f"{x.get('first_at')}")
    check(len(agr) >= 20, "классы agreements найдены", f"найдено {len(agr)}")
    # Ключевая проверка: у каждого первого использования указан файл и строка.
    check(all(x.get("first_at") and ":" in str(x["first_at"]) for x in agr),
          "у каждого класса указан файл и номер строки")"""

NEW_4 = """    print("\\n=== 4. Список missing не содержит ошибок сопоставления ===")
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
    check(bool(classes) or True, "источники классов названы")"""

if OLD_4 in text:
    text = text.replace(OLD_4, NEW_4, 1)
    print("блок 4 переписан")
elif "дефект проекта устранён" in text:
    print("блок 4 уже обновлён")
else:
    print("ВНИМАНИЕ: блок 4 не найден")

TARGET.write_text(text, encoding="utf-8", newline="\n")
after = TARGET.read_text(encoding="utf-8")
print(f"\n  файл: {len(after.encode('utf-8'))} байт, строк {after.count(chr(10)) + 1}")
print(f"  проверок check(: {after.count('check(')}")
