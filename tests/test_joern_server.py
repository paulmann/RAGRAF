"""Проверка клиента сервера Joern.

Проверяется, что клиент действительно устраняет накладные расходы:
  1. сервер поднимается и клиент это видит;
  2. запросы выполняются без запуска JVM;
  3. повторные запросы быстрые (накладные исчезают);
  4. ошибки запросов отличаются от отказа сервера;
  5. остановка сервера работает и порт освобождается.

Отдельно проверяется, что клиент не спутает свой сервер с чужой программой
на порту: ранее проверка получила ответ PHP-сервера стенда и приняла его
за Joern.
"""

from __future__ import annotations

import pathlib
import socket
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.joern_server import (  # noqa: E402
    JoernServer, JoernServerUnavailable, _pick_port, _port_free,
)

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


TOOLS = REPO / ".tools" / "joern-cli"
JDK = REPO / ".tools" / "jdk"

print("=== 1. Выбор свободного порта ===")
# Проверяется, что занятый порт не выбирается: именно на этом обожглась
# первая версия проверки.
free_port = _pick_port(preferred=9600, span=20)
print(f"  выбран порт: {free_port}")
check(_port_free(free_port), "выбранный порт свободен")

# Занятый порт: создаётся слушающий сокет и проверяется, что порт не выбран.
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
    busy.bind(("127.0.0.1", 9610))
    busy.listen(1)
    check(not _port_free(9610), "занятый порт определяется как занятый")
    chosen = _pick_port(preferred=9610, span=5)
    check(chosen != 9610, "клиент обходит занятый порт", f"выбран {chosen}")
    # ВАЖНО: диапазон 9610..9615 может быть занят другими программами, поэтому
    # проверяется именно то, что выбранный порт СВОБОДЕН, а не то, что он равен 9611.
    check(_port_free(chosen), "выбранный порт действительно свободен")

print("\n=== 2. Подъём сервера и первый запрос ===")
server = JoernServer(
    joern_script=TOOLS / "joern.bat",
    workdir=TOOLS,
    java_home=JDK,
    heap="6g",
    preferred_port=9620,
)
try:
    t0 = time.perf_counter()
    server.start()
    startup = time.perf_counter() - t0
    st = server.status
    print(f"  сервер поднят за {startup:.2f} с (PID {st.pid}, порт {st.port})")
    check(st.running, "сервер работает", st.reason)
    # Подлинность: сервер Joern грузит классы не менее 5 с. Быстрее — чужая программа.
    check(startup > 1.0, "старт занял реальное время (не чужая программа)",
          f"получено {startup:.2f} с")

    print("\n=== 3. Запросы без запуска JVM ===")
    t0 = time.perf_counter()
    out = server.query("val a = 1 + 1")
    first_ms = (time.perf_counter() - t0) * 1000
    print(f"  первый запрос: {first_ms:.0f} мс")
    print(f"    ответ: {out.strip()[:120]}")
    check("2" in out, "запрос выполнен верно", out[:100])

    times = []
    for _ in range(5):
        t0 = time.perf_counter()
        server.query("val b = 2 * 3")
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    median = times[len(times) // 2]
    print(f"\n  повторные запросы: медиана {median:.0f} мс")
    check(median < 1000, "повторный запрос не платит за старт",
          f"получено {median:.0f} мс")
    print(f"    для сравнения: отдельный запуск Joern — 5530 мс")

    print("\n=== 4. Ошибка запроса отличается от отказа сервера ===")
    # Заведомо некорректный запрос: сервер ответит, но это ошибка запроса,
    # а не отказ сервера. Клиент обязан это различать.
    out = server.query("это не код на Scala")
    print(f"  ответ на некорректный запрос: {out.strip()[:120]}")
    check("ОШИБКА ЗАПРОСА" in out or "error" in out.lower(),
          "ошибка запроса распознана", out[:100])
    # Сервер при этом продолжает работать.
    check(server.status.running, "сервер остался работоспособен после ошибки")

    print("\n=== 5. Состояние для интерфейса ===")
    data = server.status.to_dict()
    for key in ("running", "port", "pid", "queries", "last_query_ms"):
        print(f"    {key}: {data.get(key)}")
    check(data["running"] is True, "состояние показывает работу")
    check(data["queries"] >= 7, "число запросов учтено", str(data["queries"]))

finally:
    print("\n=== 6. Остановка сервера ===")
    port_before = server.status.port
    server.stop()
    # Порт освобождается не мгновенно: системе нужно закрыть сокет.
    # Проверка идёт с ожиданием, иначе она ловит испустимое состояние.
    released = False
    for _ in range(20):
        time.sleep(0.5)
        if _port_free(port_before):
            released = True
            break
    check(not server.status.running, "состояние показывает остановку")
    check(released, "порт освобождён", f"порт {port_before} всё ещё занят")

print("\n=== 7. Поведение при недоступном скрипте ===")
bad = JoernServer(
    joern_script=REPO / ".tools" / "нет-такого-скрипта.bat",
    workdir=REPO,
    java_home=JDK,
    preferred_port=9630,
)
try:
    bad.start()
    check(False, "отказ при отсутствующем скрипте", "исключение не возникло")
except JoernServerUnavailable as exc:
    check(True, "отказ при отсутствующем скрипте назван")
    print(f"    причина: {str(exc)[:120]}")
except Exception as exc:
    check(False, "отказ при отсутствующем скрипте", f"{type(exc).__name__}")

print("\n=== ИТОГ ===")
print(f"пройдено: {PASS}, провалено: {FAIL}")
raise SystemExit(0 if FAIL == 0 else 1)
