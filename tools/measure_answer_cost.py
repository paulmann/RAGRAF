#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Стоимость ответа агенту: сколько символов отдаёт операция и что она заменяет.

Зачем отдельный замер. `usage` считает объём ответов и «заменённые строки» по
журналу обращений, но по всему корпусу сразу: из него не видно, КАКАЯ операция
дорога агенту и какая дешева. Агент платит не временем, а контекстом: ответ на
40 тысяч символов вытесняет из окна всё остальное.

Что мерится, двумя независимыми способами:

  1. ЖУРНАЛ (`usage_call`): для каждой операции — сколько раз вызывали, сколько
     символов в среднем, какая доля фактов со ссылкой `файл:строка`, сколько
     времени занимает ответ. Это измеренная история использования, а не оценка;
  2. ЖИВОЙ ПРОГОН на текущем корпусе: каждая операция вызывается один раз с
     типовым аргументом, и считается, сколько символов она отдала и сколько
     символов пришлось бы прочитать, если бы те же файлы читали целиком. Отсюда
     «что заменяет чтение» — не по видам операций из таблицы, а по фактическим
     путям из ответа.

Оценки помечены: токены = символы / 4 (коэффициент проекта, `telemetry.py`),
строки = символы / 45. Обращения, факты и символы — измеренные значения.

Запуск:
    python tools/measure_answer_cost.py                (таблица + итог)
    python tools/measure_answer_cost.py --json         (машинный вид)
    python tools/measure_answer_cost.py --save out.json
    python tools/measure_answer_cost.py --live-only    (без журнала)

Коды возврата: 0 — снято; 2 — снять нечем (нет конфига или индекса).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
_TOOLS_DIR = str(Path(__file__).resolve().parent)
sys.path[:] = [p for p in sys.path if str(Path(p or ".").resolve()) != _TOOLS_DIR]

CHARS_PER_TOKEN = 4
CHARS_PER_LINE = 45


def journal_costs(conn) -> list[dict]:
    """Расход по операциям из ЖУРНАЛА обращений — измеренная история."""
    rows = conn.execute(
        """
        SELECT op,
               COUNT(*) AS calls,
               SUM(result_chars) AS chars,
               AVG(result_chars) AS chars_avg,
               SUM(fact_count) AS facts,
               SUM(facts_with_span) AS spans,
               SUM(replaces_chars) AS replaces,
               AVG(duration_ms) AS ms_avg,
               SUM(CASE WHEN error IS NOT NULL AND error <> '' THEN 1 ELSE 0 END) AS errors
          FROM usage_call
         GROUP BY op
         ORDER BY chars DESC
        """
    ).fetchall()
    out = []
    for row in rows:
        calls = int(row["calls"] or 0)
        chars = int(row["chars"] or 0)
        spans = int(row["spans"] or 0)
        facts = int(row["facts"] or 0)
        out.append({
            "op": str(row["op"]),
            "calls": calls,
            "chars": chars,
            "chars_avg": round(float(row["chars_avg"] or 0), 1),
            "facts": facts,
            "fact_share_with_span": round(spans / facts, 4) if facts else 0.0,
            "replaces_chars": int(row["replaces"] or 0),
            "ms_avg": round(float(row["ms_avg"] or 0), 1),
            "errors": int(row["errors"] or 0),
        })
    return out


