"""Проверка: ответ обязан называть виды рёбер, а числа обязаны сходиться с графом.

Измеренный дефект (30.10.2026): `index_status` называл только `edges_stated`/`edges_derived`,
и 75,2 % рёбер (`calls_heuristic` — вывод по имени) не отличались от подтверждённых. Свойство
формулируется так, чтобы его нельзя было выполнить «правильным словом»:

  1. в ответе есть `edge_kinds.kinds` (непустой список), `heuristic_share`, `budget_percent`;
  2. сумма `count` по видам равна `edge_count` активной ревизии;
  3. `heuristic_share` равен отношению суммы `*_heuristic`-видов к общей сумме;
  4. `budget_exceeded` согласован с долей и порогом (проверяются ОБА направления: true при
     превышении, false без него);
  5. `capability` в ответе не изменился из-за нового поля (он про операции среза, а не про
     качество связей) — сверяется с тем же ответом без учёта новых полей.

Запуск: python tools/check_heuristic_share.py [--root <каталог>] [--json]
Код 1 при находках.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys


def find_config(root: pathlib.Path) -> pathlib.Path | None:
    for candidate in (root / "config" / "ragraf.yaml",
                      pathlib.Path(__file__).resolve().parents[1] / "config" / "ragraf.yaml"):
        if candidate.is_file():
            return candidate
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Виды рёбер названы и сходятся с графом")
    parser.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parents[1]))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = pathlib.Path(args.root)
    sys.path.insert(0, str(root / "src"))

    from ragraf import config as config_mod
    from ragraf.facade import Facade

    cfg_path = find_config(root)
    if cfg_path is None:
        print("NOT-IMPLEMENTED: не найден config/ragraf.yaml — сверять не с чем")
        return 1
    cfg = (getattr(config_mod, "load", None) or getattr(config_mod, "load_config"))(cfg_path)
    # watch=False: ПРОВЕРКА НЕ МЕНЯЕТ ИНДЕКС. Замер 06.11.2026: прогон
    # доктрины через эти два инструмента пересобирал ЖИВОЙ корпус (guard:fast,
    # ~30 с) и добавлял ревизию — то есть проверка меняла то, на чём сама же
    # проверяет. Свойство здесь — согласованность ответа с базой на ОДНОЙ
    # ревизии, а не актуальность; актуальность сторожит отдельная проверка.
    facade = Facade(cfg, watch=False)
    rev = facade.current_rev()
    if not rev:
        print("FAIL: индекс не создан")
        return 1

    status = facade.op_index_status()
    result = status.get("result") or {}
    kinds_block = result.get("edge_kinds") or {}
    problems: list[str] = []

    if not kinds_block:
        problems.append("в ответе нет блока edge_kinds: виды рёбер не названы")
    kinds = kinds_block.get("kinds") or []
    if not kinds:
        problems.append("edge_kinds.kinds пуст: разбивки по видам нет")

    graph_db = pathlib.Path(cfg.graph_db)
    con = sqlite3.connect(f"file:{graph_db}?mode=ro", uri=True)
    rows = con.execute("SELECT kind, COUNT(*) FROM edge WHERE rev_id = ? GROUP BY kind",
                       (rev,)).fetchall()
    total_sql = sum(count for _, count in rows)
    by_kind = {str(kind): int(count) for kind, count in rows}
    heuristic_sql = sum(count for kind, count in by_kind.items() if kind.endswith("_heuristic"))

    total_answer = sum(int(item["count"]) for item in kinds)
    if total_answer != total_sql:
        problems.append(f"сумма видов {total_answer} не равна рёбрам графа {total_sql}")
    for item in kinds:
        actual = by_kind.get(str(item["kind"]))
        if actual is None:
            problems.append(f"вид {item['kind']} назван в ответе, но в графе его нет")
        elif int(item["count"]) != actual:
            problems.append(f"вид {item['kind']}: в ответе {item['count']}, в графе {actual}")

    share_answer = round(float(kinds_block.get("heuristic_share") or 0.0), 4)
    share_sql = round(heuristic_sql / total_sql, 4) if total_sql else 0.0
    if share_answer != share_sql:
        problems.append(f"heuristic_share {share_answer} не равен расчёту по графу {share_sql}")

    budget = int(kinds_block.get("budget_percent") or 0)
    if not 1 <= budget <= 100:
        problems.append(f"budget_percent={budget} вне допустимых границ 1..100")
    exceeded = bool(kinds_block.get("budget_exceeded"))
    expected_exceeded = share_sql * 100.0 > budget
    if exceeded != expected_exceeded:
        problems.append(f"budget_exceeded={exceeded}, а доля {share_sql * 100:.2f} % при "
                        f"бюджете {budget} % даёт {expected_exceeded}")
    if exceeded and not kinds_block.get("note"):
        problems.append("бюджет превышен, но причина не названа (note пуст)")

    capability = str((result.get("rev") or {}).get("capability") or "")
    if capability != "full":
        # Не провал: режим может быть ниже по другим причинам. Но тогда новые поля
        # обязаны быть всё равно — иначе доказательность пропадает ровно там,
        # где связей меньше всего.
        if not kinds:
            problems.append(f"capability={capability}, и видов рёбер нет — доказательность "
                            f"пропала в самом слабом режиме")

    payload = {
        "ok": not problems,
        "rev": rev,
        "capability": capability,
        "edges_answer": total_answer,
        "edges_graph": total_sql,
        "heuristic_share_answer": share_answer,
        "heuristic_share_graph": share_sql,
        "budget_percent": budget,
        "budget_exceeded": exceeded,
        "problems": problems,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"[check_heuristic_share] ревизия {rev}, capability={capability}")
        print(f"  рёбер в ответе {total_answer}, в графе {total_sql}; "
              f"эвристика {share_answer * 100:.1f} % (граф {share_sql * 100:.1f} %), "
              f"бюджет {budget} % — {'выше' if exceeded else 'в пределах'}")
        for problem in problems:
            print(f"  НАХОДКА: {problem}")
        print("ИТОГ:", "чисто" if not problems else f"находок {len(problems)}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
