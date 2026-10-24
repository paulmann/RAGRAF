"""Проверка: помогает ли смена --repr при экспорте Python-графа.

Установлено: экспорт падает во ВСЕХ форматах (neo4jcsv, graphml, dot)
с одной ошибкой в `MethodRef.referencedMethod` — значит, дело не в формате.

RAGRAF вызывает экспорт с `--repr=all`: это выгрузка всех представлений
(ast, cfg, ddg, pdg и прочие). На Python-графе обход `MethodRef` внутри
одного из представлений падает из-за неразрешённой ссылки.

Проверяется: работает ли экспорт с более простым `--repr=cpg` — то есть
без обхода всех представлений. Если да — для Python надо использовать его.

Ничего не меняет: только пробует и печатает результат.
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
export_script = scripts.get("joern-export")

cpg_files = sorted(runner.workdir.glob("rev-*.cpg.bin"),
                   key=lambda p: -p.stat().st_size)
if not cpg_files:
    print("  готового CPG нет")
    raise SystemExit(1)
cpg_path = cpg_files[0]
print(f"  CPG: {cpg_path.name} ({cpg_path.stat().st_size / 1024 / 1024:.1f} МБ)")
print(f"  экспортёр: {export_script.name}")

print()
print("=" * 78)
print("ПРОБЫ: РАЗНЫЕ --repr И --format")
print("=" * 78)
print(f"\n  {'repr':<10} {'format':<12} {'код':>5} {'файлов':>7}  результат")
print("  " + "-" * 70)

results: dict[tuple[str, str], tuple[int, int]] = {}
# `all` — как вызывает RAGRAF; `cpg` — базовое представление без обходов.
for repr_name in ("all", "cpg", "ast", "cfg", "pdg"):
    for fmt in ("neo4jcsv", "dot"):
        out_dir = runner.workdir / f"probe_{repr_name}_{fmt}"
        shutil.rmtree(out_dir, ignore_errors=True)
        cmd = [str(export_script), str(cpg_path),
               f"--repr={repr_name}", f"--format={fmt}", "-o", str(out_dir)]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=900, cwd=str(runner.workdir),
                env=cpg_mod.java_env(cfg),
            )
            code = proc.returncode
        except subprocess.TimeoutExpired:
            code = -1
        files = [f for f in out_dir.rglob("*") if f.is_file()] \
            if out_dir.exists() else []
        results[(repr_name, fmt)] = (code, len(files))
        verdict = "РАБОТАЕТ" if code == 0 and files else "не удалось"
        print(f"  {repr_name:<10} {fmt:<12} {code:>5} {len(files):>7}  {verdict}")
        shutil.rmtree(out_dir, ignore_errors=True)

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
working = [k for k, v in results.items() if v[0] == 0 and v[1] > 0]
if working:
    print(f"\n  Работающие сочетания: {working}")
    print("""
  Если работает `--repr=cpg` (без обхода всех представлений),
  для Python-корпусов надо использовать именно его: полный `all`
  падает на неразрешённых MethodRef в одном из представлений,
  а базовое `cpg` содержит узлы и рёбра вызовов — то, что нужно RAGRAF.
    """)
else:
    print("""
  Ни одно сочетание не работает: ошибка в самом графе Python.
  Честный вывод — граф вызовов по Python построить не удаётся,
  RAGRAF работает по данным разбора деревом (calls, calls_heuristic),
  а операция slice недоступна.
    """)
