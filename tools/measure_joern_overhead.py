"""Замер: сколько именно накладных расходов даёт запуск Joern на каждую индексацию.

Установлено: чистый старт JVM — 64 мс, но загрузка классов Joern — 5 531 мс.
То есть почти всё время старта уходит не на JVM, а на сам Joern.

Индексация запускает Joern ДВАЖДЫ:
  1. построение графа (joern-parse) — разбор PHP;
  2. экспорт графа (joern-export) — выгрузка в файлы.

Если каждый запуск платит 5,5 с за загрузку классов, то накладные расходы
составляют около 11 с на индексацию. Это и есть та доля, которую уберёт
долгоживущий процесс.

Проверяется замером:
  1. сколько занимает экспорт существующего графа (второй запуск);
  2. сколько времени уходит на саму работу против накладных;
  3. какова доля накладных в полной индексации.

Используется УЖЕ ПОСТРОЕННЫЙ граф свойств кода: он лежит в каталоге .ragraf/cpg.
Корпус не переиндексируется.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
TOOLS = REPO / ".tools" / "joern-cli"
JDK = REPO / ".tools" / "jdk"

env = dict(os.environ)
env["JAVA_HOME"] = str(JDK)
env["PATH"] = f"{JDK / 'bin'}{os.pathsep}" + env.get("PATH", "")

print("=" * 78)
print("НАКЛАДНЫЕ РАСХОДЫ ЗАПУСКА JOERN НА ИНДЕКСАЦИЮ")
print("=" * 78)

# Ищется построенный граф корпуса.
cpg_dir = None
for name, root in (("z-batteryservice", str(pathlib.Path(__file__).resolve().parents[1].parent / "z.batteryservice.ru")),
                   ("revoap", str(pathlib.Path(__file__).resolve().parents[1].parent / "revoap.com"))):
    candidate = pathlib.Path(root) / ".ragraf" / "cpg"
    if candidate.exists():
        # ВАЖНО про расширение: граф лежит как `<rev>.cpg.bin`, а не `<rev>.cpg`.
        # Первая версия скрипта искала *.cpg и ничего не находила.
        files = [p for p in candidate.glob("*.cpg.bin") if p.is_file()]
        if files:
            cpg_dir = candidate
            print(f"  найден построенный граф: {candidate}")
            newest = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:3]
            for f in newest:
                print(f"    {f.name}: {f.stat().st_size / 1024 / 1024:.1f} МБ")
            break

if cpg_dir is None:
    print("  построенный граф не найден — замер экспорта невозможен")
    cpg_file = None
else:
    # Берется самый свежий граф: он соответствует последней ревизии.
    candidates = sorted(
        cpg_dir.glob("*.cpg.bin"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    cpg_file = candidates[0] if candidates else None

print()
print("=" * 78)
print("1. ЭКСПОРТ СУЩЕСТВУЮЩЕГО ГРАФА (второй запуск Joern)")
print("=" * 78)

if cpg_file is not None:
    out_dir = REPO / ".staging" / "cpg_export_probe"
    if out_dir.exists():
        import shutil

        shutil.rmtree(out_dir, ignore_errors=True)

    script = TOOLS / "joern-export.bat"
    cmd = [
        "cmd.exe", "/c", str(script),
        "-J-Xmx8g",
        str(cpg_file),
        "--repr=all",
        "--format=neo4jcsv",
        "-o", str(out_dir),
    ]
    print(f"  граф: {cpg_file.name} ({cpg_file.stat().st_size / 1024 / 1024:.1f} МБ)")
    print("  запускается экспорт (это второй запуск Joern за индексацию)...")
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=3600, env=env, cwd=str(TOOLS),
        )
        export_seconds = time.perf_counter() - t0
        files = [p for p in out_dir.rglob("*") if p.is_file()]
        total_mb = sum(p.stat().st_size for p in files) / 1024 / 1024
        print(f"\n  экспорт завершён: код {proc.returncode}")
        print(f"  время: {export_seconds:.2f} с")
        print(f"  файлов: {len(files)}, объём: {total_mb:.1f} МБ")
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-300:]
            print(f"  вывод: {tail}")
    except subprocess.TimeoutExpired:
        print("  превышено время ожидания")
        export_seconds = 0.0
    except Exception as exc:
        print(f"  ошибка: {type(exc).__name__}: {exc}")
        export_seconds = 0.0
else:
    export_seconds = 0.0

print()
print("=" * 78)
print("2. СВОДКА: ГДЕ ВРЕМЯ В СТАДИИ ГРАФА")
print("=" * 78)

# Замеры из предыдущего скрипта.
jvm_ms = 64
joern_load_ms = 5531
startup_s = joern_load_ms / 1000

print(f"  старт JVM (без Joern):            {jvm_ms} мс")
print(f"  загрузка классов Joern:           {joern_load_ms} мс  ← основная часть")
print(f"  полный старт одного процесса:     {startup_s:.2f} с")
print(f"  запусков Joern на индексацию:     2 (построение + экспорт)")
print(f"  накладные на индексацию:          {startup_s * 2:.2f} с")
if export_seconds:
    print(f"\n  экспорт существующего графа:      {export_seconds:.2f} с")
    print(f"    из них накладные на старт:      {startup_s:.2f} с "
          f"({startup_s / export_seconds * 100:.0f}%)")
    print(f"    полезная работа:                {export_seconds - startup_s:.2f} с")

# Полная стадия из истории прогонов.
sys.path.insert(0, str(REPO / "src"))
from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

print("\n  полная стадия графа в истории индексации:")
for name, root in (("z-batteryservice", str(pathlib.Path(__file__).resolve().parents[1].parent / "z.batteryservice.ru")),
                   ("revoap", str(pathlib.Path(__file__).resolve().parents[1].parent / "revoap.com"))):
    cfg = load_config(REPO / "config" / f"{name}.yaml")
    conn = store.connect(cfg.graph_db, readonly=True)
    rows = conn.execute(
        "SELECT rev_id, joern_used FROM rev WHERE joern_used = 1"
        " ORDER BY rev_id DESC LIMIT 3"
    ).fetchall()
    for r in rows:
        rev = str(r["rev_id"])
        cov = conn.execute(
            "SELECT metric, value FROM coverage WHERE rev_id = ? AND metric = 'stage_cpg'",
            (rev,),
        ).fetchone()
        if cov:
            value = float(cov["value"] or 0)
            share = startup_s * 2 / value * 100 if value else 0
            print(f"    {name} {rev}: {value:.2f} с, "
                  f"накладные на старт {startup_s * 2:.1f} с = {share:.0f}%")
    conn.close()

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print(f"""
  Долгоживущий процесс Joern убирает НЕ старт JVM (64 мс), а загрузку классов
  самого Joern — {startup_s:.2f} с на запуск. Так как индексация запускает
  Joern дважды, экономия составляет около {startup_s * 2:.1f} с на индексацию.

  Сравнение с полной стадией:
    * если стадия занимает 63 с — экономия около 17%;
    * если стадия занимает 16 с — экономия около 68%, потому что накладные
      не зависят от объёма работы.

  ВАЖНО: сама работа (разбор PHP и построение модели) переносится в старый
  процесс без изменений. Значит основной выигрыш — на МАЛЫХ корпусах и при
  частых индексациях, где накладные составляют большую долю.
""")

# Уборка пробного экспорта.
probe = REPO / ".staging" / "cpg_export_probe"
if probe.exists():
    import shutil

    shutil.rmtree(probe, ignore_errors=True)
    print("  пробный экспорт удалён")
