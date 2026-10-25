"""Диагностика экспорта CPG: полный вывод joern-export и попытки с лимитом памяти."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.cpg import find_java, find_joern_scripts, java_env, tools_root  # noqa: E402


def main() -> int:
    cfg = load_config(REPO / "config" / "revoap.yaml")
    scripts = find_joern_scripts(cfg)
    workdir = cfg.ragraf_dir / "cpg"
    cpg = workdir / "rev-0014.cpg.bin"
    if not cpg.exists():
        candidates = sorted(workdir.glob("*.cpg.bin"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            print("нет ни одного cpg.bin")
            return 1
        cpg = candidates[0]
    print(f"CPG: {cpg.name} ({cpg.stat().st_size / 1_048_576:.1f} МБ)")

    out_dir = tools_root(cfg) / "exp_test"
    if out_dir.exists():
        import shutil

        shutil.rmtree(out_dir, ignore_errors=True)

    for heap in ("2g", "6g", "12g"):
        print(f"\n=== joern-export с -Xmx{heap} ===")
        env = java_env(cfg)
        env["JAVA_OPTS"] = f"-Xmx{heap}"
        cmd = [
            str(scripts["joern-export"]),
            str(cpg),
            "--repr=all",
            "--format=graphson",
            "-o",
            str(out_dir),
        ]
        t0 = time.perf_counter()
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, cwd=str(workdir), timeout=1800,
        )
        dt = time.perf_counter() - t0
        print(f"код: {proc.returncode}, время: {dt:.1f} с")
        print(f"stdout: {(proc.stdout or '')[-600:]}")
        err = (proc.stderr or "")
        # Показываем первую ошибку, а не хвост стека
        for line in err.splitlines():
            if "Error" in line or "error" in line or "Exception" in line or "OutOfMemory" in line:
                print(f"ошибка: {line.strip()[:300]}")
                break
        files = sorted(out_dir.glob("*.json")) if out_dir.exists() else []
        if files:
            print(f"OK: {files[0].name} ({files[0].stat().st_size / 1_048_576:.1f} МБ)")
            return 0
        if out_dir.exists():
            import shutil

            shutil.rmtree(out_dir, ignore_errors=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
