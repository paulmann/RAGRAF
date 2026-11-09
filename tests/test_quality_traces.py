# -*- coding: utf-8 -*-
"""Трассы подтверждённых сбоев (X7): поведение, а не наличие таблицы.

Что проверяется:

  1. записывается только сбой: пройденный кейс и кейс без состояния — отказ;
  2. трасса несёт всё нужное для повтора: объявление кейса целиком, шаги,
     решения гейта, ревизию и сеанс;
  3. повтор того же сбоя не плодит записей, а возвращает прежний id;
  4. отпечаток канонический: порядок ключей и форматирование на него не влияют,
     а изменение шагов — влияет;
  5. список трасс компактный (без содержимого), `load` отдаёт содержимое;
  6. повтор: `still_failing`, `fixed` только на той же ревизии, `other_rev` на
     другой, `not_reproducible` без трассы и при сбое оценки;
  7. связка с проверкой кейсов: сбой из журнала сеанса записывается с недостающим
     шагом, а после появления шага повтор говорит `fixed`;
  8. границы: ревизия обязательна, шаги обрезаются до предела, а не копятся.

Песочница: свой `control.sqlite` во временном каталоге; живой индекс не
открывается вовсе.
"""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import quality_cases as cases_mod  # noqa: E402
from ragraf import quality_traces as tr  # noqa: E402

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


def sandbox() -> pathlib.Path:
    for base in (os.environ.get("TEMP", ""), os.environ.get("TMP", ""),
                 str(REPO / ".ragraf" / "tmp")):
        if not base:
            continue
        try:
            pathlib.Path(base).mkdir(parents=True, exist_ok=True)
            return pathlib.Path(tempfile.mkdtemp(prefix="ragraf-x7-trace-", dir=base))
        except OSError:
            continue
    raise SystemExit("нет каталога для песочницы")


