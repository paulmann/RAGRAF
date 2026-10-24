"""Проверка: вызывается ли обходной путь получения вызовов.

В логе переиндексации нет примечания об обходе, хотя я его добавил.
Значит либо обход не вызывается, либо падает раньше, чем пишет примечание.

Проверяется прямым вызовом: берётся готовый CPG и вызывается
`runner.query` с запросом вызовов — тот же путь, что использует обход.

Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf import cpg as cpg_mod  # noqa: E402

cfg = load_config(REPO / "config" / "sagaai.yaml")
runner = cpg_mod.JoernRunner(cfg)

files = sorted(runner.workdir.glob("rev-*.cpg.bin"),
               key=lambda p: -p.stat().st_size)
print(f"  файлов CPG: {len(files)}")
for f in files[:4]:
    print(f"    {f.name} ({f.stat().st_size / 1024 / 1024:.1f} МБ)")

if not files:
    print("  CPG нет")
    raise SystemExit(1)
cpg_path = files[0]
rev_id = cpg_path.stem.replace(".cpg", "")
print(f"\n  проверяется: {cpg_path.name}, ревизия {rev_id}")

print()
print("=" * 78)
print("ВЫЗОВ runner.query (тот же путь, что у обхода)")
print("=" * 78)
started = time.perf_counter()
try:
    ok, note, rows = runner.query(rev_id, cpg_mod.QUERY_CALLS, timeout=1800)
    seconds = time.perf_counter() - started
    print(f"  ok={ok}, секунд {seconds:.1f}")
    print(f"  записей: {len(rows) if isinstance(rows, list) else rows}")
    if note:
        print(f"  примечание: {str(note)[:200]}")
    if isinstance(rows, list) and rows:
        print(f"\n  пример записи:")
        for k, v in list(rows[0].items())[:8]:
            print(f"    {k} = {str(v)[:60]}")
except Exception as exc:
    print(f"  ИСКЛЮЧЕНИЕ: {type(exc).__name__}: {exc}")

print()
print("=" * 78)
print("ПРОВЕРКА: КАКОЙ ФАЙЛ БЕРЁТ ОБХОД")
print("=" * 78)
# Обход вызывает runner.query(rev_id, ...) — проверяется, что query
# находит файл по имени ревизии, а не по самому свежему.
print(f"  ожидаемое имя файла: {runner.workdir / (rev_id + '.cpg.bin')}")
exists = (runner.workdir / f"{rev_id}.cpg.bin").exists()
print(f"  файл существует: {exists}")

# Список всех ревизий, которые видит query.
revs = sorted(p.stem.replace(".cpg", "")
              for p in runner.workdir.glob("rev-*.cpg.bin"))
print(f"  ревизии с файлами: {revs}")
