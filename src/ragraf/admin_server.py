"""Интерфейс администратора RAGRAF: локальный веб-сервер без зависимостей.

Стек и почему такой
    Проект ставится на машину разработчика и не должен тянуть библиотеки.
    Поэтому сервер построен на стандартном `http.server` из Python — ноль
    зависимостей, ноль сборки, запуск одной командой. То же решение принято
    для самого RAGRAF (SQLite вместо сервера, tree-sitter вместо компилятора):
    инструмент обязан работать там, где есть только Python и код.

Безопасность
    * Слушается **только 127.0.0.1** — наружу интерфейс не доступен по замыслу.
      Это интерфейс администратора локальной машины, а не сетевая панель.
    * Токен доступа обязателен для изменяющих запросов: он создаётся при старте
      и печатается в вывод. Так случайная вкладка браузера не может изменить
      настройки, даже если сервер запущен.
    * Только POST для изменений: ни одна настройка не меняется от перехода
      по ссылке (защита от случайного клика и предзагрузки браузером).
    * Секретов в интерфейсе нет: RAGRAF не хранит паролей, реквизиты баз
      берутся из переменных окружения.

Что показывает
    * разработки: ревизии, контрольные суммы, состояние (актуально или нет);
    * размеры баз и их состав по таблицам;
    * статистику использования: обращения, факты со ссылками, оценка экономии;
    * качество индекса (блок quality): нормы векторов, размеры чанков, сигнатуры;
    * настройки: пороги авто-реиндексации и параметры индексации.

Запуск
    python tools/ragraf-admin.py --port 8777
"""

from __future__ import annotations

import json
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# Корень репозитория: файл лежит в src/ragraf/, значит корень — на два уровня выше.
# Раньше стояло parents[1], и каталог статики искался в src/admin/static —
# поэтому стили и скрипт отдавали 404 (найдено проверкой tools/check_admin.py).
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from ragraf import catalog as catalog_mod  # noqa: E402
from ragraf import settings as settings_mod  # noqa: E402
from ragraf import store, util  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

# Токен сессии: создаётся при запуске и печатается администратору.
ACCESS_TOKEN = secrets.token_urlsafe(24)

# Кэш тяжёлых сведений: сбор занимает время, а страница обновляется часто.
_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 5.0


def _cached(key: str, factory) -> Any:
    """Вернуть значение из кэша или собрать заново. Ошибки не кэшируются."""
    import time

    now = time.time()
    hit = _CACHE.get(key)
    if hit and (now - hit[0]) < _CACHE_TTL:
        return hit[1]
    value = factory()
    _CACHE[key] = (now, value)
    return value


def collect_catalog() -> dict[str, Any]:
    """Разработки с состоянием: ревизия, сумма, совпадение, размеры."""
    catalog = catalog_mod.Catalog()
    summary = catalog.summary()
    for row in summary["entries"]:
        row["hash_matches"] = None
        row["size"] = {}
        entry = catalog_mod.Entry.from_dict(row)
        if not entry.root or not entry.config:
            continue
        cfg_path = Path(entry.config)
        if not cfg_path.exists():
            row["check_error"] = "конфиг не найден"
            continue
        try:
            cfg = load_config(cfg_path)
            # Сверка контрольной суммы БЕЗ индексации: интерфейс только читает.
            from ragraf import watch as watch_mod

            now_hash, now_config, files_now, _ = watch_mod.current_hashes(cfg)
            row["hash_matches"] = bool(entry.corpus_hash and now_hash == entry.corpus_hash)
            row["files_now"] = files_now
            row["config_matches"] = bool(
                not entry.config_hash or now_config == entry.config_hash
            )
            row["size"] = storage_sizes(cfg)
        except Exception as exc:
            row["check_error"] = type(exc).__name__
    return summary


def storage_sizes(cfg) -> dict[str, Any]:
    """Размеры баз разработки и состав по таблицам.

    Размер считается по файлам SQLite — это то, что занимает место на диске.
    Состав по таблицам нужен, чтобы видеть, что именно растёт: граф, лексика
    или журнал.
    """
    out: dict[str, Any] = {"files": [], "tables": [], "total_bytes": 0}
    targets = [
        ("граф кода", cfg.graph_db),
        ("лексика и векторы", cfg.lexvec_db),
        ("журнал и телеметрия", cfg.control_db),
    ]
    for title, path in targets:
        try:
            size = Path(path).stat().st_size
            # WAL-файл тоже занимает место и часто больше основного.
            wal = Path(str(path) + "-wal")
            wal_size = wal.stat().st_size if wal.exists() else 0
            out["files"].append({
                "title": title,
                "path": util.to_posix(path),
                "bytes": size + wal_size,
                "wal_bytes": wal_size,
            })
            out["total_bytes"] += size + wal_size
        except OSError:
            out["files"].append({"title": title, "path": util.to_posix(path),
                                 "bytes": 0, "missing": True})

    try:
        conn = store.connect(cfg.graph_db, readonly=True)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        for r in rows:
            name = str(r["name"])
            # FTS5 создаёт служебные таблицы: их считать не нужно, иначе
            # список превращается в шум.
            if name.endswith(("_data", "_idx", "_content", "_docsize", "_config")):
                continue
            try:
                count = conn.execute(f"SELECT COUNT(*) AS c FROM \"{name}\"").fetchone()["c"]
            except Exception:
                continue
            out["tables"].append({"name": name, "rows": int(count or 0)})
        conn.close()
    except Exception as exc:
        out["tables_error"] = type(exc).__name__
    return out


