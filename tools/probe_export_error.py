"""Полный текст ошибки экспорта через штатный метод JoernRunner.export."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.cpg import JoernRunner, _heap_arg, _shell_cmd, php_env  # noqa: E402


def main() -> int:
    cfg = load_config(REPO / "config" / "revoap.yaml")
    runner = JoernRunner(cfg)
    rev = "rev-0017"
    cpg = cfg.ragraf_dir / "cpg" / f"{rev}.cpg.bin"
    if not cpg.exists():
        cands = sorted((cfg.ragraf_dir / "cpg").glob("*.cpg.bin"), key=lambda p: p.stat().st_mtime)
        cpg = cands[-1]
        rev = cpg.stem.replace(".cpg", "")
    print(f"rev={rev}, cpg={cpg.name} ({cpg.stat().st_size / 1_048_576:.1f} МБ)")

    script = runner.scripts.get("joern-export")
    out_dir = cfg.ragraf_dir / "cpg" / f"export_{rev}_neo4jcsv"
    import shutil

    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)

    cmd = [
        str(script),
        *([_heap_arg(cfg)] if _heap_arg(cfg) else []),
        str(cpg),
        "--repr=all",
        "--format=neo4jcsv",
        "-o",
        str(out_dir),
    ]
    finalized = _shell_cmd(cmd, script)
    print(f"тип команды: {type(finalized).__name__}")
    print(f"команда: {finalized}")
    proc = subprocess.run(
        finalized, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=php_env(cfg), cwd=str(runner.workdir),
    )
    print(f"код={proc.returncode}")
    print(f"файлов создано: {len(list(out_dir.iterdir())) if out_dir.exists() else 0}")
    print("\n=== stderr полностью ===")
    print(proc.stderr or "(пусто)")
    print("\n=== stdout полностью ===")
    print(proc.stdout or "(пусто)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
