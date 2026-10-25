"""Сравнение: чем мой вызов Joern отличается от рабочего вызова RAGRAF.

Факты:
  * `runner.query()` RAGRAF работает и отдаёт 172 184 записи;
  * мои собственные вызовы с `println` падают с
    «unable to parse param input cpgFile»;
  * это не зависит от пути (проверено на пути с `!` и без него).

Значит различие в СПОСОБЕ вызова. Надо найти его сравнением кода,
а не гадать.

Ничего не меняет.
"""

from __future__ import annotations

import inspect
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf import cpg as cpg_mod  # noqa: E402

cfg = load_config(REPO / "config" / "sagaai.yaml")
runner = cpg_mod.JoernRunner(cfg)

print("=" * 78)
print("1. КАК RAGRAF ФОРМИРУЕТ КОМАНДУ ЗАПРОСА")
print("=" * 78)
# Показывается исходный код метода query: именно он работает.
src = inspect.getsource(type(runner).query)
for i, line in enumerate(src.splitlines(), 1):
    if any(k in line for k in ("cmd", "--script", "--param", "--heap",
                               "_shell_cmd", "cpgFile", "outFile",
                               "script_file", "subprocess")):
        print(f"  {i:>3}: {line.strip()[:100]}")

print()
print("=" * 78)
print("2. ЧТО ИМЕННО РАЗЛИЧАЕТСЯ")
print("=" * 78)
# Извлекаются аргументы, которые RAGRAF добавляет помимо моего набора.
import re  # noqa: E402

# Ищется, добавляется ли heap-параметр и в каком виде.
heap = cpg_mod._heap_arg(cfg)
print(f"  heap-параметр: {heap!r}")
print(f"  он добавляется в команду сборки, но, возможно, и в запрос")

# Проверяется: есть ли в query() добавление heap.
has_heap = "_heap_arg" in src or "heap" in src.lower()
print(f"  query() использует heap: {'да' if has_heap else 'нет'}")

print()
print("=" * 78)
print("3. ПРОБА: ПОВТОРИТЬ РАБОЧИЙ ВЫЗОВ ДОСЛОВНО")
print("=" * 78)
# Берётся рабочая команда RAGRAF и запускается напрямую с печатью.
import subprocess  # noqa: E402
import tempfile  # noqa: E402

script_text = cpg_mod.QUERY_CALLS
script_file = runner.workdir / "probe_working.sc"
cpg_mod.util.write_text(script_file, script_text)
out_file = runner.workdir / "probe_working.json"
if out_file.exists():
    out_file.unlink()

script = cpg_mod.find_joern_scripts(cfg).get("joern")
cpg_files = sorted(runner.workdir.glob("rev-*.cpg.bin"),
                   key=lambda p: -p.stat().st_size)
cpg_path = cpg_files[0]

# Команда собирается ТОЧНО как в query().
cmd = [
    str(script),
    "--script",
    str(script_file),
    "--param",
    f"cpgFile={cpg_path}",
    "--param",
    f"outFile={out_file}",
]
print(f"  команда (как в query):")
for part in cmd:
    print(f"    {str(part)[:110]}")
print(f"  через _shell_cmd: {'да' if isinstance(cpg_mod._shell_cmd(cmd, script), str) else 'нет'}")

proc = subprocess.run(
    cpg_mod._shell_cmd(cmd, script),
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    timeout=1800, cwd=str(runner.workdir), env=cpg_mod.php_env(cfg),
)
print(f"\n  код возврата: {proc.returncode}")
print(f"  файл результата создан: {out_file.exists()}")
if out_file.exists():
    print(f"    размер: {out_file.stat().st_size / 1024:.0f} КБ")
err = (proc.stderr or "").strip()
if err:
    print(f"  stderr (первые 3 строки):")
    for line in err.splitlines()[:3]:
        print(f"    {line[:110]}")

print()
print("=" * 78)
print("4. В ЧЁМ РАЗЛИЧИЕ ОКРУЖЕНИЯ")
print("=" * 78)
# Единственное видимое различие: env. RAGRAF использует php_env,
# мои проверки — java_env.
print(f"  php_env содержит JAVA_HOME: "
      f"{'JAVA_HOME' in cpg_mod.php_env(cfg)}")
print(f"  java_env содержит JAVA_HOME: "
      f"{'JAVA_HOME' in cpg_mod.java_env(cfg)}")
print(f"  php_env содержит PATH:      {'PATH' in cpg_mod.php_env(cfg)}")
print(f"  java_env содержит PATH:     {'PATH' in cpg_mod.java_env(cfg)}")
print(f"  php_env содержит JAVA_OPTS: {'JAVA_OPTS' in cpg_mod.php_env(cfg)}")
print(f"  java_env содержит JAVA_OPTS: {'JAVA_OPTS' in cpg_mod.java_env(cfg)}")

for name in ("php_env", "java_env"):
    fn = getattr(cpg_mod, name)
    env = fn(cfg)
    keys = sorted(k for k in env if k in ("JAVA_HOME", "PATH", "JAVA_OPTS",
                                          "RAGRAF_JOERN_HEAP_GB"))
    print(f"\n  {name}: {keys}")

script_file.unlink(missing_ok=True)
out_file.unlink(missing_ok=True)
print("\n  временные файлы удалены")
