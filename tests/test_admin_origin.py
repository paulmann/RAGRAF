"""Граница доверия панели: чужой `Host` и чужой `Origin` получают отказ.

Зачем отдельный файл. Панель слушает только 127.0.0.1, и раньше считалось, что
этого достаточно. Это не так: DNS-rebinding указывает ЧУЖИМ именем на 127.0.0.1,
браузер владельца считает запрос «своим» и отдаёт ответ панели чужой странице.
Замерено 24.10.2026 до правки: запрос с `Host: evil.example:<порт>` получал 200
на `/api/version`, проверок `Origin`/`Host` в `src/ragraf/admin_server.py` не было
ни одной.

Проверяется СВОЙСТВО, а не написание. Поднимается настоящий обработчик панели на
свободном порту, запросы идут по HTTP с разными заголовками:

  1. запрос без `Origin` из loopback — РАЗРЕШЁН (его не посылают curl, скрипты и
     локальные инструменты; запрет сломал бы штатные способы обращения);
  2. свой `Host` и свой `Origin` — разрешены;
  3. чужой `Origin` — отказ 403 с причиной в теле;
  4. `Origin: null` (песочница браузера, `file://`) — отказ;
  5. чужой `Host` — отказ, включая подделку `localhost.evil.example`;
  6. чужой порт в `Host` или `Origin` — отказ;
  7. отказ действует для POST и для ПОДКЛАССА, переопределяющего `do_GET`
     (именно так в панели отдаётся статика — без этой проверки статика
     осталась бы открытой);
  8. отказ происходит ДО разбора маршрута: несуществующий путь с чужим `Host`
     даёт 403, а не 404.

Токены и пароли здесь не нужны и не печатаются: отказ границы доверия наступает
раньше любой проверки доступа.
"""

from __future__ import annotations

import http.client
import json
import pathlib
import sys
import threading
from http.server import ThreadingHTTPServer

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import admin_server  # noqa: E402

PASS = 0
FAIL = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


class _StaticLike(admin_server.Handler):
    """Подкласс, переопределяющий `do_GET` так же, как статика панели.

    `super().do_GET()` не вызывается намеренно: проверка внутри `do_GET`
    такой маршрут не покрыла бы. Свойство: граница доверия обязана действовать
    и здесь, иначе `/style.css` и `/app.js` остаются доступны чужой странице.
    """

    def do_GET(self) -> None:  # noqa: N802
        self._send_text("static-body", "text/plain; charset=utf-8")


def serve(handler_cls) -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    port = int(server.server_address[1])
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


