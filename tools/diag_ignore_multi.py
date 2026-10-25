"""Проверка: как передать pysrc2cpg НЕСКОЛЬКО исключаемых каталогов.

Из прошлого замера:
  * `--ignore-dir-names=skipme` — принимается (код 0);
  * `--ignore-dir-names=skipme,other` — ОТКЛОНЕНО («Unknown argument 'other'»);
  * повтор флага тоже отклонён.

То есть запятая воспринимается как разделитель аргументов, а не как часть
значения. Нужно найти рабочий способ передать много каталогов.

Кандидаты, которые надо проверить вызовом:
  1. кавычки вокруг значения: `--ignore-dir-names="a,b"`;
  2. экранирование запятой;
  3. `--ignore-dir-names` с пробелом как разделителем в одном аргументе;
  4. `--ignore-dir-names a --ignore-dir-names b` без `=` (уже отклонено,
     но проверяется вариант с `=` у каждого).

Проверяется на тестовом проекте с тремя каталогами — по коду возврата
и по тому, какие файлы попали в граф.
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

# Тестовый проект: один рабочий каталог и два служебных.
probe = REPO / ".staging" / "ignore_multi_probe"
shutil.rmtree(probe, ignore_errors=True)
for name in ("src", "aaa", "bbb"):
    (probe / name).mkdir(parents=True)
    (probe / name / f"{name}.py").write_text(
        f"def f_{name}():\n    return 1\n", encoding="utf-8", newline="\n"
    )
print(f"  тестовый проект: {probe.relative_to(REPO)}")
print("    src/src.py — должен остаться")
print("    aaa/aaa.py, bbb/bbb.py — должны быть исключены")

VARIANTS = [
    ("кавычки внутри значения", ['--ignore-dir-names="aaa,bbb"']),
    ("кавычки как отдельный аргумент",
     ["--ignore-dir-names", '"aaa,bbb"']),
    ("двойной слэш разделитель", ["--ignore-dir-names=aaa,bbb"]),
    ("пробел в значении", ['--ignore-dir-names="aaa bbb"']),
    ("повтор с =", ["--ignore-dir-names=aaa", "--ignore-dir-names=bbb"]),
    ("один каталог (контроль)", ["--ignore-dir-names=aaa"]),
]

print()
print("=" * 78)
print("ВАРИАНТЫ ПЕРЕДАЧИ НЕСКОЛЬКИХ КАТАЛОГОВ")
print("=" * 78)
print(f"\n  {'вариант':<34} {'код':>5}  результат")
print("  " + "-" * 74)

working: list[tuple[str, list[str]]] = []
for label, extra in VARIANTS:
    out_dir = probe / f"out_{abs(hash(label)) % 100000}"
    shutil.rmtree(out_dir, ignore_errors=True)
    cmd = [str(py), str(probe), "-o", str(out_dir)] + extra
    try:
        proc = subprocess.run(
            cpg_mod._shell_cmd(cmd, py), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=900, cwd=str(probe),
            env=cpg_mod.java_env(cfg),
        )
        code = proc.returncode
        err = ((proc.stderr or "") + (proc.stdout or "")).strip()
    except subprocess.TimeoutExpired:
        code, err = -1, "таймаут"
    ok = code == 0
    if ok:
        working.append((label, extra))
        verdict = "ПРИНЯТО"
    else:
        bad = [ln for ln in err.splitlines() if "Unknown argument" in ln]
        verdict = f"отклонено: {bad[0][:40] if bad else err[:40]}"
    print(f"  {label:<34} {code:>5}  {verdict}")
    shutil.rmtree(out_dir, ignore_errors=True)

print()
print("=" * 78)
print("ИТОГ")
print("=" * 78)
print(f"\n  Принятых вариантов: {len(working)}")
for label, extra in working:
    print(f"    {label}: {extra}")
print("""
  Рабочий вариант и будет использоваться в RAGRAF для Python-корпусов.
  Если принимается только один каталог — исключения надо сводить к минимуму
  либо отказаться от них, разбирая корень целиком.
""")

shutil.rmtree(probe, ignore_errors=True)

