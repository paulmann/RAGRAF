"""Диагностика: почему скрипт через сервер не создаёт файл результата.

Проверено: сервер отвечает на простые запросы за 22 мс и подставляемые пути
в тексте скрипта есть. Но файл результата не создаётся, а сервер при этом
не сообщает об ошибке запроса.

Проверяется по шагам:
  1. что именно возвращает сервер на скрипт целиком;
  2. выполняется ли `os.write.over` внутри сервера вообще;
  3. доступен ли модуль `os` (в серверном режиме он может быть не импортирован);
  4. работает ли `ujson` в серверном окружении.

Причина, скорее всего, в том, что скриптовый режим и режим сервера — разные
окружения: в серверном нет автоматических импортов, которые есть в скриптовом.
"""

from __future__ import annotations

import json
import pathlib
import sys
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.cpg import _extract_script_body  # noqa: E402
from ragraf.joern_server import JoernServer  # noqa: E402

PORT = 0
import socket  # noqa: E402


def free_port(start: int) -> int:
    for p in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise RuntimeError("нет свободного порта")


PORT = free_port(9700)
TOOLS = REPO / ".tools" / "joern-cli"
JDK = REPO / ".tools" / "jdk"

cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
cpg_dir = pathlib.Path(cfg.root) / ".ragraf" / "cpg"
cpg_file = sorted(cpg_dir.glob("*.cpg.bin"),
                  key=lambda p: p.stat().st_mtime, reverse=True)[0]
print(f"граф: {cpg_file.name}")


def call(code: str, timeout: int = 600) -> tuple[bool, str]:
    payload = json.dumps({"query": code}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/query-sync", data=payload, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    try:
        data = json.loads(body)
    except Exception:
        return False, body[:300]
    if not data.get("success"):
        return False, str(data.get("err") or data.get("stderr") or "")[:400]
    return True, str(data.get("stdout") or "")


server = JoernServer(joern_script=TOOLS / "joern.bat", workdir=TOOLS,
                     java_home=JDK, heap="6g", preferred_port=PORT)
try:
    server.start()
    print(f"сервер: порт {server.status.port}\n")

    print("=== 1. Доступны ли нужные модули в серверном режиме ===")
    for label, code in (
        ("простое выражение", "1 + 1"),
        ("модуль os", 'os.Path("/tmp/x").toString'),
        ("модуль ujson", 'ujson.write(1)'),
        ("ujson.write в строку", 'ujson.write(ujson.Arr(Seq(1,2)))'),
    ):
        ok, out = call(code)
        print(f"  {label}: {'ок' if ok else 'ОШИБКА'}")
        print(f"    {out.strip()[:200]}")

    print("\n=== 2. Загрузка графа ===")
    ok, out = call(f'importCpg("{cpg_file.as_posix()}")', timeout=900)
    print(f"  importCpg: {'ок' if ok else 'ОШИБКА'}")
    print(f"    {out.strip()[:200]}")
    if not ok:
        raise SystemExit(1)

    print("\n=== 3. Простой запрос без записи в файл ===")
    ok, out = call("cpg.method.size")
    print(f"  cpg.method.size: {'ок' if ok else 'ОШИБКА'} → {out.strip()[:100]}")

    print("\n=== 4. Запись в файл вручную ===")
    probe = REPO / ".staging" / "joern_server_probe.json"
    if probe.exists():
        probe.unlink()
    code = (f'os.write.over(os.Path("{probe.as_posix()}"), '
            f'ujson.write(ujson.Arr(Seq("раз", "два"))))')
    ok, out = call(code)
    print(f"  запись: {'ок' if ok else 'ОШИБКА'}")
    print(f"    {out.strip()[:200]}")
    print(f"    файл создан: {probe.exists()}")
    if probe.exists():
        print(f"    содержимое: {probe.read_text(encoding='utf-8')[:200]}")

    print("\n=== 5. Скрипт целиком, как его подготавливает RAGRAF ===")
    from ragraf.cpg import QUERY_CALLS
    out_file = REPO / ".staging" / "joern_full_probe.json"
    if out_file.exists():
        out_file.unlink()
    body = _extract_script_body(QUERY_CALLS, str(cpg_file), str(out_file))
    # Граф уже загружен, поэтому строка importCpg выполнится повторно —
    # это допустимо и проверяет, что она не ломает запрос.
    ok, out = call(body, timeout=1200)
    print(f"  выполнение: {'ок' if ok else 'ОШИБКА'}")
    print(f"    вывод: {out.strip()[:400]}")
    print(f"    файл создан: {out_file.exists()}")
    if out_file.exists():
        data = out_file.read_text(encoding="utf-8")
        print(f"    размер: {len(data)} символов")
        print(f"    начало: {data[:200]}")

finally:
    server.stop()
    print("\nсервер остановлен")
