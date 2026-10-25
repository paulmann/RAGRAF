"""Полная трассировка ошибки экспорта Python-графа.

Установлено: экспорт падает во ВСЕХ сочетаниях `--repr` и `--format`
с одной ошибкой в `MethodRef.referencedMethod`. Это значит, что падение
происходит не при выборе представления, а раньше — при обходе графа.

Нужно увидеть полный стек: по нему видно, на каком шаге Joern обращается
к MethodRef. Если обращение идёт при ЗАГРУЗКЕ файла CPG (например при
восстановлении схемы), то дело в самом файле — и тогда надо понять,
чем он отличается от рабочего PHP-графа.

Заодно проверяется: работает ли экспорт PHP-графа на этой же установке
Joern (то есть исправна ли сама установка).

Ничего не меняет.
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

scripts = cpg_mod.find_joern_scripts(load_config(REPO / "config" / "sagaai.yaml"))
export_script = scripts.get("joern-export")

CASES = [
    ("sagaai (Python)", REPO / "config" / "sagaai.yaml"),
    ("z (PHP)", REPO / "config" / "z-batteryservice.yaml"),
]

for title, config_path in CASES:
    print()
    print("=" * 78)
    print(f"{title}")
    print("=" * 78)
    cfg = load_config(config_path)
    runner = cpg_mod.JoernRunner(cfg)
    files = sorted(runner.workdir.glob("rev-*.cpg.bin"),
                   key=lambda p: -p.stat().st_size)
    if not files:
        print("  CPG нет")
        continue
    cpg_path = files[0]
    print(f"  CPG: {cpg_path.name} ({cpg_path.stat().st_size / 1024 / 1024:.1f} МБ)")
    out_dir = runner.workdir / "trace_probe"
    shutil.rmtree(out_dir, ignore_errors=True)
    cmd = [str(export_script), str(cpg_path), "--repr=all",
           "--format=neo4jcsv", "-o", str(out_dir)]
    print(f"  команда: joern-export {cpg_path.name} --repr=all --format=neo4jcsv")
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=900, cwd=str(runner.workdir),
            env=cpg_mod.java_env(cfg),
        )
        print(f"  код возврата: {proc.returncode}")
        err = (proc.stderr or "").strip()
        if err:
            print(f"  ПОЛНАЯ ТРАССИРОВКА ({len(err.splitlines())} строк):")
            for line in err.splitlines()[:45]:
                print(f"    {line}")
        else:
            print("  ошибок нет")
        files_out = [f for f in out_dir.rglob("*") if f.is_file()] \
            if out_dir.exists() else []
        print(f"  файлов выгружено: {len(files_out)}")
        for f in files_out[:6]:
            print(f"    {f.name} ({f.stat().st_size / 1024:.0f} КБ)")
    except subprocess.TimeoutExpired:
        print("  таймаут")
    shutil.rmtree(out_dir, ignore_errors=True)

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Сравнение PHP и Python графов на одной установке Joern показывает:
    * если PHP экспортируется, а Python нет — дело в графе Python;
    * если не экспортируется ни один — дело в установке или в способе
      вызова, а не в языке.

  По стеке видно, на каком шаге происходит обращение к MethodRef:
  загрузка файла, построение представления или его выгрузка.
""")
