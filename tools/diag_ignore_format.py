"""Проверка: в каком виде pysrc2cpg принимает --ignore-dir-names.

Установлено, что при передаче списка каталогов Joern отвечает
«Unknown argument '.idea'» на каждый каталог — то есть аргумент разбирается
не так, как я предположил.

Надо проверить форматы вызовом, а не догадкой:
  1. `--ignore-dir-names=a,b,c` (один аргумент со запятыми);
  2. `--ignore-dir-names a,b,c` (флаг и значение);
  3. повторение флага: `--ignore-dir-names a --ignore-dir-names b`;
  4. тот же список без кавычек и с кавычками.

Проверяется на маленьком каталоге: берётся тестовый проект с двумя
каталогами, и смотрится, какие файлы попали в граф.
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
print(f"  парсер: {py}")

# Тестовый проект: каталог src с файлом и каталог skip с файлом.
probe = REPO / ".staging" / "ignore_probe"
shutil.rmtree(probe, ignore_errors=True)
(probe / "src").mkdir(parents=True)
(probe / "skipme").mkdir(parents=True)
(probe / "src" / "keep.py").write_text(
    "def kept():\n    return 1\n", encoding="utf-8", newline="\n"
)
(probe / "skipme" / "drop.py").write_text(
    "def dropped():\n    return 2\n", encoding="utf-8", newline="\n"
)
print(f"  тестовый проект: {probe.relative_to(REPO)}")
print(f"    src/keep.py — должен остаться")
print(f"    skipme/drop.py — должен быть исключён")

VARIANTS = [
    ("один аргумент со запятыми", ["--ignore-dir-names=skipme"]),
    ("флаг и значение", ["--ignore-dir-names", "skipme"]),
    ("повтор флага", ["--ignore-dir-names", "skipme",
                     "--ignore-dir-names", "other"]),
    ("два каталога через запятую", ["--ignore-dir-names=skipme,other"]),
    ("без исключений (контроль)", []),
]

print()
print("=" * 78)
print("ФОРМАТЫ АРГУМЕНТА")
print("=" * 78)
print(f"\n  {'вариант':<32} {'код':>5}  результат")
print("  " + "-" * 70)

for label, extra in VARIANTS:
    out_dir = probe / f"out_{abs(hash(label)) % 10000}"
    shutil.rmtree(out_dir, ignore_errors=True)
    cmd = [str(py), str(probe), "-o", str(out_dir)] + extra
    try:
        proc = subprocess.run(
            cpg_mod._shell_cmd(cmd, py),
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=900, cwd=str(probe),
            env=cpg_mod.java_env(cfg),
        )
        code = proc.returncode
        err = ((proc.stderr or "") + (proc.stdout or "")).strip()
    except subprocess.TimeoutExpired:
        code, err = -1, "таймаут"
    created = out_dir.exists() and any(out_dir.rglob("*.bin"))
    unknown = "Unknown argument" in err
    if code == 0 and created:
        verdict = "принято, граф создан"
    elif unknown:
        # Показывается, какой именно аргумент не понят.
        bad = [ln for ln in err.splitlines() if "Unknown argument" in ln]
        verdict = f"ОТКЛОНЕНО: {bad[0][:44] if bad else ''}"
    else:
        verdict = f"код {code}: {err.splitlines()[0][:44] if err else ''}"
    print(f"  {label:<32} {code:>5}  {verdict}")
    shutil.rmtree(out_dir, ignore_errors=True)

# Проверка: попал ли исключённый файл в граф.
print()
print("=" * 78)
print("ПОПАЛ ЛИ ИСКЛЮЧЁННЫЙ КАТАЛОГ В ГРАФ")
print("=" * 78)
out_dir = probe / "out_check"
shutil.rmtree(out_dir, ignore_errors=True)
cmd = [str(py), str(probe), "-o", str(out_dir),
       "--ignore-dir-names=skipme"]
proc = subprocess.run(
    cpg_mod._shell_cmd(cmd, py),
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    timeout=900, cwd=str(probe), env=cpg_mod.java_env(cfg),
)
print(f"  вызов с --ignore-dir-names=skipme: код {proc.returncode}")
if proc.returncode == 0 and (out_dir / "cpg.bin").exists():
    # Читается граф запросом: какие файлы внутри.
    runner = cpg_mod.JoernRunner(cfg)
    script = (probe / "list.sc")
    script.write_text(
        "@main def main(cpgFile: String, outFile: String): Unit = {\n"
        "  importCpg(cpgFile)\n"
        "  println(\"FILES=\" + cpg.file.name.l.mkString(\"|\"))\n"
        "}\n",
        encoding="utf-8", newline="\n",
    )
    joern = scripts.get("joern")
    out_file = probe / "out.txt"
    joern_cmd = [str(joern), "--script", str(script),
                 f"--param", f"cpgFile={out_dir / 'cpg.bin'}",
                 f"--param", f"outFile={out_file}"]
    jp = subprocess.run(
        cpg_mod._shell_cmd(joern_cmd, joern), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=900, cwd=str(probe),
        env=cpg_mod.java_env(cfg),
    )
    text = (jp.stdout or "") + (jp.stderr or "")
    files_line = [ln for ln in text.splitlines() if "FILES=" in ln]
    print(f"  файлы в графе: {files_line[0][:120] if files_line else '(нет)'}")
    kept = "keep.py" in text
    dropped = "drop.py" in text
    print(f"  keep.py внутри:   {'да' if kept else 'нет'}")
    print(f"  drop.py внутри:   {'да (исключение НЕ работает)' if dropped else 'нет (работает)'}")
else:
    err = ((proc.stderr or "") + (proc.stdout or "")).strip()
    print(f"  граф не создан: {err.splitlines()[0][:100] if err else ''}")

shutil.rmtree(probe, ignore_errors=True)
print("\n  временные файлы удалены")
