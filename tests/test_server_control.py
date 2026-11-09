#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка штатного перезапуска MCP-сервера (`ragraf server`).

Что проверяется — СВОЙСТВА поведения, а не текст модуля:

  1. реестр пишется и читается: зарегистрированный процесс виден как живой;
  2. расхождение сборки называется: запись с другой сборкой помечается `stale`;
  3. брошенная запись (процесс умер) не выдаётся за живую и прорежается;
  4. `restart` завершает живой процесс и убирает его запись — клиент поднимет
     сервер заново сам;
  5. мёртвый PID не считается живым (проверка лiveness не впадает в «жив»).

Запуск: python tests/test_server_control.py
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile
import time
import types

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import server_control  # noqa: E402

failures = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global failures
    if cond:
        print(f"  [OK ] {label}")
    else:
        failures += 1
        print(f"  [FAIL] {label}{(': ' + detail) if detail else ''}")


def _workdir() -> pathlib.Path:
    """Песочница внутри .ragraf/tmp: %TEMP% в урезанном окружении недоступен."""
    root = REPO / ".ragraf" / "tmp"
    root.mkdir(parents=True, exist_ok=True)
    return pathlib.Path(tempfile.mkdtemp(prefix="server-control-", dir=str(root)))


def main() -> int:
    sandbox = _workdir()
    cfg = types.SimpleNamespace(ragraf_dir=sandbox / ".ragraf")

    print("1. РЕЕСТР: мертвый PID / живой процесс")
    check(not server_control._pid_alive(2_147_000_000), "несуществующий PID не жив")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        time.sleep(0.2)
        check(server_control._pid_alive(proc.pid), "запущенный процесс жив")
        entry = server_control.register(cfg, proc.pid, build="build-A", corpus="demo")
        check(entry["pid"] == proc.pid and entry["build"] == "build-A",
              "запись реестра содержит pid и сборку")

        print()
        print("2. СТАТУС: живой сервер и расхождение сборки")
        scan = server_control.scan(cfg, current_build_hash="build-A")
        check(len(scan) == 1 and scan[0]["alive"], "живой сервер виден в реестре")
        check(scan[0]["stale"] is False, "та же сборка — не stale")
        scan_stale = server_control.scan(cfg, current_build_hash="build-B")
        check(scan_stale[0]["stale"] is True,
              "другая сборка на диске — stale (нужен перезапуск)")

        print()
        print("3. ПЕРЕЗАПУСК: процесс завершён, запись убрана")
        result = server_control.restart(cfg, reason="проверка")
        check(len(result["killed"]) == 1, "перезапуск завершил ровно один сервер",
              f"killed={len(result['killed'])} failed={len(result['failed'])}")
        proc.wait(timeout=15)
        check(not server_control._pid_alive(proc.pid), "процесс действительно завершён")
        check(server_control.scan(cfg) == [], "реестр пуст после перезапуска")
    finally:
        if proc.poll() is None:
            proc.kill()

    print()
    print("4. БРОШЕННАЯ ЗАПИСЬ: не выдаётся за живую и прорежается")
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait(timeout=15)
    server_control.register(cfg, dead.pid, build="build-A", corpus="demo")
    scan = server_control.scan(cfg)
    check(len(scan) == 1 and scan[0]["alive"] is False,
          "брошенная запись названа «не жив», а не выдана за живую")
    check(server_control.scan(cfg) == [],
          "брошенная запись прорежена: повторное чтение пусто")

    print()
    print("=" * 78)
    if failures:
        print(f"ИТОГ: провалов {failures}")
        return 1
    print("ИТОГ: все свойства выполнены")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
