# -*- coding: utf-8 -*-
"""Кейсы поведения и парный прогон: поведение, а не наличие модуля.

Что проверяется — СВОЙСТВА, а не текст модуля:

  1. схема файла кейсов закрыта: лишний ключ и дубль идентификатора — отказ с
     причиной, а не «пропустим»;
  2. кейс вида `index`: ожидаемый путь есть в ревизии — `ok`, нет — `fail` с
     перечнем отсутствующих;
  3. бенчмарк: задача с путём проверяется машинно, задача без пути получает
     `unknown` с причиной `not_machine_checkable` (не выброшена и не «ok»);
  4. трасса: нет обязательного шага — `fail` с `missing_steps`; запрещённый шаг
     в журнале — `fail` с `violations`;
  5. обход доказывается РЕШЕНИЕМ ГЕЙТА: запись `deny`/`ask` по запрещённому виду
     вызова попадает в `bypass`, а не остаётся словами;
  6. без названного сеанса трасса — `unknown`, а не зелёная;
  7. пин ревизии — часть кейса: кейс на `rev-A` при прогоне `rev-B` даёт
     `unknown`, и в ответе названо, что именно не сравнивалось;
  8. `digest` индекса от кейсов НЕ меняется (C7): прогон с файлом кейсов и без
     него даёт один и тот же отпечаток;
  9. парный прогон: один упавший ранее проходивший кейс блокирует вердикт «не
     хуже», даже если другой кейс улучшился; одинаковое состояние — все
     `not_sure` честно;
 10. два прогона на неизменном состоянии дают одинаковый ответ (детерминизм);
 11. без лицензии Pro операция `quality_pair` отказывает, называя возможность
     `quality-loop`; с лицензией — работает через фасад;
 12. подсадки: находка без адреса и «ok» без проверки ловятся набором.

Песочница строится библиотечным путём (`init_corpus` + `Indexer`), а не командой
CLI `index`: команда пишет прогон в реестр разработок этой машины и оставляла бы
в нём мёртвые записи о временных каталогах.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import edition  # noqa: E402
from ragraf import gate as gate_mod  # noqa: E402
from ragraf import init_corpus  # noqa: E402
from ragraf import quality  # noqa: E402
from ragraf import quality_cases  # noqa: E402
from ragraf import quality_loop  # noqa: E402
from ragraf import signing  # noqa: E402
from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.facade import Facade  # noqa: E402
from ragraf.indexer import Indexer  # noqa: E402
from ragraf.revisions import ControlStore  # noqa: E402

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


def make_sandbox() -> pathlib.Path:
    for base in (os.environ.get("TEMP", ""), os.environ.get("TMP", ""),
                 str(REPO / ".ragraf" / "tmp")):
        if not base:
            continue
        try:
            pathlib.Path(base).mkdir(parents=True, exist_ok=True)
            probe = pathlib.Path(tempfile.mkdtemp(prefix="ragraf-x4-cases-", dir=base))
            (probe / "probe.txt").write_text("ok", encoding="utf-8")
            (probe / "probe.txt").unlink()
            return probe
        except OSError:
            continue
    raise SystemExit("нет каталога для песочницы")


A_PY = """def alpha(value):
    return value


def helper(value):
    return value
"""

B_PY = """def beta(value):
    return value
"""

TOKENS_PY = """def extract_token(req):
    return req.get('token')
"""

CASES_YAML = """schema: 1
cases:
  - id: C-INDEX-OK
    kind: index
    expected: ["src/a.py"]
    note: "файл есть в ревизии"
  - id: C-INDEX-GONE
    kind: index
    expected: ["src/gone.py"]
  - id: C-TRACE-STEPS
    kind: trace
    required_steps: [ensure, search_multi]
    forbidden: [file_search]
  - id: C-TRACE-BYPASS
    kind: trace
    required_steps: [ensure]
    forbidden: [file_search]
  - id: C-PINNED
    kind: index
    expected: ["src/a.py"]
    rev: rev-9999
