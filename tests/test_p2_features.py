"""Проверки P2: дифф ревизий, режим `answer`, заметки, метрики эффекта.

Каждая часть отвечает на свой вопрос из ТЗ:
  * P2-1 `ragraf_diff(since=rev)` — что изменилось между ревизиями и не
    сломалось ли что-то (символ без вызывающих, таблица без записи);
  * P2-2 `answer=true` — сформулированный ответ со ссылками, ≤ 2 КБ;
  * P2-3 `note`/`notes` — знание, переживающее сессию;
  * P2-4 `saved_calls` — сколько вызовов чтения заменено индексом.

Работа идёт на ВРЕМЕННЫХ хранилищах: рабочий индекс и журнал не изменяются.
Стиль — самостоятельный скрипт с `check`, как остальные проверки проекта.
"""

from __future__ import annotations

import pathlib
import shutil
import sqlite3
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import code_diff  # noqa: E402
from ragraf.facade import Facade, _answer_sources, _answer_text  # noqa: E402
from ragraf.notes import NotesStore  # noqa: E402
from ragraf.revisions import ControlStore  # noqa: E402
from ragraf.telemetry import CallRecord, Telemetry  # noqa: E402

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


def make_graph(revs: dict[str, dict]) -> sqlite3.Connection:
    """Синтетический граф: ревизия → файлы, узлы, рёбра.

    Схема повторяет настоящую в той части, которую читает `code_diff`: без
    копии схемы проверка доказывала бы работу не того кода.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE file (rev_id TEXT, path TEXT, lang TEXT, lines INTEGER,
                           sha256 TEXT, parse_status TEXT, parse_note TEXT,
                           encoding TEXT);
        CREATE TABLE node (rev_id TEXT, node_id TEXT, kind TEXT, name TEXT,
                           fqn TEXT, path TEXT, start_line INTEGER,
                           end_line INTEGER, signature TEXT, props TEXT, layer TEXT);
        CREATE TABLE edge (rev_id TEXT, edge_uid TEXT, src TEXT, dst TEXT,
                           kind TEXT, provenance TEXT, evidence_path TEXT,
                           evidence_line INTEGER, confidence REAL,
                           sources TEXT, props TEXT);
        CREATE TABLE rev (rev_id TEXT PRIMARY KEY, capability TEXT, state TEXT,
                          file_count INTEGER, node_count INTEGER,
                          edge_count INTEGER, created_at TEXT);
        """
    )
    for rev, data in revs.items():
        conn.execute("INSERT INTO rev VALUES (?,?,?,?,?,?,?)",
                     (rev, data.get("capability", "full"), "ready",
                      len(data["files"]), len(data["nodes"]), len(data["edges"]),
                      "2026-10-14T00:00:00+03:00"))
        for path, sha in data["files"].items():
            conn.execute("INSERT INTO file VALUES (?,?,?,?,?,?,?,?)",
                         (rev, path, "python", 10, sha, "ok", None, "utf-8"))
        for node_id, kind, name, fqn, path, line in data["nodes"]:
            conn.execute("INSERT INTO node VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (rev, node_id, kind, name, fqn, path, line, line + 5,
                          None, "{}", "structural"))
        for src, dst, kind, provenance, confidence, path, line, props in data["edges"]:
            conn.execute("INSERT INTO edge VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (rev, f"{src}-{dst}-{kind}", src, dst, kind, provenance,
                          path, line, confidence, None, props))
    conn.commit()
    return conn


print("=== 1. P2-1: дифф файлов, символов и рёбер ===")
graph = make_graph({
    "rev-0001": {
        "files": {"a.py": "h1", "b.py": "h2", "gone.py": "h3"},
        "nodes": [("n_a", "function", "alpha", "a.py:alpha", "a.py", 1),
                  ("n_gone", "function", "gone", "gone.py:gone", "gone.py", 3)],
        "edges": [("caller", "n_a", "calls", "stated", 1.0, "a.py", 5, "{}"),
                  ("caller", "n_gone", "calls", "stated", 1.0, "a.py", 7, "{}"),
                  ("caller", "@table:orders", "queries", "stated", 1.0, "a.py", 9,
                   '{"access": "insert"}')],
    },
    "rev-0002": {
        "files": {"a.py": "h1", "b.py": "h2_new", "c.py": "h9"},
        "nodes": [("n_a", "function", "alpha", "a.py:alpha", "a.py", 1),
                  ("n_new", "function", "beta", "c.py:beta", "c.py", 2)],
        # у n_a вызывающих не осталось; записи в orders тоже
        "edges": [("caller", "n_a", "calls", "stated", 1.0, "a.py", 5, "{}"),
                  ("caller", "n_new", "calls", "stated", 1.0, "c.py", 4, "{}"),
                  ("caller", "@table:orders", "queries", "stated", 1.0, "a.py", 9,
                   '{"access": "select"}')],
    },
})
diff = code_diff.code_diff(graph, "rev-0001", "rev-0002", limit=10)
check(diff["files"]["counts"] == {"added": 1, "removed": 1, "changed": 1,
                                  "before": 3, "after": 3},
      "файлы: добавлен, удалён, изменён", str(diff["files"]["counts"]))
