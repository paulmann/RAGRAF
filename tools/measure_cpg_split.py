"""Замер: из чего состоит стадия графа свойств кода (63% времени на пилоте).

Стадия состоит из четырёх частей с разной природой:
  1. запуск Joern (joern-parse) — Java, разбор PHP в граф;
  2. экспорт графа в файлы (joern-export) — Java, запись CSV;
  3. ЧТЕНИЕ И РАЗБОР этих файлов средствами Python;
  4. слияние рёбер с существующим графом (SQLite).

Части 1 и 2 — Java, видеокарта не поможет. Части 3 и 4 — обработка данных
в Python; часть 3 (разбор десятков мегабайт CSV) теоретически может ускориться
на видеокарте, но только если она реально доминирует.

Замер идёт по УЖЕ СОХРАНЁННОМУ экспорту: Joern заново не запускается,
базы не меняются.
"""

from __future__ import annotations

import json
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

cfg = load_config(REPO / "config" / "revoap.yaml")

print("=" * 76)
print("ИЗ ЧЕГО СОСТОИТ СТАДИЯ ГРАФА СВОЙСТВ КОДА")
print("=" * 76)

# Замеры стадий из покрытия: сколько занял весь CPG.
conn = store.connect(cfg.graph_db, readonly=True)
rev = conn.execute("SELECT rev_id FROM rev ORDER BY rev_id DESC LIMIT 1").fetchone()["rev_id"]
rows = conn.execute("SELECT metric, value, note FROM coverage WHERE rev_id = ?", (rev,)).fetchall()
cpg_seconds = 0.0
for r in rows:
    if str(r["metric"]) == "stage_cpg":
        cpg_seconds = float(r["value"] or 0)
    if str(r["metric"]) == "cpg_used":
        print(f"  граф свойств кода применён: {r['value']} ({r['note'] or ''})")
conn.close()
print(f"  вся стадия cpg: {cpg_seconds:.2f} с")

# Экспорт: где лежат файлы и сколько их.
cpg_dir = pathlib.Path(cfg.root) / ".ragraf" / "cpg"
exports = sorted(
    (p for p in cpg_dir.iterdir() if p.is_dir() and p.name.startswith("export_")),
    key=lambda p: p.stat().st_mtime,
) if cpg_dir.exists() else []
print(f"\n  каталогов экспорта: {len(exports)}")
if not exports:
    print("  экспорт не найден — замер невозможен")
    raise SystemExit(0)

latest = exports[-1]
files = sorted(p for p in latest.rglob("*") if p.is_file())
total_bytes = sum(p.stat().st_size for p in files)
print(f"  последний экспорт: {latest.name}")
print(f"  файлов: {len(files)}, объём: {total_bytes / 1024 / 1024:.1f} МБ")

# Сколько строк в каждом виде файлов.
by_kind: dict[str, tuple[int, int]] = {}
t0 = time.perf_counter()
lines_total = 0
for path in files:
    kind = path.name.split("_")[0].lower()
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            n = 0
            for _ in fh:
                n += 1
    except OSError:
        continue
    prev = by_kind.get(kind, (0, 0))
    by_kind[kind] = (prev[0] + 1, prev[1] + n)
    lines_total += n
scan_seconds = time.perf_counter() - t0

print(f"\n  ЧТЕНИЕ всех файлов построчно: {scan_seconds:.2f} с")
print(f"  строк всего: {lines_total}")
for kind, (n_files, n_lines) in sorted(by_kind.items(), key=lambda kv: -kv[1][1])[:8]:
    print(f"    {kind:16} файлов {n_files:>4}, строк {n_lines:>9}")

print("\n=== ЗАМЕР: разбор CSV в структуры (часть 3) ===")
t0 = time.perf_counter()
parsed = 0
for path in files:
    if not path.name.startswith("nodes") and "node" not in path.name.lower():
        continue
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parsed += json.loads(line) if line.strip().startswith("{") else 1
    except (OSError, ValueError):
        continue
parse_seconds = time.perf_counter() - t0
print(f"  разобрано записей: {parsed} за {parse_seconds:.2f} с")

print(f"\n  итого на чтение и разбор экспорта: {scan_seconds + parse_seconds:.2f} с")
print(f"  это {((scan_seconds + parse_seconds) / cpg_seconds * 100):.1f}% от всей стадии"
      if cpg_seconds else "")

print()
print("=" * 76)
print("ВЫВОД")
print("=" * 76)
print(f"""
  Вся стадия: {cpg_seconds:.1f} с. Из них:
    * работа Joern (Java: разбор и экспорт) — {cpg_seconds - scan_seconds - parse_seconds:.1f} с;
    * чтение и разбор файлов в Python — {scan_seconds + parse_seconds:.1f} с.

  Видеокарта не ускоряет работу Java-программы Joern. Чтение и разбор файлов —
  это ввод-вывод и последовательный разбор строк: перенос на видеокарту не даст
  выигрыша, потому что данные всё равно надо прочитать с диска, а вычислений
  в этой части почти нет.
""")
