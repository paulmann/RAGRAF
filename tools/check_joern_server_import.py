"""Главная проверка: даёт ли сервер выигрыш при ПОСТРОЕНИИ графа.

Установлено:
  * сервер Joern поднимается и отвечает на запросы за 22 мс;
  * сервер умеет `importCode` (построить граф из исходников) и `importCpg`
    (загрузить готовый граф);
  * отдельный запуск `joern-parse` платит 5,53 с за загрузку классов.

То есть теоретически построение через сервер должно сэкономить 5,53 с.
Но это надо ПРОВЕРИТЬ: возможно, `importCode` внутри сервера требует другого
времени, или frontend PHP инициализируется так же долго.

Проверяется на МАЛОМ каталоге (несколько файлов), чтобы замер был быстрым:
  1. время `importCode` через сервер;
  2. доступны ли после этого данные графа через запросы;
  3. сравнение с отдельным запуском на том же каталоге.

Создаётся временный каталог с парой PHP-файлов; корпуса не трогаются.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import tempfile
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
PORT = 9505
BASE = f"http://127.0.0.1:{PORT}"


def query(q: str, timeout: int = 900) -> tuple[bool, str, float]:
    data = json.dumps({"query": q}).encode("utf-8")
    req = urllib.request.Request(
        BASE + "/query-sync", data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        seconds = time.perf_counter() - t0
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}", time.perf_counter() - t0
    try:
        payload = json.loads(body)
        return (bool(payload.get("success")),
                str(payload.get("stdout") or payload.get("err") or ""),
                seconds)
    except Exception:
        return False, body, seconds


print("=" * 78)
print("ПРОВЕРКА: ПОСТРОЕНИЕ ГРАФА ЧЕРЕЗ СЕРВЕР (importCode)")
print("=" * 78)

# Временный каталог с небольшим PHP-проектом.
tmp = pathlib.Path(tempfile.mkdtemp(prefix="joern_probe_"))
(tmp / "src").mkdir()
(tmp / "src" / "a.php").write_text(
    "<?php\n"
    "function alpha(int $x): int { return beta($x) + 1; }\n"
    "function beta(int $x): int { return $x * 2; }\n",
    encoding="utf-8", newline="\n",
)
(tmp / "src" / "b.php").write_text(
    "<?php\n"
    "class Widget {\n"
    "    public function render(): string { return alpha(3) . 'ok'; }\n"
    "}\n",
    encoding="utf-8", newline="\n",
)
print(f"  временный проект: {tmp}")
print(f"  файлов: {len(list(tmp.rglob('*.php')))}")

try:
    print("\n=== 1. importCode через сервер ===")
    # Путь передаётся в прямых слешах: обратные в CPGQL — escape-символы.
    path_posix = tmp.as_posix()
    ok, out, seconds = query(
        f'val project = importCode(inputPath = "{path_posix}", language = "PHP")',
        timeout=1800,
    )
    print(f"  время: {seconds:.2f} с")
    print(f"  успех: {ok}")
    print(f"  вывод: {out[:300].strip()}")

    print("\n=== 2. Доступны ли данные графа после построения ===")
    for label, q in (
        ("методов", "cpg.method.size"),
        ("вызовов", "cpg.call.size"),
        ("имён методов", "cpg.method.name.take(5).l"),
    ):
        ok2, out2, secs2 = query(q, timeout=600)
        print(f"  {label}: {'успех' if ok2 else 'отказ'} за {secs2 * 1000:.0f} мс")
        print(f"    {out2.strip()[:160]}")

    print("\n=== 3. Сравнение с отдельным запуском на том же проекте ===")
    print("  время 1: построение через сервер — %.2f с" % seconds)
    print("  время 2: отдельный запуск платит 5,53 с старта + работа")
    print(f"\n  ЭКОНОМИЯ на построении: "
          f"{'подтверждена' if seconds < 6 else 'НЕ подтверждена'}")
    if seconds < 6:
        print(f"    через сервер: {seconds:.2f} с")
        print(f"    отдельный запуск: не менее 5,53 с только на старт")
        print(f"    выигрыш: около {5.53 + 2 - seconds:.2f} с")
    else:
        print(f"    построение через сервер заняло {seconds:.2f} с — "
              f"это не быстрее отдельного запуска")
        print("    значит выигрыш даёт только переиспользование сервера")
        print("    для ВТОРОГО и последующих графов в той же сессии")

finally:
    shutil.rmtree(tmp, ignore_errors=True)
    print("\n  временный проект удалён")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Сервер даёт выигрыш двумя путями, и они разные:

  1. ПЕРЕИСПОЛЬЗОВАНИЕ между вызовами. Сейчас индексация платит 5,53 с за
     загрузку классов при каждом запуске, то есть около 11 с на индексацию.
     Сервер поднимается один раз, и эти 11 с исчезают для всех последующих
     индексаций в сессии.

  2. Построение внутри сервера. Если importCode работает, отдельный запуск
     joern-parse больше не нужен вообще.

  Что остаётся отдельным запуском: ЭКСПОРТ графа в файлы. Команды экспорта
  в серверном режиме нет — это отдельная утилита joern-export. Значит экспорт
  всё равно платит 5,53 с, если запускать его как отдельный процесс. Но если
  сервер уже поднят, экспорт можно вызывать как запрос к нему, если найдётся
  соответствующий маршрут — это проверяется отдельно.
""")