def call(port: int, *, method: str = "GET", path: str = "/api/health",
         headers: dict[str, str] | None = None,
         body: dict | None = None) -> tuple[int, object]:
    """Запрос к панели с ЯВНЫМИ заголовками (Host не подставляется, если задан)."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    head = dict(headers or {})
    if data is not None:
        head.setdefault("Content-Type", "application/json")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        conn.request(method, path, body=data, headers=head)
        response = conn.getresponse()
        raw = response.read().decode("utf-8", errors="replace")
        try:
            parsed: object = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
        return int(response.status), parsed
    finally:
        conn.close()


def reason_of(body: object) -> str:
    if isinstance(body, dict):
        return str(body.get("reason") or body.get("error") or "")
    return ""


print("=" * 78)
print("ГРАНИЦА ДОВЕРИЯ ПАНЕЛИ: Host и Origin")
print("=" * 78)

server, port = serve(admin_server.Handler)
try:
    print(f"\n1. Свой источник разрешён (порт {port})")
    status, body = call(port)
    check(status == 200, "запрос без Origin из loopback разрешён", f"код {status}")
    status, body = call(port, headers={"Host": f"127.0.0.1:{port}"})
    check(status == 200, "Host: 127.0.0.1:<порт> разрешён", f"код {status}")
    status, _ = call(port, headers={"Host": f"localhost:{port}"})
    check(status == 200, "Host: localhost:<порт> разрешён", f"код {status}")
    status, _ = call(port, headers={"Origin": f"http://127.0.0.1:{port}"})
    check(status == 200, "Origin: http://127.0.0.1:<порт> разрешён", f"код {status}")
    status, _ = call(port, headers={"Origin": f"http://localhost:{port}"})
    check(status == 200, "Origin: http://localhost:<порт> разрешён", f"код {status}")

    print("\n2. Чужой Origin — отказ")
    status, body = call(port, headers={"Origin": "http://evil.example"})
    check(status == 403, "Origin: http://evil.example → 403", f"код {status}")
    check("источник" in reason_of(body).lower(),
          "причина отказа названа и она про источник", reason_of(body))
    status, body = call(port, headers={"Origin": "null"})
    check(status == 403, "Origin: null → 403", f"код {status}")
    status, _ = call(port, headers={"Origin": f"http://127.0.0.1:{port + 1}"})
    check(status == 403, "чужой порт в Origin → 403", f"код {status}")
    status, _ = call(port, headers={"Origin": f"https://127.0.0.1:{port}"})
    check(status == 403, "https-источник при http-панели → 403", f"код {status}")

    print("\n3. Чужой Host — отказ (это и есть DNS-rebinding)")
    status, body = call(port, headers={"Host": f"evil.example:{port}"})
    check(status == 403, "Host: evil.example:<порт> → 403", f"код {status}")
    check("Host" in reason_of(body), "причина отказа названа и она про Host",
          reason_of(body))
    status, _ = call(port, headers={"Host": f"localhost.evil.example:{port}"})
    check(status == 403, "Host: localhost.evil.example → 403", f"код {status}")
    status, _ = call(port, headers={"Host": f"127.0.0.1:{port + 1}"})
    check(status == 403, "чужой порт в Host → 403", f"код {status}")

    print("\n4. Граница действует до разбора маршрута")
    status, _ = call(port, path="/api/health", headers={"Host": f"evil.example:{port}"})
    check(status == 403, "известный путь с чужим Host → 403", f"код {status}")
    status, _ = call(port, path="/no-such-route", headers={"Host": f"evil.example:{port}"})
    check(status == 403, "НЕИЗВЕСТНЫЙ путь с чужим Host → 403, а не 404",
          f"код {status}")

    print("\n5. POST: граница раньше проверки доступа")
    status, body = call(port, method="POST", path="/api/auth/token",
                        headers={"Origin": "http://evil.example"}, body={})
    check(status == 403, "POST с чужим Origin → 403", f"код {status}")
    check("источник" in reason_of(body).lower(),
          "отказ пришёл от границы доверия, а не от проверки доступа",
          reason_of(body))
    status, body = call(port, method="POST", path="/api/auth/token",
                        headers={"Host": f"evil.example:{port}"}, body={})
    check(status == 403, "POST с чужим Host → 403", f"код {status}")
    status, body = call(port, method="POST", path="/api/auth/token", body={})
    check(status == 403 and "token rejected" in str(body),
          "POST из loopback доходит до проверки доступа (граница не мешает)",
          f"код {status}: {body}")
finally:
    server.shutdown()
    server.server_close()

print("\n6. Подкласс, переопределяющий do_GET (статика панели)")
server, port = serve(_StaticLike)
try:
    status, body = call(port, path="/style.css")
    check(status == 200 and body == "static-body",
          "свой источник: статика отдаётся", f"код {status}: {body}")
    status, body = call(port, path="/style.css", headers={"Host": f"evil.example:{port}"})
    check(status == 403, "чужой Host на статике → 403", f"код {status}: {body}")
    status, _ = call(port, path="/style.css", headers={"Origin": "http://evil.example"})
    check(status == 403, "чужой Origin на статике → 403", f"код {status}")
finally:
    server.shutdown()
    server.server_close()

print("\n7. Чистые функции: свойства без сети")
values = [("", 8777), ("127.0.0.1:8777", 8777), ("localhost:8777", 8777),
          ("[::1]:8777", 8777), ("evil.example:8777", 8777), ("localhost", 8777),
          ("127.0.0.1:9999", 8777), ("localhost.evil.example:8777", 8777)]
for value, port_given in values:
    problem = admin_server.host_problem(value, port_given)
    expected_ok = value in ("", "127.0.0.1:8777", "localhost:8777", "[::1]:8777")
    check((problem == "") == expected_ok,
          f"host_problem({value!r}) {'разрешён' if expected_ok else 'отклонён'}",
          problem)
origins = [("", True), ("http://127.0.0.1:8777", True), ("http://localhost:8777", True),
           ("http://evil.example:8777", False), ("null", False),
           ("http://127.0.0.1:9999", False), ("http://127.0.0.1", False)]
for value, expected_ok in origins:
    problem = admin_server.origin_problem(value, 8777)
    check((problem == "") == expected_ok,
          f"origin_problem({value!r}) {'разрешён' if expected_ok else 'отклонён'}",
          problem)

print()
print(f"проверок: {PASS + FAIL}, успешно: {PASS}, провалов: {FAIL}")
sys.exit(0 if FAIL == 0 else 1)
