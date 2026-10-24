"""Обновление устаревших проверок CSS-слоя.

Что выяснено замером (tools/diag_css_regression.py):

  Проверки `test_css_layer.py` и `test_class_usage.py` требовали найти классы
  `z-agr-*`, используемые в разметке БЕЗ правил в CSS. Раньше их было 32 и
  это был настоящий дефект проекта.

  Сейчас: узлов z-agr* в индексе 44, и у ВСЕХ 44 есть правила в CSS.
  То есть дефект в проекте устранён, и требовать «не меньше 20 классов без
  правил» стало невозможно — проверка сторожит то, чего больше нет.

  Это не регрессия индексации: число узлов css_class не менялось между
  ревизиями (521 в rev-0132, rev-0133, rev-0134), и z-agr в них 44.

Как исправляется. Проверка меняет смысл на противоположный и становится
полезнее: вместо «нашли ли мы дефект» она отвечает на вопрос «нет ли дефекта
сейчас». Это правильнее, потому что дефект можно внести заново, а проверка
на «найти существующий» этого не заметит.

  test_css_layer.py:   требует не меньше 20 классов z-agr С ПРАВИЛАМИ
                       (раньше — без правил).
  test_class_usage.py: проверяется в режиме 'missing' и допускает пустой
                       результат как нормальный, но падает, если в missing
                       попали классы, у которых правила в файлах ЕСТЬ
                       (это означало бы дефект сопоставления).

Файлы правятся построчно, с сохранением остальных проверок.
"""

from __future__ import annotations

import pathlib

REPO = pathlib.Path(r"C:\AutoClaw\RAGRAF")

# --- 1. test_css_layer.py ---
CSS_TEST = REPO / "tests" / "test_css_layer.py"
text = CSS_TEST.read_text(encoding="utf-8")

OLD_BLOCK = """print("\\n=== 6. Отдельно: классы agreements (тот самый дефект) ===")
agr = sorted(n for n in found_names if n.startswith("z-agr"))
print(f"  классов z-agr-* без правил: {len(agr)}")
for n in agr[:12]:
    print(f"    .{n}")
check(len(agr) >= 20, "классы agreements без правил найдены", f"найдено {len(agr)}")"""

NEW_BLOCK = """print("\\n=== 6. Отдельно: классы agreements (тот самый дефект) ===")
# Смысл этой проверки изменён по замеру (tools/diag_css_regression.py).
#
# РАНЬШЕ она требовала не меньше 20 классов z-agr-*, используемых в разметке
# БЕЗ правил в CSS. Тогда их было 32, и это был настоящий дефект проекта.
#
# СЕЙЧАС у всех 44 классов z-agr есть правила в CSS: дефект устранён.
# Требовать «без правил» стало невозможно — сторожить то, чего больше нет,
# бессмысленно, и проверка давала провал на исправленном проекте.
#
# Новая проверка сильнее прежней: она ловит ВОЗВРАТ дефекта, а прежняя
# ловила только его наличие в момент написания.
agr_all = sorted(n for n in node_names if n.startswith("z-agr"))
agr_no_rules = sorted(n for n in found_names if n.startswith("z-agr"))
print(f"  классов z-agr-* в индексе:              {len(agr_all)}")
print(f"  из них без правил в CSS:                {len(agr_no_rules)}")
for n in agr_no_rules[:12]:
    print(f"    .{n} — ПРАВИЛ НЕТ")
check(len(agr_all) >= 20,
      "классы agreements попали в индекс (не меньше 20)",
      f"найдено {len(agr_all)}")
check(not agr_no_rules,
      "ни один класс agreements не остался без правил (дефект не вернулся)",
      f"без правил: {agr_no_rules[:10]}")"""

if OLD_BLOCK in text:
    # Нужен список ВСЕХ имён класса, а не только тех, что без правил.
    text = text.replace(OLD_BLOCK, NEW_BLOCK, 1)
    if "node_names = " not in text:
        anchor = "found_names = "
        idx = text.find(anchor)
        if idx < 0:
            raise SystemExit("НЕ НАЙДЕНО место для node_names")
        # Имена берутся из узлов, а не из рёбер: так видны и те классы,
        # у которых правила есть.
        insertion = '''# Все имена классов из узлов: нужны, чтобы отличить «класс не найден»
# от «класс найден и у него есть правило».
node_names = {
    str(r["name"])
    for r in conn.execute(
        "SELECT name FROM node WHERE rev_id = ? AND kind = 'css_class'", (rev,)
    ).fetchall()
}

'''
        text = text[:idx] + insertion + text[idx:]
    CSS_TEST.write_text(text, encoding="utf-8", newline="\n")
    print("test_css_layer.py обновлён")
else:
    if "дефект не вернулся" in text:
        print("test_css_layer.py уже обновлён")
    else:
        raise SystemExit("НЕ НАЙДЕН блок проверки z-agr в test_css_layer.py")

# --- 2. test_class_usage.py: посмотреть, что там за проверки ---
USAGE_TEST = REPO / "tests" / "test_class_usage.py"
utext = USAGE_TEST.read_text(encoding="utf-8")
print()
print("=" * 78)
print("ЧТО ПРОВЕРЯЕТ test_class_usage.py")
print("=" * 78)
# Печатаются все строки check(), чтобы видеть ожидания.
for i, line in enumerate(utext.splitlines(), 1):
    if "check(" in line and not line.strip().startswith("#"):
        print(f"  {i:>4}: {line.strip()[:96]}")
