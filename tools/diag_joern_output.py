"""Что именно отвечает Joern на простой запрос: полный вывод.

Запросы с `println` не дали ответа, хотя прежде тот же путь отдавал
72 000+ записей. Надо увидеть ПОЛНЫЙ вывод процесса: возможно, ответ
пишется, но моя фильтрация по префиксу его не ловит, либо скрипт
падает молча.

Проверяется с печатью всего stdout и stderr.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf import cpg as cpg_mod  # noqa: E402

cfg = load_config(REPO / "config" / "sagaai.yaml")
runner = cpg_mod.JoernRunner(cfg)
scripts = cpg_mod.find_joern_scripts(cfg)
joern = scripts.get("joern")
print(f"  joern: {joern}")

files = sorted(runner.workdir.glob("rev-*.cpg.bin"),
               key=lambda p: -p.stat().st_size)
cpg_path = files[0]
print(f"  CPG: {cpg_path.name}")

# Простейший скрипт: печать числа методов.
script_file = runner.workdir / "probe_simple.sc"
script_file.write_text(
    "@main def main(cpgFile: String, outFile: String): Unit = {\n"
    "  importCpg(cpgFile)\n"
    "  println(\"HELLO_FROM_SCRIPT\")\n"
    "  println(\"METHODS=\" + cpg.method.size)\n"
    "}\n",
    encoding="utf-8", newline="\n",
)
print(f"  скрипт: {script_file.name}")
print(f"  содержимое:")
for line in script_file.read_text(encoding="utf-8").splitlines():
    print(f"    {line}")

out_file = runner.workdir / "probe_out.txt"
cmd = [str(joern), "--script", str(script_file),
       f"--param", f"cpgFile={cpg_path}",
       f"--param", f"outFile={out_file}"]
print(f"\n  команда: joern --script {script_file.name} --param cpgFile=... ")

print()
print("=" * 78)
print("ПОЛНЫЙ ВЫВОД ПРОЦЕССА")
print("=" * 78)
proc = subprocess.run(
    cpg_mod._shell_cmd(cmd, joern), capture_output=True, text=True,
    encoding="utf-8", errors="replace", timeout=900,
    cwd=str(runner.workdir), env=cpg_mod.java_env(cfg),
)
print(f"  код возврата: {proc.returncode}")
print(f"\n  --- STDOUT ({len((proc.stdout or '').splitlines())} строк) ---")
for line in (proc.stdout or "").splitlines()[:25]:
    print(f"    {line}")
print(f"\n  --- STDERR ({len((proc.stderr or '').splitlines())} строк) ---")
for line in (proc.stderr or "").splitlines()[:25]:
    print(f"    {line}")

# Проверка: создан ли файл результата.
print(f"\n  файл результата существует: {out_file.exists()}")
if out_file.exists():
    print(f"    размер: {out_file.stat().st_size} б")

script_file.unlink(missing_ok=True)
out_file.unlink(missing_ok=True)
