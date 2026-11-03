"""Замер точности `impact` и `callers` на контрольном наборе: ДО и ПОСЛЕ.

Набор задан ЗАРАНЕЕ и записан в этом файле — 12 вопросов о реальных символах корпуса RAGRAF.
Эталон — список файлов, которые обязаны появиться в ответе (руками, по устройству кода, до
прогона).

Метрики `impact`:

  * `hit` — доля вопросов, где ХОТЯ БЫ ОДИН эталонный файл попал в `affected_files`;
  * `full` — доля вопросов, где ВСЕ эталонные файлы попали;
  * `stated_only` — доля вопросов, где ответ не содержит ни одной эвристической связи;
  * `files_avg` — среднее число файлов в ответе (чем больше, тем менее точен ответ).

Метрики `callers` (этап 8 — доказательность графа):

  * `confirmed_share` — доля рёбер, ПОДТВЕРЖДЁННЫХ CPG (`evidence_kind = cpg_call`).
    До этапа 8 это число нельзя было получить вовсе: ответ называл только
    `stated`/`derived`, и привязка по имени считалась доказанной связью;
  * `by_evidence` — разбивка по видам доказательства (`signature_match` —
    привязка по имени и месту объявления, `name_inference` — вывод по имени);
  * `dangling_share` — доля рёбер, ведущих в никуда: у соседа нет файла в
    корпусе (заглушка `external`) либо координата вызова не сверяется с файлом.
    Такие рёбра нельзя проверить глазами, и они не должны считаться связью.

Печатается JSON и таблица; `--save <файл>` пишет снимок, чтобы сравнить до/после.

Запуск: python tools/measure_impact_precision.py [--section impact|callers|both]
                                                  [--save %TEMP%\\edges-after.json]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

#: Контрольный набор. Каждый вопрос: цель (символ), эталонные файлы (хотя бы один обязан быть),
#: и ожидание — доказана связь или выведена. Составлен 30.10.2026 по устройству кода: цель —
#: функция/класс, эталон — файл, который её действительно использует.
QUESTIONS: list[dict] = [
    {"target": "dedupe", "expect_any": ["src/ragraf/indexer.py"]},
    {"target": "scan_corpus", "expect_any": ["src/ragraf/indexer.py", "src/ragraf/watch.py"]},
    {"target": "active_revision", "expect_any": ["src/ragraf/watch.py", "src/ragraf/admin_server.py",
                                                 "src/ragraf/neighbors.py"]},
    {"target": "record_index_run", "expect_any": ["src/ragraf/indexer.py", "src/ragraf/watch.py"]},
    {"target": "corpus_hash", "expect_any": ["src/ragraf/watch.py", "src/ragraf/indexer.py"]},
    {"target": "_bfs", "expect_any": ["src/ragraf/facade.py"]},
    {"target": "SettingsStore", "expect_any": ["src/ragraf/admin_server.py", "src/ragraf/facade.py"]},
    {"target": "validate", "expect_any": ["src/ragraf/admin_server.py"]},
    {"target": "to_posix", "expect_any": ["src/ragraf/facade.py", "src/ragraf/indexer.py"]},
    {"target": "write_json", "expect_any": ["src/ragraf/settings.py", "src/ragraf/catalog.py",
                                            "src/ragraf/facade.py"]},
    {"target": "read_json", "expect_any": ["src/ragraf/settings.py", "src/ragraf/catalog.py"]},
    {"target": "scan", "expect_any": ["src/ragraf/indexer.py", "src/ragraf/watch.py"]},
]


def measure_impact(facade, mode: str) -> dict:
    """Точность `impact`: попадание в эталонные файлы и доля эвристики."""
    hits = 0
    full = 0
    stated_only = 0
    counted = 0
    files_total = 0
    rows: list[dict] = []
    for question in QUESTIONS:
        answer = facade.op_impact(question["target"], mode=mode)
        result = answer.get("result") or {}
        if not answer.get("found"):
            rows.append({"target": question["target"], "status": "не найден"})
            continue
        counted += 1
        got = {item["path"] for item in (result.get("affected_files") or [])}
        expected = set(question["expect_any"])
        hit = bool(got & expected)
        complete = expected.issubset(got)
        share = float(result.get("heuristic_share") or 0.0)
        hits += hit
        full += complete
        stated_only += (share == 0.0)
        files_total += len(got)
        rows.append({
            "target": question["target"],
            "found": True,
            "files": len(got),
            "hit": hit,
            "full": complete,
            "heuristic_share": share,
            "edge_kinds": result.get("edge_kinds") or [],
            "evidence": result.get("evidence") or {},
        })
    return {
        "questions": len(QUESTIONS),
        "counted": counted,
        "hit": hits,
        "hit_share": round(hits / counted, 4) if counted else 0.0,
        "full": full,
        "full_share": round(full / counted, 4) if counted else 0.0,
        "stated_only": stated_only,
        "stated_only_share": round(stated_only / counted, 4) if counted else 0.0,
        "files_avg": round(files_total / counted, 1) if counted else 0.0,
        "mode": mode,
        "rows": rows,
    }


def measure_callers(facade) -> dict:
    """Точность `callers`: сколько подтверждено, сколько выведено, сколько в никуда.

    ДВА СРЕЗА, и без второго замер врал бы. `callers` отдаёт только рёбра,
    прошедшие порог достоверности (`MIN_EDGE_CONFIDENCE`), поэтому выведенные по
    имени связи с доверием 0.4 в ответ не попадают — и «выведено 0 %» читалось
    бы как «в графе нет выводов». Замер 30.10.2026: по ответу `callers` вышло
    0 %, а по всем входящим рёбрам (`edge_evidence`) — 77,8 %.

      * `visible` — то, что видит агент в ответе `callers`: рёбра выше порога,
        плюс отдельно число связей, отсечённых порогом
        (`below_confidence_floor`);
      * `all_edges` — все входящие рёбра без порога (операция `edge_evidence`):
        настоящая доля подтверждённых CPG и выведенных.

    «Ведёт в никуда» — у соседа нет файла в корпусе (заглушка `external`) либо
    координата вызова не сверяется с файлом ревизии (`evidence_ok = false`).
    Такое ребро нельзя проверить глазами, и связью оно не считается.
    """
    visible = {"edges": 0, "confirmed": 0, "derived": 0, "dangling": 0, "below_floor": 0}
    all_edges = {"edges": 0, "confirmed": 0, "derived": 0, "dangling": 0}
    by_evidence: dict[str, int] = {}
    counted = 0
    rows: list[dict] = []
    for question in QUESTIONS:
        answer = facade.op_callers(question["target"], depth=1, limit=200)
        result = answer.get("result") or {}
        if not result:
            rows.append({"target": question["target"], "status": "не найден"})
            continue
        counted += 1
        items = result.get("items") or []
        ev = result.get("evidence") or {}
        void = sum(1 for it in items
                   if str(it.get("kind") or "") == "external" or not it.get("path"))
        visible["edges"] += len(items)
        visible["confirmed"] += int(ev.get("confirmed") or 0)
        visible["derived"] += int(ev.get("derived") or 0)
        visible["dangling"] += void
        visible["below_floor"] += int(result.get("below_confidence_floor") or 0)

        all_answer = facade.op_edge_evidence(question["target"], limit=400)
        all_result = all_answer.get("result") or {}
        incoming = all_result.get("in") or []
        all_void = sum(1 for it in incoming
                       if str(it.get("other_kind") or "") == "external"
                       or not it.get("evidence_ok"))
        for it in incoming:
            kind = str(it.get("evidence_kind") or "unknown")
            by_evidence[kind] = by_evidence.get(kind, 0) + 1
        all_edges["edges"] += len(incoming)
        all_edges["confirmed"] += sum(1 for it in incoming
                                      if it.get("evidence_kind") == "cpg_call")
        all_edges["derived"] += sum(1 for it in incoming
                                    if it.get("evidence_kind") in ("name_inference", "unknown"))
        all_edges["dangling"] += all_void
        rows.append({
            "target": question["target"],
            "found": True,
            "visible_edges": len(items),
            "visible_derived": int(ev.get("derived") or 0),
            "below_floor": int(result.get("below_confidence_floor") or 0),
            "all_edges": len(incoming),
            "all_confirmed": sum(1 for it in incoming
                                 if it.get("evidence_kind") == "cpg_call"),
            "all_derived": sum(1 for it in incoming
                               if it.get("evidence_kind") in ("name_inference", "unknown")),
            "dangling": all_void,
        })

    def shares(block: dict) -> dict:
        total = block["edges"]
        for key in ("confirmed", "derived", "dangling"):
            block[f"{key}_share"] = round(block[key] / total, 4) if total else 0.0
        return block

    shares(visible)
    shares(all_edges)
    return {
        "questions": len(QUESTIONS),
        "counted": counted,
        "visible": visible,
        "all_edges": all_edges,
        "by_evidence": [
            {"evidence_kind": kind, "count": count,
             "share": round(count / all_edges["edges"], 4) if all_edges["edges"] else 0.0}
            for kind, count in sorted(by_evidence.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Точность impact и callers на контрольном наборе")
    parser.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parents[1]))
    parser.add_argument("--save", default="")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--section", default="both", choices=["impact", "callers", "both"])
    parser.add_argument("--mode", default="full", choices=["full", "hard", "all"],
                        help="режим обхода impact: full — порог 0,5; all — без порога; "
                             "hard — только доказанное")
    args = parser.parse_args()
    root = pathlib.Path(args.root)
    sys.path.insert(0, str(root / "src"))

    from ragraf import config as config_mod
    from ragraf.facade import Facade

    cfg_path = root / "config" / "ragraf.yaml"
    if not cfg_path.is_file():
        print("NOT-IMPLEMENTED: нет config/ragraf.yaml")
        return 1
    cfg = (getattr(config_mod, "load", None) or getattr(config_mod, "load_config"))(cfg_path)
    facade = Facade(cfg)

    report: dict = {"questions": len(QUESTIONS)}
    if args.section in ("impact", "both"):
        report["impact"] = measure_impact(facade, args.mode)
    if args.section in ("callers", "both"):
        report["callers"] = measure_callers(facade)

    if args.save:
        pathlib.Path(args.save).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                           encoding="utf-8", newline="\n")
        print(f"снимок: {args.save}")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    impact = report.get("impact")
    if impact:
        print(f"[impact] режим {impact['mode']}: вопросов {impact['questions']}, "
              f"осмотрено {impact['counted']}")
        print(f"  попадание в эталон (хотя бы один файл): {impact['hit']} "
              f"({impact['hit_share'] * 100:.1f} %)")
        print(f"  все эталонные файлы в ответе:           {impact['full']} "
              f"({impact['full_share'] * 100:.1f} %)")
        print(f"  ответ без эвристических связей:         {impact['stated_only']} "
              f"({impact['stated_only_share'] * 100:.1f} %)")
        print(f"  файлов в ответе в среднем:              {impact['files_avg']}")
        for row in impact["rows"]:
            if not row.get("found"):
                print(f"  ? {row['target']}: не найден в индексе")
            else:
                mark = "ok " if row["hit"] else "нет"
                print(f"  {mark} {row['target']:24s} файлов {row['files']:3d}  "
                      f"эвристика {row['heuristic_share'] * 100:5.1f} %")

    callers = report.get("callers")
    if callers:
        vis = callers["visible"]
        allv = callers["all_edges"]
        print(f"[callers] вопросов {callers['questions']}, осмотрено {callers['counted']}")
        print(f"  в ответе (выше порога): рёбер {vis['edges']}, подтверждено CPG "
              f"{vis['confirmed']} ({vis['confirmed_share'] * 100:.1f} %), выведено "
              f"{vis['derived']}, отсечено порогом {vis['below_floor']}")
        print(f"  в графе (все входящие): рёбер {allv['edges']}, подтверждено CPG "
              f"{allv['confirmed']} ({allv['confirmed_share'] * 100:.1f} %), выведено "
              f"{allv['derived']} ({allv['derived_share'] * 100:.1f} %)")
        print(f"  ведут в никуда:         {allv['dangling']} "
              f"({allv['dangling_share'] * 100:.1f} %)")
        for item in callers["by_evidence"]:
            print(f"    {item['evidence_kind']:16s} {item['count']:4d} "
                  f"({item['share'] * 100:5.1f} %)")
        for row in callers["rows"]:
            if not row.get("found"):
                print(f"  ? {row['target']}: не найден в индексе")
            else:
                print(f"    {row['target']:24s} в ответе {row['visible_edges']:3d} "
                      f"(вывод {row['visible_derived']:2d}, порог отсёк "
                      f"{row['below_floor']:3d})  в графе {row['all_edges']:4d} "
                      f"(CPG {row['all_confirmed']:3d}, вывод {row['all_derived']:4d}, "
                      f"в никуда {row['dangling']:3d})")
    return 0


if __name__ == "__main__":
    sys.exit(main())