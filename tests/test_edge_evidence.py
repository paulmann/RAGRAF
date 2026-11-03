"""Вид доказательства ребра: подтверждено или выведено (этап 8).

Какой дефект закрывается. До этапа 8 у ребра были `provenance` (stated|derived)
и `confidence`, но не было признака СПОСОБА получения связи. Один и тот же
`stated` означал и «CPG связал вызов с методом по полному имени», и «цель
найдена по имени и файлу владельца», и «вызов встроенной функции языка».
Замер 30.10.2026 на rev-0896: из 11 621 ребра `calls` подтверждение CPG имели
3 941, то есть треть, — а ответ `callers` показывал все как связи.

Что проверяется:

  1. правила классификатора: каждый способ даёт свой вид, и вид не повышается
     сам собой;
  2. доказательство без координаты не считается подтверждением: ребро с
     признаком CPG, но без `path:line`, получает `unknown`;
  3. схема: колонка `evidence_kind` появляется догоняющей миграцией, старая база
     остаётся рабочей (обратная совместимость), `schema_version` сдвигается;
  4. запись: `store.insert_edges` дописывает вид сам, принимает готовый и не
     падает на схеме без колонки (своя DDL в проверках);
  5. ответы: `callers` называет долю выведенных, `edge_evidence` даёт у каждого
     ребра вид и сверенную координату, а ПОДСАДКА «ребро без доказательства
     выдано за подтверждённое» ловится.

Стиль как у остальных проверок проекта: самостоятельный скрипт с `check`.
Работает на одноразовом хранилище в %TEMP%, сеть и чужие корпуса не нужны.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import SCHEMA_VERSION  # noqa: E402
from ragraf import evidence as ev  # noqa: E402
from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.facade import Facade  # noqa: E402
from ragraf.indexer import Indexer  # noqa: E402
from ragraf.init_corpus import create_config  # noqa: E402

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


PHP = """<?php
function ev_target_fn($x) {
    return $x + 1;
}

