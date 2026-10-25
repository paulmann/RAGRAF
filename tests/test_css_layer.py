"""Проверка главного результата: находит ли индекс расхождение стилей.

Что проверяется:
  1. классы оформления попали в граф как узлы;
  2. для каждого класса видно, где он ОПРЕДЕЛЁН (ребро defines_class) и где
     ИСПОЛЬЗОВАН (ребро uses_class) — это и есть ответ на вопрос пользователя
     «подсказывать номера строк и связанные данные»;
  3. находится ли реальный дефект: классы, используемые в разметке без правил
     в стилях;
  4. видны ли обе стороны с номерами строк.

Проверка идёт по графу индекса, то есть тем же путём, каким это увидит агент.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

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
conn = store.connect(cfg.graph_db, readonly=True)
rev = conn.execute("SELECT rev_id FROM rev ORDER BY rev_id DESC LIMIT 1").fetchone()["rev_id"]
print(f"корпус: {cfg.project}, ревизия {rev}\n")

print("=== 1. Классы оформления в графе ===")
for r in conn.execute(
    "SELECT kind, COUNT(*) AS n FROM node WHERE rev_id = ? AND kind IN"
    " ('css_class','css_var','html_id','css_rule') GROUP BY kind ORDER BY n DESC",
    (rev,),
).fetchall():
    print(f"  {r['kind']:12} {r['n']:>6}")
total = conn.execute(
    "SELECT COUNT(*) AS n FROM node WHERE rev_id = ? AND kind IN ('css_class','css_var')",
    (rev,),
).fetchone()["n"]
check(total > 100, "классы и переменные попали в граф", f"найдено {total}")

print("\n=== 2. Обе стороны одного класса ===")
# Берётся класс, у которого есть И определение, И использование.
row = conn.execute(
    """SELECT n.name, n.fqn,
              SUM(CASE WHEN e.kind = 'defines_class' THEN 1 ELSE 0 END) AS defs,
              SUM(CASE WHEN e.kind = 'uses_class'    THEN 1 ELSE 0 END) AS uses
         FROM node n JOIN edge e ON e.rev_id = n.rev_id AND e.dst = n.node_id
        WHERE n.rev_id = ? AND n.kind = 'css_class'
        GROUP BY n.node_id HAVING defs > 0 AND uses > 0
        ORDER BY uses DESC LIMIT 5""",
    (rev,),
).fetchall()
check(bool(row), "найдены классы с определением и использованием")
for r in row:
    print(f"  .{r['name']:28} определений {r['defs']}, использований {r['uses']}")

if row:
    target = row[0]["name"]
    print(f"\n=== 3. Детали класса .{target} (как это увидит агент) ===")
    print("  определения:")
    for d in conn.execute(
        """SELECT e.evidence_path, e.evidence_line, e.props
             FROM edge e JOIN node n ON n.rev_id = e.rev_id AND n.node_id = e.dst
            WHERE e.rev_id = ? AND e.kind = 'defines_class' AND n.name = ?
            LIMIT 6""",
        (rev, target),
    ).fetchall():
        print(f"    {d['evidence_path']}:{d['evidence_line']}  {str(d['props'])[:80]}")
    print("  использование:")
    for u in conn.execute(
        """SELECT e.evidence_path, e.evidence_line
             FROM edge e JOIN node n ON n.rev_id = e.rev_id AND n.node_id = e.dst
            WHERE e.rev_id = ? AND e.kind = 'uses_class' AND n.name = ?
            LIMIT 6""",
        (rev, target),
    ).fetchall():
        print(f"    {u['evidence_path']}:{u['evidence_line']}")

print("\n=== 4. ГЛАВНОЕ: расхождение — используется, но не определено в стилях ===")
# Класс считается определённым, если есть хотя бы одно ребро defines_class.
# Класс из разметки виден как ребро uses_class от файла разметки.
missing = conn.execute(
    """SELECT n.name,
              SUM(CASE WHEN e.kind = 'defines_class' THEN 1 ELSE 0 END) AS defs,
              SUM(CASE WHEN e.kind = 'uses_class'    THEN 1 ELSE 0 END) AS uses
         FROM node n JOIN edge e ON e.rev_id = n.rev_id AND e.dst = n.node_id
        WHERE n.rev_id = ? AND n.kind = 'css_class'
        GROUP BY n.node_id HAVING defs = 0 AND uses > 0
        ORDER BY uses DESC""",
    (rev,),
).fetchall()
print(f"  классов без правил в стилях: {len(missing)}")
for r in missing[:30]:
    where = conn.execute(
        """SELECT e.evidence_path, e.evidence_line FROM edge e
            JOIN node n ON n.rev_id = e.rev_id AND n.node_id = e.dst
           WHERE e.rev_id = ? AND e.kind = 'uses_class' AND n.name = ?
           LIMIT 1""",
        (rev, r["name"]),
    ).fetchone()
    place = f"{where['evidence_path']}:{where['evidence_line']}" if where else "?"
    print(f"    .{r['name']:30} использований {r['uses']:>4}  впервые {place}")
check(len(missing) > 0, "расхождение находится автоматически",
      "ни одного класса без правил — проверка не сработала")

print("\n=== 5. Сверка с независимым замером (по файлам, не по графу) ===")
sys.path.insert(0, str(REPO / ".staging"))
from extra_css_measure import collect  # noqa: E402

d = collect(pathlib.Path(cfg.root))
defined = set(d["classes_defined"])
used = set(d["classes_used"])
expected_missing = used - defined
# Все имена классов из узлов: нужны, чтобы отличить «класс не найден»
# от «класс найден и у него есть правило».
node_names = {
    str(r["name"])
    for r in conn.execute(
        "SELECT name FROM node WHERE rev_id = ? AND kind = 'css_class'", (rev,)
    ).fetchall()
}

found_names = {r["name"] for r in missing}
print(f"  замер по файлам: классов без правил {len(expected_missing)}")
print(f"  индекс:          {len(found_names)}")
# Часть расхождений — имена, используемые как класс в разметке, но не являющиеся
# классами оформления (генераторы таблиц подставляют свои значения).
# Проверяется, что все ПРОЕКТНЫЕ классы (с префиксом z-) найдены.
proj_expected = {n for n in expected_missing if n.startswith("z-")}
proj_found = {n for n in found_names if n.startswith("z-")}
print(f"  проектных (z-*) по замеру: {len(proj_expected)}")
print(f"  проектных (z-*) в индексе:  {len(proj_found)}")
# Проверка: все ПРОЕКТНЫЕ классы без правил найдены, кроме обрубков конкатенации.
# `z-agr-status--<?= $s ?>` даёт обрубок `z-agr-status--`, который классом
# не является: он часть собранного имени, и считать его «классом без правил»
# было бы ложным срабатыванием. Проверяется, что ВСЕ отличия — такие обрубки.
stubs_expected = {n for n in proj_expected if n.endswith(("-", "_"))}
check(proj_found == proj_expected - stubs_expected,
      "все проектные классы без правил найдены (кроме обрубков конкатенации)",
      f"не найдено лишнего: {sorted((proj_expected - stubs_expected) - proj_found)}, "
      f"придумано лишнего: {sorted(proj_found - proj_expected)}")
# Отдельно проверяется, что расхождение состоит только из обрубков конкатенации.
# Это ВЕРНОЕ поведение: `z-agr-status--<?= $s ?>` даёт обрубок `z-agr-status--`,
# который классом не является, и считать его «классом без правил» было бы
# ложным срабатыванием. Важно, чтобы таких отличий не было НИ ОДНОГО другого вида.
stubs_only = {n for n in (proj_expected ^ proj_found) if n.endswith(("-", "_"))}
check(stubs_only == (proj_expected ^ proj_found),
      "расхождение состоит только из обрубков конкатенации",
      f"посторонние: {sorted((proj_expected ^ proj_found) - stubs_only)}")
print(f"  обрубков конкатенации отброшено (верно): {len(stubs_only)}")
for name in sorted(stubs_only):
    print(f"    .{name} — не класс, а часть собранного имени")

# Ни один класс не должен быть выдуман: всё, что индекс нашёл без правил,
# обязано встречаться в исходном замере по файлам.
invented = proj_found - used
check(not invented, "индекс не выдумал ни одного класса",
      f"лишние: {sorted(invented)[:10]}")

print("\n=== 6. Отдельно: классы agreements (тот самый дефект) ===")
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
      f"без правил: {agr_no_rules[:10]}")

print("\n=== ИТОГ ===")
print(f"пройдено: {PASS}, провалено: {FAIL}")
conn.close()
raise SystemExit(0 if FAIL == 0 else 1)
