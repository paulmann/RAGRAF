"""Проверка: ломает ли символ `!` в пути передачу параметров Joern.

ЭТО РЕШАЮЩАЯ ПРОВЕРКА. Прошлые выводы оказались неверными из-за неё:
я дважды решал, что дело в Python-графе, хотя причина может быть в пути.

Факты:
  * путь проекта `C:\\!.Tihon\\sagaai_edit_github` содержит `!`;
  * Joern отвечает «unable to parse param input cpgFile» и
    «Unknown argument 'C:\\!.Tihon\\...\\probe_out.txt'»;
  * в cmd.exe `!` — спецсимвол отложенного раскрытия переменных;
  * на PHP-корпусе (`C:/AutoClaw/...`, без `!`) те же вызовы работали.

Ранее я проверял эту гипотезу и получил «ошибка одинакова везде». Но тогда
я передавал параметры НЕВЕРНЫМ способом (`--param` отдельным аргументом),
и ошибка разбора параметров маскировала эффект пути.

Здесь проверяется чисто: один и тот же CPG копируется в два места —
с `!` в пути и без него, — и запускается ОДИН и тот же корректный вызов.
"""

from __future__ import annotations

import pathlib
import shutil
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

files = sorted(runner.workdir.glob("rev-*.cpg.bin"),
               key=lambda p: -p.stat().st_size)
source_cpg = files[0]
print(f"  исходный CPG: {source_cpg}")
print(f"  содержит '!': {'!' in str(source_cpg)}")

# Копия в каталог без `!`.
safe_dir = REPO / ".staging" / "nobang"
safe_dir.mkdir(parents=True, exist_ok=True)
safe_cpg = safe_dir / "probe.cpg.bin"
shutil.copy2(source_cpg, safe_cpg)
print(f"  копия без '!': {safe_cpg}")

script_file = safe_dir / "probe.sc"
script_file.write_text(
    "@main def main(cpgFile: String, outFile: String): Unit = {\n"
    "  importCpg(cpgFile)\n"
    "  println(\"METHODS=\" + cpg.method.size)\n"
    "  println(\"CALLS=\" + cpg.call.size)\n"
    "  println(\"OUTSIDE=\" + cpg.call.filter(_.method.name != \"<module>\").size)\n"
    "}\n",
    encoding="utf-8", newline="\n",
)

print()
print("=" * 78)
print("ЗАПУСК: ПУТЬ С '!' И БЕЗ НЕГО")
print("=" * 78)
for label, cpg in (("с '!'", source_cpg), ("без '!'", safe_cpg)):
    out_file = safe_dir / f"out_{abs(hash(label)) % 1000}.txt"
    cmd = [str(joern), "--script", str(script_file),
           f"--param", f"cpgFile={cpg}",
           f"--param", f"outFile={out_file}"]
    try:
        proc = subprocess.run(
            cpg_mod._shell_cmd(cmd, joern), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=900,
            cwd=str(safe_dir), env=cpg_mod.java_env(cfg),
        )
        text = (proc.stdout or "") + (proc.stderr or "")
        values = [ln.strip() for ln in text.splitlines()
                  if ln.strip().startswith(("METHODS=", "CALLS=", "OUTSIDE="))]
        errors = [ln.strip() for ln in text.splitlines()
                  if "Error" in ln or "Exception" in ln or "Unknown" in ln]
        print(f"\n  путь {label}")
        print(f"    код возврата: {proc.returncode}")
        print(f"    значения: {values or '(нет)'}")
        for e in errors[:2]:
            print(f"    ошибка: {e[:110]}")
    except subprocess.TimeoutExpired:
        print(f"\n  путь {label}: таймаут")
    out_file.unlink(missing_ok=True)

# Проверка: остаются ли '!' в CPG при копировании в тот же каталог.
print()
print("=" * 78)
print("СРАВНЕНИЕ С ПРЕЖНИМИ РЕЗУЛЬТАТАМИ")
print("=" * 78)
print("""
  Что уже известно:
    * корректный путь передачи параметров (кавычки вокруг значения)
      был найден позже, чем первая проверка гипотезы с '!';
    * тогда ошибка была одинакова на обоих путях — но потому, что
      параметры передавались неверно, а не потому, что путь ни при чём.

  Если сейчас значения появляются только на пути без '!', причина
  установлена окончательно: восклицательный знак в пути.

  Тогда решение — не менять язык и не отказываться от графа,
  а исправить передачу пути (экранирование или обход cmd).
""")

shutil.rmtree(safe_dir, ignore_errors=True)
print("  временные файлы удалены")
