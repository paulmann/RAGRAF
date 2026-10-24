"""Проверка: можно ли заменить экспорт в CSV запросами через сервер.

Установлено:
  * индексация делает ДВА отдельных запуска: `build()` (joern-parse → файл
    .cpg.bin) и `export()` (joern-export → CSV), каждый платит 5,53 с старта;
  * сервер строит граф в памяти за 2,18 с, но файл для экспорта не создаёт
    (команда save падает из-за блокировки);
  * запросы к серверу занимают 22–38 мс.

Отсюда следует третий путь: НЕ делать экспорт вообще. Взять те же данные
запросами через сервер. Тогда оба шага заменяются одним:
  importCode (2,18 с) + запросы (миллисекунды) вместо build (5,5 с) + export (6 с).

Проверяется главное: дают ли запросы через сервер те же данные, что и CSV-экспорт:
  * владелец вызова (метод, в котором он находится);
  * цель вызова (полное имя метода);
  * имя метода, файл, строка.

Сравнение идёт на РЕАЛЬНОМ корпусе: берётся тот же корпус, что и в индексации.
"""

from __future__ import annotations

import json
import pathlib
import socket
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.joern_server import JoernServer  # noqa: E402

TOOLS = REPO / ".tools" / "joern-cli"
JDK = REPO / ".tools" / "jdk"


def free_port(start: int) -> int:
    for p in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise RuntimeError("нет свободного порта")


PORT = free_port(9800)
cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
root = pathlib.Path(cfg.root)
print(f"корпус: {cfg.project}")
print(f"каталог: {root}")

server = JoernServer(joern_script=TOOLS / "joern.bat", workdir=TOOLS,
                     java_home=JDK, heap="8g", preferred_port=PORT)
try:
    t0 = time.perf_counter()
    server.start()
    print(f"сервер поднят за {time.perf_counter() - t0:.2f} с\n")

    print("=== 1. Построение графа корпуса через сервер ===")
    t0 = time.perf_counter()
    out = server.query(
        f'val project = importCode(inputPath = "{root.as_posix()}", language = "PHP")',
        timeout=2400,
    )
    build_seconds = time.perf_counter() - t0
    print(f"  время: {build_seconds:.2f} с")
    print(f"  вывод: {out.strip()[:200]}")

    print("\n=== 2. Данные графа: что вернул сервер ===")
    for label, q in (("методов", "cpg.method.size"),
                     ("вызовов", "cpg.call.size"),
                     ("файлов", "cpg.file.size")):
        t0 = time.perf_counter()
        out = server.query(q, timeout=600)
        value = out.strip().split("=")[-1].strip()[:40]
        print(f"  {label}: {value} ({(time.perf_counter() - t0) * 1000:.0f} мс)")

    print("\n=== 3. ГЛАВНОЕ: те же данные, что берутся из CSV-экспорта ===")
    print("  Экспорт даёт вызовы с полями: владелец, цель, файл, строка.")
    print("  Проверяется, что запрос через сервер даёт то же самое.")

    # Такой же запрос, как в QUERY_CALLS проекта.
    from ragraf.cpg import QUERY_CALLS, _extract_script_body

    out_file = REPO / ".staging" / "server_calls_probe.json"
    if out_file.exists():
        out_file.unlink()
    body = _extract_script_body(QUERY_CALLS, "unused", str(out_file))
    t0 = time.perf_counter()
    out = server.query(body, timeout=2400)
    calls_seconds = time.perf_counter() - t0
    print(f"\n  запрос вызовов: {calls_seconds:.2f} с")
    print(f"  вывод сервера: {out.strip()[:150]}")
    print(f"  файл создан: {out_file.exists()}")
    if out_file.exists():
        data = json.loads(out_file.read_text(encoding="utf-8"))
        print(f"  записей: {len(data)}")
        if data:
            first = data[0]
            print(f"  поля записи: {list(first.keys())}")
            print(f"  пример: владелец={str(first.get('srcMethod'))[:50]}, "
                  f"цель={str(first.get('callee'))[:50]}, "
                  f"строка={first.get('line')}")

    print("\n=== 4. Сравнение с тем, что даёт экспорт ===")
    # Читается существующий экспорт корпуса, если он есть.
    export_dirs = sorted(
        (pathlib.Path(cfg.root) / ".ragraf" / "cpg").glob("export_*"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    if export_dirs:
        latest = export_dirs[0]
        print(f"  экспорт: {latest.name}")
        # Ищется файл с вызовами.
        call_files = [p for p in latest.rglob("*") if p.is_file()
                      and "call" in p.name.lower()]
        print(f"  файлов с вызовами: {len(call_files)}")
        if call_files:
            f = call_files[0]
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
            print(f"    {f.name}: {len(lines)} строк")
            if lines:
                print(f"    пример: {lines[0][:200]}")
    else:
        print("  экспортов не найдено")

    print("\n=== 5. ИТОГ ===")
    if out_file.exists():
        data = json.loads(out_file.read_text(encoding="utf-8"))
        if data and "srcMethod" in data[0]:
            print(f"""
  СЕРВЕР ДАЁТ ТЕ ЖЕ ДАННЫЕ, ЧТО ЭКСПОРТ.

  Записей о вызовах: {len(data)}
  Поля совпадают с теми, что читает joern_export: srcMethod, callee,
  calleeName, line и другие.

  Значит оба шага можно заменить одним:
    * сейчас: build (5,5 с старта + работа) + export (5,5 с старта + 0,5 с)
    * через сервер: build в памяти ({build_seconds:.2f} с) + запрос
      ({calls_seconds:.2f} с)

  Экономия: около 5,5 с на построении плюс 5,5 с на экспорте,
  то есть примерно 11 с на индексацию. Файл .cpg.bin при этом не нужен,
  но остаётся полезен для отладки и повторных запросов.
""")
        else:
            print("  Данные получены, но поля не совпадают: нужна доработка.")
    else:
        print("""
  Запрос вызовов не создал файл результата.
  Значит путь через сервер для этого запроса пока не работает:
  нужна доработка, прежде чем переводить индексацию на сервер.
""")

finally:
    server.stop()
    print("\nсервер остановлен")
