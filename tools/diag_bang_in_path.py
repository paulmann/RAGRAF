"""Проверка: ломает ли символ `!` в пути передачу параметров Joern.

Гипотеза, которую надо проверить. Проект лежит в `C:\\!.Tihon\\sagaai_edit_github`,
а восклицательный знак — спецсимвол cmd.exe при включённом «отложенном
раскрытии переменных». RAGRAF запускает `.bat`-скрипты Joern через cmd
(`_shell_cmd`), поэтому путь с `!` может искажаться при передаче.

Признаки, которые это подтверждают:
  * ошибка `unable to parse param input cpgFile` — параметр искажён;
  * на PHP-корпусе в пути без `!` те же запросы работают.

Проверяется сравнением: тот же скрипт запускается для CPG в каталоге
с `!` и в каталоге без него. Если во втором случае работает — гипотеза
верна, и надо менять способ передачи пути.

Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf import cpg as cpg_mod  # noqa: E402

cfg = load_config(REPO / "config" / "sagaai.yaml")
runner = cpg_mod.JoernRunner(cfg)
scripts = cpg_mod.find_joern_scripts(cfg)
joern = scripts.get("joern")

cpg_files = sorted(runner.workdir.glob("rev-*.cpg.bin"),
                   key=lambda p: -p.stat().st_size)
if not cpg_files:
    print("  CPG нет")
    raise SystemExit(1)
source_cpg = cpg_files[0]
print(f"  исходный CPG: {source_cpg}")
print(f"  путь содержит '!': {'!' in str(source_cpg)}")

# Копия CPG переносится в каталог БЕЗ символа `!`.
safe_dir = REPO / ".staging" / "cpg_safe_path"
safe_dir.mkdir(parents=True, exist_ok=True)
safe_cpg = safe_dir / "probe.cpg.bin"
shutil.copy2(source_cpg, safe_cpg)
print(f"  копия без '!': {safe_cpg}")

SCRIPT_BODY = (
    "@main def main(cpgFile: String, outFile: String): Unit = {\n"
    "  importCpg(cpgFile)\n"
    "  println(s\"CALLS=${cpg.call.size}\")\n"
    "  println(s\"METHODS=${cpg.method.size}\")\n"
    "}\n"
)
script_file = safe_dir / "probe.sc"
script_file.write_text(SCRIPT_BODY, encoding="utf-8", newline="\n")

print()
print("=" * 78)
print("ЗАПУСК НА ДВУХ ПУТЯХ")
print("=" * 78)


def run(cpg_path: pathlib.Path, use_shell: bool) -> tuple[int, str]:
    """Запустить Joern с указанным CPG.

    use_shell=True — путь через cmd (как делает RAGRAF для .bat);
    use_shell=False — прямой вызов (Python передаёт аргументы сам).
    """
    cmd = [str(joern), "--script", str(script_file),
           f"--param", f"cpgFile={cpg_path}",
           "--param", f"outFile={safe_dir / 'out.txt'}"]
    env = cpg_mod.java_env(cfg)
    if use_shell:
        command = cpg_mod._shell_cmd(cmd, joern)
        proc = subprocess.run(command, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=900, cwd=str(safe_dir), env=env)
    else:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=900, cwd=str(safe_dir), env=env)
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return proc.returncode, out


for label, path, use_shell in (
    ("путь с '!' через cmd", source_cpg, True),
    ("путь без '!' через cmd", safe_cpg, True),
    ("путь с '!' напрямую", source_cpg, False),
    ("путь без '!' напрямую", safe_cpg, False),
):
    print(f"\n  {label}")
    print(f"    CPG: {path}")
    try:
        code, out = run(path, use_shell)
    except subprocess.TimeoutExpired:
        print("    таймаут")
        continue
    values = [ln.strip() for ln in out.splitlines()
              if ln.strip().startswith(("CALLS=", "METHODS="))]
    errors = [ln.strip() for ln in out.splitlines()
              if "Error" in ln or "Exception" in ln or "unable" in ln]
    print(f"    код {code}, значения: {values or '(нет)'}")
    for e in errors[:2]:
        print(f"    ошибка: {e[:110]}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Если значения CALLS/METHODS появляются только на пути БЕЗ '!',
  значит причина — символ восклицательного знака в пути, а не язык Python.
  Тогда решение: передавать путь Joern так, чтобы cmd его не разбирал
  (например копировать CPG в безопасный каталог или запускать без cmd).

  Если значения не появились нигде — значит дело в самом графе Python.
""")
