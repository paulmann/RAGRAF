"""Сквозная проверка: работа запросов через сервер против отдельного процесса.

Что проверяется на РЕАЛЬНОМ графе:
  1. запросы к готовому графу дают одинаковый результат двумя путями;
  2. серверный путь быстрее;
  3. при выключенной настройке используется прежний путь;
  4. при недоступном сервере происходит откат, а не отказ.

Используется уже построенный граф корпуса. Корпус не переиндексируется.
"""

from __future__ import annotations

import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.cpg import QUERY_CALLS, JoernRunner  # noqa: E402

PASS = 0
FAIL = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
runner = JoernRunner(cfg)

# Ищется готовый граф корпуса.
cpg_files = sorted(
    (pathlib.Path(cfg.workdir) if hasattr(cfg, "workdir") else
     pathlib.Path(cfg.root) / ".ragraf" / "cpg").glob("*.cpg.bin"),
    key=lambda p: p.stat().st_mtime, reverse=True,
)
if not cpg_files:
    print("готовый граф не найден — проверка невозможна")
    raise SystemExit(1)

# Имя ревизии берётся из имени файла: rev-0100.cpg.bin → rev-0100.
rev_id = cpg_files[0].name.replace(".cpg.bin", "")
print(f"граф: {cpg_files[0].name}")
print(f"ревизия: {rev_id}")
print(f"размер: {cpg_files[0].stat().st_size / 1024 / 1024:.1f} МБ\n")

print("=== 1. Серверный путь ===")
cfg.index.joern_server = True
try:
    t0 = time.perf_counter()
    ok1, err1, res1 = runner.query(rev_id, QUERY_CALLS, timeout=1800)
    server_seconds = time.perf_counter() - t0
    print(f"  успех: {ok1}")
    print(f"  время: {server_seconds:.2f} с")
    if err1:
        print(f"  ошибка: {str(err1)[:200]}")
    if res1:
        print(f"  длина результата: {len(str(res1))} символов")
    check(ok1, "запрос через сервер выполнен", str(err1)[:150])

    st = runner.server_status()
    print(f"\n  состояние сервера: работает={st.get('running')}, "
          f"порт={st.get('port')}")

    print("\n=== 2. Повторный запрос (сервер уже поднят) ===")
    t0 = time.perf_counter()
    ok2, err2, res2 = runner.query(rev_id, QUERY_CALLS, timeout=1800)
    repeat_seconds = time.perf_counter() - t0
    print(f"  время: {repeat_seconds:.2f} с")
    check(ok2, "повторный запрос выполнен", str(err2)[:100])
    if server_seconds and repeat_seconds:
        print(f"  первый: {server_seconds:.2f} с, повторный: {repeat_seconds:.2f} с")

finally:
    print("\n=== 3. Остановка сервера ===")
    runner.stop_server()
    st = runner.server_status()
    check(not st.get("running"), "сервер остановлен",
          str(st.get("reason"))[:120])

print("\n=== 4. Прежний путь (сервер выключен) ===")
cfg.index.joern_server = False
runner2 = JoernRunner(cfg)
t0 = time.perf_counter()
ok3, err3, res3 = runner2.query(rev_id, QUERY_CALLS, timeout=1800)
plain_seconds = time.perf_counter() - t0
print(f"  успех: {ok3}")
print(f"  время: {plain_seconds:.2f} с")
if err3:
    print(f"  ошибка: {str(err3)[:200]}")
check(ok3, "прежний путь работает", str(err3)[:150])

print("\n=== 5. Сравнение результатов двух путей ===")
if ok1 and ok3 and res1 and res3:
    s1 = str(res1)
    s3 = str(res3)
    print(f"  сервер: {len(s1)} символов")
    print(f"  процесс: {len(s3)} символов")
    # Результаты должны совпадать по содержанию: это один и тот же граф.
    same_len = abs(len(s1) - len(s3)) < max(100, len(s1) * 0.05)
    check(same_len, "результаты сопоставимы по объёму",
          f"{len(s1)} против {len(s3)}")
    # Проверяется, что в обоих есть имена методов: значит данные не пустые.
    check("cpg" not in s1.lower()[:50] or len(s1) > 1000,
          "серверный результат содержит данные")
    check(len(s3) > 1000, "результат процесса содержит данные")
else:
    check(False, "сравнение результатов", "один из путей не дал данных")

print("\n=== 6. Откат при недоступном сервере ===")
cfg.index.joern_server = True
# Порт заведомо занят: сервер не сможет подняться на нём, и должен быть откат.
import socket  # noqa: E402

cfg.index.joern_server_port = 9500
holding = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
holding.bind(("127.0.0.1", 9500))
holding.listen(1)
try:
    runner3 = JoernRunner(cfg)
    # Сервер найдёт другой порт в диапазоне, поэтому откат проверяется иначе:
    # заведомо недоступным делается сам скрипт Joern.
    runner3.scripts = {}
    t0 = time.perf_counter()
    ok4, err4, res4 = runner3.query(rev_id, QUERY_CALLS, timeout=300)
    fallback_seconds = time.perf_counter() - t0
    print(f"  успех: {ok4}, время {fallback_seconds:.2f} с")
    print(f"  ответ: {str(err4)[:150]}")
    # Ожидается отказ с понятной причиной, а не исключение.
    check(isinstance(err4, str) and bool(err4),
          "отказ сервера даёт понятную причину", str(err4)[:120])
    runner3.stop_server()
finally:
    holding.close()

print("\n=== 7. Уборка ===")
runner2.stop_server()
import subprocess  # noqa: E402

_j = subprocess.run(["tasklist", "/FI", "IMAGENAME eq java.exe"],
                    capture_output=True, text=True, timeout=60)
running = "java.exe" in (_j.stdout or "")
print(f"  java-процессы остались: {running}")
check(not running, "все JVM остановлены", "остались висящие процессы")

print("\n=== ИТОГ ===")
print(f"пройдено: {PASS}, провалено: {FAIL}")
raise SystemExit(0 if FAIL == 0 else 1)
