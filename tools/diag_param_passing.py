"""Диагностика: почему прежний путь (отдельный процесс) не передаёт параметры.

Ошибка: «Option --param failed when given 'cpgFile'. unable to parse param input
cpgFile». Это значит, что Joern получил `--param` и `cpgFile` как РАЗНЫЕ
аргументы, либо путь потерялся.

Синтаксис по справке верный: `--param param1=value1`. Значит проблема в том,
как аргументы доходят до процесса: для .bat нужна оболочка cmd, и собираемая
строка команды может терять кавычки вокруг путей с пробелами.

Проверяется:
  1. какая именно строка команды собирается;
  2. доходят ли аргументы до Joern;
  3. работает ли вызов при разных способах сборки.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.cpg import _shell_cmd, _needs_shell, find_joern_scripts  # noqa: E402

cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
scripts = find_joern_scripts(cfg)
script = scripts.get("joern")
print(f"скрипт Joern: {script}")
print(f"нужна оболочка cmd: {_needs_shell(script) if script else '?'}")

workdir = pathlib.Path(cfg.root) / ".ragraf" / "cpg"
cpg_file = sorted(workdir.glob("*.cpg.bin"),
                  key=lambda p: p.stat().st_mtime, reverse=True)[0]
out_file = REPO / ".staging" / "joern_process_probe.json"
if out_file.exists():
    out_file.unlink()

# Простой скрипт для проверки передачи параметров.
probe_script = REPO / ".staging" / "joern_param_probe.sc"
probe_script.write_text(
    "@main def exec(cpgFile: String, outFile: String) = {\n"
    "  val text = s\"cpg=$cpgFile out=$outFile\"\n"
    "  os.write.over(os.Path(outFile), text)\n"
    "}\n",
    encoding="utf-8", newline="\n",
)
print(f"пробный скрипт: {probe_script.name}")

print()
print("=== 1. Что собирает текущая функция _shell_cmd ===")
cmd = [
    str(script),
    "--script", str(probe_script),
    "--param", f"cpgFile={cpg_file}",
    "--param", f"outFile={out_file}",
]
built = _shell_cmd(cmd, script)
print(f"  тип результата: {type(built).__name__}")
print(f"  команда: {str(built)[:300]}")

print()
print("=== 2. Запуск этой командой ===")
env = dict(os.environ)
env["JAVA_HOME"] = str(REPO / ".tools" / "jdk")
env["PATH"] = f"{REPO / '.tools' / 'jdk' / 'bin'}{os.pathsep}" + env.get("PATH", "")
try:
    proc = subprocess.run(
        built, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=900, env=env, cwd=str(workdir),
    )
    print(f"  код возврата: {proc.returncode}")
    tail = (proc.stderr or proc.stdout or "")[-500:]
    print(f"  вывод: {tail}")
    print(f"  файл создан: {out_file.exists()}")
    if out_file.exists():
        print(f"  содержимое: {out_file.read_text(encoding='utf-8')[:200]}")
except Exception as exc:
    print(f"  ошибка: {type(exc).__name__}: {exc}")

print()
print("=== 3. Проверка альтернативных способов вызова ===")
# Проверяется вызов через cmd с явной строкой и кавычками вокруг всех аргументов.
variants = {
    "cmd /c со всеми кавычками": (
        "cmd.exe",
        ["/c", f'"{script}" "--script" "{probe_script}" '
               f'"--param" "cpgFile={cpg_file}" "--param" "outFile={out_file}"'],
    ),
    "напрямую без cmd": ([str(script), "--script", str(probe_script),
                          "--param", f"cpgFile={cpg_file}",
                          "--param", f"outFile={out_file}"], None),
}
for label, spec in variants.items():
    if out_file.exists():
        out_file.unlink()
    try:
        if isinstance(spec, tuple):
            exe, args = spec
            result = subprocess.run(
                [exe] + args, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=900,
                env=env, cwd=str(workdir),
            )
        else:
            result = subprocess.run(
                spec, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=900,
                env=env, cwd=str(workdir),
            )
        created = out_file.exists()
        tail = (result.stderr or result.stdout or "")[-200:]
        print(f"  {label}: код {result.returncode}, файл {created}")
        if not created and tail:
            print(f"    вывод: {tail.strip()[:180]}")
        if created:
            print(f"    содержимое: {out_file.read_text(encoding='utf-8')[:150]}")
    except Exception as exc:
        print(f"  {label}: ошибка {type(exc).__name__}: {str(exc)[:80]}")

print()
print("=== 4. ВЫВОД ===")
print("""
  Сравниваются строки выше. Работающим считается тот способ, при котором
  файл результата создан и содержит переданные пути.
""")
