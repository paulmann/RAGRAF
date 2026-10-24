"""Поиск рабочего способа экспорта CPG: варианты лимита памяти и режима.

Проблема: `joern-export --repr=all --format=graphson` на полном корпусе падает с
OutOfMemoryError при любом JAVA_OPTS. Здесь проверяются:
  * документированный способ передачи памяти — аргумент -J-Xmx (не JAVA_OPTS);
  * режим --repr=cpg (экспорт подграфа на метод) вместо all;
  * режим neo4jcsv как альтернативный машинно-читаемый формат.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.cpg import find_joern_scripts, java_env, tools_root  # noqa: E402


def try_export(cfg, scripts, cpg: Path, label: str, extra: list[str],
               repr_mode: str, fmt: str) -> bool:
    out_dir = tools_root(cfg) / f"exp_try_{label}"
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    cmd = [str(scripts["joern-export"]), *extra, str(cpg),
           f"--repr={repr_mode}", f"--format={fmt}", "-o", str(out_dir)]
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=java_env(cfg), cwd=str(cpg.parent), timeout=2400,
        )
    except subprocess.TimeoutExpired:
        print(f"  {label}: тайм-аут 40 мин")
        return False
    dt = time.perf_counter() - t0
    files = sorted(out_dir.rglob("*")) if out_dir.exists() else []
    files = [f for f in files if f.is_file()]
    size = sum(f.stat().st_size for f in files) / 1_048_576
    errors = [
        line.strip()[:180]
        for line in (proc.stderr or "").splitlines()
        if "Error" in line or "Exception" in line
    ][:1]
    print(f"  {label}: код={proc.returncode} время={dt:.0f}s файлов={len(files)} "
          f"{size:.1f} МБ {errors}")
    return bool(files) and proc.returncode == 0


def main() -> int:
    cfg = load_config(REPO / "config" / "revoap.yaml")
    scripts = find_joern_scripts(cfg)
    cpg = cfg.ragraf_dir / "cpg" / "rev-0014.cpg.bin"
    print(f"CPG: {cpg.name} ({cpg.stat().st_size / 1_048_576:.1f} МБ)")

    attempts = [
        ("j18_all", ["-J-Xmx18g"], "all", "graphson"),
        ("default_cpg", [], "cpg", "graphson"),
        ("j18_neo4jcsv", ["-J-Xmx18g"], "all", "neo4jcsv"),
    ]
    for label, extra, repr_mode, fmt in attempts:
        if try_export(cfg, scripts, cpg, label, extra, repr_mode, fmt):
            print(f"\nРАБОЧИЙ ВАРИАНТ: {label} (repr={repr_mode}, format={fmt}, {extra})")
            return 0
    print("\nни один вариант не сработал")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
