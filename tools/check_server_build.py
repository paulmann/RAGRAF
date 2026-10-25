"""Проверка: можно ли построить граф через сервер вместо запуска joern-parse.

Установлено: индексация НЕ использует скриптовый режим запросов. Она делает две
вещи отдельными процессами:
  * `build()` — запускает joern-parse, строит файл .cpg.bin;
  * `export()` — запускает joern-export, выгружает граф в CSV.

Каждый запуск платит 5,53 с за загрузку классов Joern. Итого 11 с на индексацию.

Сервер даёт команду `importCode(path, language)`, которая строит граф из
исходников. Проверяется главное: создаёт ли она файл .cpg.bin, который потом
можно использовать для экспорта.

Если файл создаётся — построение можно делать через сервер, экономя 5,5 с.
Если нет — экспорт всё равно требует файла, и экономии не будет.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import socket
import sys
import tempfile
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

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


PORT = free_port(9750)

# Небольшой PHP-проект для проверки.
tmp = pathlib.Path(tempfile.mkdtemp(prefix="joern_build_probe_"))
(tmp / "a.php").write_text(
    "<?php\nfunction alpha(int $x): int { return beta($x); }\n"
    "function beta(int $x): int { return $x + 1; }\n",
    encoding="utf-8", newline="\n",
)
print(f"проект: {tmp}")

server = JoernServer(joern_script=TOOLS / "joern.bat", workdir=TOOLS,
                     java_home=JDK, heap="6g", preferred_port=PORT)
try:
    server.start()
    print(f"сервер: порт {server.status.port}, старт "
          f"{server.status.started_seconds:.2f} с\n")

    print("=== 1. importCode: построение графа из исходников ===")
    path_posix = tmp.as_posix()
    t0 = time.perf_counter()
    out = server.query(
        f'val project = importCode(inputPath = "{path_posix}", language = "PHP")',
        timeout=1800,
    )
    build_seconds = time.perf_counter() - t0
    print(f"  время: {build_seconds:.2f} с")
    print(f"  вывод: {out.strip()[:250]}")

    print("\n=== 2. Данные графа доступны сразу ===")
    for label, q in (("методов", "cpg.method.size"),
                     ("вызовов", "cpg.call.size")):
        t0 = time.perf_counter()
        out = server.query(q, timeout=300)
        print(f"  {label}: {out.strip()[:80]} ({(time.perf_counter()-t0)*1000:.0f} мс)")

    print("\n=== 3. ГЛАВНОЕ: создан ли файл графа для экспорта ===")
    # Ищутся все .cpg.bin в workspace сервера.
    found: list[pathlib.Path] = []
    for base in (TOOLS, TOOLS / "workspace", pathlib.Path.cwd()):
        if base.exists():
            found.extend(base.rglob("*.cpg.bin"))
    found = sorted(set(found), key=lambda p: p.stat().st_mtime, reverse=True)
    print(f"  найдено файлов .cpg.bin: {len(found)}")
    for f in found[:6]:
        print(f"    {f} ({f.stat().st_size / 1024:.0f} КБ)")

    # Свежий файл — тот, что создан сейчас.
    if found:
        newest = found[0]
        fresh = time.time() - newest.stat().st_mtime < 300
        print(f"\n  свежий файл: {fresh} ({newest.name})")
    else:
        fresh = False
        print("\n  файлов не найдено")

    print("\n=== 4. Команда save ===")
    out = server.query("save", timeout=600)
    print(f"  вывод: {out.strip()[:300]}")

    # Проверяется ещё раз после save.
    found2: list[pathlib.Path] = []
    for base in (TOOLS, TOOLS / "workspace", pathlib.Path.cwd()):
        if base.exists():
            found2.extend(base.rglob("*.cpg.bin"))
    found2 = sorted(set(found2), key=lambda p: p.stat().st_mtime, reverse=True)
    print(f"\n  после save файлов: {len(found2)}")
    for f in found2[:6]:
        print(f"    {f} ({f.stat().st_size / 1024:.0f} КБ)")

    print("\n=== 5. ВЫВОД ===")
    if found2:
        newest = found2[0]
        age = time.time() - newest.stat().st_mtime
        if age < 600:
            print(f"""
  ПОСТРОЕНИЕ ЧЕРЕЗ СЕРВЕР ДАЁТ ФАЙЛ ГРАФА.
  Файл: {newest}
  Создан {age:.0f} с назад, размер {newest.stat().st_size / 1024:.0f} КБ.

  Значит build() можно перевести на сервер: экономия около 5,5 с на индексацию.
  Экспорт при этом остаётся отдельным процессом — у него серверного аналога нет.
""")
        else:
            print(f"""
  Файл графа в workspace не обновлён (возраст {age:.0f} с).
  Значит построение через сервер НЕ даёт файла для экспорта:
  граф живёт только в памяти сервера.
  Тогда build() остаётся отдельным процессом, а сервер даёт выигрыш только
  на повторных ЗАПРОСАХ к готовому графу (если такие появятся).
""")
    else:
        print("""
  Файлов графа не найдено: построение через сервер не создаёт файл.
  Вывод: серверный режим не подходит для замены build() в текущем виде.
""")

finally:
    server.stop()
    shutil.rmtree(tmp, ignore_errors=True)
    print("\nсервер остановлен, временный проект удалён")
