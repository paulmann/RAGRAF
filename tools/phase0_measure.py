"""Измеритель Фазы 0: серии проб одним и тем же инструментом (CLI ragraf.py).

Запуск:
  python phase0_measure.py --label baseline --n 20 --n-slow 6 --out _analysis/phase0_baseline.json

Принципы (из промта фазы 0):
  * одна и та же команда до и после правки;
  * прогрев вне серии;
  * p50/p90/max/n, а не одно число;
  * фиксируется состояние корпуса (rev_id) до и после серии.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import statistics
import subprocess
import sys
import time

RAGRAF = pathlib.Path(__file__).resolve().parents[1]
PY = str(RAGRAF / ".venv" / "Scripts" / "python.exe")
if not pathlib.Path(PY).exists():
    PY = sys.executable
TOOL = str(RAGRAF / "tools" / "ragraf.py")

ENV = dict(os.environ)
ENV["PYTHONIOENCODING"] = "utf-8"
ENV["RAGRAF_AGENT"] = "phase0-measure"
ENV["RAGRAF_AGENT_KIND"] = "internal"


def run(args: list[str], timeout: float = 300.0) -> tuple[float, int, str]:
    """Одна проба: время стены и код возврата. Вывод не печатается (шум)."""
    start = time.perf_counter()
    proc = subprocess.run(
        [PY, TOOL, *args],
        cwd=str(RAGRAF), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=ENV, timeout=timeout,
    )
    elapsed = (time.perf_counter() - start) * 1000.0
    return elapsed, proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def series(name: str, args: list[str], n: int, *, warmup: int = 1,
           timeout: float = 300.0) -> dict:
    for _ in range(warmup):
        run(args, timeout)
    samples: list[float] = []
    codes: dict[int, int] = {}
    last = ""
    for _ in range(n):
        elapsed, code, out = run(args, timeout)
        samples.append(elapsed)
        codes[code] = codes.get(code, 0) + 1
        last = out
    samples_sorted = sorted(samples)
    def pct(k: float) -> float:
        index = min(len(samples_sorted) - 1, max(0, int(round(k * (len(samples_sorted) - 1)))))
        return round(samples_sorted[index], 3)
    mean = statistics.fmean(samples)
    sigma = statistics.pstdev(samples) if len(samples) > 1 else 0.0
    return {
        "name": name,
        "cmd": " ".join(args),
        "n": n,
        "n_ok": n >= 20,
        "p50": pct(0.50), "p90": pct(0.90), "max": round(max(samples), 3),
        "min": round(min(samples), 3), "mean": round(mean, 3), "sigma": round(sigma, 3),
        "exit_codes": codes,
        "sample_tail": last[-400:].strip(),
    }


def corpus_state() -> dict:
    """Состояние корпуса: ревизия и суммы — чтобы «до/после» были на одном rev_id."""
    path = RAGRAF / ".ragraf" / "rev.json"
    data = {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return {
        "rev": data.get("rev_id") or data.get("id") or "",
        "corpus_hash": str(data.get("corpus_hash") or "")[:24],
        "files": data.get("files") or data.get("file_count") or "",
    }


def java_procs() -> int:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq java.exe", "/NH"],
            capture_output=True, text=True, timeout=60).stdout
        return out.lower().count("java.exe")
    except Exception:  # noqa: BLE001
        return -1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--n-slow", type=int, default=6, help="пробы для catalog (дорогая операция)")
    ap.add_argument("--second-root", default=str(pathlib.Path(__file__).resolve().parents[1].parent / "BWO"))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    result: dict = {
        "label": args.label,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "python": PY,
        "corpus_before": corpus_state(),
        "java_before": java_procs(),
        "series": [],
    }

    plan = [
        ("B1_ensure_warm", ["ensure", str(RAGRAF)], args.n, 600),
        ("B2_ensure_force", ["ensure", str(RAGRAF), "--force"], args.n, 600),
        ("B3_status", ["-p", "config/ragraf.yaml", "query", "index_status"], args.n, 600),
        ("B3b_cli_status", ["-p", "config/ragraf.yaml", "status"], args.n, 600),
        ("B5_search", ["-p", "config/ragraf.yaml", "query", "search",
                       "операция ensure проверка свежести"], args.n, 600),
        ("B6_usage", ["-p", "config/ragraf.yaml", "query", "usage"], min(args.n, 10), 600),
        ("B4_catalog", ["-p", "config/ragraf.yaml", "query", "catalog"], args.n_slow, 1800),
    ]
    for name, cmd, count, timeout in plan:
        item = series(name, cmd, count, timeout=timeout)
        result["series"].append(item)
        print(f"{name:18} n={count:<3} p50={item['p50']:>10.1f} p90={item['p90']:>10.1f} "
              f"max={item['max']:>10.1f} σ={item['sigma']:>8.1f} codes={item['exit_codes']}",
              flush=True)

    # Второй корпус: проверка масштабной зависимости (если он в реестре).
    second = pathlib.Path(args.second_root)
    if second.exists():
        item = series("B1_ensure_warm_second", ["ensure", str(second)], min(args.n, 10), timeout=600)
        result["series"].append(item)
        print(f"{'B1_second':18} n={item['n']:<3} p50={item['p50']:>10.1f} "
              f"p90={item['p90']:>10.1f} codes={item['exit_codes']}", flush=True)

    result["corpus_after"] = corpus_state()
    result["java_after"] = java_procs()
    result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("сохранено:", out)
    print("rev до/после:", result["corpus_before"].get("rev"), "/", result["corpus_after"].get("rev"))
    print("java до/после:", result["java_before"], "/", result["java_after"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
