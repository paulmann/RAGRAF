"""Живая проверка страницы `/sources` и её API.

Запускает панель на отдельном порту, проходит цикл «прочитать → добавить →
просканировать → удалить» и проверяет, что:
  1. страница отдаётся и содержит заголовок;
  2. список источников читается без токена;
  3. изменение без токена отклоняется;
  4. с токеном источник добавляется, скан считает файлы и секретные места;
  5. удаление убирает запись, но НЕ трогает файлы на диске;
  6. счётчики карты доходят до таблицы (файлы/секреты), а не теряются между
     реестром и картой.

Токен берётся из файла сессии и НЕ печатается.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
PORT = 8791
BASE = f"http://127.0.0.1:{PORT}"
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


def request(path: str, *, method: str = "GET", body: dict | None = None,
            panel_key: str = "") -> tuple[int, object]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if panel_key:
        req.add_header("X-RAGRAF-Token", panel_key)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            raw = response.read().decode("utf-8")
            try:
                return response.status, json.loads(raw)
            except json.JSONDecodeError:
                return response.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": str(exc)}


print("=" * 78)
print("ЗАПУСК ПАНЕЛИ")
print("=" * 78)
env = dict(os.environ)
env["PYTHONIOENCODING"] = "utf-8"
process = subprocess.Popen(
    [sys.executable, "tools/ragraf-admin.py", "--no-browser", f"--port={PORT}"],
    cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    text=True, encoding="utf-8", errors="replace", env=env,
)
live = False
try:
    for _ in range(60):
        time.sleep(0.5)
        status, _ = request("/api/health")
        if status == 200:
            live = True
            break
    check(live, "панель поднялась на отдельном порту", f"pid={process.pid}")
    if not live:
        raise SystemExit(1)

    key_file = REPO / ".ragraf" / "admin" / "session_key"
    panel_key = key_file.read_text(encoding="utf-8").strip() if key_file.exists() else ""
    check(bool(panel_key), "ключ сессии доступен для изменяющих запросов")

    print()
    print("=" * 78)
    print("СТРАНИЦА И ЧТЕНИЕ")
    print("=" * 78)
    status, page = request("/sources")
    check(status == 200, "страница /sources отвечает 200", str(status))
    check("Источники карты" in str(page), "страница содержит заголовок")
    status, value_page = request("/value")
    check(status == 200, "страница /value отвечает 200", str(status))
    check("Польза за 30 дней" in str(value_page), "страница пользы содержит заголовок")
    status, root_page = request("/")
    check(status == 200 and 'href="/sources"' in str(root_page)
          and 'href="/value"' in str(root_page),
          "главная панель ссылается на обе страницы", str(status))
    status, listed = request("/api/sources")
    check(status == 200 and isinstance(listed, dict) and listed.get("ok"),
          "список источников читается без токена", str(status))

    print()
    print("=" * 78)
    print("ИЗМЕНЕНИЕ ТРЕБУЕТ ТОКЕН")
    print("=" * 78)
    status, denied = request("/api/sources", method="POST",
                             body={"action": "add", "root": "C:\\"})
    check(status == 403, "без токена изменение отклонено", str(status))
    check(isinstance(denied, dict) and denied.get("ok") is False,
          "отказ имеет форму ok=false")

    print()
    print("=" * 78)
    print("ЦИКЛ: ДОБАВИТЬ → СКАН → УДАЛИТЬ")
    print("=" * 78)
    with tempfile.TemporaryDirectory(prefix="ragraf-admin-src-") as tmp:
        root = pathlib.Path(tmp) / "docs"
        root.mkdir(parents=True, exist_ok=True)
        (root / "notes.md").write_text("live", encoding="utf-8")
        (root / ".env").write_text("PLACEHOLDER=never-read", encoding="utf-8")
        status, added = request("/api/sources", method="POST", panel_key=panel_key,
                                body={"action": "add", "root": str(root),
                                      "note": "live-проверка"})
        source = (added or {}).get("result") if isinstance(added, dict) else None
        check(status == 200 and source and source.get("id"),
              "источник добавлен с токеном", str(status))
        source_id = (source or {}).get("id")

        status, scanned = request("/api/sources", method="POST", panel_key=panel_key,
                                  body={"action": "scan", "id": source_id})
        scans = ((scanned or {}).get("result") or {}).get("scans") or []
        first = scans[0] if scans else {}
        check(status == 200 and first.get("status") == "ok",
              "скан выполнен", str(first.get("status")))
        check(first.get("files") == 1, "файлов в карте: 1 (без секретного)", str(first.get("files")))
        check(first.get("secrets") == 1, "секретных мест: 1", str(first.get("secrets")))

        status, listed = request("/api/sources")
        rows = [row for row in (listed or {}).get("sources", [])
                if str(row.get("id")) == str(source_id)]
        check(bool(rows), "источник виден в списке")
        if rows:
            check(rows[0].get("files") == 1, "счётчик файлов дошёл до таблицы",
                  str(rows[0].get("files")))
            check(rows[0].get("secrets") == 1, "счётчик секретов дошёл до таблицы",
                  str(rows[0].get("secrets")))

        status, removed = request("/api/sources", method="POST", panel_key=panel_key,
                                  body={"action": "remove", "id": source_id})
        check(status == 200 and isinstance(removed, dict) and removed.get("ok"),
              "источник удалён", str(status))
        check((root / ".env").exists(), "файлы на диске не тронуты удалением")
finally:
    try:
        process.terminate()
        process.wait(timeout=10)
    except Exception:  # noqa: BLE001
        try:
            process.kill()
        except Exception:  # noqa: BLE001
            pass

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