check(diff["files"]["changed"] == ["b.py"], "изменённый назван по имени",
      str(diff["files"]["changed"]))
check(diff["symbols"]["counts"]["added"] == 1
      and diff["symbols"]["counts"]["removed"] == 1,
      "символы: добавлен и удалён", str(diff["symbols"]["counts"]))
check(diff["edges"]["by_kind"]["calls"]["delta"] == 0,
      "число вызовов не изменилось", str(diff["edges"]["by_kind"]["calls"]))
# `gone` потерял вызов и сам исчез из кода — это регрессия; `alpha` вызов
# сохранил и в списке быть НЕ должен.
check(diff["callers_lost"]["count"] == 1
      and diff["callers_lost"]["items"][0]["name"] == "gone",
      "потерявший вызов назван, сохранивший — нет",
      str(diff["callers_lost"]))
check(diff["callers_lost"]["items"][0]["removed"] is True,
      "исчезнувший символ помечен removed")

print("\n=== 2. P2-1: регрессии — потеря вызывающих и записи ===")
graph2 = make_graph({
    "rev-0001": {
        "files": {"a.py": "h1"},
        "nodes": [("n_a", "function", "alpha", "a.py:alpha", "a.py", 1),
                  ("n_x", "function", "x", "a.py:x", "a.py", 20)],
        "edges": [("caller", "n_x", "calls", "stated", 1.0, "a.py", 5, "{}"),
                  ("caller", "@table:orders", "queries", "stated", 1.0, "a.py", 9,
                   '{"access": "insert"}')],
    },
    "rev-0002": {
        "files": {"a.py": "h1"},
        "nodes": [("n_a", "function", "alpha", "a.py:alpha", "a.py", 1),
                  ("n_x", "function", "x", "a.py:x", "a.py", 20)],
        "edges": [("@table:orders", "@table:orders", "queries", "stated", 1.0,
                   "a.py", 9, '{"access": "select"}')],
    },
})
d2 = code_diff.code_diff(graph2, "rev-0001", "rev-0002", limit=10)
lost = d2["callers_lost"]
check(lost["count"] == 1 and lost["items"][0]["fqn"] == "a.py:x",
      "потерявший вызывающих назван", str(lost["items"]))
check(lost["items"][0]["callers_before"] == 1
      and lost["items"][0]["callers_after"] == 0,
      "до и после названы числами", str(lost["items"][0]))
check(lost["items"][0]["removed"] is False,
      "символ есть в новой ревизии — не помечен удалённым")
wlost = d2["writes_lost"]
check(wlost["count"] == 1 and wlost["items"][0]["table"] == "orders",
      "переставшая заполняться таблица названа", str(wlost["items"]))
check(d2["modes"]["to"]["capability"] == "full", "режим ревизии назван",
      str(d2["modes"]["to"]))
same = code_diff.code_diff(graph2, "rev-0002", "rev-0002")
check(same["same"] is True and "сравнивать не с чем" in same["note"],
      "сравнение ревизии с собой названо честно", str(same.get("note")))

print("\n=== 3. P2-1: заглушки не попадают в «потерявших вызывающих» ===")
graph3 = make_graph({
    "rev-0001": {
        "files": {"a.py": "h1"},
        "nodes": [],
        "edges": [("caller", "@ext:other", "calls_heuristic", "derived", 0.4,
                   "a.py", 5, "{}")],
    },
    "rev-0002": {"files": {"a.py": "h1"}, "nodes": [], "edges": []},
})
d3 = code_diff.code_diff(graph3, "rev-0001", "rev-0002")
check(d3["callers_lost"]["count"] == 0,
      "цель-заглушка (@ext:) в регрессии не попадает",
      str(d3["callers_lost"]))

print("\n=== 4. P2-2: текст ответа и ссылки ===")
items = [
    {"path": "src/a.py", "line": 10, "name": "alpha"},
    {"path": "src/a.py", "line": 10, "name": "alpha — повтор"},
    {"path": "src/b.py", "line": 3},
]
sources, total = _answer_sources(items)
check(len(sources) == 2, "повторы ссылок убраны", str(sources))
check(total == 3, "всего ссылок посчитано верно", str(total))
text = _answer_text("Найдено", sources, total,
                    notes=["порог достоверности: 0.5"])
check(text.startswith("**Найдено**"), "заголовок ответа на месте", text[:40])
check("`src/a.py:10` — alpha" in text, "ссылка с подписью", text)
check("…и ещё 1" in text, "усечение названо числом", text)
check("> порог" in text, "оговорка в ответе", text)
check(len(text) < 2048, "ответ укладывается в 2 КБ", str(len(text)))

