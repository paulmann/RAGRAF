"""Диагностика: почему серверный режим Joern не поднимается.

Справка Joern 4.0.99 подтверждает наличие режима сервера (--server, --server-host,
--server-port, --server-auth-username/password). Но при запуске сервер не отвечает
и процесс, судя по всему, завершается.

Проверяется:
  1. что именно печатает Joern при запуске сервера — полный вывод процесса;
  2. завершается ли процесс и с каким кодом;
  3. не мешает ли что-то: память, порт, недостающие файлы.

Вывод процесса перехватывается полностью: причина отказа — в нём.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
TOOLS = REPO / ".tools" / "joern-cli"
JDK = REPO / ".tools" / "jdk"

env = dict(os.environ)
env["JAVA_HOME"] = str(JDK)
env["PATH"] = f"{JDK / 'bin'}{os.pathsep}" + env.get("PATH", "")

script = TOOLS / "joern.bat"

print("=" * 78)
print("1. ЗАПУСК СЕРВЕРА С ПОЛНЫМ ПЕРЕХВАТОМ ВЫВОДА")
print("=" * 78)

cmd = [
    "cmd.exe", "/c", str(script),
    "-J-Xmx8g",
    "--server",
    "--server-host", "127.0.0.1",
    "--server-port", "9503",
]
print(f"  команда: {' '.join(cmd[-4:])}")

proc = subprocess.Popen(
    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    text=True, encoding="utf-8", errors="replace",
    env=env, cwd=str(TOOLS),
)

# Ожидание с чтением вывода: так видно прогресс и ошибки.
lines: list[str] = []
deadline = time.time() + 90
while time.time() < deadline:
    if proc.poll() is not None:
        break
    line = proc.stdout.readline() if proc.stdout else ""
    if line:
        lines.append(line.rstrip())
        print(f"    {line.rstrip()[:150]}")
    else:
        time.sleep(0.5)

if proc.poll() is not None:
    print(f"\n  процесс завершился с кодом {proc.returncode}")
else:
    print(f"\n  процесс работает (PID {proc.pid}), вывод пока не завершён")

# Досматривается остаток вывода.
try:
    if proc.stdout:
        rest = proc.stdout.read()
        if rest:
            for line in rest.splitlines()[:30]:
                print(f"    {line[:150]}")
except Exception:
    pass

print()
print("=" * 78)
print("2. ЧТО БЫЛО В ВЫВОДЕ")
print("=" * 78)
joined = "\n".join(lines)
keywords = ("error", "exception", "fail", "cannot", "not found", "denied",
            "port", "bind", "address", "server", "listening", "started")
found = [ln for ln in lines if any(k in ln.lower() for k in keywords)]
if found:
    for line in found[:20]:
        print(f"  {line[:160]}")
else:
    print("  значимых строк не найдено")
    for line in lines[:15]:
        print(f"  {line[:160]}")

print()
print("=" * 78)
print("3. ПРОВЕРКА: РАБОТАЕТ ЛИ СОКЕТ-СОЕДИНЕНИЕ")
print("=" * 78)
import socket

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(2.0)
    result = sock.connect_ex(("127.0.0.1", 9503))
print(f"  соединение с портом 9503: {'есть' if result == 0 else 'нет'} "
      f"(код {result})")

# Остановка.
if proc.poll() is None:
    print("\n  остановка процесса...")
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
    print("  остановлен")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Причина отказа названа в выводе процесса выше. Возможные варианты:

  * режим сервера требует дополнительных параметров, которых нет в справке;
  * серверу нужен больший объём памяти или другой формат вызова;
  * на этой сборке Joern режим объявлен, но не работает (такое бывает, когда
    функция есть в справке, но не собрана в конкретной поставке).

  Решение по серверу принимается только по факту: если он не поднимается,
  переписывать построение графа на него нельзя.
""")
