"""Диагностика: почему в тесте CSS-слоя стало 0 классов z-agr без правил.

Тест `test_css_layer.py` ожидал не меньше 20 классов `z-agr-*`, которые
используются в разметке, но не имеют правил в CSS. Он давал провал.

Нужно понять, что произошло:
  а) классы вообще исчезли из индекса (регрессия моих правок);
  б) у них появились правила в CSS (тогда тест устарел — проверять нечего);
  в) изменился способ подсчёта «без правил».

Проверяются все три версии по факту.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
from collections import Counter

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
conn = store.connect(cfg.graph_db, readonly=True)
rev = conn.execute(
    "SELECT rev_id FROM rev WHERE state = 'ready' ORDER BY rev_id DESC LIMIT 1"
).fetchone()["rev_id"]
print(f"корпус: {cfg.project}, ревизия {rev}")

print()
print("=" * 78)
print("1. ЕСТЬ ЛИ КЛАССЫ z-agr В ИНДЕКСЕ ВООБЩЕ")
print("=" * 78)
rows = conn.execute(
    "SELECT node_id, name, path, start_line FROM node WHERE rev_id = ? "
    "AND kind = 'css_class' AND name LIKE 'z-agr%' ORDER BY name LIMIT 40",
    (rev,),
).fetchall()
print(f"  узлов css_class с именем z-agr*: {len(rows)}")
for r in rows[:12]:
    print(f"    {r['name']:<32} {r['path']}:{r['start_line']}")

total_agr = conn.execute(
    "SELECT COUNT(*) AS n FROM node WHERE rev_id = ? AND kind = 'css_class' "
    "AND name LIKE 'z-agr%'",
    (rev,),
).fetchone()["n"]
print(f"\n  всего таких узлов: {total_agr}")

print()
print("=" * 78)
print("2. ЕСТЬ ЛИ У НИХ ПРАВИЛА (defines_class) И ИСПОЛЬЗОВАНИЕ (uses_class)")
print("=" * 78)
stats = conn.execute(
    """SELECT n.name,
              SUM(CASE WHEN e.kind = 'defines_class' THEN 1 ELSE 0 END) AS defs,
              SUM(CASE WHEN e.kind = 'uses_class'    THEN 1 ELSE 0 END) AS uses
         FROM node n
         LEFT JOIN edge e ON e.rev_id = n.rev_id AND e.dst = n.node_id
        WHERE n.rev_id = ? AND n.kind = 'css_class' AND n.name LIKE 'z-agr%'
        GROUP BY n.node_id
        ORDER BY n.name""",
    (rev,),
).fetchall()
no_rules = [r for r in stats if (r["defs"] or 0) == 0 and (r["uses"] or 0) > 0]
print(f"  классов z-agr всего:            {len(stats)}")
print(f"  без правил, но используемых:    {len(no_rules)}")
print(f"  с правилами:                    "
      f"{len([r for r in stats if (r['defs'] or 0) > 0])}")

print("\n  первые 12 без правил:")
for r in no_rules[:12]:
    print(f"    .{r['name']:<30} определений {r['defs']}, использований {r['uses']}")

print()
print("=" * 78)
print("3. ЕСТЬ ЛИ ПРАВИЛА z-agr В ФАЙЛАХ CSS (независимо от индекса)")
print("=" * 78)
# Прямая проверка по файлам: ищем селекторы .z-agr-* в стилях.
css_rules: Counter[str] = Counter()
used_in_markup: Counter[str] = Counter()
import re  # noqa: E402

for path in pathlib.Path(str(cfg.root)).rglob("*"):
    if not path.is_file():
        continue
    rel = str(path.relative_to(cfg.root)).replace("\\", "/")
    if "/.git/" in f"/{rel}" or rel.startswith(("var/", "vendor/", "node_modules/")):
        continue
    low = rel.lower()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    names = set(re.findall(r"\.(z-agr[a-z0-9_-]*)", text))
    if low.endswith(".css"):
        for n in names:
            css_rules[n] += 1
    elif low.endswith((".php", ".htm", ".html")):
        # Использование в разметке: class="... z-agr-..."
        for match in re.finditer(r'class\s*=\s*["\']([^"\']+)["\']', text):
            for token in match.group(1).split():
                if token.startswith("z-agr"):
                    used_in_markup[token] += 1

print(f"  классов z-agr в правилах CSS:      {len(css_rules)}")
print(f"  классов z-agr в разметке:          {len(used_in_markup)}")
only_used = sorted(set(used_in_markup) - set(css_rules))
print(f"  используются, но правил нет:       {len(only_used)}")
for n in only_used[:20]:
    print(f"    .{n}")

print()
print("=" * 78)
print("4. СРАВНЕНИЕ С ПРЕДЫДУЩИМИ РЕВИЗИЯМИ")
print("=" * 78)
revs = [r["rev_id"] for r in conn.execute(
    "SELECT rev_id FROM rev WHERE state = 'ready' ORDER BY rev_id DESC LIMIT 4"
).fetchall()]
print(f"  {'ревизия':<10} {'узлов css_class':>16} {'из них z-agr':>13}")
print("  " + "-" * 42)
for rid in revs:
    all_cls = conn.execute(
        "SELECT COUNT(*) AS n FROM node WHERE rev_id = ? AND kind = 'css_class'",
        (rid,),
    ).fetchone()["n"]
    agr = conn.execute(
        "SELECT COUNT(*) AS n FROM node WHERE rev_id = ? AND kind = 'css_class' "
        "AND name LIKE 'z-agr%'",
        (rid,),
    ).fetchone()["n"]
    print(f"  {rid:<10} {all_cls:>16} {agr:>13}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print(f"""
  Факты:
    узлов z-agr* в индексе:                  {total_agr}
    классов z-agr в правилах CSS (файлы):    {len(css_rules)}
    классов z-agr в разметке (файлы):        {len(used_in_markup)}
    используются, но правил нет:             {len(only_used)}

  Возможные объяснения:
    * если узлов 0, а в файлах классы есть — регрессия индексации;
    * если в правилах CSS теперь есть эти классы — дефект УСТРАНЁН в проекте,
      и тест устарел: проверять больше нечего;
    * если правила есть в CSS, но узлов нет — регрессия разбора CSS.
""")
conn.close()
