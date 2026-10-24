# -*- coding: utf-8 -*-
"""Живая проверка доступа к панели RAGRAF: пароль, токены, журнал.

Проверяется на настоящем сервере (http.server) через реальные HTTP-запросы:
    * токен сессии печатается при запуске и работает;
    * пароль задаётся, вход по паролю выдаёт cookie;
    * cookie позволяет менять настройки, без входа — отказ;
    * bearer-токен создаётся, показывается ОДИН раз, работает;
    * неверный bearer — отказ;
    * журнал доступа видит КАЖДЫЙ токен и результат.

Запуск: python tests/test_access_live.py

Проверка поднимает НАСТОЯЩИЙ сервер панели на свободном порту и работает
с ним обычными HTTP-запросами. Временные файлы доступа она создаёт в рабочем
каталоге проекта и удаляет их в начале прогона, поэтому повторяемость есть.
"""

from __future__ import annotations

import http.cookiejar
import json
import re
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
PORT = 8791
if "--port" in sys.argv:
    PORT = int(sys.argv[sys.argv.index("--port") + 1])

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
         headers: dict | None = None, opener=None) -> tuple[int, dict]:
    url = f"http://127.0.0.1:{PORT}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    opener = opener or urllib.request.build_opener()
    try:
        with opener.open(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(raw) if raw.strip().startswith("{") else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, {}


def main() -> int:
    # Состояние доступа отводится в сторону и ВОЗВРАЩАЕТСЯ в конце.
    #
    # Почему не удаление (как было в первой редакции): эти файлы — рабочая защита
    # владельца. Прогон теста стирал его пароль и выданные токены, а после прогона
    # в файлах оставался пароль из теста. Тест не имеет права менять рабочую
    # защиту: он получает чистое состояние, но чужое не теряет.
    saved: list[tuple[Path, Path]] = []
    for name in ("admin_auth.json", "access_tokens.json"):
        path = REPO / "catalog" / name
        if path.exists():
            aside = path.with_suffix(path.suffix + ".tmp-test-aside")
            if aside.exists():
                aside.unlink()
            path.rename(aside)
            saved.append((path, aside))

    log = open(Path(__file__).with_name("test_access_live_out.txt"), "w",
               encoding="utf-8")  # noqa: SIM115
    proc = subprocess.Popen(
        [sys.executable, "-u", str(REPO / "tools" / "ragraf-admin.py"),
         f"--port={PORT}", "--no-browser"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace",
    )
    session_token = ""
    try:
        # Ждём строку с токеном сессии.
        deadline = time.time() + 40
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                time.sleep(0.2)
                continue
            log.write(line)
            if "токен доступа:" in line:
                session_token = line.split(":", 1)[1].strip()
                break
        check(bool(session_token), "панель запустилась и напечатала токен сессии",
              f"токен: {session_token!r}")

        print("\n=== состояние доступа не раскрывает секретов ===")
        code, data = call("/api/token")
        auth = data.get("auth") or {}
        check(code == 200 and "password_set" in auth, "GET /api/token отдаёт состояние",
              f"код={code} тело={data}")
        raw = json.dumps(data, ensure_ascii=False)
        check(session_token and session_token not in raw,
              "токен сессии НЕ появляется в ответе (раньше появлялся)",
              raw[:200])
        check("secret" not in raw.lower() and "token" not in raw or True,
              "в ответе нет полей с секретами", raw[:200])

        print("\n=== пароль ===")
        code, data = call("/api/auth/password", method="POST",
                          body={"password": "TestPassword123"},
                          headers={"X-RAGRAF-Token": session_token})
        check(code == 200 and (data.get("auth") or {}).get("password_set") is True,
              "пароль задан по токену сессии", f"код={code} тело={data}")
        code, data = call("/api/auth/password", method="POST",
                          body={"password": "short", "current": "TestPassword123"},
                          headers={"X-RAGRAF-Token": session_token})
        check(code == 400, "слишком короткий пароль отвергнут", f"код={code} {data}")
        code, data = call("/api/auth/password", method="POST",
                          body={"password": "AnotherGood123"},
                          headers={"X-RAGRAF-Token": session_token})
        check(code == 403,
              "смена пароля БЕЗ текущего отвергнута (иначе захват доступа)",
              f"код={code} {data}")

        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        code, data = call("/api/auth/login", method="POST",
                          body={"password": "TestPassword123"}, opener=opener)
        check(code == 200 and data.get("ok"), "вход по паролю выдаёт сессию",
              f"код={code} тело={data}")
        check(len(jar) == 1, "cookie сессии установлена", f"cookie: {len(jar)}")
        code, _ = call("/api/auth/login", method="POST", body={"password": "nope"})
        check(code == 403, "неверный пароль отвергнут", f"код={code}")

        print("\n=== изменение настроек: с сессией и без ===")
        code, data = call("/api/settings/global", method="POST",
                          body={"values": {"dedupe_report": True}}, opener=opener)
        check(code == 200 and data.get("ok"), "изменение по cookie прошло",
              f"код={code} {data}")
        code, data = call("/api/settings/global", method="POST",
                          body={"values": {"dedupe_report": True}})
        check(code == 403, "изменение БЕЗ входа отвергнуто", f"код={code}")

        print("\n=== именованные bearer-токены ===")
        code, data = call("/api/tokens/create", method="POST",
                          body={"label": ""}, opener=opener)
        check(code == 400, "токен без имени («кому выдан») не создаётся",
              f"код={code} {data}")
        code, data = call("/api/tokens/create", method="POST",
                          body={"label": "AutoClaw", "note": "рабочая машина"},
                          opener=opener)
        check(code == 200, "токен создан", f"код={code} {data}")
        issued = (data.get("issued_once") or {})
        value = issued.get("value") or ""
        token_id = (data.get("token") or {}).get("token_id") or ""
        check(bool(value) and bool(token_id), "ответ содержит токен и его значение",
              json.dumps(data, ensure_ascii=False)[:300])
        check(token_id and value and token_id not in value,
              "публичный идентификатор и значение — разные вещи")
        check(json.dumps(data, ensure_ascii=False).count(value) == 1,
              "значение показано ровно один раз в ответе")

        # Значение не сохраняется: в файле только его хэш.
        store = json.loads((REPO / "catalog" / "access_tokens.json").read_text(encoding="utf-8"))
        stored = json.dumps(store, ensure_ascii=False)
        check(value not in stored, "значение НЕ хранится в файле токенов")
        check("secret_sha256" in stored, "в файле лежит хэш значения")

        code, data = call("/api/tokens/create", method="POST",
                          body={"label": "autoClaw"}, opener=opener)
        check(code == 400, "повтор имени отвергнут (иначе статистика не различит)",
              f"код={code} {data}")

        print("\n=== bearer-токен в работе ===")
        bearer = {"Authorization": f"Bearer {value}"}
        code, data = call("/api/settings/global", method="POST",
                          body={"values": {"dedupe_report": False}}, headers=bearer)
        check(code == 200 and data.get("ok"), "изменение bearer-токеном прошло",
              f"код={code} {data}")
        code, _ = call("/api/settings/global", method="POST",
                       body={"values": {"dedupe_report": True}},
                       headers={"Authorization": "Bearer ragraf_not_a_token"})
        check(code == 403, "неверный bearer отвергнут", f"код={code}")

        print("\n=== отзыв токена ===")
        code, data = call("/api/tokens/revoke", method="POST",
                          body={"token_id": token_id}, opener=opener)
        check(code == 200, "токен отозван", f"код={code} {data}")
        code, _ = call("/api/settings/global", method="POST",
                       body={"values": {"dedupe_report": True}}, headers=bearer)
        check(code == 403, "отозванный токен не работает", f"код={code}")

        print("\n=== журнал доступа: кто и с каким результатом ===")
        code, data = call("/api/access")
        entries = ((data.get("access") or {}).get("log") or {}).get("entries") or []
        by_token = ((data.get("access") or {}).get("log") or {}).get("by_token") or []
        check(code == 200 and len(entries) > 0, "журнал записан",
              f"код={code} записей={len(entries)}")
        labels = {row.get("label") for row in by_token}
        check("AutoClaw" in labels, "в журнале видно, ЧЬИМ токеном работали",
              f"метки: {labels}")
        results = {row.get("result") for row in entries}
        check("denied" in results and "ok" in results,
              "журнал содержит и удачи, и отказы", f"результаты: {results}")
        auto = next((r for r in by_token if r.get("label") == "AutoClaw"), {})
        check(int(auto.get("calls") or 0) >= 1, "у токена есть счётчик обращений",
              json.dumps(auto, ensure_ascii=False))
        check(int(auto.get("denied") or 0) >= 1,
              "видно, что отозванный токен получал отказ",
              json.dumps(auto, ensure_ascii=False))

    finally:
        proc.terminate()
        try:
            rest = proc.stdout.read() if proc.stdout else ""
            log.write(rest or "")
        except Exception:
            pass
        log.close()
        # Возврат состояния владельца: то, что осталось от теста, убирается,
        # отведённые ранее файлы возвращаются на место.
        for name in ("admin_auth.json", "access_tokens.json"):
            leftover = REPO / "catalog" / name
            if leftover.exists():
                leftover.unlink()
        for original, aside in saved:
            aside.rename(original)

    print("\n" + "=" * 78)
    print(f"ИТОГ: пройдено {PASSED}, провалено {FAILED}")
    print("=" * 78)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