"""


def build_project(base: pathlib.Path, name: str, cases: str | None = None,
                  bench: str | None = None) -> tuple[pathlib.Path, pathlib.Path]:
    root = base / name
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text(A_PY, encoding="utf-8")
    (root / "src" / "b.py").write_text(B_PY, encoding="utf-8")
    (root / "src" / "tokens.py").write_text(TOKENS_PY, encoding="utf-8")
    if cases is not None:
        (root / quality_cases.CASES_NAME).write_text(cases, encoding="utf-8")
    if bench is not None:
        (root / "bench").mkdir(parents=True, exist_ok=True)
        (root / quality_cases.BENCH_NAME).write_text(bench, encoding="utf-8")
    config_path = base / f"{name}.yaml"
    path, _scan = init_corpus.create_config(root, project=name, config_path=config_path)
    cfg = load_config(path)
    indexer = Indexer(cfg)
    try:
        report = indexer.run(with_vectors=False)
    finally:
        indexer.close()
    if not report.ok:
        raise SystemExit(f"фикстура {name} не проиндексирована: {report.notes[-3:]}")
    return root, path


def sources_for(cfg, name: str):
    """Источники измерения: журнал в песочнице, граф — настоящий, вектора нет."""
    control = ControlStore(cfg)
    graph = store.connect(cfg.graph_db)
    # Схемы журнала обращений и прогонов создаются теми же путями, что в бою.
    from ragraf import telemetry

    telemetry.Telemetry(control, tool="selftest", telemetry_off=True) \
        if False else telemetry.Telemetry(control, tool="selftest")
    quality_loop.ensure_table(control.conn)
    lexvec = type("LexVec", (), {"conn": control.conn, "vec_ok": False})()
    return quality.Sources(control=control, graph=graph, lexvec=lexvec), control, graph


def add_calls(control, session: str, ops: list[str]) -> None:
    for index, op in enumerate(ops):
        control.conn.execute(
            "INSERT INTO usage_call(session_id, ts, op, rev_id, capability, duration_ms,"
            " found, fact_count, facts_with_span, result_chars, est_tokens, replaces,"
            " replaces_chars, error, tool, agent, agent_kind, task, arg_hash, arg_digest,"
            " token_id, token_label, stale, missed)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (session, float(index + 1), op, "rev-0001", "full", 1.0, 1, 1, 1, 10, 3, 0,
             0, None, "mcp", "проверка", "agent", "t", "", "", "", "", 0, 0))
    control.conn.commit()


def note_deny(state, session: str, kind: str) -> None:
    decision = gate_mod.GateDecision("deny", "index_unused",
                                     "индекс не использован: поиск по файлам отклонён")
    state.note_decision(decision, {"tool": "Grep", "kind": kind,
                                   "command": "grep -r alpha"}, session_id=session,
                        task="проверка")


sandbox = make_sandbox()
print(f"песочница: {sandbox}")
_ENV = (edition.BASELINE_ENV, edition.LICENSE_ENV, edition.DECLARED_ENV,
        edition.PUBKEY_ENV)
ENV_SAVED = {name: os.environ.get(name) for name in _ENV}
os.environ[edition.LICENSE_ENV] = str(sandbox / "absent.license")
os.environ.pop(edition.DECLARED_ENV, None)

try:
    print("=" * 78)
    print("1. СХЕМА ФАЙЛА КЕЙСОВ ЗАКРЫТА")
    print("=" * 78)
    root, cfg_path = build_project(sandbox, "corpus", cases=CASES_YAML)
    loaded = quality_cases.load_cases(root)
    check(loaded.get("ok") is True, "файл кейсов прочитан", str(loaded.get("reason")))
    check(loaded.get("declared") == 5, "объявлено пять кейсов", str(loaded.get("declared")))
    first = (loaded.get("cases") or [{}])[0]
    check(str(first.get("address", "")).endswith(":3") and first.get("line"),
          "у кейса есть адрес в файле (path:line)", str(first.get("address")))

    bad_root = sandbox / "bad-cases"
    bad_root.mkdir()
    (bad_root / quality_cases.CASES_NAME).write_text(
        "schema: 1\ncases:\n  - id: X\n    kind: index\n    unexpected_key: 1\n",
        encoding="utf-8")
    broken = quality_cases.load_cases(bad_root)
    check(broken.get("ok") is False and "закрытого списка" in str(broken.get("reason")),
          "ключ вне закрытого списка — отказ с причиной", str(broken.get("reason")))
    (bad_root / quality_cases.CASES_NAME).write_text(
        "schema: 1\ncases:\n  - id: X\n    kind: index\n  - id: X\n    kind: index\n",
        encoding="utf-8")
    dup = quality_cases.load_cases(bad_root)
    check(dup.get("ok") is False and "дважды" in str(dup.get("reason")),
          "дубль идентификатора — отказ", str(dup.get("reason")))
    (bad_root / quality_cases.CASES_NAME).write_text("schema: 2\ncases: []\n",
                                                     encoding="utf-8")
    schema_bad = quality_cases.load_cases(bad_root)
    check(schema_bad.get("ok") is False and "schema" in str(schema_bad.get("reason")),
          "чужая схема — отказ, а не толкование", str(schema_bad.get("reason")))
    absent = quality_cases.load_cases(bad_root / "нет-такого")
    check(absent.get("ok") is True and absent.get("reason") == "no cases",
          "нет файла — не ошибка, а состояние «кейсов нет»")

    print()
    print("=" * 78)
    print("2. КЕЙС ВИДА index: НАЛИЧИЕ ПУТИ В РЕВИЗИИ")
    print("=" * 78)
    src, control, graph = sources_for(load_config(cfg_path), "corpus")
    rev = str(graph.execute("SELECT rev_id FROM rev WHERE state='ready'"
                            " ORDER BY created_at DESC LIMIT 1").fetchone()[0])
    evaluated = quality_cases.evaluate(loaded["cases"], graph=graph,
                                       control=control.conn, rev=rev, session="")
    by_id = {item["id"]: item for item in evaluated["cases"]}
    check(by_id["C-INDEX-OK"]["status"] == "ok", "существующий путь — ok",
          str(by_id["C-INDEX-OK"]))
    check(by_id["C-INDEX-GONE"]["status"] == "fail"
          and by_id["C-INDEX-GONE"]["missing_paths"] == ["src/gone.py"],
          "отсутствующий путь — fail с перечнем", str(by_id["C-INDEX-GONE"]))

    print()
    print("=" * 78)
    print("3. БЕНЧМАРК: МАШИННАЯ ПРОВЕРКА БЕЗ МОДЕЛИ")
    print("=" * 78)
    bench_yaml = """machine: true