function ev_caller_fn($y) {
    return ev_target_fn($y);
}
"""


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    section("1. Правила классификатора")
    check(ev.classify("calls", "stated", {"source": "cpg", "resolved_by": "fullname"}, 1.0,
                      "src/a.py", 10) == "cpg_call",
          "CPG по полному имени — подтверждение")
    check(ev.classify("calls", "stated", {"source": "cpg", "resolved_by": "name_file"}, 0.9,
                      "src/a.py", 10) == "cpg_call",
          "CPG по имени и файлу — тоже подтверждение (способ виден отдельно)")
    check(ev.classify("calls_heuristic", "derived", {"source": "cpg", "resolved_by": "name_only"},
                      0.6, "src/a.py", 10) == "name_inference",
          "CPG по одному имени — вывод, а не подтверждение")
    check(ev.classify("calls_heuristic", "derived",
                      {"source": "cpg", "resolved_by": "mro_base"}, 0.5,
                      "src/a.py", 10) == "name_inference",
          "поиск в базовых классах — вывод")
    check(ev.classify("calls", "stated", {}, 1.0, "src/a.py", 10) == "signature_match",
          "ребро разбора деревом без CPG — привязка по имени и месту")
    check(ev.classify("calls", "derived", {}, 0.4, "src/a.py", 10) == "name_inference",
          "низкая достоверность — вывод по имени")
    check(ev.classify("calls_heuristic", "derived", {}, 0.4, "src/a.py", 10) == "name_inference",
          "эвристический вид ребра — всегда вывод")
    check(ev.classify("contains", "stated", {}, 1.0, "src/a.py", 10) == "structural",
          "структурное ребро не выдаётся за вызов")
    check(ev.classify("calls", "stated",
                      {"source": "cpg", "resolved_by": "fullname", "evidence_kind": "name_inference"},
                      1.0, "src/a.py", 10) == "name_inference",
          "записанный вид приоритетнее выведенного")
    check(ev.is_derived("name_inference") and ev.is_derived("unknown")
          and not ev.is_derived("cpg_call"),
          "выводом считаются только name_inference и unknown")

    section("2. Доказательство без координаты — не доказательство")
    check(ev.classify("calls", "stated", {"source": "cpg", "resolved_by": "fullname"},
                      1.0, "", 0) == "unknown",
          "ребро с признаком CPG, но без координаты — вид не определён")
    check(ev.is_derived(ev.classify("calls", "stated",
                                    {"source": "cpg", "resolved_by": "fullname"}, 1.0, "", 0)),
          "такое ребро не попадает в подтверждённые")

    tmp = Path(tempfile.mkdtemp(prefix="ragraf_evidence_"))
    try:
        section("3. Схема: догоняющая миграция и обратная совместимость")
        old = tmp / "old_graph.sqlite"
        conn = sqlite3.connect(str(old))
        conn.executescript(
            """CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
               CREATE TABLE rev (rev_id TEXT PRIMARY KEY, parent_rev TEXT,
                                 created_at TEXT NOT NULL, corpus_hash TEXT NOT NULL DEFAULT '',
                                 config_hash TEXT NOT NULL DEFAULT '',
                                 extractor_versions TEXT NOT NULL DEFAULT '{}',
                                 state TEXT NOT NULL DEFAULT 'ready',
                                 capability TEXT NOT NULL DEFAULT 'full',
                                 joern_used INTEGER NOT NULL DEFAULT 0,
                                 file_count INTEGER NOT NULL DEFAULT 0,
                                 node_count INTEGER NOT NULL DEFAULT 0,
                                 edge_count INTEGER NOT NULL DEFAULT 0,
                                 schema_version INTEGER NOT NULL DEFAULT 1);
               CREATE TABLE edge (rev_id TEXT NOT NULL, edge_uid TEXT NOT NULL,
                                  src TEXT NOT NULL, dst TEXT NOT NULL, kind TEXT NOT NULL,
                                  provenance TEXT NOT NULL, evidence_path TEXT,
                                  evidence_line INTEGER, confidence REAL NOT NULL DEFAULT 1.0,
                                  sources TEXT NOT NULL DEFAULT '[]',
                                  props TEXT NOT NULL DEFAULT '{}',
                                  PRIMARY KEY (rev_id, edge_uid));
               INSERT INTO edge(rev_id, edge_uid, src, dst, kind, provenance, evidence_path,
                                evidence_line, confidence, sources, props)
               VALUES('rev-old', 'u1', 'a', 'b', 'calls', 'stated', 'src/a.py', 5, 1.0, '[]',
                      '{"source": "cpg", "resolved_by": "fullname"}');"""
        )
        conn.commit()
        check("evidence_kind" not in store.edge_columns(conn),
              "до миграции колонки нет (как на базе, построенной до этапа 8)")
        store.init_graph(conn)
        check("evidence_kind" in store.edge_columns(conn), "миграция добавила колонку")
        version = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        check(version is not None and str(version[0]) == str(SCHEMA_VERSION),
              "schema_version записан", f"в базе {version}, ожидается {SCHEMA_VERSION}")
        check(conn.execute("SELECT COUNT(*) FROM edge").fetchone()[0] == 1,
              "старые рёбра сохранены миграцией")
        check(conn.execute("SELECT evidence_kind FROM edge").fetchone()[0] == "",
              "у старого ребра вид пуст — «построено до этапа 8», а не «нет доказательств»")
        # Обратная совместимость чтения: вид восстанавливается по полям ребра.
        row = conn.execute("SELECT kind, provenance, props, confidence, evidence_path,"
                           " evidence_line FROM edge").fetchone()
        restored = ev.classify(row[0], row[1], row[2], row[3], row[4], row[5])
        check(restored == "cpg_call",
              "у старого ребра вид восстановлен по полям, без переиндексации", restored)

        section("4. Запись рёбер: 11 полей, 12 полей, схема без колонки")
        base = ("rev-x", "uid-11", "a", "b", "calls", "stated", "src/a.py", 7, 0.9, "[]",
                '{"source": "cpg", "resolved_by": "name_file"}')
        store.insert_edges(conn, [base])
        got = conn.execute("SELECT evidence_kind FROM edge WHERE edge_uid='uid-11'").fetchone()[0]
        check(got == "cpg_call", "строка из 11 полей: вид вычислен при записи", str(got))
        store.insert_edges(conn, [("rev-x", "uid-12", "a", "c", "calls_heuristic", "derived",
                                   "src/a.py", 8, 0.4, "[]", "{}", "name_inference")])
        got12 = conn.execute("SELECT evidence_kind FROM edge WHERE edge_uid='uid-12'").fetchone()[0]
        check(got12 == "name_inference", "строка из 12 полей: готовый вид принят как есть", str(got12))
        store.insert_edges(conn, [("rev-x", "uid-13", "a", "d", "calls", "stated", "", 0, 1.0,
                                   "[]", '{"source": "cpg", "resolved_by": "fullname"}')])
        got13 = conn.execute("SELECT evidence_kind FROM edge WHERE edge_uid='uid-13'").fetchone()[0]
        check(got13 == "unknown", "ребро без координаты записано как «вид не определён»", str(got13))

        bare = tmp / "bare.sqlite"
        if bare.exists():
            bare.unlink()
        c2 = sqlite3.connect(str(bare))
        c2.executescript(
            """CREATE TABLE edge (rev_id TEXT, edge_uid TEXT, src TEXT, dst TEXT, kind TEXT,
                                  provenance TEXT, evidence_path TEXT, evidence_line INT,
                                  confidence REAL, sources TEXT, props TEXT);"""
        )
        try:
            store.insert_edges(c2, [base, ("rev-x", "uid-12b", "a", "c", "calls", "stated",
                                           "src/a.py", 9, 1.0, "[]", "{}")])
            written = c2.execute("SELECT COUNT(*) FROM edge").fetchone()[0]
        except Exception as exc:  # noqa: BLE001 — падение здесь и есть дефект
            written = -1
            print(f"      исключение: {type(exc).__name__}: {exc}")
        check(written == 2, "схема без колонки принимает запись (не падает)", str(written))
        c2.close()
        conn.close()

        section("5. Живой корпус: ответы называют доказательство")
        src = tmp / "proj" / "src"
        src.mkdir(parents=True)
        (src / "calls.php").write_text(PHP, encoding="utf-8", newline="\n")
        (src / "other.php").write_text("<?php\nfunction ev_other_fn() { return 2; }\n",
                                       encoding="utf-8", newline="\n")
        # Третий файл нужен только чтобы каталог прошёл порог «похоже на проект»
        # (минимум три файла с кодом) — на проверку доказательств он не влияет.
        (src / "third.php").write_text("<?php\nfunction ev_third_fn() { return 3; }\n",
                                       encoding="utf-8", newline="\n")
        (tmp / "proj" / ".gitignore").write_text("probe\n", encoding="utf-8", newline="\n")
        cfg_path, _scan = create_config(tmp / "proj", project="evidenceprobe",
                                        config_path=tmp / "proj" / "evidence.yaml")
        text = cfg_path.read_text(encoding="utf-8").replace(
            "joern_enabled: true", "joern_enabled: false")
        cfg_path.write_text(text, encoding="utf-8", newline="\n")
        cfg = load_config(cfg_path)
        idx = Indexer(cfg)
        try:
            report = idx.run(with_vectors=False, budget_seconds=300)
        finally:
            idx.close()
        check(report.ok, "корпус проиндексирован", str(report.notes))

        fc = Facade(cfg, tool="selftest", telemetry=False, watch=False)
        try:
            callers = fc.op_callers("ev_target_fn", depth=1, limit=50)
            result = callers.get("result") or {}
            check(bool(result.get("items")), "у цели есть вызывающий",
                  str(result.get("count")))
            summary = result.get("evidence") or {}
            check(summary.get("total") == len(result.get("items") or []),
                  "callers называет сводку доказательств по всем связям",
                  f"сводка {summary.get('total')}, связей {len(result.get('items') or [])}")
            check("derived_share" in summary and "confirmed" in summary,
                  "в сводке есть доля выводов и число подтверждённых")
            item = (result.get("items") or [{}])[0]
            check("evidence_kind" in item and "evidence_label" in item,
                  "у каждого ребра ответа назван вид доказательства", str(item.get("evidence_kind")))
            noted = any("вид доказательства" in str(x) for x in (callers.get("limits") or []))
            check(noted or (summary.get("derived") == 0),
                  "доля выводов названа в ограничениях, когда выводы есть",
                  str(callers.get("limits")))

            edges = fc.op_edge_evidence("ev_caller_fn", limit=50)
            eres = edges.get("result") or {}
            check(bool(edges.get("found")), "edge_evidence отвечает на символ",
                  str(edges.get("error")))
            out_items = eres.get("out") or []
            check(bool(out_items), "edge_evidence отдаёт исходящие рёбра", str(eres.get("count")))
            cpg_items = [it for it in out_items if it.get("evidence_kind") == "cpg_call"]
            check(all(str(it.get("path") or "") and int(it.get("line") or 0) > 0
                      for it in cpg_items),
                  "у каждого подтверждённого ребра есть путь и строка",
                  str([(it.get("path"), it.get("line")) for it in cpg_items]))
            check(all(it.get("evidence_ok") for it in cpg_items),
                  "координаты подтверждённых рёбер сверены с файлом ревизии")
            check(not any(it.get("evidence_kind") == "cpg_call" and not it.get("evidence_ok")
                          for it in out_items),
                  "подтверждённым не названо ребро без сверенной координаты")

            section("6. Подсадка: ребро без доказательства выдано за подтверждённое")
            rev = fc.current_rev()
            gconn = store.connect(cfg.graph_db)
            gconn.execute(
                "INSERT OR REPLACE INTO node(rev_id, node_id, kind, name, fqn, path,"
                " start_line, end_line, signature, props, layer)"
                " VALUES(?, 'sbx_fake_a', 'function', 'sbx_fake_a', 'sbx_fake_a',"
                " 'src/calls.php', 6, 8, '', '{}', 'structural')", (rev,))
            gconn.execute(
                "INSERT OR REPLACE INTO edge(rev_id, edge_uid, src, dst, kind, provenance,"
                " evidence_path, evidence_line, confidence, sources, props, evidence_kind)"
                " VALUES(?, 'sbx_fake_edge', 'sbx_fake_a', 'sbx_fake_a', 'calls', 'stated',"
                " '', 0, 1.0, '[]', '{\"source\": \"cpg\", \"resolved_by\": \"fullname\"}', '')",
                (rev,))
            gconn.commit()
            gconn.close()
            planted = fc.op_edge_evidence("sbx_fake_a", limit=20)
            pres = planted.get("result") or {}
            pitems = (pres.get("out") or []) + (pres.get("in") or [])
            fake = [it for it in pitems if it.get("edge_kind") == "calls"]
            check(bool(fake), "подсаженное ребро видно в ответе", str(pres.get("count")))
            check(all(it.get("evidence_kind") != "cpg_call" for it in fake),
                  "подсаженное ребро БЕЗ координаты не названо подтверждённым",
                  str([it.get("evidence_kind") for it in fake]))
            check(all(it.get("evidence_ok") is False for it in fake),
                  "и помечено как непроверяемое по координате",
                  str([it.get("evidence_ok") for it in fake]))
            psummary = pres.get("evidence") or {}
            check(int(psummary.get("confirmed") or 0) == 0,
                  "в сводке подтверждённых ноль: вывод не выдан за факт",
                  str(psummary.get("confirmed")))
            check(any("координата" in str(x) for x in (planted.get("limits") or [])),
                  "ответ называет, что координата не сверена",
                  str(planted.get("limits")))
            gconn = store.connect(cfg.graph_db)
            gconn.execute("DELETE FROM edge WHERE edge_uid = 'sbx_fake_edge'")
            gconn.execute("DELETE FROM node WHERE node_id = 'sbx_fake_a'")
            gconn.commit()
            gconn.close()
        finally:
            fc.close()
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 78)
    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
    print("=" * 78)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())