"""Реестр живых MCP-серверов и штатный перезапуск после правки кода.

Зачем отдельный модуль. MCP-сервер — долгоживущий процесс: он держит модули,
загруженные при старте, и не замечает правок исходников. Ответ честно несёт
`code_stale` и подсказку `restart`, но подсказка без команды — это отказ без
выхода. Здесь штатный способ:

* сервер при старте пишет себя в реестр (PID, отпечаток сборки, корпус);
* `ragraf server status` показывает живые серверы и расхождение их сборки с
  диском;
* `ragraf server restart` завершает их — клиент (AutoClaw, HARNESS) поднимает
  stdio-сервер заново на новом коде.

Сервер НЕ может перезапустить себя: его stdout — это протокол MCP. Поэтому
«перезапуск» — это завершение процесса, а не команда серверу, и это сказано
явно, а не спрятано.

Реестр — файлы `<корпус>/.ragraf/mcp/<pid>.json`. Каталог `.ragraf` исключён из
сканера корпуса по умолчанию, поэтому реестр не входит в `corpus_hash` и не
является историей ревизий.
"""

from __future__ import annotations

import json
import os
import pathlib
import signal
import sys
import time
from typing import Any

#: Подкаталог состояния внутри `<корпус>/.ragraf`.
STATE_SUBDIR = "mcp"

#: Код, которым Windows сообщает «процесс ещё работает» (`GetExitCodeProcess`).
_STILL_ACTIVE = 259


def state_dir(cfg: Any) -> pathlib.Path:
    """Каталог реестра серверов этого корпуса."""
    return pathlib.Path(str(cfg.ragraf_dir)) / STATE_SUBDIR


def _entry_path(cfg: Any, pid: int) -> pathlib.Path:
    return state_dir(cfg) / f"{int(pid)}.json"


def current_build() -> str:
    """Отпечаток сборки, которая лежит НА ДИСКЕ прямо сейчас.

    Импорт ленивый: модуль не должен тянуть серверный код на импорте, иначе
    получится цикл (`mcp_server` импортирует этот модуль для регистрации).
    """
    from .mcp_server import build_fingerprint

    return build_fingerprint()


def register(cfg: Any, pid: int | None = None, build: str | None = None,
             corpus: str | None = None) -> dict[str, Any]:
    """Записать живой сервер в реестр. Возвращает записанную запись."""
    pid = int(pid if pid is not None else os.getpid())
    entry = {
        "pid": pid,
        "started_at": time.time(),
        "build": str(build if build is not None else current_build()),
        "corpus": str(corpus) if corpus else None,
        "python": sys.executable or "",
        "platform": sys.platform,
    }
    path = _entry_path(cfg, pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entry, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return entry


def unregister(cfg: Any, pid: int | None = None) -> None:
    """Убрать запись при штатном завершении. Отсутствие записи — не ошибка."""
    try:
        _entry_path(cfg, int(pid if pid is not None else os.getpid())).unlink()
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    pid = int(pid)
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return int(code.value) == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Процесс есть, но не наш: он жив, просто сигнал не разрешён.
        return True


def _terminate(pid: int) -> bool:
    pid = int(pid)
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
        if not handle:
            return False
        try:
            return bool(kernel32.TerminateProcess(handle, 1))
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return False


def scan(cfg: Any, current_build_hash: str | None = None,
         prune_dead: bool = True) -> list[dict[str, Any]]:
    """Записи реестра с вердиктом «жив / брошен / код на диске новее».

    Мёртвые записи удаляются (`prune_dead`): иначе после перезапуска клиента
    реестр копил бы PID умерших процессов и «статус» врал бы о живых.
    """
    directory = state_dir(cfg)
    if not directory.is_dir():
        return []
    live_build = current_build_hash if current_build_hash is not None else current_build()
    entries: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            entries.append({"pid": path.stem, "alive": False, "readable": False,
                            "path": str(path)})
            if prune_dead:
                try:
                    path.unlink()
                except OSError:
                    pass
            continue
        pid = data.get("pid")
        alive = _pid_alive(pid) if isinstance(pid, int) else False
        entry = {
            "pid": pid,
            "alive": alive,
            "readable": True,
            "build": data.get("build", ""),
            "stale": bool(alive and data.get("build") != live_build),
            "corpus": data.get("corpus"),
            "started_at": data.get("started_at"),
            "python": data.get("python", ""),
            "path": str(path),
        }
        if not alive and prune_dead:
            try:
                path.unlink()
            except OSError:
                pass
        entries.append(entry)
    return entries


def restart(cfg: Any, reason: str = "") -> dict[str, Any]:
    """Завершить живые серверы корпуса. Клиент поднимет их заново сам."""
    entries = scan(cfg, prune_dead=False)
    killed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for entry in entries:
        if not entry.get("alive"):
            continue
        pid = entry.get("pid")
        if _terminate(pid):
            killed.append(entry)
            try:
                _entry_path(cfg, pid).unlink()
            except OSError:
                pass
        else:
            failed.append(entry)
    return {
        "killed": killed,
        "failed": failed,
        "reason": reason,
        "note": ("клиент (AutoClaw, HARNESS) поднимает stdio-сервер заново сам; "
                 "новый статус виден той же командой после следующего вызова"),
    }
