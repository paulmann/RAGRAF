"""Замер baseline и «с индексом» для полигона z.batteryservice.ru.

Назначение
    Собрать числа, по которым можно судить о выгодности RAGRAF. Без baseline
    любые утверждения «стало быстрее» недоказуемы, поэтому замер снимается
    ДО начала работы с индексом и повторяется после.

Что измеряется
    M1  навигация: сколько tool-вызовов нужно, чтобы найти место в коде;
    M3  стоимость: сколько файлов и строк приходится прочитать;
    M6  проверяемость: доля ответов со валидной ссылкой file:line.

Метод
    Набор задач фиксирован заранее (bench/tasks_z.yaml). Для каждой задачи
    считается МИНИМАЛЬНОЕ достаточное число шагов: сколько файлов надо открыть,
    чтобы ответить. Шаги baseline определяются внешним инструментом (rg) —
    ровно тем, чем работает агент без индекса.

    ВАЖНО: это инструментальный замер, а не «агент поработал». Он даёт
    воспроизводимую нижнюю границу: сколько чтения требует задача навигации.
    Оценка «сколько токенов потратит агент» получается умножением на среднюю
    плотность файла, что честно помечено как приближение.

Запуск
    python tools/bench_corpus.py baseline --corpus z-batteryservice
    python tools/bench_corpus.py indexed  --corpus z-batteryservice
Артефакт
    bench/results/<corpus>_<mode>_<timestamp>.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.util import to_posix  # noqa: E402

# Расширения, которые считаются исходным кодом при замере.
CODE_EXT = {".php", ".sql", ".md"}

# Средняя плотность кода: символов на файл (оценка по пилоту, помечается в отчёте).
AVG_CHARS_PER_FILE = 14_000


def run_rg(args: list[str], root: Path) -> tuple[int, float]:
    """Запустить rg и вернуть (число совпадений, время выполнения)."""
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            ["rg", "--no-heading", "--files-with-matches", *args, "."],
            cwd=str(root), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
        )
        out = proc.stdout or ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 0, time.perf_counter() - t0
    files = [line for line in out.splitlines() if line.strip()]
    return len(files), time.perf_counter() - t0


def measure_baseline(cfg, tasks: list[dict]) -> dict:
    """Baseline: сколько вызовов нужно агенту без индекса.

    Модель замеряется в двух вариантах, и в отчёт берётся КОНСЕРВАТИВНЫЙ
    (наименьший для baseline) — чтобы выгода индекса не была завышена:

      minimal_calls    = поиски + 1 открытие файла.
                          Допущение в пользу baseline: агент с первого раза
                          открывает именно нужный файл, без лишних попыток.
      realistic_calls  = поиски + min(кандидатов, 5).
                          Агент просматривает кандидатов подряд, пока не найдёт.

    Важная защита от самообмана: если по всем шаблонам кандидатов много,
    это означает, что задача навигации ЛЁГКАЯ для rg (найти файл несложно),
    и записывать её в выгоду индекса нельзя. В таком случае сохраняется
    candidates_best, чтобы читатель отчёта сам это увидел.
    """
    root = cfg.root
    results = []

    for task in tasks:
        patterns = task.get("rg_patterns") or []
        if not patterns:
            continue

        steps = 0
        seconds = 0.0
        per_pattern = []
        counts: list[int] = []

        for pattern in patterns:
            count, dt = run_rg([pattern], root)
            steps += 1
            seconds += dt
            counts.append(count)
            per_pattern.append({"pattern": pattern, "files": count, "seconds": round(dt, 3)})
            if count > 0:
                break   # агент останавливается, получив непустой результат

        # Лучший (наименьший непустой) список кандидатов — то, с чем агент
        # начинает работу. 0 означает «ничего не найдено» — тогда открытий нет.
        non_zero = [c for c in counts if c > 0]
        candidates_best = min(non_zero) if non_zero else 0

        minimal_calls = steps + (1 if candidates_best > 0 else 0)
        realistic_calls = steps + (min(candidates_best, 5) if candidates_best > 0 else 0)

        results.append({
            "id": task["id"],
            "question": task["question"],
            "patterns": per_pattern,
            "search_steps": steps,
            "candidates": candidates_best,
            "minimal_calls": minimal_calls,
            "realistic_calls": realistic_calls,
            "total_tool_calls": minimal_calls,   # консервативная оценка
            "seconds": round(seconds, 3),
            "est_chars_read": minimal_calls * AVG_CHARS_PER_FILE,
        })

    total_calls = sum(r["total_tool_calls"] for r in results)
    total_realistic = sum(r["realistic_calls"] for r in results)
    total_seconds = sum(r["seconds"] for r in results)
    return {
        "mode": "baseline",
        "tasks": len(results),
        "total_tool_calls": total_calls,
        "total_tool_calls_realistic": total_realistic,
        "avg_tool_calls": round(total_calls / len(results), 2) if results else 0,
        "total_seconds": round(total_seconds, 3),
        "est_chars_read": sum(r["est_chars_read"] for r in results),
        "details": results,
    }


def measure_indexed(cfg, tasks: list[dict], facade) -> dict:
    """С индексом: сколько операций фасада нужно, чтобы ответить на задачу.

    Что считается кроме числа вызовов:
      * facts_with_path — факты с полем path и строкой (start_line либо line);
      * hit_expected — попал ли ответ в файл-цель задачи. Это ГЛАВНЫЙ критерий:
        один вызов фасада бесполезен, если он указал не туда. Файл-цель берётся
        из поля expect_any задачи (список подстрок путей).
        Для задач без expect_any критерий не считается (null), а не «зачтён».
    """
    results = []

    for task in tasks:
        ops = task.get("ragraf_ops") or []
        if not ops:
            continue

        calls = 0
        facts = 0
        with_path = 0
        seconds = 0.0
        found = False
        op_log = []
        seen_paths: list[str] = []

        for op in ops:
            calls += 1
            t0 = time.perf_counter()
            data = dispatch(facade, op)
            dt = time.perf_counter() - t0
            seconds += dt

            items = extract_items(data)
            facts += len(items)
            for it in items:
                p = it.get("path")
                line = it.get("start_line", it.get("line"))
                if p and line is not None:
                    with_path += 1
                if p:
                    seen_paths.append(str(p))
            if data.get("found"):
                found = True
            op_log.append({
                "op": op["op"],
                "args": op.get("args", {}),
                "seconds": round(dt, 3),
                "found": bool(data.get("found")),
                "items": len(items),
                "index_rev": data.get("index_rev"),
                "capability": data.get("capability"),
            })
            if found and task.get("stop_on_found", True):
                break

        # Попадание в целевой файл: достаточно любого ожидаемого пути среди выдачи.
        expect = task.get("expect_any") or []
        hit_expected: bool | None = None
        if expect:
            hit_expected = any(
                any(str(e) in p for e in expect) for p in seen_paths
            )

        results.append({
            "id": task["id"],
            "question": task["question"],
            "ops": op_log,
            "total_tool_calls": calls,
            "facts_returned": facts,
            "facts_with_path": with_path,
            "seconds": round(seconds, 3),
            "found": found,
            "hit_expected": hit_expected,
            "expect_any": expect,
            "top_paths": seen_paths[:5],
            "est_chars_read": 0,   # индекс отдаёт структуру, а не файлы целиком
        })

    total_calls = sum(r["total_tool_calls"] for r in results)
    total_facts = sum(r["facts_with_path"] for r in results)
    total_all = sum(r["facts_returned"] for r in results)
    hit_tasks = [r for r in results if r["hit_expected"] is not None]
    hits = sum(1 for r in hit_tasks if r["hit_expected"])
    return {
        "mode": "indexed",
        "tasks": len(results),
        "total_tool_calls": total_calls,
        "avg_tool_calls": round(total_calls / len(results), 2) if results else 0,
        "total_seconds": round(sum(r["seconds"] for r in results), 3),
        "facts_with_path": total_facts,
        "facts_total": total_all,
        "verifiability": round(total_facts / total_all, 4) if total_all else 0.0,
        "hit_tasks": len(hit_tasks),
        "hit_expected": hits,
        "hit_rate": round(hits / len(hit_tasks), 4) if hit_tasks else 0.0,
        "details": results,
    }


def dispatch(facade, op: dict) -> dict:
    """Вызвать операцию фасада по описанию из набора задач."""
    name = op.get("op", "")
    args = op.get("args", {}) or {}
    mapping = {
        "search": lambda: facade.op_search(args.get("query", ""), limit=args.get("limit", 10)),
        "symbol": lambda: facade.op_symbol(args.get("name", ""), limit=args.get("limit", 10)),
        "callers": lambda: facade.op_callers(args.get("fqn", ""), depth=args.get("depth", 1)),
        "callees": lambda: facade.op_callees(args.get("fqn", ""), depth=args.get("depth", 1)),
        "impact": lambda: facade.op_impact(args.get("target", ""), depth=args.get("depth", 2)),
        "repo_map": lambda: facade.op_repo_map(budget_tokens=args.get("budget", 1500)),
        "table_usage": lambda: facade.op_table_usage(args.get("table", "")),
        "schema": lambda: facade.op_schema(table=args.get("table")),
        "index_status": lambda: facade.op_index_status(),
    }
    fn = mapping.get(name)
    if fn is None:
        return {"op": name, "found": False, "result": [], "error": "операция не поддерживается"}
    try:
        return fn()
    except Exception as exc:
        return {"op": name, "found": False, "result": [], "error": f"{type(exc).__name__}: {exc}"}


def extract_items(data: dict) -> list[dict]:
    """Привести результат операции к плоскому списку элементов."""
    res = data.get("result")
    items: list[dict] = []
    if isinstance(res, list):
        items = [x for x in res if isinstance(x, dict)]
    elif isinstance(res, dict):
        for key in ("affected_files", "stated", "derived", "items", "references",
                    "sql_literals", "files", "direct_reader_writers", "indirect", "columns"):
            val = res.get(key)
            if isinstance(val, list):
                items.extend(x for x in val if isinstance(x, dict))
    return items


def load_tasks(path: Path) -> list[dict]:
    try:
        import yaml
    except ImportError:
        raise SystemExit("нужен PyYAML (см. requirements.txt)")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(data.get("tasks") or [])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["baseline", "indexed", "compare"])
    ap.add_argument("--corpus", default="z-batteryservice")
    ap.add_argument("--tasks", default="")
    args = ap.parse_args()

    cfg_path = REPO / "config" / f"{args.corpus}.yaml"
    if not cfg_path.exists():
        print(f"нет конфигурации корпуса: {cfg_path}")
        return 1
    cfg = load_config(cfg_path)

    tasks_path = Path(args.tasks) if args.tasks else (REPO / "bench" / "tasks_z.yaml")
    if not tasks_path.exists():
        print(f"нет набора задач: {tasks_path}")
        return 1
    tasks = load_tasks(tasks_path)
    print(f"корпус: {cfg.project} ({cfg.root})")
    print(f"задач в наборе: {len(tasks)}")

    out_dir = REPO / "bench" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")

    if args.mode == "baseline":
        report = measure_baseline(cfg, tasks)
        report["corpus"] = args.corpus
        report["generated_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        report["note"] = (
            "Baseline: без индекса. Консервативная оценка: поиски плюс ОДНО открытие "
            "файла (допущение в пользу baseline — агент сразу открывает нужный файл). "
            "Реалистичный вариант (просмотр до 5 кандидатов) — в поле "
            "total_tool_calls_realistic. Кандидаты = файлов по лучшему непустому шаблону. "
            f"Оценка прочитанных символов — приближение по средней плотности файла "
            f"({AVG_CHARS_PER_FILE} символов), а не измеренный факт."
        )
        path = out_dir / f"{args.corpus}_baseline_{stamp}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8", newline="\n")
        print(f"\nBASELINE: задач {report['tasks']}, вызовов {report['total_tool_calls']} "
              f"(в среднем {report['avg_tool_calls']}), реалистично {report['total_tool_calls_realistic']}, "
              f"время {report['total_seconds']} с")
        print(f"артефакт: {path.relative_to(REPO)}")
        for row in report["details"]:
            print(f"  {row['id']}: поисков {row['search_steps']}, кандидатов {row['candidates']} "
                  f"→ вызовов {row['minimal_calls']} (реалистично {row['realistic_calls']})")
        return 0

    # Для режима с индексом нужен фасад: он читает индекс.
    from ragraf.facade import Facade  # noqa: E402

    facade = Facade(cfg)
    try:
        report = measure_indexed(cfg, tasks, facade)
    finally:
        facade.close()

    report["corpus"] = args.corpus
    report["generated_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    report["note"] = (
        "С индексом: число вызовов = операции фасада. Возвращаемые факты содержат "
        "path:line, поэтому проверяемость считается по факту, а не оценивается."
    )
    path = out_dir / f"{args.corpus}_indexed_{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")

    print(f"\nС ИНДЕКСОМ: задач {report['tasks']}, вызовов {report['total_tool_calls']} "
          f"(в среднем {report['avg_tool_calls']}), время {report['total_seconds']} с")
    print(f"проверяемость (доля фактов с path:line): {report['verifiability']:.2%}")
    print(f"попадание в файл-цель: {report['hit_expected']} из {report['hit_tasks']} "
          f"({report['hit_rate']:.0%})")
    print(f"артефакт: {path.relative_to(REPO)}")
    for row in report["details"]:
        mark = "ЦЕЛЬ" if row["hit_expected"] else ("—" if row["hit_expected"] is None else "МИМО")
        top = row["top_paths"][0] if row["top_paths"] else "(пусто)"
        print(f"  {row['id']}: операций {row['total_tool_calls']}, фактов со ссылкой "
              f"{row['facts_with_path']}, {mark}: {top}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