def answer_paths(payload, out: set[str]) -> None:
    """Собрать все значения поля `path` из ответа — любой вложенности."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == "path" and isinstance(value, str) and value:
                out.add(value)
            else:
                answer_paths(value, out)
    elif isinstance(payload, list):
        for item in payload:
            answer_paths(item, out)


def referenced_chars(root: Path, paths: set[str]) -> tuple[int, int]:
    """Сколько символов занимают файлы из ответа (и сколько их удалось прочитать)."""
    total = 0
    read = 0
    for rel in sorted(paths):
        try:
            data = (root / rel).read_bytes()
        except OSError:
            continue
        read += 1
        total += len(data.decode("utf-8", errors="ignore"))
    return total, read


def _top_symbol(facade, rev: str) -> str:
    """Символ с наибольшим числом входящих связей — типовой аргумент для операций.

    Имя берётся из ГРАФА, а не из литерала: литерал отстал бы от корпуса и
    мерил бы «не найдено» вместо стоимости ответа.
    """
    try:
        rows = facade.graph.execute(
            """
            SELECT n.name, COUNT(*) AS c
              FROM edge e JOIN node n ON n.rev_id = e.rev_id AND n.node_id = e.dst
             WHERE e.rev_id = ? AND n.name <> '' AND n.kind IN ('function', 'method')
             GROUP BY n.name
             ORDER BY c DESC
             LIMIT 10
            """, (rev,)).fetchall()
    except Exception:
        return ""
    for row in rows:
        name = str(row["name"])
        if name:
            return name
    return ""


def _first_table(facade) -> str:
    """Имя первой таблицы из слоя схемы, если схема собрана.

    Пустая строка — не «нет таблиц», а «схемы в этом корпусе нет» (например,
    `db.enabled=false`).Разница называется в отчёте, а не сливается с ошибкой.
    """
    try:
        payload = facade.op_schema()
        for key in ("live_table_names", "result"):
            value = payload.get(key)
            if isinstance(value, list) and value:
                first = value[0]
                name = first.get("name") if isinstance(first, dict) else first
                if name:
                    return str(name)
    except Exception:
        pass
    return ""


def project_answer(payload) -> dict:
    """Верхний уровень ответа без служебной оболочки: его и видит агент."""
    if isinstance(payload, dict) and "result" in payload:
        return {"result": payload.get("result"),
                "found": payload.get("found"),
                "limits": payload.get("limits") or []}
    return payload if isinstance(payload, dict) else {"result": payload}


def live_costs(facade, rev: str, question: str) -> list[dict]:
    """Живой прогон: символы ответа и символы файлов, на которые он указывает."""
    root = Path(facade.cfg.root)
    symbol = _top_symbol(facade, rev)
    table = _first_table(facade)

    calls: list[tuple[str, str, callable]] = [
        ("search", "вопрос о корпусе, обрезка 5",
         lambda: facade.op_search(question, limit=5)),
        ("search", "вопрос о корпусе, обрезка 20",
         lambda: facade.op_search(question, limit=20)),
        ("search_multi", "литеральный поиск по строке",
         lambda: facade.op_search_multi(["budget_exceeded"], limit=10)),
        ("repo_map", "карта корпуса", lambda: facade.op_repo_map()),
        ("duplicates", "группы копий", lambda: facade.op_duplicates()),
        ("dead_code", "что не используется", lambda: facade.op_dead_code()),
        ("entrypoints", "точки входа", lambda: facade.op_entrypoints()),
        ("routes", "маршруты", lambda: facade.op_routes()),
        ("freshness", "свежесть индекса", lambda: facade.op_freshness()),
        ("index_status", "состояние индекса", lambda: facade.op_index_status()),
        ("excludes", "исключения корпуса", lambda: facade.op_excludes()),
    ]
    if symbol:
        calls += [
            ("symbol", f"символ «{symbol}»", lambda: facade.op_symbol(symbol)),
            ("callers", f"вызывающие «{symbol}»",
             lambda: facade.op_callers(symbol, depth=1, limit=100)),
            ("callees", f"вызываемые «{symbol}»",
             lambda: facade.op_callees(symbol, depth=1, limit=100)),
            ("code", f"тело «{symbol}»", lambda: facade.op_code(fqn=symbol)),
        ]
    skipped: list[str] = []
    if table:
        calls += [
            ("schema", f"схема таблицы «{table}»", lambda: facade.op_schema(table)),
            ("table_usage", f"кто трогает «{table}»",
             lambda: facade.op_table_usage(table)),
        ]
    else:
        # Пропуск НАЗЫВАЕТСЯ, а не теряется: иначе отчёт выглядит как «этих
        # операций нет», хотя они есть, а схемы в корпусе — нет.
        skipped.append("schema / table_usage / write_check: схема БД в корпусе "
                       "не собрана (db.enabled=false)")

    out: list[dict] = []
    for op, note, call in calls:
        started = time.perf_counter()
        try:
            payload = call()
        except Exception as exc:  # noqa: BLE001 — отчёт не имеет права падать
            out.append({"op": op, "note": note, "error": f"{type(exc).__name__}: {exc}"})
            continue
        seconds = time.perf_counter() - started
        body = project_answer(payload)
        chars = len(json.dumps(body, ensure_ascii=False))
        paths: set[str] = set()
        answer_paths(body.get("result"), paths)
        file_chars, files_read = referenced_chars(root, paths)
        out.append({
            "op": op,
            "note": note,
            "seconds": round(seconds, 3),
            "answer_chars": chars,
            "files": files_read,
            "files_chars": file_chars,
            "saved_ratio": round(1 - chars / file_chars, 4) if file_chars else 0.0,
        })
    for note in skipped:
        out.append({"op": "(пропуск)", "note": note})
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Стоимость ответа агенту по операциям")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--save", default="")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--live-only", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    sys.path.insert(0, str(root / "src"))

    from ragraf import config as config_mod
    from ragraf import formats  # noqa: F401 — единый реестр форматов грузится вместе с пакетом
    from ragraf.facade import Facade

    config_path = root / "config" / "ragraf.yaml"
    if not config_path.is_file():
        print(f"NOT-IMPLEMENTED: нет {config_path}")
        return 2
    cfg = (getattr(config_mod, "load", None) or getattr(config_mod, "load_config"))(config_path)
    facade = Facade(cfg, tool="dev", agent="RAGRAF-Measure", agent_kind="internal",
                    task="этап13:стоимость", telemetry=False)
    try:
        rev = facade.current_rev()
        if not rev:
            print("NOT-IMPLEMENTED: индекс пуст — мерить нечего")
            return 2
        question = ("Где считается контрольная сумма корпуса и почему файлы "
                    "перед этим сортируются")
        report: dict = {"root": str(root), "rev": rev}
        if not args.live_only:
            report["journal"] = journal_costs(facade.control.conn)
        report["live"] = live_costs(facade, rev, question)

        if args.save:
            Path(args.save).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                       encoding="utf-8", newline="\n")
            print(f"снимок: {args.save}")
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0

        print(f"[стоимость] ревизия {rev}")
        if report.get("journal"):
            print("\nПо ЖУРНАЛУ обращений (измеренная история использования):")
            print(f"  {'операция':16s} {'вызовов':>8s} {'символов':>12s} "
                  f"{'на вызов':>9s} {'со ссылкой':>10s} {'мс':>7s}")
            for row in report["journal"][:18]:
                print(f"  {row['op']:16s} {row['calls']:8d} {row['chars']:12d} "
                      f"{row['chars_avg']:9.0f} {row['fact_share_with_span'] * 100:9.1f} % "
                      f"{row['ms_avg']:7.0f}")
            total_chars = sum(r["chars"] for r in report["journal"])
            total_calls = sum(r["calls"] for r in report["journal"])
            total_replaces = sum(r["replaces_chars"] for r in report["journal"])
            print(f"  итого: вызовов {total_calls}, символов {total_chars} "
                  f"({round(total_chars / max(1, total_calls))} на вызов), "
                  f"заменили чтения файлов {total_replaces} символов")

        print("\nЖИВОЙ ПРОГОН на этом корпусе (одна операция — один вызов):")
        print(f"  {'операция':14s} {'ответ':>8s} {'файлов':>7s} {'эти файлы':>11s} "
              f"{'экономия':>9s}  примечание")
        live_ok = [r for r in report["live"] if "answer_chars" in r]
        for row in report["live"]:
            if "answer_chars" not in row:
                print(f"  {row['op']:14s} {row.get('error') or row.get('note')}")
                continue
            print(f"  {row['op']:14s} {row['answer_chars']:8d} {row['files']:7d} "
                  f"{row['files_chars']:11d} {row['saved_ratio'] * 100:8.1f} %  {row['note']}")
        if live_ok:
            answer = sum(r["answer_chars"] for r in live_ok)
            files = sum(r["files_chars"] for r in live_ok)
            print(f"  итого: ответы {answer} символов против {files} символов файлов; "
                  f"экономия {round(1 - answer / files, 4) * 100:.1f} %")
            print(f"  оценка: ~{answer // CHARS_PER_TOKEN} токенов вместо "
                  f"~{files // CHARS_PER_TOKEN}; ~{answer // CHARS_PER_LINE} строк вместо "
                  f"~{files // CHARS_PER_LINE}")
        print("\nСтрока для отчёта: "
              f"ответы {sum(r['answer_chars'] for r in live_ok)} символов на "
              f"{len(live_ok)} операций, средняя {round(sum(r['answer_chars'] for r in live_ok) / max(1, len(live_ok)))}")
        return 0
    finally:
        facade.close()


if __name__ == "__main__":
    sys.exit(main())
