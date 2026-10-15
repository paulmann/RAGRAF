"""Пробы каналов и клиентов: фактическое состояние вместо предположения.

Зачем отдельный модуль
    Настройка «режим RAGRAF» полезна ровно настолько, насколько она проверяема.
    Иначе это надпись: владелец выбрал `both`, в интерфейсе написано «оба
    канала», а фактически не работает ни один. Поэтому каждый режим имеет пробу,
    которая поднимает канал и получает от него ответ, а клиенты проверяются по
    своему собственному признаку (файл, запись в базе, процесс).

Что именно проверяется и почему так
    * **CLI** — запуск `tools/ragraf.py catalog`: это единственный канал, который
      не зависит ни от регистрации сервера, ни от перезапуска приложения.
    * **stdio MCP** — сервер поднимается как процесс, ему отправляются
      `initialize` и `tools/list`, считается число инструментов. Проба повторяет
      путь теста `tests/test_mcp_compat.py`, чтобы вердикт панели и вердикт теста
      не могли разойтись.
    * **HTTP MCP** — запросы к `/healthz` и `/readyz` уже поднятого сервиса.
      Поднимать его за владельца нельзя: это отдельный процесс с открытым портом.
    * **AutoClaw** — число строк в `governed_connected_sources` (внешние
      инструменты). База открывается ТОЛЬКО на чтение: запись в неё в обход
      продукта ломает учёт прав и артефактов.
    * **DeepSeek Harness** — наличие блока `mcp-ragraf` в профиле и процесса
      приложения.
    * **SagaAI** — наличие файла инструмента и конфига корпуса, на который он
      ссылается.

Каждая проба возвращает словарь: `ok`, `kind` (что это), `detail` (что видно) и
`action` (что сделать, если не работает). Без `action` отказ бесполезен.

Автор: Михаил Дейнекин
Сайт: https://Deynekin.com
Почта: Mikhail@Deynekin.com
"""

from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "tools"
ADMIN_CONFIG = REPO / "config" / "ragraf.yaml"

# Домашний каталог и адреса каталогов данных: берутся через API, потому что
# переменных окружения вида APPDATA/LOCALAPPDATA в окружении выполнения нет.
USER_HOME = Path(os.path.expanduser("~"))
DSH_HOME = Path(os.environ.get("DSH_HOME") or (USER_HOME / ".dsh"))
DSH_PROFILE_PATCH = DSH_HOME / "profiles" / "desktop" / "cordis.patch.yml"

# Расположение инструмента SagaAI задаётся настройкой окружения: инструмент
# живёт в ЧУЖОМ проекте, и угадывать локальный путь нельзя — на другой машине он
# другой. Пустое значение означает «не заявлен», и состояние сообщается как
# «не найден», а не выдумывается.
_saga_env = os.environ.get("RAGRAF_SAGA_ROOT") or ""
SAGA_ROOT: Path | None = Path(_saga_env) if _saga_env else None
SAGA_TOOL: Path | None = (
    SAGA_ROOT / "orchestrators" / "dev_agent" / "functions" / "ragraf.py"
    if SAGA_ROOT else None
)
SAGA_CONFIG = REPO / "config" / "sagaai.yaml"

MODE_HTTP_PORT = 8765


def _appdata() -> Path:
    """Каталог данных приложения: переменная → реестр → домашний каталог.

    Первая редакция брала только переменную `APPDATA`, и проба клиента AutoClaw
    отвечала «каталог данных не найден», хотя приложение установлено и работало.
    Измерено: `os.environ.get("APPDATA")` в окружении выполнения равно `None`.
    Поэтому источников три, и берётся первый доступный:

      1. переменная окружения (обычная сессия пользователя);
      2. реестр пользователя — `Shell Folders/AppData` (точный путь системы);
      3. домашний каталог плюс `AppData/Roaming` (последний резерв).

    Так проба не зависит от того, какие переменные ей передали, — а именно на
    этом она и сломалась.
    """
    env = os.environ.get("APPDATA")
    if env and Path(env).is_dir():
        return Path(env)
    try:
        import winreg

        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders") as key:
            value = winreg.QueryValueEx(key, "AppData")[0]
        if value and Path(value).is_dir():
            return Path(value)
    except Exception:
        pass
    return Path(os.path.expanduser("~")) / "AppData" / "Roaming"


