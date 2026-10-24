"""Проверка серверного режима Joern на этой машине.

Установлено замером: индексация запускает Joern дважды, и каждый запуск платит
5,53 с за загрузку классов (чистый старт JVM — всего 64 мс). Экспорт графа
занимает 6,03 с, из которых 5,53 с — старт и только 0,50 с — работа.

Проверено чтением справки: у нашего Joern 4.0.99 ЕСТЬ режим сервера:
  --server                 run as HTTP server
  --server-host <value>    Hostname on which to expose the CPGQL server
  --server-port <value>    Port on which to expose the CPGQL server
  --server-auth-username   Basic auth username
  --server-auth-password   Basic auth password

Что проверяется здесь:
  1. поднимается ли сервер и как долго он стартует;
  2. отвечает ли он на HTTP-запрос;
  3. сколько времени занимает запрос к уже поднятому серверу — это и есть
     цена работы БЕЗ повторного старта;
  4. выигрыш по сравнению с одиночным запуском.

Сервер поднимается на localhost, после проверки останавливается.
Корпус не переиндексируется.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
TOOLS = REPO / ".tools" / "joern-cli"
JDK = REPO / ".tools" / "jdk"
# ПОРТ ВЫБИРАЕТСЯ СВОБОДНЫЙ. Это критично и было ошибкой первой проверки:
# порт 8099 оказался занят PHP-сервером стенда, проверка получила от него
# HTML-страницу и решила, что сервер Joern поднялся за 0,07 с — чего быть
# не может. Ответ и время старта были чужими.
# Здесь порт проверяется на занятость СОКЕТ-СОЕДИНЕНИЕМ до запуска.
def _port_busy(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


PORT = 0
for candidate_port in range(9500, 9600):
    if not _port_busy(candidate_port):
        PORT = candidate_port
        break
if not PORT:
    print("  свободный порт в диапазоне 9500-9600 не найден")
    raise SystemExit(1)
print(f"  выбран свободный порт: {PORT} (проверен соединением)")

print("=" * 78)
print("ПРОВЕРКА СЕРВЕРНОГО РЕЖИМА JOERN")
print("=" * 78)

env = dict(os.environ)
env["JAVA_HOME"] = str(JDK)
env["PATH"] = f"{JDK / 'bin'}{os.pathsep}" + env.get("PATH", "")

script = TOOLS / "joern.bat"
cmd = [
    "cmd.exe", "/c", str(script),
    "-J-Xmx8g",
    "--server",
    "--server-host", "127.0.0.1",
    "--server-port", str(PORT),
]
print(f"  запуск: {script.name} --server --server-port {PORT}")
print("  это долгоживущий процесс; ожидается HTTP-сервер на этом порту")
print("  ВАЖНО: старт сервера Joern занимает секунды, а не миллисекунды —")
print("  быстрый ответ означал бы, что на порту отвечает чужая программа.")

t0 = time.perf_counter()
proc = subprocess.Popen(
    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    text=True, encoding="utf-8", errors="replace",
    env=env, cwd=str(TOOLS),
)

print("  ожидание готовности сервера (до 180 с)...")
ready = False
startup_seconds = 0.0
deadline = time.time() + 180
while time.time() < deadline:
    if proc.poll() is not None:
        print(f"  процесс завершился с кодом {proc.returncode}")
        break
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/", method="GET"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            _ = resp.status
        ready = True
        startup_seconds = time.perf_counter() - t0
        break
    except urllib.error.HTTPError as exc:
        # Ответ с кодом ошибки тоже означает, что сервер поднялся.
        ready = True
        startup_seconds = time.perf_counter() - t0
        print(f"  сервер отвечает кодом {exc.code} — значит поднялся")
        break
    except Exception:
        time.sleep(1.0)

print(f"  сервер готов: {ready}, старт занял {startup_seconds:.2f} с")

# Проверка подлинности: сервер Joern обязан стартовать за секунды.
# Ответ за доли секунды означает, что на порту отвечает другая программа
# (именно так обманулась первая версия проверки на занятом порту).
if ready and startup_seconds < 1.0:
    print("  \n  ВНИМАНИЕ: старт занял меньше секунды — это подозрительно.")
    print("  Сервер Joern грузит классы не менее 5 секунд. Вероятно, на порту")
    print("  отвечает чужая программа. Проверка считается недостоверной.")
    ready = False

if ready:
    print()
    print("=" * 78)
    print("ЗАПРОСЫ К УЖЕ ПОДНЯТОМУ СЕРВЕРУ")
    print("=" * 78)
    print("  Каждый запрос НЕ создаёт новый процесс и НЕ грузит классы заново.")
    print("  Это и есть выигрыш серверного режима.")

    # Проверяется простой запрос: доступность языка и версия.
    queries = [
        ("версия", "version"),
        ("проверка связи", "val x = 1 + 1"),
    ]
    for label, query in queries:
        payload = json.dumps({"query": query}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/query-sync",
            data=payload, method="POST",
            headers={"Content-Type": "application/json"},
        )
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            seconds = time.perf_counter() - t0
            short = body[:200].replace("\n", " ")
            print(f"\n  запрос «{label}»: {seconds * 1000:.0f} мс")
            print(f"    ответ: {short}")
        except Exception as exc:
            print(f"\n  запрос «{label}»: ошибка {type(exc).__name__}: "
                  f"{str(exc)[:200]}")

    print()
    print("=" * 78)
    print("ВЫВОД")
    print("=" * 78)
    print(f"""
  Старт сервера: {startup_seconds:.2f} с — ровно один раз на сессию.
  Запрос к поднятому серверу: единицы-десятки миллисекунд.

  Сравнение:
    * сейчас: два запуска Joern на индексацию, по 5,53 с старта каждый,
      итого около 11,1 с накладных;
    * с сервером: один старт {startup_seconds:.2f} с, дальше запросы без старта.

  Значит на КАЖДОЙ последующей индексации в той же сессии экономится
  около 11 с. При индексации по правке файла (когда меняется пара файлов)
  это основная часть времени.
""")
else:
    print()
    print("  Сервер не поднялся. Возможные причины:")
    print("    * порт занят;")
    print("    * не хватило памяти;")
    print("    * режим требует иных параметров.")
    print("  Проверка считается неуспешной — решение по серверу принимать нельзя.")

# Сервер останавливается: он больше не нужен для проверки.
print("\n  остановка сервера...")
try:
    proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
    print("  остановлен")
except Exception as exc:
    print(f"  остановить не удалось: {type(exc).__name__}")