def journal(path: pathlib.Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    tr.ensure_schema(conn)
    return conn


def case_one(case_id: str = "trace-1") -> dict:
    return {"id": case_id, "kind": "trace", "address": "docs/a.md:10",
            "expected": [], "required_steps": ["ensure", "search"],
            "forbidden": ["raw_read"]}


def failed(missing: list[str] | None = None) -> dict:
    return {"status": "fail", "reason": "нет обязательных шагов",
            "address": "docs/a.md:10",
            "missing_steps": list(missing if missing is not None else ["ensure"]),
            "violations": [], "bypass": [], "missing_paths": []}


def passed() -> dict:
    return {"status": "ok", "reason": "шаги на месте", "address": "",
            "missing_steps": [], "violations": [], "bypass": [],
            "missing_paths": []}


def main() -> int:
    base = sandbox()
    conn = journal(base / "control.sqlite")

    print("=" * 78)
    print("1. СХЕМА, ОТПЕЧАТОК И ЗАКРЫТЫЕ СПИСКИ")
    print("=" * 78)
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table'"
                       " AND name='quality_trace'").fetchone()
    check(row is not None, "таблица трасс создаётся на пустой базе")
    check(tr.ACTIONS == ("save", "list", "replay"),
          "виды вызова — закрытый список", str(tr.ACTIONS))
    check("fixed" in tr.VERDICTS and "other_rev" in tr.VERDICTS,
          "вердикты повтора названы", str(tr.VERDICTS))
    left = {"b": 2, "a": 1}
    right = {"a": 1, "b": 2}
    check(tr.canonical(left) == tr.canonical(right),
          "канонизация гасит порядок ключей")
    check(tr.digest(left) == tr.digest(right), "отпечаток от порядка не зависит")
    check(len(tr.digest({"x": 1})) == 64, "отпечаток — sha256 в hex")
    check(tr.digest({"steps": ["ensure"]}) != tr.digest({"steps": ["ensure", "x"]}),
          "изменение шагов меняет отпечаток")

    print()
    print("=" * 78)
    print("2. ЗАПИСЬ: ТОЛЬКО СБОЙ, ПОЛНЫЙ СОСТАВ, БЕЗ ДУБЛЕЙ")
    print("=" * 78)
    refused = ""
    try:
        tr.save(conn, case=case_one(), result=passed(), steps=["ensure"],
                gate_rows=[], rev="rev-0001", session="s-1")
    except tr.TraceRefusal as exc:
        refused = str(exc)
    check(bool(refused) and "сбой" in refused,
          "пройденный кейс не записывается", refused)
    refused_unknown = ""
    try:
        tr.save(conn, case=case_one(), result={"status": "unknown"},
                steps=[], gate_rows=[], rev="rev-0001", session="s-1")
    except tr.TraceRefusal as exc:
        refused_unknown = str(exc)
    check(bool(refused_unknown), "кейс без состояния не записывается")
    refused_rev = ""
    try:
        tr.save(conn, case=case_one(), result=failed(), steps=[],
                gate_rows=[], rev="", session="s-1")
    except tr.TraceRefusal as exc:
        refused_rev = str(exc)
    check("ревизия" in refused_rev, "трасса без ревизии не записывается",
          refused_rev)

    saved = tr.save(conn, case=case_one(), result=failed(),
                    steps=["ensure", "search"],
                    gate_rows=[{"kind": "raw_read", "verdict": "blocked"}],
                    rev="rev-0001", session="s-1", label="первый сбой")
    check(saved["saved"] is True and saved["id"] > 0, "сбой записан", str(saved))
    stored = tr.load(conn, saved["id"]) or {}
    payload = stored.get("payload") or {}
    check((payload.get("case") or {}).get("id") == "trace-1",
          "объявление кейса сохранено целиком")
    check((payload.get("case") or {}).get("required_steps") == ["ensure", "search"],
          "обязательные шаги сохранены в объявлении")
    check(payload.get("rev") == "rev-0001" and payload.get("session") == "s-1",
          "ревизия и сеанс названы", f"{payload.get('rev')}/{payload.get('session')}")
    check(payload.get("steps") == ["ensure", "search"], "шаги записаны",
          str(payload.get("steps")))
    check((payload.get("gate") or [{}])[0].get("verdict") == "blocked",
          "решение гейта записано")
    check((payload.get("result") or {}).get("missing_steps") == ["ensure"],
          "недостающий шаг записан вместе с трассой")

    again = tr.save(conn, case=case_one(), result=failed(),
                    steps=["ensure", "search"],
                    gate_rows=[{"kind": "raw_read", "verdict": "blocked"}],
                    rev="rev-0001", session="s-1", label="первый сбой")
    check(again["saved"] is False and again["id"] == saved["id"],
          "повтор сбоя возвращает прежний id, а не новый", str(again))
    check(tr.traces(conn)["total"] == 1, "дубль не появился в журнале")

    second = tr.save(conn, case=case_one("trace-2"), result=failed(["search"]),
                     steps=["ensure"], gate_rows=[], rev="rev-0001",
                     session="s-1")
    check(second["saved"] is True and second["id"] != saved["id"],
          "другой сбой — новая запись")
    check(tr.traces(conn)["total"] == 2, "в журнале две разные трассы")

    print()
    print("=" * 78)
    print("3. СПИСОК КОМПАКТНЫЙ, СОДЕРЖИМОЕ — ТОЛЬКО У LOAD")
    print("=" * 78)
    listed = tr.traces(conn)
    check("payload" not in listed["traces"][0],
          "список не тащит содержимое трассы", str(list(listed["traces"][0])[:6]))
    check(listed["traces"][0]["id"] == second["id"],
          "свежая трасса идёт первой")
    one_case = tr.traces(conn, case_id="trace-1")
    check(one_case["total"] == 2 and one_case["shown"] == 1,
          "фильтр по кейсу оставляет одну запись", str(one_case["shown"]))
    check(tr.traces(conn, limit=1)["shown"] == 1, "предел выдачи соблюдается")
    check(tr.load(conn, 999999) is None, "несуществующая трасса не выдумывается")

    print()
    print("=" * 78)
    print("4. ПОВТОР: ВЕРДИКТ НАЗЫВАЕТ РЕВИЗИЮ, А НЕ ТОЛЬКО УСПЕХ")
    print("=" * 78)
    still = tr.replay(conn, saved["id"], rev="rev-0001",
                      evaluator=lambda case, session: failed())
    check(still["verdict"] == "still_failing", "падение осталось падением",
          str(still["verdict"]))
    fixed = tr.replay(conn, saved["id"], rev="rev-0001",
                      evaluator=lambda case, session: passed())
    check(fixed["verdict"] == "fixed" and fixed["same_rev"] is True,
          "на той же ревизии прохождение — исправление", str(fixed["verdict"]))
    moved = tr.replay(conn, saved["id"], rev="rev-0002",
                      evaluator=lambda case, session: passed())
    check(moved["verdict"] == "other_rev" and moved["same_rev"] is False,
          "на другой ревизии прохождение не выдаётся за исправление",
          str(moved["verdict"]))
    check(moved["rev_before"] == "rev-0001" and moved["rev_now"] == "rev-0002",
          "обе ревизии названы")
    missing = tr.replay(conn, 999999, rev="rev-0001",
                        evaluator=lambda case, session: passed())
    check(missing["verdict"] == "not_reproducible" and missing["found"] is False,
          "без трассы повтора нет", str(missing["verdict"]))

    def explode(case: dict, session: str) -> dict:
        raise RuntimeError("оценка недоступна")

    broken = tr.replay(conn, saved["id"], rev="rev-0001", evaluator=explode)
    check(broken["verdict"] == "not_reproducible"
          and "повтор не выполнен" in str(broken["reason"]),
          "сбой оценки назван, а не выдан за прохождение",
          str(broken.get("reason"))[:80])
    check(fixed["status_before"] == "fail" and fixed["status_now"] == "ok",
          "состояние до и после названо")
    check(fixed["steps_before"] == 2, "число шагов прежней трассы сохранено")

    print()
    print("=" * 78)
    print("5. СВЯЗКА С ПРОВЕРКОЙ КЕЙСОВ: ШАГ, СБОЙ, ПОЧИНКА")
    print("=" * 78)
    log = base / "session.sqlite"
    control = sqlite3.connect(log)
    control.execute("CREATE TABLE usage_call (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    " session_id TEXT, ts REAL NOT NULL, op TEXT NOT NULL)")
    for op in ("ensure", "search"):
        control.execute("INSERT INTO usage_call(session_id, ts, op) VALUES(?,?,?)",
                        ("s-real", 1.0, op))
    control.commit()
    real_case = case_one("trace-real")
    real_case["required_steps"] = ["ensure", "quality_suite"]
    result = cases_mod.evaluate_one(real_case, graph=None, control=control,
                                    rev="rev-0001", session="s-real",
                                    gate_state=None)
    check(result["status"] == "fail"
          and result["missing_steps"] == ["quality_suite"],
          "кейс падает на отсутствующем шаге", str(result.get("missing_steps")))
    real_saved = tr.save(conn, case=real_case, result=result,
                         steps=["ensure", "search"], gate_rows=[],
                         rev="rev-0001", session="s-real",
                         case_source="cases.yaml")
    real_payload = (tr.load(conn, real_saved["id"]) or {}).get("payload") or {}
    check((real_payload.get("result") or {}).get("missing_steps")
          == ["quality_suite"], "трасса несёт недостающий шаг из журнала")
    check(real_payload.get("case_source") == "cases.yaml",
          "источник объявления назван")

    def evaluate_again(case: dict, session: str) -> dict:
        return cases_mod.evaluate_one(case, graph=None, control=control,
                                      rev="rev-0001", session=session,
                                      gate_state=None)

    repeated = tr.replay(conn, real_saved["id"], rev="rev-0001",
                         evaluator=evaluate_again)
    check(repeated["verdict"] == "still_failing",
          "повтор на том же журнале снова падает", str(repeated["verdict"]))
    control.execute("INSERT INTO usage_call(session_id, ts, op) VALUES(?,?,?)",
                    ("s-real", 2.0, "quality_suite"))
    control.commit()
    repaired = tr.replay(conn, real_saved["id"], rev="rev-0001",
                         evaluator=evaluate_again)
    check(repaired["verdict"] == "fixed",
          "после появления шага повтор говорит «исправлено»",
          str(repaired["verdict"]))
    control.close()

    print()
    print("=" * 78)
    print("6. ГРАНИЦЫ: ШАГИ ОБРЕЗАЮТСЯ, ЗАПИСЬ НЕ РАСТЁТ ОТ ЧТЕНИЯ")
    print("=" * 78)
    long_steps = [f"op-{index}" for index in range(tr.MAX_STEPS + 120)]
    capped = tr.save(conn, case=case_one("trace-long"), result=failed(),
                     steps=long_steps, gate_rows=[], rev="rev-0001",
                     session="s-long")
    capped_payload = (tr.load(conn, capped["id"]) or {}).get("payload") or {}
    check(len(capped_payload.get("steps") or []) == tr.MAX_STEPS,
          "шаги обрезаны до предела", str(len(capped_payload.get("steps") or [])))
    before = tr.traces(conn)["total"]
    for _ in range(3):
        tr.traces(conn)
        tr.load(conn, capped["id"])
    check(tr.traces(conn)["total"] == before,
          "чтение не добавляет записей")
    check(json.dumps(capped_payload, ensure_ascii=False),
          "содержимое трассы сериализуемо")
    conn.close()

    print()
    print("=" * 78)
    print(f"ИТОГ: проверок {PASS}, успешно {PASS}, провалов {FAIL}")
    print("=" * 78)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