def collect_usage(cfg) -> dict[str, Any]:
    """Статистика использования: обращения, факты, оценка экономии.

    Возвращается в трёх разрезах, как просил владелец:
      * общая по всем агентам (поля верхнего уровня);
      * по каждому агенту (`by_agent`) и по семействам (`by_agent_kind`).

    Фильтры здесь не применяются: панель показывает общую картину,
    а выбор конкретного агента делает клиент по полю `by_agent`.
    """
    try:
        from ragraf.facade import Facade

        # Панель представляется своим именем: иначе её собственные
        # обращения (чтение статистики и состояния) попадали бы в журнал
        # как `unknown` и искажали картину работы агентов.
        facade = Facade(cfg, tool="admin", telemetry=True, watch=False,
                        agent="RAGRAF-Admin", agent_kind="internal")
        try:
            return facade.usage_summary(since_session=False)
        finally:
            facade.close()
    except Exception as exc:
        return {"error": type(exc).__name__}


def collect_quality(cfg) -> dict[str, Any]:
    """Качество индекса: нормы векторов, чанки, сигнатуры."""
    try:
        from ragraf.facade import Facade

        facade = Facade(cfg, tool="admin", telemetry=False, watch=False,
                        agent="RAGRAF-Admin", agent_kind="internal")
        try:
            status = facade.op_index_status()
            result = status.get("result") or {}
            # Состояние вычислителя показывается рядом с качеством: если векторы
            # считаются не там, где ожидал администратор, это видно сразу.
            compute: dict[str, Any] = {}
            try:
                compute = facade.embedder.compute_status().to_dict()
            except Exception as exc:
                compute = {"error": type(exc).__name__}
            return {
                "quality": result.get("quality") or {},
                "counts": status.get("counts") or {},
                "rev": (result.get("rev") or {}).get("rev_id"),
                "capability": status.get("capability"),
                "stages": result.get("stages") or [],
                "compute": compute,
            }
        finally:
            facade.close()
    except Exception as exc:
        return {"error": type(exc).__name__}


def build_state() -> dict[str, Any]:
    """Полное состояние для интерфейса. Одна функция — одна точка сбора."""

    def factory() -> dict[str, Any]:
        catalog = collect_catalog()
        entries = catalog.get("entries") or []
        detail: dict[str, Any] = {}
        for row in entries:
            name = str(row.get("category") or "")
            cfg_path = Path(str(row.get("config") or ""))
            if not cfg_path.exists():
                continue
            try:
                cfg = load_config(cfg_path)
            except Exception:
                continue
            detail[name] = {
                "usage": collect_usage(cfg),
                "quality": collect_quality(cfg),
            }
        return {
            "catalog": catalog,
            "detail": detail,
            "settings": settings_mod.state(
                {str(e.get("category")): e for e in entries}
            ).to_dict(),
            "ragraf_root": util.to_posix(REPO),
        }

    return _cached("state", factory)


