"""Следы обращений к MCP-серверу: дошёл ли клиент до RAGRAF на самом деле.

Зачем. Панель показывает инструкцию по подключению, но не могла сказать, подключился
ли клиент: настройка выглядела завершённой, а обращений не было — и владелец не знал,
работает ли связь. Здесь записывается КАЖДОЕ обращение: и из HTTP-сервиса, и из
stdio-процесса, который запускает сам клиент. Файл общий намеренно — это разные
процессы, и «кто постучался» должно быть видно из панели.

Три состояния, которые из этого получаются:
    красное — обращений не видно вовсе;
    жёлтое  — HTTP-сервис поднят, но клиент ещё не обращался;
    зелёное — обращение было (видно, кто, когда и последний инструмент).

Запись атомарна и никогда не поднимает исключение наружу: сбой учёта не должен
ломать ни MCP-сервер, ни панель.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]

#: Файл следов. Переопределяется переменной окружения — нужно проверкам, которые
#: не должны трогать общий файл (иначе проверка выглядела бы как подключение).
PATH = Path(
    os.environ.get("RAGRAF_CONTACT_FILE")
    or (REPO / ".ragraf" / "admin" / "mcp_contacts.json")
)

#: Признак канала для записи: http выставляет HTTP-сервис, stdio — по умолчанию.
TRANSPORT = os.environ.get("RAGRAF_MCP_TRANSPORT") or "stdio"

#: Хвост инструментов на клиента: сколько имён хранить.
TAIL = 20

_LOCK = threading.Lock()


def _read() -> dict[str, Any]:
    try:
        raw = json.loads(PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"clients": {}}
    return raw if isinstance(raw, dict) else {"clients": {}}


def _write(payload: dict[str, Any]) -> None:
    tmp = PATH.with_suffix(".json.tmp")
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(PATH)
    except Exception:
        # Учёт — удобство, а не источник правды: не записалось — не мешаем работе.
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def record(agent: str, method: str, tool: str = "", transport: str = "",
           client_name: str = "") -> None:
    """Записать обращение клиента. Никогда не поднимает исключение.

    `agent` — имя, известное по параметру запуска; `client_name` — имя из
    `clientInfo` запроса `initialize`. Второе важнее: HTTP-сервис
    поднимается с `--agent AutoClaw`, поэтому без отдельного признака
    чужой клиент выглядел бы как подключение AutoClaw.
    """
    name = (agent or "").strip() or "unknown"
    shown = (client_name or "").strip()
    move = (transport or TRANSPORT).strip() or "stdio"
    try:
        with _LOCK:
            payload = _read()
            clients = payload.setdefault("clients", {})
            item = clients.setdefault(name, {})
            stamp = time.time()
            item.setdefault("first_at", stamp)
            item["last_at"] = stamp
            item["transport"] = move
            item["agent"] = name
            if shown:
                item["client_info"] = shown
            item["source"] = "client" if (shown or item.get("client_info")) else "launch"
            item["calls"] = int(item.get("calls") or 0) + 1
            item["last_method"] = str(method or "")
            if tool:
                item["last_tool"] = str(tool)
                tools = item.setdefault("tools", [])
                if tool not in tools:
                    tools.append(str(tool))
                    del tools[:-TAIL]
            payload["updated_at"] = stamp
            _write(payload)
    except Exception:
        pass


def mark_service(port: int) -> None:
    """Отметить запуск HTTP-сервиса: панель отличает «поднят» от «не поднят»."""
    try:
        with _LOCK:
            payload = _read()
            payload["service"] = {"started_at": time.time(), "port": int(port),
                                  "pid": os.getpid()}
            _write(payload)
    except Exception:
        pass


def state(within_seconds: int = 900) -> dict[str, Any]:
    """Состояние для панели: кто обращался, когда и чем закончилось.

    Окно «связь есть» — 15 минут. Оно намеренно больше короткой паузы между
    вызовами: клиент MCP обращается по потребности, и молчание в несколько минут
    не означает разрыв. Состояние «было, но давно» отличается от «не было никогда»:
    это разные выводы и разные подсказки владельцу.
    """
    payload = _read()
    now = time.time()
    clients: list[dict[str, Any]] = []
    for name, item in (payload.get("clients") or {}).items():
        if not isinstance(item, dict):
            continue
        last = float(item.get("last_at") or 0.0)
        clients.append({
            "agent": str(name),
            "client_info": item.get("client_info") or "",
            "source": item.get("source") or "launch",
            "transport": item.get("transport") or "",
            "calls": int(item.get("calls") or 0),
            "last_at": last,
            "age_seconds": round(now - last) if last else None,
            "recent": bool(last) and (now - last) <= float(within_seconds),
            "last_method": item.get("last_method") or "",
            "last_tool": item.get("last_tool") or "",
            "tools": list(item.get("tools") or []),
        })
    clients.sort(key=lambda row: row.get("last_at") or 0.0, reverse=True)
    service = payload.get("service") if isinstance(payload.get("service"), dict) else {}
    result: dict[str, Any] = {
        "path": str(PATH),
        "within_seconds": int(within_seconds),
        "clients": clients,
        "last": clients[0] if clients else None,
        "service_started_at": float(service.get("started_at") or 0.0),
        "service_port": int(service.get("port") or 0),
    }
    # Готовые выводы считает сервер, а не страница: правило сопоставления имён
    # должно быть одно, иначе интерфейс и модуль разойдутся.
    result["links"] = {kind: match(kind, result) for kind in WORDS}
    return result


#: Слова, по которым запись относится к клиенту. Имя приходит из `--agent` или из
#: `clientInfo` запроса `initialize`, поэтому сравнение идёт по вхождению.
#: `zwork` добавлен по замеру 15.10.2026: MCP-клиент AutoClaw представляется именно
#: так (`client_info: zwork` при `agent: AutoClaw`). Без этого панель говорила
#: «обращений от AutoClaw не видно», хотя подключение состоялось.
WORDS: dict[str, tuple[str, ...]] = {
    "autoclaw": ("autoclaw", "zwork"),
    "harness": ("harness", "deepseek", "dsh"),
}


def match(kind: str, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Запись о клиенте указанного вида (`autoclaw` или `harness`), если была.

    Сравниваются ОБА имени записи, но не одинаково для разных каналов:

    * `client_info` — имя, которым представился сам клиент (по протоколу MCP он
      обязан это сделать). Значимо всегда;
    * `agent` — имя из параметра запуска. У stdio-сервера этот параметр задаёт САМ
      клиент своей командной строкой (`--agent AutoClaw`), поэтому там имя значимо.
      У HTTP-сервиса `--agent` задаётся один раз при запуске сервиса, и по нему
      чужой клиент выглядел бы как подключение AutoClaw — поэтому для записей
      канала http берётся только `client_info`.

    Ищем по имени, а не по факту обращения: проба панели тоже обращается к
    сервису, и её запись не должна выглядеть как подключение клиента.
    """
    data = payload if isinstance(payload, dict) else state()
    words = [w for w in WORDS.get(str(kind), ("",)) if w]
    for item in data.get("clients") or []:
        info = str(item.get("client_info") or "").lower()
        agent = str(item.get("agent") or "").lower()
        transport = str(item.get("transport") or "")
        candidates = [info, agent] if transport != "http" else [info]
        for candidate in candidates:
            if candidate and any(word in candidate for word in words):
                return item
    return None
