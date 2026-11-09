"""Провенанс документов и выводов (X1): версии, интервалы, рёбра, зависимые.

Проверяется ПОВЕДЕНИЕ, а не наличие таблиц. Свойства:

  1. `doc_asof` до начала действия — не находит; внутри интервала старой
     редакции — возвращает старую, а новую не подмешивает;
  2. без даты `doc_search` показывает только текущую редакцию, заменённая
     доступна явным `include_superseded` и помечена `superseded`;
  3. `doc_get` по адресу старой редакции отвечает `superseded`, а с датой —
     `current`: адрес не теряется, но и не выдаётся за текущий;
  4. `dependents` идёт по рёбрам до заданной глубины, считает `outside_cap`,
     разделяет `derived` («пересчитать») и `stated` («решение человека»);
  5. ротация ревизии удаляет строки всех новых таблиц, факты живой — целы;
  6. Community получает отказ с названием возможности и издания, Pro работает;
  7. ответ детерминирован: два прогона на неизменном состоянии совпадают;
  8. факт без адреса не выдаётся и считается в `rejected_no_citation`;
  9. кодовый индекс не регрессировал: поиск по коду работает как прежде;
  10. `stale_cascade` отвечает на вопрос «что пересчитать»: глубина,
      `outside_cap`, `apply`/`clear`, отказ на два действия сразу и на
      `apply` без `changed`, пометки читаются без `changed` и не плодят
      записей, факт без адреса отвергается.

Песочница, а не живой корпус: каталог во временном хранилище, живой индекс не
меняется ни на байт. Реестр разработок снимается и возвращается — иначе прогон
сдвигал бы строку реестра в README (измерено 28.10.2026).
"""

from __future__ import annotations

import atexit
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

PASS = 0
FAIL = 0

CATALOG = REPO / "catalog" / "catalog.json"
_CATALOG_BYTES = CATALOG.read_bytes() if CATALOG.exists() else None


def _restore_registry() -> None:
    if _CATALOG_BYTES is None:
        return
    try:
        CATALOG.write_bytes(_CATALOG_BYTES)
    except OSError:
        pass


atexit.register(_restore_registry)


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


def run(args: list[str], timeout: int = 600,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    merged = dict(os.environ)
    merged.pop("RAGRAF_EDITION", None)
    merged.pop("RAGRAF_EDITION_LICENSE", None)
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, "tools/ragraf.py", *args],
        cwd=str(REPO), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, env=merged,
    )


def as_json(result: subprocess.CompletedProcess) -> dict:
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


POLICY_OLD = """---
document_id: policy
valid_from: 2024-01-01
valid_to: 2024-12-31
---

# Политика хранения 2024

Правило MARKER-POLICY-OLD: срок хранения тридцать дней.
"""

POLICY_NEW = """---
document_id: policy
valid_from: 2025-01-01
---

# Политика хранения 2025

Правило MARKER-POLICY-NEW: срок хранения сорок пять дней.
"""

NOTES = """# Заметки без реквизитов

Строка с маркером MARKER-NOTE-UNKNOWN: у этого документа даты нет.
"""

CODE = "def provenance_code_marker():\n    return 'MARKER-CODE-ZETA'\n"


def open_lexvec(cfg_path: pathlib.Path) -> tuple[sqlite3.Connection, dict]:
    from ragraf import config as config_mod

    cfg = config_mod.load(cfg_path)
    conn = sqlite3.connect(str(cfg.lexvec_db))
    conn.row_factory = sqlite3.Row
    return conn, {"cfg": cfg, "graph": str(cfg.graph_db), "lex": str(cfg.lexvec_db)}


