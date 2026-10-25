"""Живая проверка страницы `/knowledge` и её Markdown-документов.

Страница — единственное место, откуда владелец копирует мастер-промт и research
контура «Знания агентов». Проверка запускает панель на отдельном порту и
подтверждает то, что легко сломать молча:

  1. `/knowledge` отдаётся без токена и содержит заголовок и кнопки копирования;
  2. все четыре Markdown-документа отдаются с типом `text/markdown` и непусты;
  3. закрытый список файлов: чужой путь и обход каталога дают 404;
  4. главная панель ссылается на страницу (иначе её не найти);
  5. документы читаются как UTF-8 и содержат ожидаемые разделы.

Токен не печатается и не нужен: страница только читает файлы.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
PORT = 8792
BASE = f"http://127.0.0.1:{PORT}"
PASS = 0
FAIL = 0

DOCUMENTS = {
    "prompt.md": "Мастер-промт",
    "research.md": "Research",
    "short-prompt.md": "Короткий промт",
    "readme.md": "Контур «Знания агентов»",
}


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


def request(path: str) -> tuple[int, str, str]:
    req = urllib.request.Request(BASE + path)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return (
                response.status,
                response.read().decode("utf-8", errors="replace"),
                str(response.headers.get("Content-Type") or ""),
            )
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace"), ""
    except Exception as exc:  # noqa: BLE001
        return 0, json.dumps({"error": str(exc)}), ""


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
        status, _, _ = request("/api/health")
        if status == 200:
            live = True
            break
    check(live, "панель поднялась на отдельном порту", f"pid={process.pid}")
    if not live:
        raise SystemExit(1)

    print()
    print("=" * 78)
    print("СТРАНИЦА")
    print("=" * 78)
    status, page, content_type = request("/knowledge")
    check(status == 200, "страница /knowledge отвечает 200", str(status))
    check("text/html" in content_type, "страница отдаётся как HTML", content_type)
    check("Знания агентов" in page, "страница содержит заголовок")
    check('data-copy-doc="prompt"' in page, "есть кнопка копирования полного промта")
    check('id="start-phrase"' in page, "есть стартовая фраза для новой сессии")
    check('data-panel="checklist"' in page, "есть чек-лист критериев приёмки")

    print()
    print("=" * 78)
    print("ДОКУМЕНТЫ")
    print("=" * 78)
    for name, marker in DOCUMENTS.items():
        status, text, content_type = request(f"/knowledge/{name}")
        check(status == 200, f"/knowledge/{name} отвечает 200", str(status))
        check("text/markdown" in content_type,
              f"/knowledge/{name}: тип text/markdown", content_type)
        check(len(text) > 1000, f"/knowledge/{name}: документ непуст",
              str(len(text)))
        check(marker in text, f"/knowledge/{name}: найден ожидаемый раздел")

    print()
    print("=" * 78)
    print("ЗАКРЫТЫЙ СПИСОК И ССЫЛКА")
    print("=" * 78)
    status, _, _ = request("/knowledge/secret.md")
    check(status == 404, "неизвестный документ даёт 404", str(status))
    status, _, _ = request("/knowledge/%2e%2e/src/ragraf/admin_server.py")
    check(status == 404, "обход каталога через %2e%2e даёт 404", str(status))
    status, root_page, _ = request("/")
    check(status == 200 and 'href="/knowledge"' in root_page,
          "главная панель ссылается на страницу знаний")

    print()
    print("=" * 78)
    print("КОДИРОВКА")
    print("=" * 78)
    for name in DOCUMENTS:
        path = REPO / "admin" / "knowledge" / name
        raw = path.read_bytes()
        check(not raw.startswith(b"\xef\xbb\xbf"), f"{name}: нет BOM")
        check(b"\r\n" not in raw, f"{name}: переводы строк LF")
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
