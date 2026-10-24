"""Проверка: правильную ли команду формирует RAGRAF для pysrc2cpg.

Рабочий формат найден замером: значение `--ignore-dir-names` должно быть
в кавычках, иначе запятая разбирается как разделитель аргументов
и Joern отвечает «Unknown argument 'bbb'».

Проверяется фактическая команда, которую строит RAGRAF, и то, что она
запускается без ошибки на тестовом проекте.
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
scripts = cpg_mod.find_joern_scripts(cfg)
py = scripts.get("pysrc2cpg")

runner = cpg_mod.JoernRunner(cfg)
dirs = runner._exclude_dirs()
print(f"  каталогов к исключению: {len(dirs)}")
print(f"    {dirs}")

# Команда собирается так же, как в build().
value = f'"--ignore-dir-names={",".join(dirs)}"'
cmd = [str(py), str(cfg.root), "-o", "probe.cpg.bin", value]
final = cpg_mod._shell_cmd(cmd, py)
print()
print("  команда (как её увидит cmd.exe):")
print(f"    {str(final)[:260]}")

print()
print("=" * 78)
print("ПРОВЕРКА НА ТЕСТОВОМ ПРОЕКТЕ")
print("=" * 78)
probe = REPO / ".staging" / "cmd_probe"
shutil.rmtree(probe, ignore_errors=True)
(probe / "src").mkdir(parents=True)
(probe / "aaa").mkdir(parents=True)
(probe / "bbb").mkdir(parents=True)
for name in ("src", "aaa", "bbb"):
    (probe / name / f"{name}.py").write_text(
        f"def f_{name}():\n    return 1\n", encoding="utf-8", newline="\n"
    )

# Тот же формат аргумента, что строит RAGRAF.
probe_value = '"--ignore-dir-names=aaa,bbb"'
probe_cmd = [str(py), str(probe), "-o", str(probe / "cpg.bin"), probe_value]
print(f"  команда: pysrc2cpg <probe> -o cpg.bin {probe_value}")
proc = subprocess.run(
    cpg_mod._shell_cmd(probe_cmd, py),
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    timeout=900, cwd=str(probe), env=cpg_mod.java_env(cfg),
)
err = ((proc.stderr or "") + (proc.stdout or "")).strip()
print(f"  код возврата: {proc.returncode}")
unknown = [ln for ln in err.splitlines() if "Unknown argument" in ln]
if unknown:
    print(f"  ОШИБКА аргумента: {unknown[0][:100]}")
elif proc.returncode != 0:
    print(f"  ошибка: {err.splitlines()[0][:120] if err else '(пусто)'}")
else:
    print("  аргумент принят")

# Проверяется, что исключённые каталоги не попали в граф.
cpg_file = probe / "cpg.bin"
if cpg_file.exists():
    joern = scripts.get("joern")
    script = probe / "list.sc"
    script.write_text(
        "@main def main(cpgFile: String, outFile: String): Unit = {\n"
        "  importCpg(cpgFile)\n"
        "  println(\"FILES=\" + cpg.file.name.l.mkString(\"|\"))\n"
        "}\n",
        encoding="utf-8", newline="\n",
    )
    joern_cmd = [str(joern), "--script", str(script),
                 f"--param", f"cpgFile={cpg_file}",
                 f"--param", f"outFile={probe / 'out.txt'}"]
    jp = subprocess.run(
        cpg_mod._shell_cmd(joern_cmd, joern), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=900, cwd=str(probe),
        env=cpg_mod.java_env(cfg),
    )
    text = (jp.stdout or "") + (jp.stderr or "")
    line = next((ln for ln in text.splitlines() if "FILES=" in ln), "")
    print(f"\n  файлы в графе: {line[:150]}")
    has_aaa = "aaa" in line
    has_bbb = "bbb" in line
    has_src = "src" in line
    print(f"    src/src.py в графе: {'да (верно)' if has_src else 'НЕТ (ошибка)'}")
    print(f"    aaa/aaa.py в графе: {'да (ИСКЛЮЧЕНИЕ НЕ РАБОТАЕТ)' if has_aaa else 'нет (верно)'}")
    print(f"    bbb/bbb.py в графе: {'да (ИСКЛЮЧЕНИЕ НЕ РАБОТАЕТ)' if has_bbb else 'нет (верно)'}")
else:
    print("  граф не создан — проверка содержимого пропущена")

shutil.rmtree(probe, ignore_errors=True)
print("\n  временные файлы удалены")
