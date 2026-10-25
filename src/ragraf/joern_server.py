"""Клиент сервера Joern: запросы без запуска JVM на каждый вызов.

Зачем этот модуль
    Замерено на этой машине: индексация запускает Joern дважды, и каждый запуск
    платит **5,53 с** за загрузку классов (чистый старт JVM — всего 64 мс).
    Итого около **11 с накладных расходов на индексацию**. Экспорт готового
    графа занимает 6,03 с, из которых 5,53 с — старт и только 0,50 с — работа.
    То есть 92% времени уходит на запуск, а не на дело.

    У Joern 4.0.99 есть серверный режим — проверено вызовом:
        joern --server --server-host 127.0.0.1 --server-port <порт>
    Сервер поднимается один раз, а запросы к нему занимают **22–38 мс**.
    Сборка графа через сервер (`importCode`) заняла **2,11 с** вместо 5,53 с
    только на старт отдельного процесса.

Как устроено
    Сервер принимает запросы CPGQL по HTTP:
      * POST /query-sync  {"query": "<код>"} → {"success": bool, "stdout": ...}
      * GET  /result/<uuid>                   → результат отложенного запроса

    Клиент поднимает сервер при первом обращении, ждёт готовности и держит
    процесс до остановки. Если сервер недоступен, клиент сообщает об этом,
    и вызывающий код работает по-старому — через отдельные процессы.

Чего этот модуль НЕ делает
    Не экспортирует граф в файлы: команды экспорта в серверном режиме нет,
    это отдельная утилита `joern-export`. Экспорт остаётся отдельным процессом,
    но его результат (перечень узлов и рёбер) можно получить и через сервер
    обычными запросами — это и делается в `cpg.py`.

Безопасность
    Сервер слушает только 127.0.0.1. Авторизация не включена: доступ к порту
    ограничен локальной машиной, а сам сервер не является границей безопасности
    (это указано в его документации). Для доступа с других машин потребуется
    --server-auth-username и --server-auth-password.
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import subprocess
import time
import urllib.error
import urllib.request

from . import util
from dataclasses import dataclass, field
from typing import Any

# Сколько ждать готовности сервера. Загрузка классов Joern занимает секунды,
# а на крупном CPG сервер поднимается дольше.
STARTUP_TIMEOUT_SEC = 180.0
# Таймаут запроса: построение графа на крупном корпусе — минуты.
QUERY_TIMEOUT_SEC = 3600.0


class JoernServerUnavailable(Exception):
    """Сервер Joern не поднялся: вызывающий код должен работать по-старому."""


@dataclass
class ServerStatus:
    """Состояние сервера: показывается в интерфейсе администратора."""

    enabled: bool = False
    running: bool = False
    port: int = 0
    pid: int = 0
    started_seconds: float = 0.0
    queries: int = 0
    last_query_ms: float = 0.0
    reason: str = ""
    reused_across_runs: int = 0
    requests: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "running": self.running,
            "port": self.port,
            "pid": self.pid,
            "started_seconds": round(self.started_seconds, 2),
            "queries": self.queries,
            "last_query_ms": round(self.last_query_ms, 1),
            "reason": self.reason,
            "reused_across_runs": self.reused_across_runs,
            "requests": self.requests,
        }


def _port_free(port: int) -> bool:
    """Свободен ли порт. Проверка ЗАНЯТИЕМ, а не соединением.

    Почему занятием, а не соединением. В первой версии порт проверялся попыткой
    соединиться. На замерах выяснилось, что это НЕДЕТЕРМИНИРОВАННО: при только
    что созданном слушающем сокете соединение иногда проходит, иногда нет, и
    проверка давала разные ответы на одном и том же состоянии. Из-за этого
    выбранный порт мог оказаться занятым.

    Проверка занятием (bind) точна: если сокет привязался, порт свободен.
    Но есть особенность: на Windows сокет, уже привязанный другим процессом,
    может требовать SO_REUSEADDR. Поэтому при отказе привязки порт считается
    занятым — это безопаснее, чем ошибочно свободным.

    Дополнительно проверяется соединением: если привязка удалась, но порт
    кому-то отвечает, он тоже считается занятым (такое бывает с сокетами
    в состоянии TIME_WAIT и с прокси).
    """
    # 1. Попытка занять.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
    except OSError:
        return False
    # 2. Проверка соединением на случай, если порт всё же кому-то отвечает.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            return False
    return True


def _pick_port(preferred: int = 9500, span: int = 100) -> int:
    """Выбрать свободный порт в заданном диапазоне."""
    for port in range(preferred, preferred + span):
        if _port_free(port):
            return port
    raise JoernServerUnavailable(
        f"нет свободного порта в диапазоне {preferred}..{preferred + span}"
    )


@dataclass
class JoernServer:
    """Управление долгоживущим сервером Joern.

    Один объект — один сервер. Сервер поднимается лениво, при первом запросе,
    и живёт до вызова `stop()`. Это позволяет вызывающему коду не заботиться
    о времени жизни: он просто выполняет запросы.
    """

    joern_script: pathlib.Path
    workdir: pathlib.Path
    java_home: pathlib.Path | None = None
    heap: str = "8g"
    preferred_port: int = 9500
    status: ServerStatus = field(default_factory=ServerStatus)
    _proc: subprocess.Popen | None = None
    _port: int = 0

    def _env(self) -> dict[str, str]:
        """Окружение для запуска: JAVA_HOME обязателен для скрипта Joern."""
        env = dict(os.environ)
        if self.java_home and self.java_home.exists():
            env["JAVA_HOME"] = str(self.java_home)
            env["PATH"] = f"{self.java_home / 'bin'}{os.pathsep}" + env.get("PATH", "")
        # Тот же личный временный каталог, что у построения CPG (см.
        # `cpg.joern_tmp`): zstd-jni распаковывает нативную библиотеку при
        # старте КАЖДОЙ JVM, и общий `%TEMP%` уже отказывал в записи.
        tmpdir = self.workdir / "tmp"
        tmpdir.mkdir(parents=True, exist_ok=True)
        flag = f"-Djava.io.tmpdir={tmpdir}"
        existing = env.get("JAVA_TOOL_OPTIONS", "").strip()
        env["JAVA_TOOL_OPTIONS"] = f"{existing} {flag}".strip()
        return env

    def start(self) -> None:
        """Поднять сервер и дождаться готовности."""
        if self._proc is not None and self._proc.poll() is None:
            return
        if not self.joern_script.exists():
            raise JoernServerUnavailable(f"скрипт Joern не найден: {self.joern_script}")

        port = _pick_port(self.preferred_port)
        self._port = port
        # Форма запуска общая с `cpg.py` (`util.cmd_command`): сервер — тот же
        # `.bat`, и разбор командной строки у него тот же. Замерено 24.10.2026:
        # `&` без пробела разрывал команду, `%` раскрывался в значение
        # переменной. Здесь это отказ с причиной, а не тихая подмена.
        try:
            command = util.cmd_command([
                str(self.joern_script),
                f"-J-Xmx{self.heap}",
                "--server",
                "--server-host", "127.0.0.1",
                "--server-port", str(port),
            ])
        except util.CmdArgumentError as exc:
            raise JoernServerUnavailable(str(exc)) from exc
        started = time.perf_counter()
        try:
            self._proc = subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=self._env(), cwd=str(self.workdir),
            )
        except Exception as exc:
            raise JoernServerUnavailable(
                f"не удалось запустить сервер: {type(exc).__name__}: {exc}"
            ) from exc

        # Ожидание готовности: сервер обязан ответить на HTTP-запрос.
        deadline = time.time() + STARTUP_TIMEOUT_SEC
        while time.time() < deadline:
            if self._proc.poll() is not None:
                raise JoernServerUnavailable(
                    f"сервер завершился с кодом {self._proc.returncode}"
                )
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/result/00000000-0000-4000-8000-000000000000",
                    method="GET",
                )
                with urllib.request.urlopen(req, timeout=5):
                    pass
                break
            except urllib.error.HTTPError:
                # Любой HTTP-ответ означает, что сервер поднялся.
                break
            except Exception:
                time.sleep(1.0)
        else:
            self.stop()
            raise JoernServerUnavailable(
                f"сервер не ответил за {STARTUP_TIMEOUT_SEC:.0f} с"
            )

        self.status.running = True
        self.status.port = port
        self.status.pid = int(self._proc.pid or 0)
        self.status.started_seconds = time.perf_counter() - started
        self.status.reason = (
            f"сервер поднят за {self.status.started_seconds:.2f} с; "
            f"запросы к нему не платят за загрузку классов"
        )

    def query(self, code: str, timeout: float = QUERY_TIMEOUT_SEC) -> str:
        """Выполнить запрос CPGQL. Возвращает вывод сервера.

        Успех определяется полем `success` в ответе: сервер отвечает кодом 200
        даже на ошибку в запросе, и без проверки этого поля ошибка пройдёт
        незамеченной.
        """
        if not self.status.running:
            self.start()
        payload = json.dumps({"query": code}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self._port}/query-sync",
            data=payload, method="POST",
            headers={"Content-Type": "application/json"},
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            raise JoernServerUnavailable(
                f"запрос не выполнен: {type(exc).__name__}: {exc}"
            ) from exc
        self.status.queries += 1
        self.status.requests += 1
        self.status.last_query_ms = (time.perf_counter() - started) * 1000

        try:
            result = json.loads(body)
        except Exception:
            return body
        if not result.get("success"):
            error = str(result.get("err") or result.get("stderr") or "")
            # Ошибка внутри запроса — это не отказ сервера, а ошибка запроса.
            return f"ОШИБКА ЗАПРОСА: {error[:500]}"
        return str(result.get("stdout") or "")

    def stop(self) -> None:
        """Остановить сервер ВМЕСТЕ с дочерним JVM.

        Почему нельзя просто terminate: сервер запускается через cmd.exe,
        и завершение cmd НЕ убивает дочернюю JVM. Замерено: после остановки
        оставался java-процесс на 375 МБ, который продолжал держать порт.
        Это утечка: несколько индексаций — несколько висящих JVM.

        Поэтому дерево процессов убивается целиком: `taskkill /T` завершает и
        обёртку cmd.exe, и JVM одной командой, а список потомков по системе —
        запасной путь, если дерево уже распалось.
        """
        proc = self._proc
        if proc is None:
            return
        pid = int(proc.pid or 0)
        # taskkill /T ДО ожидания: он работает, пока cmd.exe жив и держит связь
        # с JVM. Замер 16.10.2026: три JVM-сироты держали порты 9500–9502.
        if pid:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True, text=True, timeout=60,
                )
            except Exception:
                pass
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:
            pass

        # Убиваются потомки: именно они держат порт и память.
        self._kill_descendants(pid)
        self._proc = None
        self.status.running = False
        self.status.reason = "сервер остановлен"

    def _kill_descendants(self, parent_pid: int) -> None:
        """Завершить дочерние процессы запущенного сервера.

        Используется WMIC через PowerShell: он показывает дерево процессов
        без дополнительных библиотек. Если команда недоступна, попытка просто
        не выполняется — это лучше, чем упасть при остановке.
        """
        if not parent_pid:
            return
        try:
            listing = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                 "Get-CimInstance Win32_Process | "
                 "Select-Object ProcessId,ParentProcessId,CommandLine | "
                 "ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=60,
            )
            text = (listing.stdout or "").strip()
            if not text:
                return
            data = json.loads(text)
            if isinstance(data, dict):
                data = [data]
        except Exception:
            return

        # Ищутся потомки рекурсивно: server -> cmd -> java.
        children: dict[int, list[int]] = {}
        for item in data:
            try:
                child = int(item.get("ProcessId") or 0)
                parent = int(item.get("ParentProcessId") or 0)
            except (TypeError, ValueError):
                continue
            children.setdefault(parent, []).append(child)

        def collect(pid: int, out: list[int]) -> None:
            for child in children.get(pid, []):
                out.append(child)
                collect(child, out)

        targets: list[int] = []
        collect(parent_pid, targets)
        for pid in targets:
            try:
                proc = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True, text=True, timeout=30,
                )
            except Exception:
                continue

    def __enter__(self) -> "JoernServer":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()
