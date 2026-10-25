"""Итоговое состояние: корпуса синхронны, процессы убраны.

Проверяется:
  1. сторож свежести по всем корпусам: изменился ли код после индексации;
  2. какие java-процессы относятся к RAGRAF (их можно останавливать),
     а какие — чужие;
  3. порт панели администратора.

Чужие процессы не трогаются: останавливать можно только своё.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.facade import Facade  # noqa: E402

print("=" * 78)
print("1. СТОРОЖ СВЕЖЕСТИ ПО КОРПУСАМ")
print("=" * 78)
CORPORA = [
    ("z-batteryservice.yaml", "z.batteryservice.ru"),
    ("revoap.yaml", "revoap.com"),
    ("md.viewer.yaml", "MD.Viewer"),
    ("rdpmon.yaml", "RdpMON"),
    ("sagaai.yaml", "sagaai"),
]
for config_name, title in CORPORA:
    path = REPO / "config" / config_name
    if not path.exists():
        continue
    cfg = load_config(path)
    # watch=False: проверка не должна запускать переиндексацию.
    fac = Facade(cfg, tool="selftest", telemetry=False, watch=False)
    try:
        try:
            result = fac.op_freshness()
            changed = result.get("changed")
            rev = result.get("rev_id") or result.get("index_rev")
            mark = "чисто" if changed is False else f"ТРЕБУЕТСЯ РЕИНДЕКСАЦИЯ ({changed})"
            print(f"  {title:<24} ревизия {str(rev):<10} {mark}")
        except Exception as exc:
            print(f"  {title:<24} ошибка {type(exc).__name__}: {str(exc)[:60]}")
    finally:
        fac.close()

print()
print("=" * 78)
print("2. ПРОЦЕССЫ JAVA: КАКИЕ ОТНОСЯТСЯ К RAGRAF")
print("=" * 78)
# Определяется по командной строке: у процессов RAGRAF в ней есть joern
# или путь к инструментам RAGRAF.
try:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='java.exe'\" | "
         "Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120,
    )
    import json

    raw = (out.stdout or "").strip()
    processes = json.loads(raw) if raw.startswith(("[", "{")) else []
    if isinstance(processes, dict):
        processes = [processes]
    own = []
    others = []
    for p in processes:
        cmd = str(p.get("CommandLine") or "")
        pid = p.get("ProcessId")
        if "RAGRAF" in cmd and "joern" in cmd.lower():
            own.append((pid, cmd[:100]))
        elif "joern" in cmd.lower():
            others.append((pid, cmd[:100]))
    print(f"  процессов java всего: {len(processes)}")
    print(f"  процессов Joern от RAGRAF: {len(own)}")
    for pid, cmd in own[:6]:
        print(f"    PID {pid}: {cmd}")
    print(f"  процессов Joern, запущенных не RAGRAF: {len(others)}")
    for pid, cmd in others[:3]:
        print(f"    PID {pid}: {cmd}")
    print(f"  прочих java (не Joern): "
          f"{len(processes) - len(own) - len(others)}")
except Exception as exc:
    print(f"  не удалось разобрать процессы: {type(exc).__name__}: {exc}")

print()
print("=" * 78)
print("3. ПОРТ ПАНЕЛИ АДМИНИСТРАТОРА")
print("=" * 78)
import socket  # noqa: E402

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(2)
busy = sock.connect_ex(("127.0.0.1", 8777)) == 0
sock.close()
print(f"  порт 8777: {'занят (панель работает)' if busy else 'свободен'}")
print("""
  Панель администратора запускалась мной для проверок и должна быть
  остановлена: она держит порт и оставляет сессии в журнале.
""")