print("\n=== 5. P2-3: заметки ===")
tmp = pathlib.Path(tempfile.mkdtemp(prefix="ragraf_notes_"))
try:
    ctrl = ControlStore.__new__(ControlStore)
    ctrl.cfg = None
    ctrl.conn = sqlite3.connect(str(tmp / "control.sqlite"))
    ctrl.conn.row_factory = sqlite3.Row
    notes = NotesStore(ctrl)
    first = notes.add("в MySQL часть колонок генерируемые — писать нельзя",
                      task="task-A", tags=["mysql", "schema"], agent="AutoClaw")
    check(first["source"] == "note", "запись помечена источником note", str(first))
    check(first["tags"] == ["mysql", "schema"], "метки сохранены", str(first["tags"]))
    notes.add("этот каталог — копия, рабочий другой", task="task-B", tags=["layout"])
    check(notes.count() == 2, "две заметки записаны", str(notes.count()))
    check(notes.count(task="task-A") == 1, "счёт по задаче", str(notes.count(task="task-A")))
    only_a = notes.list(task="task-A")
    check(len(only_a) == 1 and only_a[0]["task"] == "task-A",
          "фильтр по задаче работает", str(only_a))
    by_text = notes.list(query="генерируем")
    check(len(by_text) == 1 and "генерируемые" in by_text[0]["text"],
          "поиск по подстроке работает", str(by_text))
    check(notes.list(query="нетакоготекста") == [], "пустой результат — не ошибка")
    order = notes.list()
    check(len(order) == 2, "без фильтров — все заметки", str(len(order)))
    empty_failed = False
    try:
        notes.add("   ")
    except ValueError:
        empty_failed = True
    check(empty_failed, "пустая заметка отклонена")
    long_text = notes.add("x" * 5000)
    check(len(long_text["text"]) == 4000,
          "слишком длинная заметка обрезана до предела",
          str(len(long_text["text"])))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n=== 6. P2-4: saved_calls в журнале ===")
tmp2 = pathlib.Path(tempfile.mkdtemp(prefix="ragraf_saved_"))
try:
    ctrl2 = ControlStore.__new__(ControlStore)
    ctrl2.cfg = None
    ctrl2.conn = sqlite3.connect(str(tmp2 / "control.sqlite"))
    ctrl2.conn.row_factory = sqlite3.Row
    tel = Telemetry(ctrl2, tool="selftest", agent="Probe", task="p2")
    tel.record(CallRecord(op="search", rev_id="r1", capability="full",
                          duration_ms=10.0, found=True, fact_count=3,
                          facts_with_span=3, result_chars=300, error=None,
                          stale=False, missed=False))
    tel.record(CallRecord(op="search", rev_id="r1", capability="full",
                          duration_ms=20.0, found=False, fact_count=0,
                          facts_with_span=0, result_chars=20, error=None,
                          stale=False, missed=True))
    tel.record(CallRecord(op="usage", rev_id="r1", capability="full",
                          duration_ms=1.0, found=True, fact_count=1,
                          facts_with_span=1, result_chars=50, error=None,
                          stale=False, missed=False))
    summary = tel.summary()
    check(summary["saved_calls"] == 1,
          "saved_calls считает только заменяющие операции с фактами",
          str(summary["saved_calls"]))
    check(summary["stale_served"] == 0, "stale_served = 0", str(summary["stale_served"]))
    check(summary["misses"] == 1, "misses считает честное «не найдено»",
          str(summary["misses"]))
    check(summary["hit_rate"] == round(2 / 3, 4),
          "hit_rate — доля вызовов с фактами", str(summary["hit_rate"]))
    check("измерение по журналу" in summary["saved_calls_note"],
          "оговорка о способе счёта есть", summary["saved_calls_note"][:60])
    ctrl2.conn.close()
finally:
    shutil.rmtree(tmp2, ignore_errors=True)

print("\n=== 7. Операции P2 зарегистрированы на всех поверхностях ===")
from ragraf.cli import OPS  # noqa: E402
from ragraf import mcp_server as mcp  # noqa: E402

for op in ("diff", "note", "notes"):
    check(op in OPS, f"операция {op} есть в списке CLI")
for tool in ("ragraf_diff", "ragraf_note", "ragraf_notes"):
    names = [t["name"] for t in mcp.TOOLS]
    check(tool in names, f"инструмент {tool} объявлен")
    check(tool in mcp._TOOL_META, f"у инструмента {tool} есть описание")
check(hasattr(Facade, "op_diff") and hasattr(Facade, "op_note")
      and hasattr(Facade, "op_notes"), "методы операций есть в фасаде")
for tool in ("ragraf_search", "ragraf_search_multi", "ragraf_symbol",
             "ragraf_callers", "ragraf_impact"):
    schema = next(t["inputSchema"] for t in mcp.TOOLS if t["name"] == tool)
    check("answer" in schema["properties"], f"у {tool} есть параметр answer")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
