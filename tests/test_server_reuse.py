"""Решающий замер: даёт ли сервер выигрыш при ПОВТОРНЫХ прогонах.

Установлено: при ОДНОМ прогоне серверный путь медленнее на 3,9 с, потому что
в стадию входит старт сервера (6,5 с). Значит вопрос в переиспользовании:
окупается ли он, когда в одной сессии делается несколько прогонов.

Сравниваются два способа:
  * ПРЕЖНИЙ: каждый прогон = два новых процесса Joern (по 5,53 с загрузки каждый);
  * СЕРВЕР: сервер поднимается ОДИН раз, дальше прогоны идут без старта.

Замеряются три прогона подряд каждым способом. Результат покажет точку,
с которой сервер начинает выигрывать.
"""

from __future__ import annotations

import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.cpg import JoernRunner  # noqa: E402

cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
REV = "rev-0104"

print("=" * 78)
print("ЗАМЕР: ТРИ ПРОГОНА ПОДРЯД КАЖДЫМ СПОСОБОМ")
print("=" * 78)

print("\n--- Способ 1: прежний (два процесса на прогон) ---")
cfg.index.joern_server = False
plain_times: list[float] = []
for i in range(3):
    runner = JoernRunner(cfg)
    t0 = time.perf_counter()
    build = runner.build(f"{REV}_p{i}")
    exported = runner.export(f"{REV}_p{i}", fmt="neo4jcsv") if build.ok else None
    seconds = time.perf_counter() - t0
    plain_times.append(seconds)
    ok = build.ok and (exported.ok if exported else False)
    print(f"  прогон {i + 1}: {seconds:.2f} с (успех={ok})")

print("\n--- Способ 2: сервер (поднимается ОДИН раз) ---")
cfg.index.joern_server = True
runner = JoernRunner(cfg)
server_times: list[float] = []
for i in range(3):
    t0 = time.perf_counter()
    build = runner.build_via_server(f"{REV}_s{i}")
    if build.ok:
        ok_calls, rows, note = runner.calls_via_server(f"{REV}_s{i}")
        seconds = time.perf_counter() - t0
        print(f"  прогон {i + 1}: {seconds:.2f} с "
              f"(успех={ok_calls}, записей={len(rows)})")
    else:
        seconds = time.perf_counter() - t0
        print(f"  прогон {i + 1}: {seconds:.2f} с (ошибка: {build.note[:80]})")
    server_times.append(seconds)

# Сервер останавливается.
runner.stop_server()
time.sleep(2)

print()
print("=" * 78)
print("РЕЗУЛЬТАТ")
print("=" * 78)
print(f"  {'прогон':>8} {'прежний, с':>12} {'сервер, с':>11} {'разница':>10}")
print("  " + "-" * 46)
total_plain = 0.0
total_server = 0.0
for i, (p, s) in enumerate(zip(plain_times, server_times), start=1):
    total_plain += p
    total_server += s
    mark = " ← сервер быстрее" if s < p else ""
    print(f"  {i:>8} {p:>12.2f} {s:>11.2f} {s - p:>+10.2f}{mark}")
print("  " + "-" * 46)
print(f"  {'ИТОГО':>8} {total_plain:>12.2f} {total_server:>11.2f} "
      f"{total_server - total_plain:>+10.2f}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
first_gain = plain_times[0] - server_times[0]
later_gain = (sum(plain_times[1:]) / 2) - (sum(server_times[1:]) / 2)
print(f"""
  Первый прогон: сервер {'быстрее' if first_gain > 0 else 'медленнее'} на
  {abs(first_gain):.2f} с — в него входит старт сервера.

  Последующие прогоны: сервер {'быстрее' if later_gain > 0 else 'медленнее'} на
  {abs(later_gain):.2f} с — старт уже оплачен.

  Полный итог за три прогона: серверный способ
  {'экономит' if total_server < total_plain else 'теряет'} 
  {abs(total_plain - total_server):.2f} с.
""")
if total_server < total_plain:
    print("  Значит серверный путь окупается, когда прогонов несколько.")
    print("  Практически это означает: держать сервер живым между прогонами.")
elif abs(total_plain - total_server) < 3:
    print("  Разница в пределах погрешности даже за три прогона.")
    print("  Причина: построение через importCode делает больше проходов,")
    print("  чем joern-parse, и это съедает экономию на старте.")
else:
    print("  Серверный путь проигрывает и на нескольких прогонах.")
    print("  Значит его нельзя включать по умолчанию: это регрессия.")
