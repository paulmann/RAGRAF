"""MCP по Streamable HTTP: один сервис для AutoClaw, HARNESS и Hermes.

Зачем отдельная точка входа, а не замена stdio
    У AutoClaw в режиме «Local program» нет поля рабочего каталога (`cwd`), а в
    режиме «Online service» нет процесса — значит корпус сам не определится.
    Поэтому stdio остаётся рабочим путём, а этот сервер добавляется РЯДОМ:
    он нужен, когда клиент подключается по URL и умеет передавать заголовок
    `Authorization` (так работают Online service в AutoClaw, `streamable-http`
    в DeepSeek Harness и URL-подключение в Hermes Agent).

Что переиспользуется
    Слой инструментов берётся у стандартного сервера: `McpServer.handle` из
    `ragraf.mcp_server`. Здесь нет ни одной своей реализации операции — иначе
    два транспорта неизбежно разошлись бы в поведении и в правах.

Свойства, за которые отвечает транспорт
    * слушает только явно указанный адрес, по умолчанию 127.0.0.1;
    * **отказывается стартовать**, если адрес не loopback, а токены не заведены
      (иначе сервис открыт сети без проверки);
    * принимает `Authorization: Bearer <значение>`; сравнение — по хэшу через
      тот же `TokenStore`, что у панели (единая выдача доступа);
    * проверяет `Origin` по списку, если список задан;
    * ограничивает размер тела запроса, время выполнения и число одновременных
      запросов;
    * пишет каждое обращение в журнал доступа (`admin_access_log`): кто, чем,
      что и с каким результатом — без тел запросов и без значений доступа;
    * `GET /healthz` — процесс жив; `GET /readyz` — корпус и хранилища
      доступны. Обе точки не раскрывают ни путей, ни конфигурации, ни секретов.

Версия MCP: реализован Streamable HTTP в минимальном объёме — POST с одним
JSON-RPC сообщением и ответом `application/json` либо потоком SSE, сессия через
заголовок `Mcp-Session-Id`, `DELETE` для закрытия сессии. Большего не требуется:
операции короткие, потоковая отдача нужна только для совместимости.

Автор: Михаил Дейнекин
Сайт: https://Deynekin.com
Почта: Mikhail@Deynekin.com
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import access as access_mod  # noqa: E402
from ragraf import catalog as catalog_mod  # noqa: E402
from ragraf import ensure as ensure_mod  # noqa: E402
from ragraf import util  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.mcp_server import McpServer  # noqa: E402

# Ограничения. Значения намеренно скромные: это локальный сервис, и лучше
# получить понятный отказ, чем занять машину одним запросом.
MAX_BODY_BYTES = 1_000_000
REQUEST_TIMEOUT_SECONDS = 300
MAX_CONCURRENCY = 8

# Свойства процесса, собираемые из аргументов.
CONFIG_PATH: str | None = (
    # Корпус можно задать переменной окружения, а не только ключом запуска:
    # так его передают клиенты, у которых нет поля рабочего каталога.
    os.environ.get("RAGRAF_PROJECT") or os.environ.get("RAGRAF_CONFIG") or None
)
AGENT = "unknown"
AGENT_KIND = "unknown"
ACCESS_REQUIRED = True
ALLOWED_ORIGINS: list[str] = []
TOKENS = access_mod.TokenStore()
ACCESS_LOG = access_mod.AccessLog(None)
_SEMAPHORE = threading.BoundedSemaphore(MAX_CONCURRENCY)
_SESSIONS: dict[str, dict[str, Any]] = {}
_SESSIONS_LOCK = threading.Lock()


def _log(text: str) -> None:
    """Диагностика только в stderr: stdout занят протоколом."""
    sys.stderr.write(f"[ragraf-mcp-http] {text}\n")
    sys.stderr.flush()


def wire_access_log() -> None:
    """Подключить журнал доступа к хранилищу корпуса (или тому, что найдётся)."""
    order: list[str] = []
    if CONFIG_PATH:
        order.append(CONFIG_PATH)
    try:
        entries = catalog_mod.Catalog().summary().get("entries") or []
        order += [str(e.get("config") or "") for e in entries]
    except Exception:
        pass
    for config_path in order:
        if not config_path:
            continue
        path = Path(config_path)
        if not path.is_file():
            continue
        try:
            cfg = load_config(path)
            import sqlite3

            Path(cfg.control_db).parent.mkdir(parents=True, exist_ok=True)
            # check_same_thread=False: запросы обслуживаются в потоках сервера,
            # а журнал append-only, поэтому конкуренции за строки нет.
            conn = sqlite3.connect(str(cfg.control_db), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")

            class _Ctl:
                pass

            box = _Ctl()
            box.conn = conn
            log = access_mod.AccessLog(box)
            log.ensure_schema()
            ACCESS_LOG.control = box
            _log(f"журнал доступа: {util.to_posix(cfg.control_db)}")
            return
        except Exception:
            continue


def resolve_project() -> str | None:
    """Определить конфиг корпуса, если он не задан явно.

    Порядок: рабочий каталог через реестр разработок, затем сам каталог.
    Ровно тот же порядок, что у stdio-сервера, — чтобы поведение не расходилось.
    """
    if CONFIG_PATH:
        return CONFIG_PATH
    cwd = Path.cwd()
    try:
        category = catalog_mod.Catalog().category_for_root(cwd)
        if category:
            entry = catalog_mod.Catalog().get(category)
            if entry is not None and Path(entry.config).is_file():
                return str(entry.config)
    except Exception:
        pass
    ok, _reason = ensure_mod.looks_like_project(cwd)
    if ok:
        outcome = ensure_mod.ensure(cwd, create=False, index=False)
        if outcome.config and Path(outcome.config).is_file():
            return str(outcome.config)
    return None


class Handler(BaseHTTPRequestHandler):
    """Обработчик MCP по HTTP."""

    server_version = "RAGRAF-mcp-http/1.0"
    protocol_version = "HTTP/1.1"

    # ---------------- служебное ----------------

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _json(self, payload: dict[str, Any], code: int = 200,
              extra: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # Диагностические точки не должны раскрывать содержимое: запрет
        # кеширования и nosniff закрывают самые частые способы утечки.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _origin_ok(self) -> bool:
        """Разрешён ли источник запроса.

        Если список не задан, запрос пропускается: у машинных клиентов заголовка
        `Origin` может не быть вовсе, и требовать его — значит ломать
        server-to-server. Если список задан, посторонний источник отвергается:
        так защищают случаи, когда браузер обращается к сервису со страницы.
        """
        if not ALLOWED_ORIGINS:
            return True
        origin = str(self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        return origin in ALLOWED_ORIGINS

    def _bearer(self) -> str:
        raw = str(self.headers.get("Authorization") or "").strip()
        if raw.lower().startswith("bearer "):
            return raw[7:].strip()
        return ""

    def _principal(self) -> tuple[access_mod.Principal | None, str]:
        """Кто пришёл. Проверка значения идёт через общую выдачу доступа."""
        if not ACCESS_REQUIRED:
            return access_mod.Principal(kind="session", label="без проверки"), ""
        value = self._bearer()
        if not value:
            return None, "заголовок Authorization не передан"
        record, allowed, why = TOKENS.authorize(value)
        if not allowed:
            # ВАЖНО: при отказе возвращается None, а не «Principal с пустыми
            # правами». Первая редакция возвращала объект, и вызывающий код
            # принимал его за успешный вход: отозванный и неверный доступ
            # получали ответ 200 (измерено живой проверкой). Отказ — это
            # отсутствие входа, и выражать его надо так же, как отсутствие
            # заголовка. Метка известного, но отозванного доступа кладётся
            # в поле для журнала, чтобы владелец видел, ЧЕЙ доступ отказал.
            self._denied_label = record.label if record else ""
            self._denied_scopes = list(record.scopes) if record else []
            self._denied_id = record.token_id if record else ""
            return None, why
        return access_mod.Principal(kind="token", label=record.label,
                                    scopes=list(record.scopes),
                                    token_id=record.token_id), ""

    def _audit(self, *, operation: str, result: str,
               principal: access_mod.Principal | None, reason: str = "",
               detail: str = "", duration_ms: float = 0.0) -> None:
        ACCESS_LOG.record(surface="mcp-http", operation=operation, result=result,
                          principal=principal, remote=self.address_string(),
                          reason=reason, duration_ms=duration_ms, detail=detail)

    def _read_body(self) -> tuple[dict[str, Any] | None, str]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None, "нечитаемая длина тела"
        if length <= 0:
            return {}, ""
        if length > MAX_BODY_BYTES:
            return None, f"тело больше предела {MAX_BODY_BYTES} байт"
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8")), ""
        except Exception as exc:
            return None, f"тело не разобрано как JSON: {type(exc).__name__}"

    # ---------------- GET ----------------

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"

        if path == "/healthz":
            self._json({"ok": True, "service": "ragraf-mcp-http",
                        "alive": True})
            return

        if path == "/readyz":
            # Готовность: есть ли корпус и отвечают ли хранилища. Пути и
            # конфигурация НЕ раскрываются — только факт готовности и причина.
            project = resolve_project()
            ready = False
            reason = "корпус не определён"
            if project:
                try:
                    cfg = load_config(project)
                    from ragraf.store import connect

                    conn = connect(cfg.graph_db, readonly=True)
                    conn.execute("SELECT 1").fetchone()
                    conn.close()
                    ready = True
                    reason = ""
                except Exception as exc:
                    reason = f"хранилище недоступно: {type(exc).__name__}"
            self._json({"ok": ready, "ready": ready, "reason": reason},
                       200 if ready else 503)
            return

        if path == "/mcp":
            # GET на /mcp по спецификации открывает поток серверных сообщений.
            # Он нужен только при потоковой отдаче; сервис отвечает отказом,
            # чтобы клиент не ждал вечно открытого соединения.
            self._json({"ok": False, "error": "поток событий не поддерживается: "
                                              "используйте POST /mcp"}, 405)
            return

        self._json({"ok": False, "error": "не найдено"}, 404)

    # ---------------- DELETE ----------------

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path != "/mcp":
            self._json({"ok": False, "error": "не найдено"}, 404)
            return
        sid = str(self.headers.get("Mcp-Session-Id") or "")
        principal, why = self._principal()
        with _SESSIONS_LOCK:
            existed = _SESSIONS.pop(sid, None) is not None
        self._audit(operation="delete_session", result="ok" if existed else "denied",
                    principal=principal, reason=why or ("сессия закрыта" if existed
                                                        else "сессия не найдена"))
        self._json({"ok": True, "closed": existed})

    # ---------------- POST ----------------

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path != "/mcp":
            self._json({"ok": False, "error": "не найдено"}, 404)
            return

        if not self._origin_ok():
            origin = str(self.headers.get("Origin") or "")
            self._audit(operation="origin", result="denied", principal=None,
                        reason=f"источник не разрешён: {origin[:60]}")
            self._json({"ok": False, "error": "источник запроса не разрешён"}, 403)
            return

        self._denied_label = ""
        self._denied_scopes = []
        self._denied_id = ""
        principal, why = self._principal()
        if principal is None:
            # Если доступ известен, но отозван или просрочен — в журнал идёт его
            # метка: владелец должен видеть, чей доступ перестал работать.
            denied = (access_mod.Principal(kind="token", label=self._denied_label,
                                           scopes=self._denied_scopes,
                                           token_id=self._denied_id)
                      if self._denied_label else None)
            self._audit(operation="auth", result="denied", principal=denied,
                        reason=why)
            self._json({"ok": False, "error": "нужен доступ: передайте заголовок "
                                              "Authorization со значением выданного доступа"},
                       401, {"WWW-Authenticate": "Bearer"})
            return

        body, problem = self._read_body()
        if body is None:
            self._audit(operation="body", result="error", principal=principal,
                        reason=problem)
            self._json({"ok": False, "error": problem}, 400)
            return

        method = str(body.get("method") or "")
        started = time.time()

        if not _SEMAPHORE.acquire(blocking=False):
            self._audit(operation=method or "unknown", result="error",
                        principal=principal, reason="превышен предел одновременных")
            self._json({"ok": False, "error": "слишком много одновременных "
                                              "запросов, повторите позже"}, 429)
            return
        try:
            result = self._dispatch(body, principal)
        finally:
            _SEMAPHORE.release()

        duration = (time.time() - started) * 1000.0
        ok = "error" not in (result or {})
        self._audit(operation=method or "unknown",
                    result="ok" if ok else "error", principal=principal,
                    detail=f"id={body.get('id')}",
                    duration_ms=duration)
        if principal.token_id:
            TOKENS.touch(principal.token_id, result="ok" if ok else "error")

        headers = {}
        sid = str(self.headers.get("Mcp-Session-Id") or "")
        if method == "initialize" and not sid:
            sid = secrets.token_urlsafe(18)
            with _SESSIONS_LOCK:
                _SESSIONS[sid] = {"created_at": time.time(),
                                  "label": principal.label}
            headers["Mcp-Session-Id"] = sid
        # Клиент просит поток событий — отдаём ответ тем же телом, но с типом
        # потока: операции короткие, отдельного канала не требуется.
        accept = str(self.headers.get("Accept") or "")
        if "text/event-stream" in accept and result is not None:
            payload = f"event: message\ndata: {json.dumps(result, ensure_ascii=False)}\n\n"
            body_bytes = payload.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Content-Length", str(len(body_bytes)))
            self.send_header("Cache-Control", "no-store")
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body_bytes)
            return
        # Уведомления не имеют ответа: по спецификации отвечаем пустым кодом.
        if result is None:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            return
        self._json(result, 200, headers)

    # ---------------- обработка ----------------

    def _dispatch(self, body: dict[str, Any],
                  principal: access_mod.Principal) -> dict[str, Any] | None:
        """Обработать сообщение тем же слоем, что у stdio-сервера."""
        project = resolve_project()
        if not project:
            return {
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "error": {"code": -32002,
                          "message": "корпус не определён: укажите --project "
                                     "или запустите сервер из каталога проекта"},
            }
        server = McpServer(project, agent=AGENT, agent_kind=AGENT_KIND,
                           access_label=principal.label)
        try:
            return server.handle(body)
        except Exception as exc:
            _log(f"ошибка обработки {body.get('method')}: {type(exc).__name__}")
            return {
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "error": {"code": -32603, "message": "внутренняя ошибка сервера"},
            }
        finally:
            try:
                server.close()
            except Exception:
                pass


def main(argv: list[str]) -> int:
    global CONFIG_PATH, AGENT, AGENT_KIND, ACCESS_REQUIRED, ALLOWED_ORIGINS
    port = 8765
    host = "127.0.0.1"
    def value_of(name: str, index: int) -> str:
        """Значение аргумента: и «--имя=значение», и «--имя значение».

        Прежде форма через пробел молча не понималась у `--port`, `--agent` и
        `--agent-kind` (у `--project` понималась): сервис поднимался на порту по
        умолчанию, хотя запускающий указал другой (замерено 15.10.2026).
        """
        if argv[index].startswith(name + "="):
            return argv[index].split("=", 1)[1]
        return argv[index + 1] if index + 1 < len(argv) else ""

    for i, arg in enumerate(argv):
        if arg == "--project" or arg.startswith("--project="):
            CONFIG_PATH = value_of("--project", i)
        elif arg == "--port" or arg.startswith("--port="):
            port = int(value_of("--port", i) or port)
        elif arg == "--host" or arg.startswith("--host="):
            host = value_of("--host", i) or host
        elif arg == "--agent" or arg.startswith("--agent="):
            AGENT = value_of("--agent", i) or AGENT
        elif arg == "--agent-kind" or arg.startswith("--agent-kind="):
            AGENT_KIND = value_of("--agent-kind", i) or AGENT_KIND
        elif arg == "--no-auth":
            ACCESS_REQUIRED = False
        elif arg.startswith("--allowed-origins="):
            ALLOWED_ORIGINS = [o.strip() for o in arg.split("=", 1)[1].split(",")
                               if o.strip()]

    AGENT = AGENT if AGENT != "unknown" else os.environ.get("RAGRAF_AGENT", "unknown")
    AGENT_KIND = (AGENT_KIND if AGENT_KIND != "unknown"
                  else os.environ.get("RAGRAF_AGENT_KIND", "unknown"))

    loopback = host in ("127.0.0.1", "localhost", "::1")
    tokens = TOKENS.state()
    if not loopback and not ACCESS_REQUIRED:
        _log("ОТКАЗ: адрес не loopback, а проверка доступа выключена. "
             "Это открыло бы сервис сети без единой проверки. "
             "Заведите доступ в панели (раздел «Доступ») или слушайте 127.0.0.1.")
        return 2
    if not loopback and tokens["active"] == 0:
        _log("ОТКАЗ: адрес не loopback, а действующих значений доступа нет. "
             "Заведите их в панели: раздел «Доступ».")
        return 2

    wire_access_log()
    project = resolve_project()
    _log(f"корпус: {project or 'не определён (будет выбран операцией use_project)'}")

    server = ThreadingHTTPServer((host, port), Handler)
    # Следы обращений для панели: канал фиксируется явно, потому что в тот же
    # файл пишет stdio-процесс, который запускает сам клиент. Отметка о запуске
    # даёт панели среднее состояние: сервис поднят, а обращений ещё нет.
    os.environ["RAGRAF_MCP_TRANSPORT"] = "http"
    try:
        from ragraf import contact as contact_mod

        contact_mod.mark_service(port)
    except Exception:
        pass
    _log(f"слушает http://{host}:{port}/mcp")
    _log(f"проверка доступа: {'включена' if ACCESS_REQUIRED else 'ВЫКЛЮЧЕНА'}; "
         f"действующих значений: {tokens['active']}")
    if ALLOWED_ORIGINS:
        _log(f"разрешённые источники: {', '.join(ALLOWED_ORIGINS)}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _log("остановлен")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