with tempfile.TemporaryDirectory(prefix="ragraf-prov-",
                                 ignore_cleanup_errors=True) as tmp:
    base = pathlib.Path(tmp)
    (base / "policy-2024.md").write_text(POLICY_OLD, encoding="utf-8")
    (base / "policy-2025.md").write_text(POLICY_NEW, encoding="utf-8")
    (base / "notes.md").write_text(NOTES, encoding="utf-8")
    (base / "mod.py").write_text(CODE, encoding="utf-8")
    config = base / "corpus.yaml"

    print("=" * 78)
    print("1. INIT И INDEX: ВЕРСИИ ПОПАЛИ В ХРАНИЛИЩЕ")
    print("=" * 78)
    init = run(["init", str(base), "--name", "provenance", "--config", str(config)])
    check(init.returncode == 0, "init выполнен", init.stderr[-300:])
    index = run(["-p", str(config), "index", "--no-vectors"])
    check(index.returncode == 0, "index выполнен",
          (index.stderr or index.stdout)[-600:])

    conn, paths = open_lexvec(config)
    versions = {str(row["path"]): dict(row) for row in conn.execute(
        "SELECT path, doc_id, logical_id, version_id, valid_from, valid_to,"
        " superseded, superseded_by, source FROM document_version"
    )}
    check(len(versions) >= 3, "версии документов записаны", str(len(versions)))
    old = versions.get("policy-2024.md") or {}
    new = versions.get("policy-2025.md") or {}
    notes_row = versions.get("notes.md") or {}
    check(old.get("valid_from") == "2024-01-01" and old.get("valid_to") == "2024-12-31",
          "интервал старой редакции разобран", str(old))
    check(new.get("valid_from") == "2025-01-01", "интервал новой редакции разобран",
          str(new))
    check(int(old.get("superseded") or 0) == 1,
          "старая редакция помечена заменённой", str(old))
    check(str(old.get("superseded_by")) == str(new.get("version_id")),
          "ссылка замены ведёт на новую редакцию", str(old.get("superseded_by")))
    check((notes_row.get("source") or "") == "unknown",
          "документ без реквизитов назван unknown, а не выдуманной датой",
          str(notes_row))
    facts = int(conn.execute("SELECT COUNT(*) FROM fact_assertion").fetchone()[0])
    check(facts > 0, "факты чанков записаны", str(facts))

    print()
    print("=" * 78)
    print("2. DOC_ASOF: РЕДАКЦИЯ НА ДАТУ")
    print("=" * 78)
    past = as_json(run(["--json", "-p", str(config), "query", "doc_asof",
                        "MARKER-POLICY", "--date", "2024-06-01", "--limit", "10"]))
    hits = (past.get("result") or {}).get("hits") or []
    policy_hits = [item for item in hits
                   if str(item.get("path") or "").startswith("policy-")]
    paths_hit = {str(item.get("path")) for item in policy_hits}
    check(past.get("found") is True and paths_hit == {"policy-2024.md"},
          "на 2024-06-01 найдена только старая редакция", str(paths_hit))
    check((past.get("result") or {}).get("asof") == "2024-06-01",
          "ось даты названа в ответе", str(past.get("result", {}).get("asof")))
    check(all(item.get("validity") == "current" for item in policy_hits),
          "найденное помечено current", str([item.get("validity") for item in policy_hits]))
    check(all(item.get("version_id") == old.get("version_id") for item in policy_hits),
          "версия в ответе — версия 2024 года",
          str([item.get("version_id") for item in policy_hits]))

    fresh = as_json(run(["--json", "-p", str(config), "query", "doc_asof",
                         "MARKER-POLICY", "--date", "2025-06-01", "--limit", "10"]))
    fresh_hits = [item for item in (fresh.get("result") or {}).get("hits") or []
                  if str(item.get("path") or "").startswith("policy-")]
    fresh_paths = {str(item.get("path")) for item in fresh_hits}
    check(fresh_paths == {"policy-2025.md"},
          "на 2025-06-01 найдена только новая редакция", str(fresh_paths))

    before = as_json(run(["--json", "-p", str(config), "query", "doc_asof",
                          "MARKER-POLICY", "--date", "2020-01-01", "--limit", "10"]))
    before_policy = [item for item in (before.get("result") or {}).get("hits") or []
                     if str(item.get("path") or "").startswith("policy-")]
    check(not before_policy,
          "до начала действия не найдено ни одной редакции", str(before_policy)[:200])

    unknown = as_json(run(["--json", "-p", str(config), "query", "doc_asof",
                           "MARKER-NOTE", "--date", "2025-06-01", "--limit", "10"]))
    unknown_hits = (unknown.get("result") or {}).get("hits") or []
    check(bool(unknown_hits) and unknown_hits[0].get("validity") == "unknown",
          "документ без интервала не выброшен и назван unknown",
          str(unknown_hits[:1]))

    print()
    print("=" * 78)
    print("3. DOC_SEARCH: ПО УМОЛЧАНИЮ ТОЛЬКО ТЕКУЩАЯ")
    print("=" * 78)
    current = as_json(run(["--json", "-p", str(config), "query", "doc_search",
                           "MARKER-POLICY", "--limit", "10"]))
    current_hits = [item for item in (current.get("result") or {}).get("hits") or []
                    if str(item.get("path") or "").startswith("policy-")]
    current_paths = {str(item.get("path")) for item in current_hits}
    check(current_paths == {"policy-2025.md"},
          "заменённая редакция скрыта по умолчанию", str(current_paths))
    check(int((current.get("result") or {}).get("filtered_by_validity") or 0) >= 1,
          "число скрытых названо", str((current.get("result") or {}).get(
              "filtered_by_validity")))
    check(any("include_superseded" in str(item) for item in current.get("limits") or []),
          "предел называет, как показать заменённые", str(current.get("limits"))[-200:])

    with_old = as_json(run(["--json", "-p", str(config), "query", "doc_search",
                            "MARKER-POLICY", "--limit", "10",
                            "--include-superseded"]))
    old_hits = [item for item in (with_old.get("result") or {}).get("hits") or []
                if str(item.get("path")) == "policy-2024.md"]
    check(bool(old_hits), "явный флаг показывает заменённую редакцию",
          str((with_old.get("result") or {}).get("hits"))[:200])
    check(old_hits and old_hits[0].get("validity") == "superseded",
          "показанная старая редакция помечена superseded",
          str(old_hits[:1]))

    print()
    print("=" * 78)
    print("4. DOC_GET: АДРЕС НЕ ТЕРЯЕТСЯ, НО НЕ ВЫДАЁТСЯ ЗА ТЕКУЩИЙ")
    print("=" * 78)
    locator_old = str(old_hits[0].get("locator")) if old_hits else ""
    got = as_json(run(["--json", "-p", str(config), "query", "doc_get",
                       "--locator", locator_old]))
    got_result = got.get("result") or {}
    check(got.get("found") is True, "адрес старой редакции разрешается", locator_old)
    check(got_result.get("validity") == "superseded",
          "без даты адрес назван заменённым", str(got_result.get("validity")))
    got_asof = as_json(run(["--json", "-p", str(config), "query", "doc_get",
                            "--locator", locator_old, "--asof", "2024-06-01"]))
    check((got_asof.get("result") or {}).get("validity") == "current",
          "с датой тот же адрес назван текущим",
          str((got_asof.get("result") or {}).get("validity")))

    print()
    print("=" * 78)
    print("5. DEPENDENTS: ГЛУБИНА, OUTSIDE_CAP, STATED ПРОТИВ DERIVED")
    print("=" * 78)
    from ragraf import provenance as prov

    seed = conn.execute(
        "SELECT fact_id, rev_id, doc_id, version_id, path, locator FROM fact_assertion"
        " WHERE path = 'policy-2025.md' ORDER BY locator LIMIT 1"
    ).fetchone()
    check(seed is not None, "есть факт-основание", str(seed))
    if seed is not None:
        chain = ["test-fact-A", "test-fact-B", "test-fact-C",
                 "test-fact-D", "test-fact-E"]
        for position, fact_id in enumerate(chain):
            kind = "chunk" if fact_id == "test-fact-C" else "relation"
            provenance = "stated" if fact_id == "test-fact-C" else "derived"
            conn.execute(
                "INSERT OR REPLACE INTO fact_assertion(rev_id, fact_id, doc_id,"
                " version_id, chunk_id, path, locator, kind, provenance, confidence,"
                " valid_from, valid_to, superseded, superseded_by, inputs_count)"
                " VALUES(?, ?, ?, ?, NULL, 'policy-2025.md', ?, ?, ?, 1.0,"
                " '2025-01-01', NULL, 0, '', ?)",
                (seed["rev_id"], fact_id, seed["doc_id"], seed["version_id"],
                 f"policy-2025.md:{100 + position}", kind, provenance,
                 1 if position else 0),
            )
        edges = [(chain[index + 1], chain[index]) for index in range(len(chain) - 1)]
        edges.append(("test-fact-A", str(seed["fact_id"])))
        for derived, inputs in edges:
            conn.execute(
                "INSERT OR REPLACE INTO derivation_edge(rev_id, derived_fact_id,"
                " input_fact_id, kind) VALUES(?, ?, ?, 'derived_from')",
                (seed["rev_id"], derived, inputs),
            )
        conn.commit()

        deps = as_json(run(["--json", "-p", str(config), "query", "dependents",
                            "--fact", "test-fact-A", "--depth", "3"]))
        data = deps.get("result") or {}
        got_ids = [item.get("fact_id") for item in data.get("dependents") or []]
        check(got_ids == ["test-fact-B", "test-fact-C", "test-fact-D"],
              "три шага найдены и упорядочены по расстоянию", str(got_ids))
        check(int(data.get("outside_cap") or 0) == 1,
              "за глубиной назван один факт, а не потерян", str(data.get("outside_cap")))
        check(int((data.get("classified") or {}).get("stated") or 0) == 1,
              "утверждение человека отделено от вывода",
              str(data.get("classified")))
        stated_row = next((item for item in data.get("dependents") or []
                           if item.get("fact_id") == "test-fact-C"), {})
        check(stated_row.get("action") == "решение человека",
              "у stated назван человек, а не пересчёт", str(stated_row))

        only_derived = as_json(run(["--json", "-p", str(config), "query",
                                    "dependents", "--fact", "test-fact-A",
                                    "--depth", "3", "--no-stated"]))
        only_data = only_derived.get("result") or {}
        check(int(only_data.get("skipped_stated") or 0) == 1,
              "--no-stated скрывает утверждение и считает его",
              str(only_data.get("skipped_stated")))

        by_address = as_json(run(["--json", "-p", str(config), "query", "dependents",
                                  "--locator", str(seed["locator"]), "--depth", "1"]))
        check((by_address.get("result") or {}).get("resolved_by") == "locator",
              "адрес разрешается в факт", str(by_address.get("result", {}).get(
                  "resolved_by")))

    print()
    print("=" * 78)
    print("5б. STALE_CASCADE: ЧТО ПЕРЕСЧИТАТЬ, ПОМЕТКИ И ЗАКРЫТИЕ")
    print("=" * 78)
    if seed is not None:
        # Факт без адреса в цепочке: он обязан быть отвергнут, а не показан.
        conn.execute(
            "INSERT OR REPLACE INTO fact_assertion(rev_id, fact_id, doc_id,"
            " version_id, chunk_id, path, locator, kind, provenance, confidence,"
            " valid_from, valid_to, superseded, superseded_by, inputs_count)"
            " VALUES(?, 'test-fact-NOADDR', ?, ?, NULL, 'policy-2025.md', '',"
            " 'relation', 'derived', 1.0, '2025-01-01', NULL, 0, '', 1)",
            (seed["rev_id"], seed["doc_id"], seed["version_id"]))
        conn.execute(
            "INSERT OR REPLACE INTO derivation_edge(rev_id, derived_fact_id,"
            " input_fact_id, kind) VALUES(?, 'test-fact-NOADDR', 'test-fact-D',"
            " 'derived_from')", (seed["rev_id"],))
        conn.commit()

        def cascade_call(*extra: str) -> dict:
            return as_json(run(["--json", "-p", str(config), "query",
                                "stale_cascade", *extra]))

        one_step = cascade_call("--changed", "test-fact-A", "--depth", "1")
        data = one_step.get("result") or {}
        ids = [item.get("fact_id") for item in data.get("plan") or []]
        check(ids == ["test-fact-B"], "глубина 1 даёт только ближайший вывод", str(ids))
        check(all(item.get("action") == "пересчитать"
                  for item in data.get("plan") or []),
              "вывод назван пересчётом", str(data.get("plan")))
        check(all(str(item.get("locator") or "").startswith("policy-2025.md:")
                  for item in data.get("plan") or []),
              "у каждой строки есть адрес", str(data.get("plan")))

        two_steps = cascade_call("--changed", "test-fact-A", "--depth", "2")
        two = two_steps.get("result") or {}
        two_ids = [item.get("fact_id") for item in two.get("plan") or []]
        check(two_ids == ["test-fact-B", "test-fact-C"],
              "глубина 2 доходит до утверждения человека", str(two_ids))
        stated = next((item for item in two.get("plan") or []
                       if item.get("fact_id") == "test-fact-C"), {})
        check(stated.get("action") == "решение человека",
              "утверждение человека не выдано за пересчёт", str(stated))
        check((two.get("counts") or {}).get("to_recompute") == 1
              and (two.get("counts") or {}).get("stated") == 1,
              "счётчики разделяют вывод и утверждение", str(two.get("counts")))

        three_steps = cascade_call("--changed", "test-fact-A", "--depth", "3")
        three = three_steps.get("result") or {}
        three_ids = [item.get("fact_id") for item in three.get("plan") or []]
        check("test-fact-D" in three_ids, "глубина 3 доходит до D", str(three_ids))
        check(int((three.get("counts") or {}).get("outside_cap") or 0) == 1,
              "факт за пределом назван, а не потерян",
              str((three.get("counts") or {}).get("outside_cap")))
        check("test-fact-NOADDR" not in three_ids,
              "факт без адреса не выдан", str(three_ids))
        deep = cascade_call("--changed", "test-fact-A", "--depth", "4")
        deep_data = deep.get("result") or {}
        deep_ids = [item.get("fact_id") for item in deep_data.get("plan") or []]
        check("test-fact-NOADDR" not in deep_ids
              and int((deep_data.get("counts") or {}).get(
                  "rejected_no_citation") or 0) >= 1,
              "факт без адреса посчитан в rejected_no_citation",
              str(deep_data.get("counts")))

        four = cascade_call("--changed", "test-fact-A", "--depth", "4")
        rows = (four.get("result") or {}).get("plan") or []
        row_d = next((item for item in rows if item.get("fact_id") == "test-fact-D"),
                     {})
        check(row_d.get("cause_fact_id") == "test-fact-A",
              "причина названа изменённым входом, а не соседом", str(row_d))
        check(row_d.get("input_fact_id") == "test-fact-C",
              "ближайший вход назван отдельно", str(row_d))

        applied = cascade_call("--changed", "test-fact-A", "--depth", "2",
                               "--apply")
        applied_counts = (applied.get("result") or {}).get("counts") or {}
        check(applied_counts.get("marked") == 1,
              "пометка поставлена только выводу", str(applied_counts))
        check(applied_counts.get("skipped_stated") == 1,
              "утверждение человека не помечено", str(applied_counts))

        marks = cascade_call()
        mark_rows = (marks.get("result") or {}).get("plan") or []
        check([item.get("fact_id") for item in mark_rows] == ["test-fact-B"],
              "пометки читаются без changed", str(mark_rows))
        again = cascade_call("--changed", "test-fact-A", "--depth", "2", "--apply")
        again_counts = (again.get("result") or {}).get("counts") or {}
        check(again_counts.get("marked") == 0
              and again_counts.get("repeated") == 1,
              "повторная пометка не плодит записей", str(again_counts))

        cleared = cascade_call("--clear-marks")
        check((cleared.get("result") or {}).get("cleared") == 1,
              "закрытие считает снятые пометки", str(cleared.get("result")))
        check(((cascade_call().get("result") or {}).get("plan") or []) == [],
              "после закрытия пометок не осталось")

        both = cascade_call("--changed", "test-fact-A", "--apply", "--clear-marks")
        check(both.get("found") is False
              and "одно действие" in str(both.get("error") or ""),
              "apply и clear вместе — отказ", str(both.get("error"))[:160])
        blind = cascade_call("--apply")
        check(blind.get("found") is False
              and "что изменилось" in str(blind.get("error") or ""),
              "apply без changed — отказ", str(blind.get("error"))[:160])

        lost_ref = cascade_call("--changed", "нетакого-факта")
        check(int((lost_ref.get("result") or {}).get("counts", {}).get(
                  "unresolved") or 0) == 1,
              "неразрешённая ссылка названа, а не проглочена",
              str((lost_ref.get("result") or {}).get("counts")))

        by_locator = cascade_call("--changed", str(seed["locator"]), "--depth", "1")
        seed_rows = (by_locator.get("result") or {}).get("seeds") or []
        check(len(seed_rows) == 1 and seed_rows[0].get("resolved_by") == "locator",
              "адрес разрешается в факт", str(seed_rows))

        first = cascade_call("--changed", "test-fact-A", "--depth", "3")
        second = cascade_call("--changed", "test-fact-A", "--depth", "3")
        check((first.get("result") or {}).get("plan")
              == (second.get("result") or {}).get("plan"),
              "два прогона дают одинаковый отчёт")

        closed = run(["--json", "-p", str(config), "query", "stale_cascade",
                      "--changed", "test-fact-A"],
                     env={"RAGRAF_EDITION": "community",
                          "RAGRAF_EDITION_LICENSE": ""})
        closed_data = as_json(closed)
        refused = closed_data.get("refused") if isinstance(
            closed_data.get("refused"), dict) else closed_data
        check(closed_data.get("found") is not True
              and (refused.get("feature") == "provenance"
                   or "provenance" in str(closed_data)),
              "Community получает отказ с названием возможности",
              str(closed_data)[:200])

    print()
    print("=" * 78)
    print("6. COMMUNITY: ОТКАЗ С ВОЗМОЖНОСТЬЮ И СЛЕДУЮЩИМ ДЕЙСТВИЕМ")
    print("=" * 78)
    closed = run(["--json", "-p", str(config), "query", "doc_asof",
                  "MARKER-POLICY", "--date", "2025-06-01"],
                 env={"RAGRAF_EDITION": "community", "RAGRAF_EDITION_LICENSE": ""})
    closed_data = as_json(closed)
    refused = closed_data.get("refused") or {}
    check(closed_data.get("found") is False and bool(refused),
          "Community получает отказ, а не пустоту", str(closed_data)[:300])
    check(refused.get("feature") == "provenance"
          and refused.get("requires_edition") == "pro",
          "отказ называет возможность и нужное издание", str(refused))

    print()
    print("=" * 78)
    print("7. ДЕТЕРМИНИЗМ")
    print("=" * 78)
    first = as_json(run(["--json", "-p", str(config), "query", "doc_asof",
                         "MARKER-POLICY", "--date", "2024-06-01"]))
    second = as_json(run(["--json", "-p", str(config), "query", "doc_asof",
                          "MARKER-POLICY", "--date", "2024-06-01"]))
    check((first.get("result") or {}) == (second.get("result") or {}),
          "два прогона дают одинаковый результат")

    print()
    print("=" * 78)
    print("8. РОТАЦИЯ: СТРОКИ СТАРОЙ РЕВИЗИИ УДАЛЯЮТСЯ, ЖИВОЙ — ЦЕЛЫ")
    print("=" * 78)
    from ragraf import config as config_mod
    from ragraf import storage as storage_mod

    cfg = config_mod.load(config)
    graph = sqlite3.connect(str(cfg.graph_db))
    old_rev = str((graph.execute(
        "SELECT rev_id FROM rev ORDER BY created_at ASC LIMIT 1"
    ).fetchone() or [""])[0])
    graph.close()
    check(bool(old_rev), "старая ревизия найдена", old_rev)

    tables = set()
    lex_conn = sqlite3.connect(str(cfg.lexvec_db))
    tables = set(storage_mod.tables_with_rev(lex_conn))
    lex_conn.close()
    check({"document_version", "fact_assertion", "derivation_edge"} <= tables,
          "политика хранения видит все три новые таблицы", str(sorted(tables)))

    (base / "extra.md").write_text("# Дополнение\n\nMARKER-EXTRA.\n", encoding="utf-8")
    second_index = run(["-p", str(config), "index", "--no-vectors"])
    check(second_index.returncode == 0, "вторая ревизия собрана",
          (second_index.stderr or second_index.stdout)[-400:])
    graph = sqlite3.connect(str(cfg.graph_db))
    active_rev = str((graph.execute(
        "SELECT rev_id FROM rev ORDER BY created_at DESC LIMIT 1"
    ).fetchone() or [""])[0])
    graph.close()
    check(active_rev and active_rev != old_rev, "активная ревизия сменилась",
          f"{old_rev} -> {active_rev}")
    conn.close()
    from ragraf import revisions as revisions_mod

    control = revisions_mod.ControlStore(cfg)
    try:
        rotated = control.rotate(1, batch=1)
    finally:
        control.close()
    check(old_rev in rotated, "ротация удалила старую ревизию", str(rotated))
    conn = sqlite3.connect(str(cfg.lexvec_db))
    old_facts = int(conn.execute(
        "SELECT COUNT(*) FROM fact_assertion WHERE rev_id = ?", (old_rev,)
    ).fetchone()[0])
    live_facts = int(conn.execute(
        "SELECT COUNT(*) FROM fact_assertion WHERE rev_id = ?", (active_rev,)
    ).fetchone()[0])
    check(old_facts == 0, "факты удалённой ревизии ушли", str(old_facts))
    check(live_facts > 0, "факты живой ревизии целы", str(live_facts))

    print()
    print("=" * 78)
    print("9. КОДОВЫЙ ИНДЕКС НЕ РЕГРЕССИРОВАЛ")
    print("=" * 78)
    code_hit = as_json(run(["--json", "-p", str(config), "query", "search",
                            "MARKER-CODE-ZETA", "--limit", "3"]))
    check(code_hit.get("found") is True,
          "поиск по коду находит маркер", str(code_hit)[:200])

print()
print("=" * 78)
print(f"ИТОГ: {PASS} пройдено, {FAIL} провалено")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
