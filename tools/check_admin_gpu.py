"""Проверка: видит ли панель администратора настройки видеокарты.

Проверяется через HTTP, то есть тем же путём, каким её видит администратор:
  1. настройки видеокарты присутствуют в общих настройках;
  2. они же видны в разрезе разработок, с указанием источника значения;
  3. состояние вычислителя возвращается вместе с качеством индекса;
  4. изменение настройки видеокарты сохраняется и читается обратно.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8777"
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


def request(path: str, method: str = "GET", payload: dict | None = None,
            token: str | None = None):
    url = BASE + path
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["X-RAGRAF-Token"] = token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, raw
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}"


print("=== 1. Настройки видеокарты в общих настройках ===")
code, res = request("/api/settings")
settings = (res or {}).get("settings", {}) if isinstance(res, dict) else {}
glob = settings.get("global_settings") or {}
print(f"  всего общих настроек: {len(glob)}")
for key in ("use_gpu", "gpu_min_texts", "gpu_batch_size"):
    print(f"    {key} = {glob.get(key)}")
    check(key in glob, f"{key} присутствует в общих настройках")

descriptions = settings.get("descriptions") or {}
for key in ("use_gpu", "gpu_min_texts"):
    check(key in descriptions, f"{key} имеет пояснение")
    if key in descriptions:
        print(f"    пояснение: {str(descriptions[key])[:100]}")

bounds = settings.get("bounds") or {}
print(f"  границы для порога: {bounds.get('gpu_min_texts')}")
check("gpu_min_texts" in bounds, "у порога есть границы значений")

print("\n=== 2. Настройки по разработкам ===")
per = settings.get("per_project") or {}
check(bool(per), "настройки по разработкам присутствуют")
for name, values in per.items():
    src = values.get("use_gpu__source")
    print(f"    {name}: use_gpu={values.get('use_gpu')}, "
          f"порог={values.get('gpu_min_texts')}, источник={src}")
    check(values.get("use_gpu") is not None, f"{name}: настройка видеокарты видна")

print("\n=== 3. Состояние вычислителя вместе с качеством ===")
code, state = request("/api/state")
st = (state or {}).get("state", {}) if isinstance(state, dict) else {}
detail = st.get("detail") or {}
check(bool(detail), "состояние по разработкам получено")
for name, values in detail.items():
    quality = values.get("quality") or {}
    compute = quality.get("compute") or {}
    print(f"    {name}: вычислитель {compute.get('backend')} "
          f"на {compute.get('device')}")
    print(f"      причина: {str(compute.get('reason'))[:90]}")
    check("backend" in compute, f"{name}: состояние вычислителя есть")
    check("reason" in compute, f"{name}: причина выбора названа")

print("\n=== 4. Изменение настройки через панель ===")
code, tok = request("/api/token")
token = (tok or {}).get("token", "") if isinstance(tok, dict) else ""
check(bool(token), "токен доступа получен")

before = glob.get("gpu_min_texts")
new_value = 2000 if before != 2000 else 2500
code, res = request("/api/settings/global", "POST",
                    {"values": {"gpu_min_texts": new_value}}, token=token)
check(code == 200 and isinstance(res, dict) and res.get("ok"),
      "изменение порога сохранено", f"код {code}")

request("/api/cache/clear", "POST", {"token": token}, token=token)
code, res2 = request("/api/settings")
after = ((res2 or {}).get("settings") or {}).get("global_settings") or {}
check(after.get("gpu_min_texts") == new_value,
      "значение прочитано обратно", f"получено {after.get('gpu_min_texts')}")

# Возврат исходного значения.
request("/api/settings/global", "POST",
        {"values": {"gpu_min_texts": before}}, token=token)

print("\n=== 5. Проверка границ через панель ===")
code, res = request("/api/settings/global", "POST",
                    {"values": {"gpu_min_texts": 10}}, token=token)
check(code == 400, "слишком низкий порог отклонён", f"код {code}")
print(f"    причина: {str((res or {}).get('error'))[:100]}")

code, res = request("/api/settings/global", "POST",
                    {"values": {"use_gpu": "иногда"}}, token=token)
check(code == 400, "не-логическое значение переключателя отклонено", f"код {code}")

print("\n=== ИТОГ ===")
print(f"пройдено: {PASS}, провалено: {FAIL}")
raise SystemExit(0 if FAIL == 0 else 1)