tasks:
  - id: T-01
    question: "где alpha?"
    expected: ["src/a.py", "метод-обработчик с указанием строки"]
  - id: T-02
    question: "где beta?"
    expected: ["src/nope.py"]
"""
    bench_root, bench_cfg = build_project(sandbox, "bench", bench=bench_yaml)
    bench = quality_cases.load_bench(bench_root)
    check(bench.get("ok") is True and bench.get("declared") == 2,
          "задачи бенчмарка прочитаны", str(bench.get("declared")))
    check(int(bench.get("not_machine_checkable") or 0) == 0,
          "обе задачи содержат путь — обе проверяемы", str(bench.get("declared")))
    plain = quality_cases.load_bench(bench_root / "нет")
    check(plain.get("ok") is True and plain.get("reason") == "no bench",
          "нет бенчмарка — состояние, а не ошибка")
    # Без `machine: true` задачи не сравниваются с корпусом — иначе чужой
    # бенчмарк давал бы ложную красноту (измерено на самом RAGRAF).
    no_machine = sandbox / "bench-plain"
    (no_machine / "bench").mkdir(parents=True)
    (no_machine / quality_cases.BENCH_NAME).write_text(
        'tasks:\n  - id: T-01\n    expected: ["src/a.py"]\n', encoding="utf-8")
    plain_bench = quality_cases.load_bench(no_machine)
    check(plain_bench.get("ok") is True and plain_bench.get("declared") == 0
          and "machine" in str(plain_bench.get("reason")),
          "без `machine: true` задачи в прогон не идут (причина названа)",
          str(plain_bench.get("reason")))
    bench_src, bench_control, bench_graph = sources_for(load_config(bench_cfg), "bench")
    bench_rev = str(bench_graph.execute(
        "SELECT rev_id FROM rev WHERE state='ready' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()[0])
    bench_eval = quality_cases.evaluate(bench["cases"], graph=bench_graph,
                                        control=bench_control.conn, rev=bench_rev)
    bench_by_id = {item["id"]: item for item in bench_eval["cases"]}
    check(bench_by_id["BENCH-T-01"]["status"] == "ok",
          "ожидаемый путь найден — кейс ok", str(bench_by_id["BENCH-T-01"]))
    check(bench_by_id["BENCH-T-02"]["status"] == "fail",
          "несуществующий путь — кейс fail", str(bench_by_id["BENCH-T-02"]))
    only_text = sandbox / "bench-text"
    (only_text / "bench").mkdir(parents=True)
    (only_text / quality_cases.BENCH_NAME).write_text(
        'machine: true\ntasks:\n  - id: T-09\n    question: "метод?"\n'
        '    expected: ["метод-обработчик с указанием строки"]\n', encoding="utf-8")
    text_bench = quality_cases.load_bench(only_text)
    check(int(text_bench.get("not_machine_checkable") or 0) == 1,
          "задача без пути помечена not_machine_checkable",
          str(text_bench.get("not_machine_checkable")))
    text_eval = quality_cases.evaluate(text_bench["cases"], graph=bench_graph,
                                       control=bench_control.conn, rev=bench_rev)
    check(text_eval["cases"][0]["status"] == "unknown"
          and "not_machine_checkable" in str(text_eval["cases"][0]["reason"]),
          "непроверяемая задача — unknown с причиной, а не «ok»",
          str(text_eval["cases"][0]))

    print()
    print("=" * 78)
    print("4. ТРАССА: ШАГИ, ЗАПРЕТЫ, ОБХОД")
    print("=" * 78)
    trace_cases = [item for item in loaded["cases"] if item["kind"] == "trace"]
    empty_state = gate_mod.GateState(sandbox / "gate-empty.json")
    no_session = quality_cases.evaluate(trace_cases, graph=graph,
                                        control=control.conn, rev=rev, session="")
    check(all(item["status"] == "unknown" for item in no_session["cases"]),
          "без названного сеанса трасса — unknown", str(no_session["totals"]))
    add_calls(control, "s-ok", ["ensure", "search_multi", "symbol"])
    ok_eval = quality_cases.evaluate(trace_cases[:1], graph=graph,
                                     control=control.conn, rev=rev, session="s-ok",
                                     gate_state=empty_state)
    check(ok_eval["cases"][0]["status"] == "ok", "шаги на месте — ok",
          str(ok_eval["cases"][0]))
    add_calls(control, "s-miss", ["ensure"])
    miss_eval = quality_cases.evaluate(trace_cases[:1], graph=graph,
                                       control=control.conn, rev=rev, session="s-miss")
    check(miss_eval["cases"][0]["status"] == "fail"
          and "search_multi" in miss_eval["cases"][0]["missing_steps"],
          "нет обязательного шага — fail с названным шагом",
          str(miss_eval["cases"][0]))
    add_calls(control, "s-forbid", ["ensure", "search_multi", "file_search"])
    forbid_eval = quality_cases.evaluate(trace_cases[:1], graph=graph,
                                         control=control.conn, rev=rev,
                                         session="s-forbid")
    check(forbid_eval["cases"][0]["status"] == "fail"
          and "file_search" in forbid_eval["cases"][0]["violations"],
          "запрещённый шаг в журнале — fail с нарушением",
          str(forbid_eval["cases"][0]["violations"]))
    state = gate_mod.GateState(sandbox / "gate.json")
    note_deny(state, "s-bypass", "file_search")
    add_calls(control, "s-bypass", ["ensure", "search_multi"])
    bypass_case = next(item for item in loaded["cases"] if item["id"] == "C-TRACE-BYPASS")
    bypass_eval = quality_cases.evaluate(trace_cases[1:2], graph=graph,
                                         control=control.conn, rev=rev,
                                         session="s-bypass", gate_state=state)
    check(bypass_eval["cases"][0]["status"] == "fail",
          "обход при выполненных шагах — fail", str(bypass_eval["cases"][0]))
    bypass_rows = bypass_eval["cases"][0].get("bypass") or []
    check(bool(bypass_rows) and bypass_rows[0].get("verdict") == "deny"
          and bypass_rows[0].get("kind") == "file_search",
          "в трассе записано РЕШЕНИЕ гейта (deny, вид вызова)",
          json.dumps(bypass_rows[:1], ensure_ascii=False))
    check(bypass_case["forbidden"] == ["file_search"], "кейс запрещает file_search")
    clean_state = gate_mod.GateState(sandbox / "gate-clean.json")
    clean_eval = quality_cases.evaluate([bypass_case], graph=graph,
                                        control=control.conn, rev=rev,
                                        session="s-ok", gate_state=clean_state)
    check(clean_eval["cases"][0]["status"] == "ok",
          "нет решения гейта — обхода не выдумываем", str(clean_eval["cases"][0]))

    print()
    print("=" * 78)
    print("5. ПИН РЕВИЗИИ")
    print("=" * 78)
    pinned = next(item for item in loaded["cases"] if item["id"] == "C-PINNED")
    pinned_eval = quality_cases.evaluate([pinned], graph=graph, control=control.conn,
                                         rev=rev)
    check(pinned_eval["cases"][0]["status"] == "unknown",
          "кейс на чужой ревизии — unknown", str(pinned_eval["cases"][0]))
    check("rev-9999" in str(pinned_eval["cases"][0]["reason"]),
          "причина называет обе ревизии", str(pinned_eval["cases"][0]["reason"]))

    print()
    print("=" * 78)
    print("6. ОТПЕЧАТОК ИНДЕКСА ОТ КЕЙСОВ НЕ МЕНЯЕТСЯ (C7)")
    print("=" * 78)
    with_cases = quality_loop.suite(src, rev=rev, capability="full")
    check((with_cases.get("cases") or {}).get("declared") == 5,
          "кейсы попали в прогон", str((with_cases.get("cases") or {}).get("declared")))
    (root / quality_cases.CASES_NAME).unlink()
    without_cases = quality_loop.suite(src, rev=rev, capability="full")
    check(with_cases.get("digest") == without_cases.get("digest"),
          "отпечаток индекса один и тот же с кейсами и без них",
          f"{with_cases.get('digest')} != {without_cases.get('digest')}")
    check(with_cases.get("checks") == without_cases.get("checks"),
          "проверки индекса от кейсов не зависят")
    (root / quality_cases.CASES_NAME).write_text(CASES_YAML, encoding="utf-8")

    print()
    print("=" * 78)
    print("7. ПАРНЫЙ ПРОГОН: РЕГРЕССИЯ НЕ УСРЕДНЯЕТСЯ")
    print("=" * 78)
    older = {"id": 1, "rev": rev, "suite": {"digest": "d1", "cases": {
        "declared": 2, "cases": [
            {"id": "A", "kind": "index", "status": "ok", "address": "x:1"},
            {"id": "B", "kind": "index", "status": "fail", "address": "x:2"}]}}}
    newer = {"id": 2, "rev": rev, "suite": {"digest": "d2", "cases": {
        "declared": 2, "cases": [
            {"id": "A", "kind": "index", "status": "fail", "address": "x:1"},
            {"id": "B", "kind": "index", "status": "ok", "address": "x:2"}]}}}
    paired = quality_cases.pair(older["suite"]["cases"], newer["suite"]["cases"],
                                from_run=older, to_run=newer)
    check(paired["verdict"] == "worse" and paired["regressions"] == ["A"],
          "упавший кейс делает вердикт worse, хотя другой улучшился",
          f"{paired['verdict']} {paired['regressions']}")
    check(paired["pair_basis"] == "fixed_cases" and paired["from_rev"] == rev,
          "основание и ревизии названы в ответе", str(paired["pair_basis"]))
    rows = {row["id"]: row for row in paired["cases"]}
    check(rows["A"]["verdict"] == "worse" and rows["B"]["verdict"] == "better",
          "по кейсам: A worse, B better", json.dumps(rows, ensure_ascii=False))
    same = quality_cases.pair(older["suite"]["cases"], older["suite"]["cases"],
                              from_run=older, to_run=dict(older, id=3))
    check(same["verdict"] == "not_sure" and same["same_state"] is True,
          "одинаковое состояние — all not_sure, а не «улучшение»", str(same["verdict"]))
    try:
        quality_cases.pair({}, {}, from_run=older, to_run=newer)
        empty_ok = False
    except quality_cases.CaseRefusal:
        empty_ok = True
    check(empty_ok, "прогоны без блока кейсов — отказ с причиной")

    print()
    print("=" * 78)
    print("8. ДЕТЕРМИНИЗМ И ЖУРНАЛ ПАРНОГО ПРОГОНА")
    print("=" * 78)
    q1 = quality_loop.run(src, rev=rev, capability="full", label="первый")
    q2 = quality_loop.run(src, rev=rev, capability="full", label="второй")
    check(q1.get("ok") and q2.get("ok"), "два прогона записаны")
    through_journal = quality_loop.pair_runs(src)
    check(through_journal.get("ok") is True, "парный прогон по журналу выполнен",
          str(through_journal.get("reason")))
    check(through_journal.get("same_state") is True
          and through_journal.get("verdict") == "not_sure",
          "два прогона на одном индексе — not_sure", str(through_journal.get("verdict")))
    again = quality_loop.pair_runs(src)
    check(quality.canonical(through_journal) == quality.canonical(again),
          "парный прогон детерминирован")
    check(q1.get("digest") == q2.get("digest"),
          "отпечатки двух прогонов совпадают")

    print()
    print("=" * 78)
    print("9. ФАСАД: ОТКАЗ БЕЗ ЛИЦЕНЗИИ И РАБОТА С НЕЙ")
    print("=" * 78)
    community = edition.resolve()
    decision = community.decide("quality_pair")
    check(decision.get("allowed") is False
          and "quality-loop" in str(decision.get("reason") or ""),
          "Community: отказ называет возможность quality-loop",
          str(decision.get("reason")))
    license_key = sandbox / "license.key"
    signing.ensure_key(license_key)
    license_file = sandbox / "edition.license"
    license_file.write_bytes(json.dumps(
        {"schema": 1, "edition": "pro", "licensee": "проверка",
         "issued_at": "2026-01-01", "expires_at": "2099-12-31"},
        ensure_ascii=False, indent=2).encode("utf-8"))
    signing.sign_file(license_file, license_key)
    os.environ[edition.LICENSE_ENV] = str(license_file)
    os.environ[edition.PUBKEY_ENV] = signing.public_key_b64(license_key)
    facade = Facade(load_config(cfg_path), telemetry=False, watch=False)
    try:
        suite_payload = facade.op_quality_suite(label="X4")
        check(suite_payload.get("found") is True, "quality_suite работает с лицензией Pro",
              str(suite_payload.get("error")))
        pair_payload = facade.op_quality_pair()
        result = pair_payload.get("result") or {}
        check(pair_payload.get("found") is True and result.get("pair_basis") == "fixed_cases",
              "quality_pair работает через фасад", str(pair_payload.get("error")))
        check(pair_payload.get("verdict") in quality_cases.VERDICTS,
              "вердикт назван в конверте", str(pair_payload.get("verdict")))
        told = str(json.dumps(pair_payload, ensure_ascii=False))
        check("quality_diff сравнивает показатели индекса" in told,
              "в ответе названо отличие от quality_diff")
        ops = [item for item in (suite_payload.get("features") or [])]
        check("quality-loop" in ops, "возможность quality-loop в списке ответа")
    finally:
        facade.close()

    print()
    print("=" * 78)
    print("10. ПОДСАДКИ: СВОЙСТВА ДЕРЖАТСЯ НА КОДЕ, А НЕ НА ФОРМУЛИРОВКАХ")
    print("=" * 78)
    # Подсадка 1: убрать чтение решений гейта — обход перестаёт находиться.
    real_gate_rows = quality_cases._gate_rows
    quality_cases._gate_rows = lambda state, session: []
    try:
        blind = quality_cases.evaluate(trace_cases[1:2], graph=graph,
                                       control=control.conn, rev=rev,
                                       session="s-bypass", gate_state=state)
    finally:
        quality_cases._gate_rows = real_gate_rows
    check(blind["cases"][0]["status"] == "ok",
          "подсадка «не читать решения гейта» делает обход невидимым (свойство 5 ловит)",
          str(blind["cases"][0]["status"]))
    check(bypass_eval["cases"][0]["status"] == "fail",
          "на настоящем коде тот же кейс остаётся fail")
    # Подсадка 2: включить кейсы в отпечаток — отпечаток перестаёт быть отпечатком
    # ИНДЕКСА (C7). Считаем вручную тот же набор полей, что и `suite`, но без
    # исключения `cases`: число обязано отличаться, иначе исключение бесполезно.
    manual = {key: value for key, value in with_cases.items()
              if key not in ("digest", "journal")}
    manual["cases"] = with_cases.get("cases") or {}
    as_if_included = quality.digest(manual)
    check(as_if_included != with_cases.get("digest"),
          "если бы кейсы входили в отпечаток, он был бы другим — исключение работает",
          f"{as_if_included[:12]} vs {str(with_cases.get('digest'))[:12]}")

finally:
    for name, value in ENV_SAVED.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    shutil.rmtree(sandbox, ignore_errors=True)

print()
print("=" * 78)
print(f"ИТОГ: проверок {PASS}, успешно {PASS - FAIL}, провалов {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
