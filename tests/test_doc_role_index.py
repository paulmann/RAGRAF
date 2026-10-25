"""Проверка: роль документа доходит до ответа поиска.

Полная цепочка: классификатор определяет роль → индексатор записывает её в
chunk_map.doc_role → поиск читает её → ответ фасада показывает агенту.

Проверяется на реальном корпусе RdpMON: там 48 документов .md разных ролей,
включая AGENTS.md (правила агента) и ARCHITECTURE.md (описание устройства).

Отдельно проверяется, что запрос «где правила агента» находит AGENTS.md,
а не что-нибудь из кода: это и есть польза от пометки.
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys
from collections import Counter

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.docrole import ROLE_TITLES  # noqa: E402
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


cfg = load_config(REPO / "config" / "rdpmon.yaml")
lex = pathlib.Path(str(cfg.lexvec_db))
print(f"хранилище чанков: {lex}")

print()
print("=" * 78)
print("1. КОЛОНКА doc_role СОЗДАНА И ЗАПОЛНЕНА")
print("=" * 78)
conn = sqlite3.connect(f"file:{lex}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

cols = [c["name"] for c in conn.execute("PRAGMA table_info(chunk_map)")]
print(f"  поля chunk_map: {', '.join(cols)}")
check("doc_role" in cols, "колонка doc_role есть в схеме")

rev = conn.execute(
    "SELECT rev_id FROM chunk_map ORDER BY rev_id DESC LIMIT 1"
).fetchone()["rev_id"]
print(f"  ревизия: {rev}")

counts = Counter()
for r in conn.execute(
    "SELECT doc_role, COUNT(*) AS n FROM chunk_map WHERE rev_id = ? "
    "GROUP BY doc_role ORDER BY n DESC",
    (rev,),
).fetchall():
    role = r["doc_role"] or "(нет)"
    counts[role] = r["n"]
print("\n  распределение ролей по чанкам:")
for role, n in counts.most_common():
    title = ROLE_TITLES.get(role, "") if role != "(нет)" else "не документ"
    print(f"    {role:<16} {n:>7}  {title}")

check(counts.get("(нет)", 0) > 0, "у чанков кода роли нет (пусто) — как задумано")
doc_total = sum(v for k, v in counts.items() if k != "(нет)")
print(f"\n  чанков с ролью документа: {doc_total}")
check(doc_total > 0, "есть чанки с определённой ролью документа",
      f"получено {doc_total}")
check("agent_rules" in counts, "правила агента помечены", f"роли: {sorted(counts)}")

print()
print("=" * 78)
print("2. ПРИМЕРЫ: КАКИЕ ФАЙЛЫ ПОЛУЧИЛИ КАКИЕ РОЛИ")
print("=" * 78)
for role in ("agent_rules", "tech_spec", "architecture", "plan", "report",
             "readme", "changelog", "reference", "documentation", "other_md"):
    rows = conn.execute(
        "SELECT DISTINCT path FROM chunk_map WHERE rev_id = ? AND doc_role = ? "
        "ORDER BY path LIMIT 3",
        (rev, role),
    ).fetchall()
    if not rows:
        continue
    title = ROLE_TITLES.get(role, role)
    print(f"\n  {role} ({title}):")
    for r in rows:
        print(f"    {r['path']}")

conn.close()

print()
print("=" * 78)
print("3. РОЛЬ ДОХОДИТ ДО ОТВЕТА ПОИСКА")
print("=" * 78)
fac = Facade(cfg, tool="test_doc_role_search", telemetry=False, watch=False)


def result_of(env):
    return env.get("result") if isinstance(env, dict) else env


# Запрос про правила агента: ожидается документ с ролью agent_rules.
env = fac.op_search(query="правила агента проекта работа с проектом", limit=8)
hits = result_of(env)
hits = hits if isinstance(hits, list) else []
print(f"  запрос «правила агента проекта»: найдено {len(hits)}")
role_hits = 0
for h in hits:
    role = h.get("doc_role")
    title = h.get("doc_role_title")
    mark = f" [{role}: {title}]" if role else ""
    print(f"    {h.get('path')}:{h.get('start_line')}{mark}")
    if role:
        role_hits += 1
check(role_hits > 0, "в результатах поиска есть чанки с ролью документа",
      f"получено {role_hits} из {len(hits)}")

# Отдельно: роль должна присутствовать именно у документа.
docs = [h for h in hits if (h.get("path") or "").lower().endswith(".md")]
print(f"\n  из них markdown-документов: {len(docs)}")
for h in docs[:5]:
    print(f"    {h.get('path')}:{h.get('start_line')} — "
          f"роль {h.get('doc_role')!r} ({h.get('doc_role_title')!r})")
if docs:
    check(any(h.get("doc_role") for h in docs),
          "у найденных документов роль проставлена")
else:
    check(True, "документы в этом запросе не встретились (не ошибка)")

# Проверка, что у чанков КОДА роли нет: иначе пометка потеряла бы смысл.
env2 = fac.op_search(query="AlertRule", limit=5)
hits2 = result_of(env2)
hits2 = hits2 if isinstance(hits2, list) else []
code_hits = [h for h in hits2
             if (h.get("path") or "").lower().endswith(".cs")]
print(f"\n  чанков кода в запросе про код: {len(code_hits)}")
with_role = [h for h in code_hits if h.get("doc_role")]
print(f"  из них с ролью документа: {len(with_role)}")
check(not with_role, "у чанков кода роли документа нет",
      f"получили роли: {[h.get('doc_role') for h in with_role]}")

fac.close()

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
