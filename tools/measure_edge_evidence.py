"""Замер доказательности графа: чем подтверждено каждое ребро и куда оно ведёт.

ЗАЧЕМ. Этап 8 мастер-плана начинается с числа, а не с правки: сколько рёбер
каждого вида, у скольких есть координата вызова, сколько подтверждено CPG и
сколько выведено по имени. Те же числа снимаются ПОСЛЕ правки, поэтому замер
обязан быть повторяемым — он и есть источник чисел для отчёта.

ЧТО СЧИТАЕТСЯ (все числа — по одной ревизии, из самого хранилища):

  * рёбра по видам и происхождению;
  * наличие координаты (`evidence_path` + `evidence_line`), и существует ли
    названный файл в ревизии, не выходит ли строка за конец файла;
  * сколько рёбер ведут в никуда (цели нет среди узлов ревизии);
  * вид доказательства по классификатору `ragraf.evidence` — отдельно для
    рёбер вызова, потому что именно они попадают в `callers`/`callees`;
  * сколько рёбер несут записанный `evidence_kind` (после этапа 8), а сколько
    восстановлены по полям ребра (ревизии, построенные раньше).

Запуск: python tools/measure_edge_evidence.py [--rev rev-XXXX] [--json]
                                              [--save <файл>] [--root <каталог>]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Доказательность рёбер графа")
    parser.add_argument("--root", default=str(REPO))
    parser.add_argument("--rev", default="", help="ревизия (по умолчанию активная)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--save", default="")
    args = parser.parse_args()

    root = pathlib.Path(args.root)
    sys.path.insert(0, str(root / "src"))

    from ragraf import config as config_mod
    from ragraf import evidence as evidence_mod
    from ragraf import store

    cfg_path = root / "config" / "ragraf.yaml"
    if not cfg_path.is_file():
        print(f"нет конфигурации корпуса: {cfg_path}")
        return 1
    cfg = config_mod.load(cfg_path)
    conn = store.connect(cfg.graph_db)
    conn.row_factory = None
    if args.rev:
        rev = args.rev
    else:
        row = conn.execute(
            "SELECT rev_id FROM rev WHERE state = 'ready' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        rev = str(row[0]) if row else ""
    if not rev:
        print("в хранилище нет готовой ревизии")
        return 1

    file_lines = {
        str(r[0]): int(r[1] or 0)
        for r in conn.execute("SELECT path, lines FROM file WHERE rev_id = ?", (rev,))
    }
    node_ids = {str(r[0]) for r in conn.execute(
        "SELECT node_id FROM node WHERE rev_id = ?", (rev,))}

    rows = conn.execute(
        """SELECT edge_uid, src, dst, kind, provenance, evidence_path, evidence_line,
                  confidence, props, COALESCE(evidence_kind, '') AS evidence_kind
             FROM edge WHERE rev_id = ?""",
        (rev,),
    ).fetchall()

    by_kind: dict[str, dict] = {}
    by_evidence: dict[str, int] = {}
    call_evidence: dict[str, int] = {}
    recorded = 0
    recovered = 0
    with_path = 0
    with_line = 0
    bad_file = 0
    bad_line = 0
    dangling = 0
    heuristic_budget_marker = 0

    for r in rows:
        kind = str(r[3])
        provenance = str(r[4])
        path = str(r[5] or "")
        line = int(r[6] or 0)
        slot = by_kind.setdefault(kind, {"count": 0, "stated": 0, "derived": 0,
                                         "with_path": 0, "with_line": 0})
        slot["count"] += 1
        slot[provenance] = slot.get(provenance, 0) + 1
        if path:
            slot["with_path"] += 1
            with_path += 1
        if line > 0:
            slot["with_line"] += 1
            with_line += 1

        props = evidence_mod._props_dict(r[8])
        # Записанный вид читается из КОЛОНКИ, а не из свойств: свойства — это
        # вход классификатора, а колонка — его результат, записанный при
        # построении. Смешать их значило бы мерить вход вместо выхода.
        stored = str(r[9] or "").strip()
        kind_ev = stored or evidence_mod.classify(kind, provenance, props, r[7],
                                                  path, line)
        if stored in evidence_mod.EVIDENCE_KINDS:
            recorded += 1
        else:
            recovered += 1
        by_evidence[kind_ev] = by_evidence.get(kind_ev, 0) + 1
        if evidence_mod.is_call_kind(kind):
            call_evidence[kind_ev] = call_evidence.get(kind_ev, 0) + 1
        if str(props.get("source") or "") == "cpg":
            heuristic_budget_marker += 1

        if path and path not in file_lines:
            bad_file += 1
        elif path and line > 0 and file_lines.get(path, 0) and line > file_lines[path]:
            bad_line += 1
        if str(r[2]) not in node_ids:
            dangling += 1

    total = len(rows)
    calls_total = sum(call_evidence.values())
    summary = {
        "rev": rev,
        "total": total,
        "by_kind": [
            {"kind": kind, **data}
            for kind, data in sorted(by_kind.items(), key=lambda kv: (-kv[1]["count"], kv[0]))
        ],
        "evidence": {
            "counts": [
                {"evidence_kind": kind, "label": evidence_mod.label(kind), "count": count,
                 "share": round(count / total, 4) if total else 0.0}
                for kind, count in sorted(by_evidence.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
            "call_edges": calls_total,
            "call_counts": [
                {"evidence_kind": kind, "label": evidence_mod.label(kind), "count": count,
                 "share": round(count / calls_total, 4) if calls_total else 0.0}
                for kind, count in sorted(call_evidence.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
            "cpg_confirmed_call_edges": call_evidence.get("cpg_call", 0),
            "derived_call_edges": sum(count for kind, count in call_evidence.items()
                                      if evidence_mod.is_derived(kind)),
            "columns_recorded": recorded,
            "columns_recovered_from_fields": recovered,
        },
        "coordinates": {
            "with_path": with_path,
            "with_line": with_line,
            "path_not_in_rev": bad_file,
            "line_beyond_file": bad_line,
            "dst_not_in_nodes": dangling,
        },
    }

    if args.save:
        pathlib.Path(args.save).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        print(f"снимок: {args.save}")
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    print(f"[edge-evidence] ревизия {rev}: рёбер {total}")
    print("  вид ребра            всего  stated  derived  с путём  со строкой")
    for item in summary["by_kind"]:
        print(f"  {item['kind']:20s} {item['count']:6d} {item['stated']:7d} "
              f"{item['derived']:8d} {item['with_path']:8d} {item['with_line']:10d}")
    print("  --- доказательство ---")
    for item in summary["evidence"]["counts"]:
        print(f"  {item['evidence_kind']:16s} {item['count']:6d}  {item['share'] * 100:5.1f} %  "
              f"{item['label']}")
    print(f"  рёбер вызова: {calls_total}")
    for item in summary["evidence"]["call_counts"]:
        print(f"    {item['evidence_kind']:16s} {item['count']:6d}  {item['share'] * 100:5.1f} %")
    print(f"    из них подтверждено CPG: {summary['evidence']['cpg_confirmed_call_edges']}, "
          f"выведено: {summary['evidence']['derived_call_edges']}")
    print(f"  колонка evidence_kind записана: {recorded}, "
          f"восстановлена по полям ребра: {recovered}")
    print("  --- координаты ---")
    print(f"  с путём {with_path}, со строкой {with_line}")
    print(f"  путь не найден в ревизии: {bad_file}, строка за концом файла: {bad_line}, "
          f"цель не найдена среди узлов: {dangling}")
    return 0


if __name__ == "__main__":
    sys.exit(main())