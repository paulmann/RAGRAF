"""Замер: сколько времени в построении графа свойств кода уходит на старт JVM.

Вопрос из предложения №2: если Joern запускается как новый процесс на каждый
вызов, платим ли мы за старт JVM отдельно от самой работы? Если да — выгоден
долгоживущий процесс. Если время уходит на разбор PHP, а не на старт, то
экономить нечего.

Что замеряется:
  1. время запуска JVM без работы (joern --version или пустой вызов);
  2. время построения CPG для корпуса целиком;
  3. время экспорта построенного CPG;
  4. сколько из этого занимает именно старт JVM.

Все замеры только читают существующие файлы и запускают справку Joern;
корпус не переиндексируется.
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

print("=" * 78)
print("ЗАМЕР ВРЕМЕНИ ЗАПУСКА JVM И РАБОТЫ JOERN")
print("=" * 78)

# Окружение: JAVA_HOME обязателен, иначе joern.bat не находит Java.
env = dict(os.environ)
env["JAVA_HOME"] = str(JDK)
env["PATH"] = f"{JDK / 'bin'}{os.pathsep}" + env.get("PATH", "")

scripts = {
    "joern": TOOLS / "joern.bat",
    "joern-parse": TOOLS / "joern-parse.bat",
    "joern-export": TOOLS / "joern-export.bat",
}
for name, path in scripts.items():
    print(f"  {name:<14} {'есть' if path.exists() else 'НЕТ':<6} {path.name}")

print()
print("=" * 78)
print("1. СТАРТ JVM БЕЗ РАБОТЫ")
print("=" * 78)
print("  Замеряется запуск java -version: это чистая цена старта JVM")
print("  без загрузки Joern, без frontend, без разбора кода.")

java = JDK / "bin" / "java.exe"
if java.exists():
    times = []
    for _ in range(5):
        t0 = time.perf_counter()
        subprocess.run([str(java), "-version"], capture_output=True, timeout=120)
        times.append(time.perf_counter() - t0)
    times.sort()
    jvm_start = times[len(times) // 2]
    print(f"\n  старт JVM (java -version): {jvm_start * 1000:.0f} мс "
          f"(медиана из 5)")
    print(f"    минимум: {times[0] * 1000:.0f} мс, максимум: {times[-1] * 1000:.0f} мс")
else:
    jvm_start = 0.0
    print(f"  java.exe не найден: {java}")

print()
print("=" * 78)
print("2. СТАРТ JOERN С ЗАГРУЗКОЙ ЕГО КЛАССОВ")
print("=" * 78)
print("  `joern --version` грузит классы Joern, но не разбирает код.")
print("  Разница со стартом JVM — это цена загрузки самого Joern.")

script = scripts["joern"]
if script.exists():
    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        try:
            subprocess.run(
                ["cmd.exe", "/c", str(script), "--version"],
                capture_output=True, text=True, timeout=600,
                env=env, cwd=str(TOOLS),
            )
        except Exception as exc:
            print(f"  вызов не удался: {type(exc).__name__}")
            break
        times.append(time.perf_counter() - t0)
    if times:
        times.sort()
        joern_start = times[len(times) // 2]
        print(f"\n  старт Joern (--version): {joern_start * 1000:.0f} мс")
        print(f"    из них старт JVM:       {jvm_start * 1000:.0f} мс")
        print(f"    загрузка классов Joern: "
              f"{max(0, joern_start - jvm_start) * 1000:.0f} мс")

print()
print("=" * 78)
print("3. РЕАЛЬНАЯ СТАДИЯ ПОСТРОЕНИЯ ГРАФА (из журнала индексации)")
print("=" * 78)
sys.path.insert(0, str(REPO / "src"))
from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

for name in ("z-batteryservice", "revoap"):
    cfg = load_config(REPO / "config" / f"{name}.yaml")
    conn = store.connect(cfg.graph_db, readonly=True)
    rev = conn.execute(
        "SELECT rev_id FROM rev ORDER BY rev_id DESC LIMIT 1"
    ).fetchone()["rev_id"]
    rows = conn.execute(
        "SELECT metric, value, note FROM coverage WHERE rev_id = ?", (rev,)
    ).fetchall()
    stage_cpg = 0.0
    files = 0
    for r in rows:
        m = str(r["metric"])
        if m == "stage_cpg":
            stage_cpg = float(r["value"] or 0)
        if m == "files_total":
            files = int(r["value"] or 0)
    print(f"  {name}: файлов {files}, стадия графа свойств кода "
          f"в последней ревизии: {stage_cpg:.2f} с")
    conn.close()

print()
print("=" * 78)
print("ЧТО ЭТО ЗНАЧИТ")
print("=" * 78)
print("""
  Ключевое сравнение: старт JVM (десятки-сотни миллисекунд) против всей стадии
  построения графа (десятки секунд).

  Если старт занимает малую долю стадии, то долгоживущий процесс сэкономит
  именно эту малую долю, а не «всё время JVM». Основное время уходит на разбор
  PHP и построение модели — это работа, которая выполняется независимо от того,
  новый процесс или старый.

  Точная оценка выигрыша — в выводе выше.
""")