class Handler(BaseHTTPRequestHandler):
    """Обработчик запросов интерфейса.

    Разделение простое: GET отдаёт данные, POST меняет настройки. Любое
    изменение требует токен — он приходит в заголовке или в теле.
    """

    server_version = "RAGRAF-admin/1.0"

    # ---------------- служебное ----------------

    def log_message(self, fmt: str, *args: Any) -> None:
        """Логи пишутся только в stderr: в консоли полезно видеть запросы."""
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send_json(self, payload: dict[str, Any], code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, code: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(min(length, 1_000_000))
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _token_ok(self, payload: dict[str, Any]) -> bool:
        """Проверка токена изменяющих запросов."""
        given = str(self.headers.get("X-RAGRAF-Token") or payload.get("token") or "")
        # Сравнение постоянного времени: обычное == раскрывает префикс по времени.
        return bool(given) and secrets.compare_digest(given, ACCESS_TOKEN)

    # ---------------- GET ----------------

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/":
            self._send_html(PAGE_HTML)
            return
        if path == "/api/state":
            self._send_json({"ok": True, "state": build_state()})
            return
        if path == "/api/token":
            # Токен отдаётся только локальному клиенту: сервер слушает 127.0.0.1,
            # поэтому доступ к этой точке уже ограничен машиной.
            self._send_json({"ok": True, "token": ACCESS_TOKEN, "port": self.server.server_port})
            return
        if path == "/api/settings":
            catalog = catalog_mod.Catalog()
            entries = {str(e.get("category")): e for e in catalog.summary()["entries"]}
            self._send_json({"ok": True, "settings": settings_mod.state(entries).to_dict()})
            return
        if path == "/api/health":
            self._send_json({"ok": True, "version": "1.0", "root": util.to_posix(REPO)})
            return
        self._send_json({"ok": False, "error": "не найдено"}, 404)

    # ---------------- POST ----------------

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        payload = self._read_body()

        if not self._token_ok(payload):
            self._send_json(
                {"ok": False, "error": "неверный или отсутствующий токен доступа "
                                       "(токен печатается при запуске сервера)"},
                403,
            )
            return

        if path == "/api/settings/global":
            values = payload.get("values") or {}
            try:
                saved = settings_mod.SettingsStore().save(values)
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
                return
            _CACHE.clear()
            self._send_json({"ok": True, "settings": saved,
                             "note": "настройки применятся при следующей проверке "
                                     "свежести; текущая ревизия не меняется"})
            return

        if path == "/api/settings/project":
            name = str(payload.get("category") or "")
            values = payload.get("values") or {}
            catalog = catalog_mod.Catalog()
            entry = catalog.get(name)
            if entry is None:
                self._send_json({"ok": False, "error": f"разработка не найдена: {name}"}, 404)
                return
            cfg_path = Path(entry.config)
            if not cfg_path.exists():
                self._send_json({"ok": False, "error": "конфиг разработки не найден"}, 404)
                return
            try:
                written = settings_mod.write_project_settings(cfg_path, values)
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
                return
            except OSError as exc:
                self._send_json({"ok": False, "error": f"не удалось записать: {exc}"}, 500)
                return
            _CACHE.clear()
            self._send_json({"ok": True, "written": written, "config": util.to_posix(cfg_path)})
            return

        if path == "/api/cache/clear":
            _CACHE.clear()
            self._send_json({"ok": True, "note": "кэш интерфейса очищен"})
            return

        self._send_json({"ok": False, "error": "не найдено"}, 404)


PAGE_HTML = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RAGRAF — управление</title>
<link rel="stylesheet" href="/style.css">
</head>
<body>
<header class="topbar">
  <div class="brand">
    <span class="brand__mark" aria-hidden="true"></span>
    <span class="brand__name">RAGRAF</span>
    <span class="brand__sub">панель управления</span>
  </div>
  <div class="topbar__actions">
    <span class="status" id="status">загрузка…</span>
    <button class="btn btn--ghost" id="reload" type="button">Обновить</button>
  </div>
</header>
<main id="app" class="layout">
  <p class="muted" id="loading">Собираю сведения о разработках…</p>
</main>
<script src="/app.js"></script>
</body>
</html>
"""


def main(argv: list[str]) -> int:
    port = 8777
    open_browser = True
    for arg in argv:
        if arg.startswith("--port="):
            port = int(arg.split("=", 1)[1])
        elif arg == "--no-browser":
            open_browser = False

    # Статика отдаётся из пакета: интерфейс должен работать без установки файлов.
    static = REPO / "admin" / "static"

    def build_handler() -> type[Handler]:
        class StaticHandler(Handler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path in ("/style.css", "/app.js"):
                    name = parsed.path.lstrip("/")
                    path = static / name
                    if path.exists():
                        body = path.read_bytes()
                        self.send_response(200)
                        self.send_header(
                            "Content-Type",
                            "text/css; charset=utf-8" if name.endswith(".css")
                            else "application/javascript; charset=utf-8",
                        )
                        self.send_header("Content-Length", str(len(body)))
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        self.wfile.write(body)
                        return
                super().do_GET()

        return StaticHandler

    server = ThreadingHTTPServer(("127.0.0.1", port), build_handler())
    url = f"http://127.0.0.1:{port}/"
    print("=" * 68)
    print("RAGRAF — интерфейс управления запущен")
    print(f"адрес:        {url}")
    print(f"токен доступа: {ACCESS_TOKEN}")
    print("доступ только с этой машины (127.0.0.1): сетевого доступа нет по замыслу")
    print("остановка: Ctrl+C")
    print("=" * 68)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nостановлен")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
