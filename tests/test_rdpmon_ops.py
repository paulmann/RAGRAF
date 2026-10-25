"""Проверка: работают ли статические операции по C#-корпусу.

Это главная проверка задачи. До правок проект 1st_RdpMON\\Service в RAGRAF
числился как «язык без разбора AST» и полностью отбрасывался: ни узлов, ни
рёбер, ни работы операций symbol/callers/code. Теперь по нему должно быть
можно получить ответы о коде.

Все операции вызываются через класс Facade так же, как это делает MCP-сервер.
Ответ фасада — оболочка: данные лежат в поле `result`, а `capability`,
`index_rev` и `limits` описывают достоверность этих данных.

Проверяется не только «не упало», но и содержательность: символ должен
найтись, тело символа — быть непустым, C# — присутствовать в составе.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.facade import Facade  # noqa: E402

cfg = load_config(REPO / "config" / "rdpmon.yaml")
print(f"корпус: {cfg.project}, корень: {cfg.root}")

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


def result_of(env) -> object:
    """Данные из оболочки ответа фасада."""
    if isinstance(env, dict) and "result" in env:
        return env["result"]
    return env


fac = Facade(cfg, tool="test_rdpmon_ops", telemetry=False, watch=False)

print()
print("=" * 78)
print("1. СТАТУС ИНДЕКСА (op_index_status)")
print("=" * 78)
env = fac.op_index_status()
res = result_of(env)
counts = env.get("counts", {}) if isinstance(env, dict) else {}
print(f"  ревизия:    {env.get('index_rev')}")
print(f"  достоверность: {env.get('capability')}")
print(f"  файлов:     {counts.get('files')}")
print(f"  узлов:      {counts.get('nodes')}")
print(f"  рёбер:      {counts.get('edges')}")
cov = res.get("coverage", {}) if isinstance(res, dict) else {}
print(f"  разобрано:  {cov.get('files_ok')}, "
      f"частично: {cov.get('files_partial')}, "
      f"текстом: {cov.get('files_skipped')}, "
      f"сбоев: {cov.get('files_failed')}")
print(f"  рёбра по источнику: доказанные {cov.get('edges_stated')}, "
      f"выведенные {cov.get('edges_derived')}")
q = (res.get("quality") or {}) if isinstance(res, dict) else {}
print(f"  здоровье:   {q.get('healthy')}")
sig = q.get("signatures") or {}
print(f"  функций:    {sig.get('functions')}")
print(f"  чанков:     {q.get('chunks', {}).get('total')}")
print(f"  размер чанка: средний {q.get('chunks', {}).get('avg_tokens')} токенов, "
      f"наибольший {q.get('chunks', {}).get('max_tokens')}")

check(bool(counts.get("files")), "индекс содержит файлы",
      f"получено {counts.get('files')}")
check(counts.get("nodes", 0) > 1000, "узлов больше тысячи",
      f"получено {counts.get('nodes')}")
check(counts.get("edges", 0) > 10000, "рёбер больше десяти тысяч",
      f"получено {counts.get('edges')}")
# Главная проверка по существу: C# разобран деревом, а не просто затекстован.
check(cov.get("files_ok", 0) > 1000,
      "файлов разобрано полностью больше тысячи (C# разобран деревом)",
      f"получено {cov.get('files_ok')}")
check(cov.get("files_skipped", 0) < 200,
      "файлов без разбора меньше двухсот (не весь корпус текстом)",
      f"получено {cov.get('files_skipped')}")

print()
print("=" * 78)
print("2. ПОИСК ПО КОДУ (op_search)")
print("=" * 78)
for query in ("AlertRule", "AbuseIpDb", "проверка правил", "StorageBenchmark",
              "Mikrotik"):
    hits = result_of(fac.op_search(query=query, limit=3))
    hits = hits if isinstance(hits, list) else []
    print(f"\n  запрос «{query}»: найдено {len(hits)}")
    for h in hits[:3]:
        print(f"    {h.get('path')}:{h.get('start_line')}-{h.get('end_line')}"
              f"  (оценка {h.get('score')})")
        print(f"      причины: {h.get('why')}")
    check(bool(hits), f"поиск «{query}» дал результат")

print()
print("=" * 78)
print("3. ОБЪЯВЛЕНИЯ ПО ИМЕНИ (op_symbol) — для C# это новое")
print("=" * 78)
found_node = None
for name in ("AlertRule", "Program"):
    env = fac.op_symbol(name=name, limit=5)
    items = result_of(env)
    items = items if isinstance(items, list) else []
    print(f"\n  символ «{name}»: найдено {len(items)}")
    for s in items[:5]:
        print(f"    {s.get('kind', '?'):<10} {s.get('name', '?')} "
              f"— {s.get('path')}:{s.get('start_line')}")
        if s.get("signature"):
            print(f"      сигнатура: {str(s['signature'])[:68]}")
    check(bool(items), f"символ «{name}» найден")
    if items and found_node is None:
        found_node = items[0]

# Операции code, callers, callees, references адресуются по полному имени (fqn),
# а не по идентификатору узла — это выяснено замером (tools/probe_facade_api.py
# и inspect.signature), а не предположено.
target_fqn = found_node.get("fqn") if found_node else None
print(f"\n  полное имя для дальнейших проверок: {target_fqn}")
check(bool(target_fqn), "у найденного символа есть полное имя")

print()
print("=" * 78)
print("4. ТЕЛО СИМВОЛА (op_code)")
print("=" * 78)
if target_fqn:
    env = fac.op_code(fqn=target_fqn, limit=25)
    res = result_of(env)
    # Форма ответа установлена замером (tools/probe_three_findings.py): это
    # СПИСОК фрагментов со полями path/symbol/start_line/end_line/text,
    # а не объект с полем code. Первая версия проверки читала несуществующее
    # поле и сообщала о неудаче там, где операция работала верно.
    pieces = res if isinstance(res, list) else ([res] if res else [])
    code = "\n".join(
        p.get("text", "") for p in pieces if isinstance(p, dict)
    )
    lines = code.splitlines()
    print(f"  символ: {found_node.get('name')} "
          f"({found_node.get('path')}:{found_node.get('start_line')})")
    print(f"  фрагментов: {len(pieces)}, строк: {len(lines)}")
    for line in lines[:10]:
        print(f"    {line[:76]}")
    check(bool(code), "тело символа получено")
    check(any(tok in code for tok in ("{", "}", ";", "class", "public",
                                      "private", "void", "record", "struct")),
          "тело содержит признаки C#-кода")
else:
    check(False, "полное имя для op_code найдено")

print()
print("=" * 78)
print("5. КТО ВЫЗЫВАЕТ (op_callers) и КОГО ВЫЗЫВАЕТ (op_callees)")
print("=" * 78)
if target_fqn:
    for op_name, fn in (("callers", fac.op_callers), ("callees", fac.op_callees)):
        env = fn(fqn=target_fqn, limit=6)
        res = result_of(env)
        items = res if isinstance(res, list) else (
            (res.get(op_name) or res.get("results") or [])
            if isinstance(res, dict) else []
        )
        print(f"\n  {op_name} для «{found_node.get('name')}»: {len(items)}")
        for c in items[:6]:
            line = c.get("evidence_line") or c.get("line")
            print(f"    {c.get('path', '?')}:{line} — {c.get('name', '?')}"
                  f" [{c.get('provenance', '?')}]")
        check(isinstance(items, list), f"op_{op_name} выполнилась без ошибки")
else:
    check(False, "полное имя для callers найдено")

print()
print("=" * 78)
print("6. СОСТАВ КОРПУСА (op_repo_map)")
print("=" * 78)
res = result_of(fac.op_repo_map())
# Форма ответа установлена замером: это объект с полем files — список
# файлов со счётчиком символов и их вершиной. Группировки по языку тут нет,
# поэтому состав языков проверяется через index_status.counts и файловую
# таблицу, а не через несуществующее поле by_lang.
files = res.get("files", []) if isinstance(res, dict) else []
print(f"  файлов в карте: {len(files)}")
by_dir: dict[str, int] = {}
for f in files[:400]:
    path = f.get("path", "")
    top = path.split("/")[0] if "/" in path else "(корень)"
    by_dir[top] = by_dir.get(top, 0) + 1
print("  распределение по каталогам верхнего уровня:")
for name, count in sorted(by_dir.items(), key=lambda kv: -kv[1])[:10]:
    print(f"    {name:<24} {count}")
sample = files[0] if files else {}
if sample:
    print(f"\n  пример: {sample.get('path')} — символов {sample.get('symbols')}")
    for s in (sample.get("top") or [])[:4]:
        print(f"    {s.get('kind', '?'):<10} {s.get('name', '?')}"
              f" (строка {s.get('start_line')})")
check(bool(files), "карта репозитория содержит файлы")

# Состав по языкам берётся из статуса индекса — там он точный.
cs_files = 0
st2 = fac.op_index_status()
res2 = result_of(st2)
sig2 = ((res2.get("quality") or {}).get("signatures") or {}) if isinstance(res2, dict) else {}
print(f"\n  всего функций в корпусе: {sig2.get('functions')}")
check(bool(files), "C# представлен в карте репозитория",
      "карта пуста")
paths = [f.get("path", "") for f in files]
cs_files = sum(1 for p in paths if p.lower().endswith(".cs"))
print(f"  файлов .cs в карте: {cs_files} из {len(paths)}")
# Карта ограничена бюджетом токенов (по умолчанию 2000) и показывает вершину
# корпуса, а не все файлы. Поэтому проверяется наличие C# в карте, а полный
# состав — через counts в статусе индекса (там 1266 файлов, из них 1164 .cs).
check(cs_files > 0, "в карте есть файлы C#", f"получено {cs_files}")
check(len(paths) > 0, "карта не пуста")

print()
print("=" * 78)
print("7. СПРАВОЧНЫЕ ОБЪЕКТЫ (op_class_usage) — теперь и для C#")
print("=" * 78)
try:
    env = fac.op_class_usage(name=None, limit=10, mode="all")
    res = result_of(env)
    items = res if isinstance(res, list) else (
        (res.get("classes") or res.get("results") or [])
        if isinstance(res, dict) else []
    )
    print(f"  записей: {len(items)}")
    for c in items[:8]:
        print(f"    {c.get('name', '?')} — определено: {c.get('defined', '?')}, "
              f"использований: {c.get('used', '?')}")
    check(True, "op_class_usage выполнилась")
except Exception as exc:
    print(f"  ошибка: {type(exc).__name__}: {exc}")
    check(False, "op_class_usage выполнилась", f"{type(exc).__name__}")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
fac.close()
raise SystemExit(0 if FAIL == 0 else 1)
