"""Замер M2: насколько граф связывает файлы, менявшиеся вместе (ACCEPTANCE.md §5).

Что именно измеряется — важная оговорка, без неё цифра ничего не значит.

Задача строго «предсказать impact до правки» требует построения индекса на состоянии
ДО каждого коммита (103 ревизии) — это дорого и на первом проходе не делается. Поэтому
здесь измеряется более слабое, но честно называемое свойство:

    СВЯЗНОСТЬ СОИЗМЕНЁННЫХ ФАЙЛОВ:
    для коммита, изменившего несколько PHP-файлов, берётся «главный» изменённый символ
    (публичный метод файла с наибольшей долей изменённых строк) и проверяется, какие из
    остальных изменённых в том же коммите файлов достижимы от него в графе.

    recall  = достижимые соизменённые файлы / все соизменённые файлы
    precision = достижимые файлы, действительно менявшиеся / все достижимые файлы

Baseline: наивное «затронут только тот файл, где символ» — даёт recall ≈ 1/(число файлов).

Это нижняя граница пользы графа: если соизменённые файлы не связаны в графе даже
структурно, то impact-анализ по этому графу заведомо не сможет их предсказать.

Запуск: python tools/bench_m2.py [--commits 20] [--depth 3]
Артефакт: bench/results/M2_<rev>.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store, util  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402


def git(args: list[str], cwd: Path) -> str:
    out = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return out.stdout


def list_commits(root: Path, limit: int) -> list[str]:
    text = git(["log", f"-{limit}", "--format=%H"], root)
    return [line.strip() for line in text.splitlines() if line.strip()]


def changed_files(root: Path, sha: str) -> list[str]:
    """PHP-файлы, изменённые в коммите (без тестов и служебных каталогов)."""
    text = git(["show", "--name-only", "--format=", sha], root)
    out: list[str] = []
    for line in text.splitlines():
        p = line.strip().replace("\\", "/")
        if not p.endswith(".php"):
            continue
        if p.startswith(("tests/", "var/", "public/")):
            continue
        out.append(p)
    return sorted(set(out))


def diff_hunks(root: Path, sha: str, path: str) -> tuple[set[int], int]:
    """Строки, затронутые в файле, и число добавленных/удалённых строк."""
    text = git(["show", "--format=", "-U0", sha, "--", path], root)
    lines: set[int] = set()
    changed = 0
    for m in re.finditer(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", text, re.MULTILINE):
        start = int(m.group(1))
        count = int(m.group(2) or 1)
        for i in range(start, start + max(count, 1)):
            lines.add(i)
        changed += max(count, 1)
    for line in text.splitlines():
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            changed += 1
    return lines, changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commits", type=int, default=20, help="сколько коммитов анализировать")
    ap.add_argument("--depth", type=int, default=3, help="глубина обхода графа")
    args = ap.parse_args()

    cfg = load_config(REPO / "config" / "revoap.yaml")
    root = cfg.root
    rev = util.read_json(cfg.rev_file, default={}).get("rev_id")
    if not rev:
        print("индекс не создан")
        return 1

    conn = store.connect(cfg.graph_db, readonly=True)

    def symbol_of_line(path: str, line: int) -> dict | None:
        row = conn.execute(
            """SELECT node_id, kind, name, fqn, path, start_line, end_line FROM node
               WHERE rev_id = ? AND path = ? AND kind IN ('method','function')
                 AND start_line <= ? AND end_line >= ?
               ORDER BY (end_line - start_line) LIMIT 1""",
            (rev, path, line, line),
        ).fetchone()
        return dict(row) if row else None

    def reachable_files(node_id: str, depth: int, limit: int = 5000) -> set[str]:
        """Файлы, достижимые от символа в ОБА направления.

        Почему оба: если изменение затрагивает метод, то затронуты как те, кто его
        вызывает (входящие рёбра), так и то, от чего он зависит (исходящие).
        Односторонний обход (только исходящие) давал recall 0.09 и медиану 0.00 —
        это была ошибка замера, а не свойство графа: изменения конструкторов через
        внедрение зависимостей распространяются ИМЕННО по входящим рёбрам.
        """
        frontier = [node_id]
        seen_nodes: set[str] = {node_id}
        files: set[str] = set()
        for _ in range(depth):
            if not frontier:
                break
            nxt: list[str] = []
            for i in range(0, len(frontier), 200):
                part = frontier[i : i + 200]
                marks = ",".join("?" for _ in part)
                rows = conn.execute(
                    f"""SELECT src, dst FROM edge WHERE rev_id = ?
                        AND (src IN ({marks}) OR dst IN ({marks}))
                        AND kind IN ('calls','calls_heuristic','instantiates','queries',
                                     'imports','handles','renders','extends','implements',
                                     'contains','defines')""",
                    (rev, *part, *part),
                ).fetchall()
                for r in rows:
                    for other in (str(r["src"]), str(r["dst"])):
                        if other in seen_nodes:
                            continue
                        seen_nodes.add(other)
                        nxt.append(other)
                    if len(seen_nodes) > limit:
                        break
            for nid in nxt:
                row = conn.execute(
                    "SELECT path FROM node WHERE rev_id = ? AND node_id = ?", (rev, nid)
                ).fetchone()
                if row and row["path"]:
                    files.add(str(row["path"]))
            frontier = nxt
        return files

    results: list[dict] = []
    shas = list_commits(root, args.commits * 6)
    for sha in shas:
        if len(results) >= args.commits:
            break
        files = changed_files(root, sha)
        if len(files) < 2:
            continue

        # Главный символ: в файле с наибольшим числом изменённых строк берём метод,
        # накрывающий первую изменённую строку.
        best: tuple[int, str, dict] | None = None
        for path in files:
            lines, changed = diff_hunks(root, sha, path)
            if not lines:
                continue
            for line in sorted(lines):
                sym = symbol_of_line(path, line)
                if sym and (best is None or changed > best[0]):
                    best = (changed, path, sym)
                    break
        if best is None:
            continue

        _, primary_path, sym = best
        reached = reachable_files(str(sym["node_id"]), args.depth)
        others = [f for f in files if f != primary_path]
        if not others:
            continue

        # Наивный baseline: предсказан только файл, где лежит символ.
        naive_hit = 0
        graph_hit = [f for f in others if f in reached]

        precision_den = len(reached) if reached else 1
        precision_num = len([f for f in reached if f in files])

        results.append(
            {
                "commit": sha[:10],
                "date": git(["show", "-s", "--format=%cI", sha], root).strip(),
                "files_changed": len(files),
                "primary_file": primary_path,
                "primary_symbol": sym["fqn"],
                "others": others,
                "reachable": sorted(graph_hit),
                "naive_recall": round(naive_hit / len(others), 4),
                "graph_recall": round(len(graph_hit) / len(others), 4),
                "graph_precision": round(precision_num / precision_den, 4),
            }
        )

    conn.close()

    if not results:
        print("не удалось собрать ни одного коммита с несколькими PHP-файлами")
        return 1

    def avg(key: str) -> float:
        return round(sum(r[key] for r in results) / len(results), 4)

    report = {
        "metric": "M2 — связность соизменённых файлов (нижняя граница пользы графа)",
        "method": "обход графа в оба направления от главного изменённого символа",
        "rev": rev,
        "depth": args.depth,
        "commits_analyzed": len(results),
        "recall_mean": avg("graph_recall"),
        "recall_median": round(
            sorted(r["graph_recall"] for r in results)[len(results) // 2], 4
        ),
        "precision_mean": avg("graph_precision"),
        "baseline_naive_recall_mean": avg("naive_recall"),
        "cases_full_recall": sum(1 for r in results if r["graph_recall"] >= 0.999),
        "cases_zero_recall": sum(1 for r in results if r["graph_recall"] == 0.0),
        "reference_orient": (
            "Измеренный потолок в литературе: precision 69% / recall 79% / F1 55% "
            "(LCIP, arXiv:1512.07435, Java, симуляция мутациями). Наш случай иной "
            "(PHP, реальные правки, измеряется связность, а не полное предсказание) — "
            "сравнение только как порядок величины."
        ),
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "cases": results,
    }

    out_dir = REPO / "bench" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"M2_{rev}.json"
    out_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8", newline="\n")

    print(f"M2 — связность соизменённых файлов (ревизия {rev}, глубина {args.depth})")
    print(f"  проанализировано коммитов: {len(results)}")
    print(f"  recall графа (среднее):    {report['recall_mean']:.3f}")
    print(f"  recall графа (медиана):    {report['recall_median']:.3f}")
    print(f"  precision графа (среднее): {report['precision_mean']:.3f}")
    print(f"  baseline «только сам файл»: {report['baseline_naive_recall_mean']:.3f}")
    print(f"  коммитов с полной связностью: {report['cases_full_recall']}")
    print(f"  коммитов с нулевой:           {report['cases_zero_recall']}")
    print(f"  артефакт: {out_file.relative_to(REPO)}")
    print("\n  разбор 6 худших случаев:")
    for r in sorted(results, key=lambda x: x["graph_recall"])[:6]:
        missed = [f for f in r["others"] if f not in r["reachable"]]
        print(f"    {r['commit']} {r['primary_symbol']}: recall={r['graph_recall']:.2f} "
              f"не связаны: {', '.join(missed[:3])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
