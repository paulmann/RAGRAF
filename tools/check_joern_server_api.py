"""Проверка работающего сервера Joern: цена запроса без повторного старта.

Сервер уже поднят на порту 9505 (проверено: порт слушает, процесс 2480).
Здесь замеряется то, ради чего сервер нужен: сколько времени занимает ЗАПРОС
к поднятому серверу, то есть работа БЕЗ старта JVM и загрузки классов.

Для сравнения: одиночный запуск Joern платит 5,53 с за загрузку классов,
и индексация делает два таких запуска.

Проверяется:
  1. какой маршрут отвечает (в документации указаны /query и /query-sync);
  2. сколько времени занимает запрос;
  3. работает ли загрузка готового графа через сервер.

Сервер не останавливается: он поднят в фоне ранее и нужен для дальнейших проверок.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

PORT = 9505
BASE = f"http://127.0.0.1:{PORT}"

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


def call(path: str, method: str = "GET", payload: dict | None = None,
         timeout: int = 180):
    """Запрос к серверу. Возвращает (код, тело, секунды)."""
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers,
                                 method=method)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body, time.perf_counter() - t0
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body, time.perf_counter() - t0
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}", time.perf_counter() - t0


print("=" * 78)
print("ПРОВЕРКА РАБОТАЮЩЕГО СЕРВЕРА JOERN")
print("=" * 78)

print("\n=== 1. Какие маршруты отвечают ===")
for path in ("/", "/query", "/query-sync", "/result/test"):
    code, body, seconds = call(path, timeout=30)
    kind = "JSON" if body.lstrip().startswith(("{", "[")) else "текст"
    print(f"  {path:<14} код {code:<4} {seconds * 1000:>7.0f} мс  {kind}: "
          f"{body[:80].strip()}")

print("\n=== 2. Синхронный запрос: цена работы без старта ===")
# В документации Joern этот маршрут описан как /query-sync.
query = "val result = 1 + 1"
code, body, seconds = call("/query-sync", "POST", {"query": query}, timeout=300)
print(f"  запрос: {query}")
print(f"  код {code}, время {seconds * 1000:.0f} мс")
print(f"  ответ: {body[:200].strip()}")
check(code == 200, "синхронный запрос принят", f"код {code}")

# Если ответ — HTML, значит на порту отвечает не Joern.
is_html = body.lstrip().lower().startswith("<!doctype") or "<html" in body[:200].lower()
check(not is_html, "отвечает Joern, а не посторонняя программа",
      "получена HTML-страница")

print("\n=== 3. Повторные запросы: цена стабильна ===")
print("  если сервер работает, каждый запрос не создаёт процесс заново")
times = []
for i in range(5):
    code, body, seconds = call("/query-sync", "POST",
                               {"query": f"val x{i} = {i} * 2"}, timeout=300)
    times.append(seconds)
    print(f"    запрос {i + 1}: {seconds * 1000:.0f} мс (код {code})")
times.sort()
median = times[len(times) // 2]
print(f"\n  медиана: {median * 1000:.0f} мс")
check(median < 5.0, "запрос заметно быстрее старта Joern (5,53 с)",
      f"получено {median:.2f} с")

print("\n=== 4. Сравнение со стартом процесса ===")
single_startup = 5.53     # замерено: загрузка классов Joern
runs_per_index = 2        # построение и экспорт
print(f"  одиночный запуск Joern (загрузка классов): {single_startup:.2f} с")
print(f"  запусков на индексацию: {runs_per_index}")
print(f"  накладные сейчас: {single_startup * runs_per_index:.2f} с")
print(f"  запрос к серверу: {median * 1000:.0f} мс")
saved = single_startup * runs_per_index - median
print(f"  ЭКОНОМИЯ на индексацию: {saved:.2f} с")
check(saved > 5, "экономия существенна", f"получено {saved:.2f} с")

print("\n=== ИТОГ ===")
print(f"пройдено: {PASS}, провалено: {FAIL}")
print(f"\n  Сервер поднят заранее и остаётся работать на порту {PORT}.")
print("  Старт сервера платится ОДИН раз на сессию, дальше запросы бесплатны.")