def _python() -> str:
    return sys.executable or "python"


# ---------------------------------------------------------------------------
# Каналы
# ---------------------------------------------------------------------------


def probe_cli() -> dict[str, Any]:
    """CLI: запускается ли обёртка и видит ли она реестр разработок."""
    started = time.time()
    try:
        proc = subprocess.run(
            [_python(), str(TOOLS / "ragraf.py"), "catalog"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, cwd=str(REPO),
        )
    except Exception as exc:
        return {
            "ok": False, "kind": "cli", "title": "Работа через оболочку (CLI)",
            "detail": f"запуск не удался: {type(exc).__name__}",
            "action": "проверьте, что python доступен и tools/ragraf.py на месте",
        }
    text = (proc.stdout or "") + (proc.stderr or "")
    m = re.search(r"разработок в реестре:\s*(\d+)", text)
    count = int(m.group(1)) if m else 0
    ok = proc.returncode == 0 and count > 0
    return {
        "ok": ok, "kind": "cli", "title": "Работа через оболочку (CLI)",
        "detail": (f"реестр отвечает, разработок: {count}" if ok
                   else f"код {proc.returncode}; {text.strip().splitlines()[0][:120] if text.strip() else 'нет вывода'}"),
        "action": ("" if ok else
                   "выполните: python tools/ragraf.py catalog — и посмотрите причину"),
        "seconds": round(time.time() - started, 2),
    }


def probe_mcp_stdio(tools_count_min: int = 20) -> dict[str, Any]:
    """stdio MCP: поднять сервер и посчитать инструменты.

    Проба намеренно поднимает НАСТОЯЩИЙ сервер и делает те же два запроса, что
    тест `tests/test_mcp_compat.py`: тогда вердикт панели и вердикт теста не могут
    разойтись между собой.
    """
    started = time.time()
    cmd = [_python(), str(TOOLS / "ragraf-mcp.py"),
           "--project", str(ADMIN_CONFIG),
           "--agent", "RAGRAF-Admin", "--agent-kind", "internal"]
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", cwd=str(REPO),
        )
        payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in (
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05",
                        "clientInfo": {"name": "RAGRAF-Admin"},
                        "capabilities": {}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )) + "\n"
        out, err = proc.communicate(payload, timeout=180)
    except Exception as exc:
        return {
            "ok": False, "kind": "mcp-stdio", "title": "MCP как локальный процесс",
            "detail": f"сервер не поднялся: {type(exc).__name__}",
            "action": "проверьте запуск: python tools/ragraf-mcp.py --project config/ragraf.yaml",
        }
    count = 0
    for line in (out or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("id") == 2:
            count = len((msg.get("result") or {}).get("tools") or [])
    ok = count >= tools_count_min
    return {
        "ok": ok, "kind": "mcp-stdio", "title": "MCP как локальный процесс",
        "detail": (f"сервер отвечает, инструментов: {count}" if ok
                   else f"сервер ответил, инструментов: {count} (ожидается не меньше {tools_count_min})"),
        "action": ("" if ok else
                   "проверьте tests/test_mcp_compat.py — он повторяет ту же пробу подробнее"),
        "seconds": round(time.time() - started, 2),
    }


def _http_json(url: str, timeout: float = 10.0,
               headers: dict[str, str] | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(raw) if raw.strip().startswith("{") else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, {}
    except Exception:
        return 0, {}


def probe_mcp_http(port: int = MODE_HTTP_PORT) -> dict[str, Any]:
    """HTTP MCP: отвечает ли уже поднятый сервис.

    Сервис НЕ поднимается пробой: это отдельный процесс с открытым портом, и
    запускать его за владельца панель не имеет права. Проба только спрашивает.
    """
    started = time.time()
    # Свободен ли порт: если занят кем-то другим, это тоже нужно видеть.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        busy = s.connect_ex(("127.0.0.1", port)) == 0

    code, health = _http_json(f"http://127.0.0.1:{port}/healthz")
    if code == 200 and health.get("alive") is True:
        rcode, ready = _http_json(f"http://127.0.0.1:{port}/readyz")
        return {
            "ok": rcode == 200 and ready.get("ready") is True,
            "kind": "mcp-http", "title": "MCP по URL (Online service)",
            "detail": (f"сервис отвечает на :{port}, корпус готов"
                       if rcode == 200 and ready.get("ready") else
                       f"сервис отвечает, но корпус не готов: {ready.get('reason') or 'причина не названа'}"),
            "action": ("" if rcode == 200 and ready.get("ready") else
                       "проверьте корпус: /readyz называет причину"),
            "seconds": round(time.time() - started, 2),
        }
    return {
        "ok": False, "kind": "mcp-http", "title": "MCP по URL (Online service)",
        "detail": (f"порт {port} занят другим процессом, HTTP MCP не отвечает"
                   if busy else f"на порту {port} никто не слушает"),
        "action": (f"запустите: python tools/ragraf-mcp-http.py --port={port} "
                   f"--project config/ragraf.yaml"),
        "seconds": round(time.time() - started, 2),
    }


# ---------------------------------------------------------------------------
# Клиенты
# ---------------------------------------------------------------------------


def _connected_sources() -> list[tuple[str, int]]:
    """Сколько внешних инструментов заведено в каждом аккаунте AutoClaw.

    Читается ТОЛЬКО на чтение: запись в базу продукта в обход продукта ломает
    учёт прав и артефактов, поэтому панель сюда не пишет.
    """
    out: list[tuple[str, int]] = []
    base = _appdata() / "AutoClaw-oversea-official" / "accounts"
    if not base.is_dir():
        return out
    for acc in sorted(base.glob("*")):
        db = acc / "runtime" / "runtime.sqlite"
        if not db.is_file():
            continue
        try:
            conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
            count = conn.execute(
                "SELECT COUNT(*) FROM governed_connected_sources").fetchone()[0]
            conn.close()
            out.append((acc.name[:12], int(count)))
        except Exception:
            out.append((acc.name[:12], -1))
    return out


def probe_autoclaw() -> dict[str, Any]:
    """AutoClaw: заведён ли RAGRAF как внешний инструмент."""
    sources = _connected_sources()
    if not sources:
        return {
            "ok": False, "kind": "autoclaw", "title": "AutoClaw",
            "detail": "каталог данных AutoClaw не найден",
            "action": "проверьте, установлен ли AutoClaw на этой машине",
        }
    total = sum(n for _n, n in sources if n > 0)
    accounts = ", ".join(f"{name}…: {n}" for name, n in sources)
    if total > 0:
        return {
            "ok": True, "kind": "autoclaw", "title": "AutoClaw",
            "detail": f"внешних инструментов заведено: {total} ({accounts})",
            "action": "",
        }
    return {
        "ok": False, "kind": "autoclaw", "title": "AutoClaw",
        "detail": f"внешних инструментов нет ни в одном аккаунте ({accounts})",
        "action": ("откройте Settings → Tools & connections → Add tool и впишите "
                   "значения из блока «AutoClaw» на этой странице (AutoClaw "
                   "регистрирует серверы только через свой интерфейс)"),
    }


def probe_harness() -> dict[str, Any]:
    """DeepSeek Harness: есть ли блок подключения и запущено ли приложение."""
    if not DSH_HOME.is_dir():
        return {
            "ok": False, "kind": "harness", "title": "DeepSeek Harness",
            "detail": f"каталог {DSH_HOME} не найден — приложение не настроено",
            "action": "установите DeepSeek Harness или проверьте путь DSH_HOME",
        }
    patch = DSH_PROFILE_PATCH
    block = ""
    if patch.is_file():
        text = patch.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"serverName:\s*ragraf[\s\S]{0,400}?transport:\s*([a-z-]+)",
                      text)
        block = m.group(1) if m else ""
    running = False
    try:
        proc = subprocess.run(
            ["powershell", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-Process -Name 'DeepSeek Harness' -ErrorAction SilentlyContinue | "
             "Measure-Object).Count"],
            capture_output=True, text=True, timeout=30,
        )
        running = (proc.stdout or "0").strip().isdigit() and int(proc.stdout.strip()) > 0
    except Exception:
        running = False

    if block:
        return {
            "ok": True, "kind": "harness", "title": "DeepSeek Harness",
            "detail": (f"подключение заведено, транспорт {block}; "
                       f"приложение {'запущено' if running else 'не запущено'}"),
            "action": "" if running else "перезапустите приложение, чтобы блок вступил в силу",
        }
    return {
        "ok": False, "kind": "harness", "title": "DeepSeek Harness",
        "detail": (f"подключение не заведено (каталог профиля: {patch.parent})"
                   if patch.parent.is_dir() else "каталог профиля не найден"),
        "action": "нажмите «Настроить Harness» на этой странице: панель добавит блок в профиль",
    }


def probe_sagaai() -> dict[str, Any]:
    """SagaAI: есть ли инструмент и конфиг корпуса, на который он ссылается."""
    if not (SAGA_TOOL and SAGA_TOOL.is_file()):
        return {
            "ok": False, "kind": "sagaai", "title": "SagaAI",
            "detail": ("инструмент не найден: "
                       f"{SAGA_TOOL or 'путь не задан (RAGRAF_SAGA_ROOT)'}"),
            "action": ("если проект SagaAI лежит в другом каталоге, инструмент "
                       "подключается в его own репозитории: панель чужой проект "
                       "не правит"),
        }
    assert SAGA_TOOL is not None  # проверено выше: читаем только найденный
    text = SAGA_TOOL.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'RAGRAF_CONFIG",\s*r?"([^"]+)"', text)
    config = str(m.group(1)) if m else ""
    config_ok = bool(config) and Path(config).is_file()
    return {
        "ok": config_ok,
        "kind": "sagaai", "title": "SagaAI",
        "detail": (f"инструмент есть, конфиг корпуса на месте: {config}" if config_ok
                   else f"инструмент есть, конфиг не найден: {config or 'не указан'}"),
        "action": ("" if config_ok else
                   "создайте корпус для проекта SagaAI: "
                   "python tools/ragraf.py ensure <каталог sagaai>"),
    }


