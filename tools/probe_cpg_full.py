"""Пробная сборка CPG на полном корпусе с полным выводом ошибки.

Запускается вручную: нужен полный текст исключения Joern, чтобы понять причину
падения на реальном проекте (на подкаталоге src/Core сборка проходила).
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.cpg import find_joern_scripts, find_php, find_java, php_env, tools_root  # noqa: E402


def main() -> int:
    cfg = load_config(REPO / "config" / "revoap.yaml")
    scripts = find_joern_scripts(cfg)
    print(f"joern-parse: {scripts.get('joern-parse')}")
    print(f"php: {find_php()}")
    print(f"java: {find_java(cfg)}")

    out = tools_root(cfg) / "full-test.cpg.bin"
    if out.exists():
        out.unlink()

    cmd = [
        str(scripts["joern-parse"]),
        str(cfg.root),
        "--language", "PHP",
        "-o", str(out),
    ]
    for rel in ("vendor", "var", ".zwork"):
        if (cfg.root / rel).exists():
            cmd.extend(["--frontend-args", "--exclude", rel])
    print("команда:", " ".join(f'"{c}"' if " " in c else c for c in cmd))

    t0 = time.perf_counter()
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=php_env(cfg), cwd=str(tools_root(cfg) / "joern-cli"),
    )
    dt = time.perf_counter() - t0
    print(f"код возврата: {proc.returncode}, время: {dt:.1f} с")
    print(f"\n=== STDOUT (последние 3000 символов) ===\n{(proc.stdout or '')[-3000:]}")
    print(f"\n=== STDERR (последние 4000 символов) ===\n{(proc.stderr or '')[-4000:]}")
    print(f"\nCPG создана: {out.exists()} ({out.stat().st_size / 1_048_576:.1f} МБ)" if out.exists() else "CPG не создана")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
