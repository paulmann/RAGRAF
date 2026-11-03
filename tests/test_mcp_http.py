# -*- coding: utf-8 -*-
"""Живая проверка MCP по Streamable HTTP: протокол, доступ, журнал.

Запуск: python tests/test_mcp_http.py

Порт не фиксирован. Замерено 02.11.2026: порт 8795 держал в состоянии Bound
сторонний процесс (`avp`, антивирус), и `bind` падал с `WinError 10013` — набор
краснел по чужой причине, а не по дефекту продукта. Поэтому порт выбирается
проверкой свободных из диапазона и печатается в отчёте: провал по занятому
порту маскировал бы настоящий.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]


def pick_port(start: int = 8795, tries: int = 20) -> int:
    """Первый свободный порт диапазона: фиксированный может быть занят чужим."""
    import socket

    for candidate in range(start, start + tries):
        probe = socket.socket()
        try:
            probe.bind(("127.0.0.1", candidate))
        except OSError:
            continue
        finally:
            probe.close()
        return candidate
    raise SystemExit(f"нет свободного порта в диапазоне {start}..{start + tries - 1}: "
                     "проверка невозможна, причина внешняя")


PORT = pick_port()
BASE = f"http://127.0.0.1:{PORT}"
PASSED = 0
FAILED = 0


def check(ok: bool, label: str, detail: str = "") -> bool:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  [OK ] {label}")
    else:
        FAILED += 1
        print(f"  [FAIL] {label}")
        if detail:
            print(f"         {detail}")
    return ok


def call(path: str, *, method: str = "GET", body: dict | None = None,
         headers: dict | None = None) -> tuple[int, dict, dict]:
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8", "replace")
            out = json.loads(raw) if raw.strip().startswith("{") else {}
            return resp.status, out, dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw), dict(exc.headers)
        except Exception:
            return exc.code, {}, dict(exc.headers)


def main() -> int:
    # Выдача доступа: проверка пользуется тем же хранилищем, что и панель.
    sys.path.insert(0, str(REPO / "src"))
    from ragraf import access as access_mod

    store = access_mod.TokenStore()
    # Снимок живого хранилища: проверка обязана вернуть файл БАЙТ В БАЙТ.
    # До этапа 11 она удаляла свои записи токенов, но не строки журнала,
    # которые сервер дописывал в тот же файл, поэтому после каждого прогона
    # `check_all` сообщал «ЖИВОЕ СОСТОЯНИЕ ИЗМЕНЕНО наборами: access_tokens.json»
    # и завершался кодом 1. Тест, меняющий живое состояние, нельзя считать
    # пройденным: он оставляет след, которого не было до него.
    snapshot = store.path.read_bytes() if store.path.exists() else None
    # Убираем прошлые проверочные записи, чтобы прогон был повторяемым.
    for record in store.all():
        if record.label.startswith("ProbeHTTP"):
            store.delete(record.token_id)
    record, value = store.create("ProbeHTTP-HARNESS", note="живая проверка HTTP")
    record2, value2 = store.create("ProbeHTTP-Revoked", note="будет отозван")
    store.revoke(record2.token_id)

    out_path = REPO / "tests" / "test_mcp_http_out.txt"
    log = open(out_path, "w", encoding="utf-8")  # noqa: SIM115
    proc = subprocess.Popen(
        [sys.executable, "-u", str(REPO / "tools" / "ragraf-mcp-http.py"),
         f"--port={PORT}", "--project", str(REPO / "config" / "ragraf.yaml"),
         "--agent", "ProbeHTTP", "--agent-kind", "external"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", cwd=str(REPO),
    )
    try:
        ready = False
        deadline = time.time() + 40
        while time.time() < deadline:
            line = proc.stdout.readline()
            if line:
                log.write(line)
                if "слушает" in line:
                    ready = True
                    break
            else:
                time.sleep(0.2)
        check(ready, "сервер поднялся и сообщил адрес")

        print("\n=== диагностика ===")
        code, data, _ = call("/healthz")
        check(code == 200 and data.get("alive") is True, "/healthz отвечает",
              f"код={code} {data}")
        code, data, _ = call("/readyz")
        check(code == 200 and data.get("ready") is True, "/readyz подтверждает готовность",
              f"код={code} {data}")
        raw = json.dumps(data, ensure_ascii=False)
        check("C:" not in raw and "sqlite" not in raw.lower(),
              "диагностика не раскрывает путей и хранилищ", raw[:160])

        print("\n=== доступ ===")
        code, data, _ = call("/mcp", method="POST",
                             body={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        check(code == 401, "без заголовка доступа — отказ", f"код={code} {data}")
        code, data, _ = call("/mcp", method="POST",
                             body={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                             headers={"Authorization": "Bearer ragraf_" + "nope"})
        check(code == 401, "неверное значение доступа — отказ", f"код={code}")
        code, data, _ = call("/mcp", method="POST",
                             body={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                             headers={"Authorization": f"Bearer {value2}"})
        check(code == 401, "отозванный доступ не работает", f"код={code}")

        print("\n=== протокол MCP ===")
        auth = {"Authorization": f"Bearer {value}"}
        code, data, headers = call("/mcp", method="POST", headers=auth, body={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05",
                       "clientInfo": {"name": "ProbeHTTP"}, "capabilities": {}}})
        check(code == 200 and data.get("result", {}).get("serverInfo", {}).get("name")
              == "ragraf", "initialize отвечает", f"код={code} {data}")
        sid = headers.get("Mcp-Session-Id") or ""
        check(bool(sid), "сервер выдал идентификатор сессии", f"заголовки={list(headers)}")

        code, data, _ = call("/mcp", method="POST", headers=auth,
                             body={"jsonrpc": "2.0", "method":
                                   "notifications/initialized"})
        check(code == 202, "уведомление принято без тела ответа", f"код={code}")

        code, data, _ = call("/mcp", method="POST", headers=auth,
                             body={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [t.get("name") for t in
                 (data.get("result") or {}).get("tools", [])]
        check(len(names) >= 24, f"tools/list отдаёт набор инструментов ({len(names)})")

        code, data, _ = call("/mcp", method="POST", headers=auth,
                             body={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                   "params": {"name": "ragraf_index_status",
                                              "arguments": {}}})
        content = (data.get("result") or {}).get("content") or []
        head = content[0].get("text", "").splitlines()[0] if content else ""
        check("rev=" in head, "вызов инструмента вернул факт", f"ответ: {head!r}")

        code, data, _ = call("/mcp", method="POST", headers=auth,
                             body={"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                   "params": {"name": "ragraf_use_project",
                                              "arguments": {"project": "revoap.com"}}})
        text = ((data.get("result") or {}).get("content") or [{}])[0].get("text", "")
        check("op=use_project" in text, "переключение корпуса работает по HTTP",
              text.splitlines()[0] if text else "")

        print("\n=== ограничения ===")
        code, data, _ = call("/mcp", method="POST", headers=auth,
                             body={"jsonrpc": "2.0", "id": 5, "method": "tools/list"},
                             )
        code_origin, data_origin, _ = call(
            "/mcp", method="POST", headers={**auth, "Origin": "https://evil.example"},
            body={"jsonrpc": "2.0", "id": 6, "method": "tools/list"})
        check(code_origin in (200, 403),
              "посторонний Origin обрабатывается предсказуемо",
              f"код={code_origin} (список источников не задан — пропуск допустим)")
        code, data, _ = call("/mcp", method="POST", headers=auth,
                             body={"jsonrpc": "2.0", "id": 7, "method": "tools/list",
                                   "padding": "x" * 10})
        check(code == 200, "умеренное тело принято", f"код={code}")
        code, data, _ = call("/mcp", method="POST", headers=auth,
                             body={"jsonrpc": "2.0", "id": 8, "method": "нет_такого"})
        check("error" in (data or {}), "неизвестный метод даёт ошибку протокола",
              json.dumps(data, ensure_ascii=False)[:160])

        print("\n=== журнал доступа: кто и с каким результатом ===")
        from ragraf.config import load as load_config

        cfg = load_config(REPO / "config" / "ragraf.yaml")
        code, data, _ = call("/healthz")
        # Журнал читается тем же модулем, что пишет (единая точка).
        import sqlite3

        conn = sqlite3.connect(str(cfg.control_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT label, result, COUNT(*) AS n FROM admin_access_log"
            " WHERE surface = 'mcp-http' AND label LIKE 'ProbeHTTP%'"
            " GROUP BY label, result ORDER BY n DESC").fetchall()
        labels = {r["label"] for r in rows}
        check("ProbeHTTP-HARNESS" in labels,
              "в журнале видно, чьим доступом работали",
              f"метки: {labels}")
        results = {r["result"] for r in rows}
        check("ok" in results and "denied" in results,
              "журнал содержит и удачи, и отказы", f"результаты: {results}")
        denied = conn.execute(
            "SELECT COUNT(*) AS n FROM admin_access_log"
            " WHERE surface = 'mcp-http' AND result = 'denied'").fetchone()["n"]
        check(int(denied) >= 2, "отказы записаны отдельно", f"отказов: {denied}")

    finally:
        proc.terminate()
        try:
            log.write(proc.stdout.read() or "")
        except Exception:
            pass
        log.close()
        for record in (record, record2):
            try:
                store.delete(record.token_id)
            except Exception:
                pass
        # Возврат снимка: содержимое файла обязано совпасть побайтово.
        try:
            if snapshot is None:
                if store.path.exists():
                    store.path.unlink()
            else:
                store.path.write_bytes(snapshot)
                access_mod._restrict(store.path)  # noqa: SLF001 — права те же, что у хранилища
            now = store.path.read_bytes() if store.path.exists() else None
            restored = (now == snapshot)
        except Exception as exc:  # noqa: BLE001 — сообщаем, а не глотаем
            restored = False
            print(f"  [FAIL] живое хранилище не восстановлено: {exc}")
        check(restored, "живое хранилище токенов возвращено как было")

    print("\n" + "=" * 78)
    print(f"ИТОГ: пройдено {PASSED}, провалено {FAILED}")
    print("=" * 78)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