# ---------------------------------------------------------------------------
# Сводка
# ---------------------------------------------------------------------------

MODES = ("cli", "mcp-stdio", "mcp-http", "both")


def probe_all(mode: str = "cli") -> dict[str, Any]:
    """Все пробы: каналы и клиенты, с вердиктом по выбранному режиму.

    Режим определяет, какие каналы СЧИТАЮТСЯ обязательными. Остальные всё равно
    проверяются и показываются: владелец должен видеть, что второй канал доступен
    даже когда он не ожидается.
    """
    mode = mode if mode in MODES else "cli"
    channels: list[dict[str, Any]] = []
    if mode in ("cli", "both"):
        channels.append(probe_cli())
    if mode in ("mcp-stdio", "both"):
        channels.append(probe_mcp_stdio())
    if mode in ("mcp-http", "both"):
        channels.append(probe_mcp_http())
    if mode == "cli":
        # Второй канал показывается справочно, но вердикт по нему не выдаётся.
        channels.append(_as_reference(probe_mcp_stdio()))

    clients = [probe_autoclaw(), probe_harness(), probe_sagaai()]
    required = [c for c in channels if not c.get("reference")]
    return {
        "mode": mode,
        "channels": channels,
        "clients": clients,
        "ok": all(c.get("ok") for c in required) if required else False,
        "mode_ok": all(c.get("ok") for c in required) if required else False,
        "note": ("режим описывает ожидаемый канал; второй канал при этом остаётся "
                 "доступен — CLI и MCP не конфликтуют"),
    }


def _as_reference(probe: dict[str, Any]) -> dict[str, Any]:
    """Пометка «справочно»: канал показан, но вердикт по нему не выносится."""
    out = dict(probe)
    out["reference"] = True
    out["title"] = f"{out.get('title')} (справочно)"
    return out
