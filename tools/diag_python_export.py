"""Диагностика: почему экспорт CPG падает на Python-графе.

Установлено:
  * `pysrc2cpg` найден и CPG для sagaai СТРОИТСЯ (шаг cpg прошёл);
  * падает ЭКСПОРТ: `joern-export` возвращает код 1 и ошибку
    `java.util.NoSuchElementException: next on empty iterator`
    в `AccessNeighborsForMethodRef$.referencedMethod` (файл MethodRef.scala).

Это ошибка самого Joern при обходе связей MethodRef: у какого-то узла
нет ожидаемой ссылки на метод. Такое бывает на графах, где часть
MethodRef не разрешена.

Задача: понять, можно ли обойти это — например, экспортировать другой
формат (не neo4jcsv), или задать другой тип экспорта.

Проверяется вызовом: собирается CPG и пробуются разные режимы экспорта.
Ничего в проектах не меняется.
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

print("=" * 78)
print("1. СОСТОЯНИЕ: ЕСТЬ ЛИ ГОТОВЫЙ CPG")
print("=" * 78)
workdir = runner.workdir
print(f"  рабочий каталог: {workdir}")
cpg_files = sorted(workdir.glob("*.cpg.bin"))
print(f"  файлов CPG: {len(cpg_files)}")
for f in cpg_files:
    print(f"    {f.name} ({f.stat().st_size / 1024 / 1024:.1f} МБ)")

print()
print("=" * 78)
print("2. ЧТО УМЕЕТ joern-export (справка)")
print("=" * 78)
scripts = cpg_mod.find_joern_scripts(cfg)
export_script = scripts.get("joern-export")
print(f"  скрипт: {export_script}")
if export_script:
    proc = subprocess.run(
        [str(export_script), "--help"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=180, cwd=str(runner.workdir),
        env=cpg_mod.java_env(cfg),
    )
    text = (proc.stdout or "") + (proc.stderr or "")
    for line in text.splitlines()[:30]:
        print(f"    {line}")

print()
print("=" * 78)
print("3. КАК RAGRAF ВЫЗЫВАЕТ ЭКСПОРТ")
print("=" * 78)
src = (REPO / "src" / "ragraf" / "cpg.py").read_text(encoding="utf-8")
# Показывается блок формирования команды экспорта.
idx = src.find("def export(")
block = src[idx:idx + 2600]
for i, line in enumerate(block.splitlines(), 1):
    if any(k in line for k in ("cmd", "--", "fmt", "neo4j", "format",
                               "export_script", "script")):
        print(f"    {line.strip()[:90]}")

print()
print("=" * 78)
print("4. ПРОБА: ЭКСПОРТ В РАЗНЫХ ФОРМАТАХ")
print("=" * 78)
if not cpg_files:
    print("  готового CPG нет — экспорт пробовать не на чем")
else:
    cpg_path = max(cpg_files, key=lambda p: p.stat().st_size)
    print(f"  используется: {cpg_path.name}")
    out_base = runner.workdir / "export_probe"
    # Пробуются форматы, которые поддерживает Joern 4.
    for fmt in ("neo4jcsv", "graphml", "dot", "sarif"):
        out_dir = out_base / fmt
        if out_dir.exists():
            import shutil

            shutil.rmtree(out_dir, ignore_errors=True)
        cmd = [str(export_script), str(cpg_path), "--format", fmt,
               "-o", str(out_dir)]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=600, cwd=str(runner.workdir),
                env=cpg_mod.java_env(cfg),
            )
        except subprocess.TimeoutExpired:
            print(f"\n  {fmt}: таймаут")
            continue
        files = list(out_dir.rglob("*")) if out_dir.exists() else []
        file_count = len([f for f in files if f.is_file()])
        print(f"\n  {fmt}: код {proc.returncode}, файлов {file_count}")
        err = (proc.stderr or "").strip().splitlines()
        if err:
            for line in err[-3:]:
                print(f"    {line[:110]}")

    # Уборка пробных выгрузок.
    import shutil  # noqa: E402

    if out_base.exists():
        shutil.rmtree(out_base, ignore_errors=True)

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Если экспорт в neo4jcsv падает, а другой формат работает — можно
  переключить RAGRAF на рабочий формат для Python-корпусов.

  Если падают все форматы — ошибка в самом графе (неразрешённые MethodRef),
  и тогда честный вывод: граф вызовов по Python в этом проекте не строится,
  RAGRAF работает по данным разбора деревом.
""")
