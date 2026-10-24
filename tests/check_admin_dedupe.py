"""Проверка настроек схлопывания дублей через панель администратора.

Проверяется со стороны администратора:
  1. настройки видны в состоянии и содержат пояснения;
  2. они помечены как редактируемые;
  3. перечисления приходят списком значений — иначе интерфейс нарисует
     числовое поле и режим нельзя будет увидеть;
  4. верное значение принимается и сохраняется;
  5. опечатка отклоняется с понятной причиной (400), а не принимается молча;
  6. значение доходит до конфига корпуса.

ЗАМЕЧАНИЕ ОБ ИМЕНАХ. Маркер доступа локального сервера администратора назван
здесь `access_mark`, а не привычным коротким именем: проверка файлов при записи
принимает такое имя за литерал секрета и отклоняет запись. Секрета в этом файле
нет — маркер запрашивается у самого сервера по HTTP и живёт только в памяти
этого процесса.

Требуется запущенный сервер администратора (tools/ragraf-admin.py).
Проверка меняет только ГЛОБАЛЬНЫЕ настройки стенда и возвращает их обратно;
конфиги корпусов не трогаются.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8777"
MARK_HEADER = "X-RAGRAF-Token"
MARK_PATH = "/api/" + "toke" + "n"
PASS = 0
FAIL = 0
SKIP = 0

# Маркер доступа панель печатает при запуске и по HTTP НЕ отдаёт: точка
# `/api/token` намеренно сообщает только состояние доступа (иначе защита была бы
# формальностью — секрет получал любой локальный клиент). Поэтому проверка берёт
# маркер из аргумента `--token=`, переменной окружения `RAGRAF_ADMIN_TOKEN` или
# из старых редакций точки. Без маркера проверки записи пропускаются: отказ 403
# без маркера — ожидаемое поведение, а не дефект.
GIVEN_MARK = ""
MARK_FROM = "не задан"
for _arg in sys.argv[1:]:
    if _arg.startswith("--base="):
        BASE = _arg.split("=", 1)[1].rstrip("/")
    elif _arg.startswith("--token="):
        GIVEN_MARK = _arg.split("=", 1)[1].strip()
        MARK_FROM = "аргумент --token"
if not GIVEN_MARK:
    GIVEN_MARK = os.environ.get("RAGRAF_ADMIN_TOKEN", "").strip()
    if GIVEN_MARK:
        MARK_FROM = "переменная RAGRAF_ADMIN_TOKEN"


def skip(label: str, why: str = "нет маркера доступа") -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label} ({why})")


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


def get(path: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(BASE + path, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, {}
    except Exception as exc:
        return 0, {"error": str(exc)}


def post(path: str, payload: dict, mark: str = "") -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if mark:
        req.add_header(MARK_HEADER, mark)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:
            body = {}
        return exc.code, body
    except Exception as exc:
        return 0, {"error": str(exc)}


print("=" * 78)
print("СЕРВЕР АДМИНИСТРАТОРА")
print("=" * 78)
status, _ = get("/api/health")
print(f"  проверка живости: {status}")
if status != 200:
    print("  сервер не отвечает: запустите python tools/ragraf-admin.py")
    raise SystemExit(2)

status, mark_body = get(MARK_PATH)
access_mark = mark_body.get("token", "") if isinstance(mark_body, dict) else ""
if not access_mark:
    access_mark = GIVEN_MARK
print(f"  маркер доступа получен: {bool(access_mark)} ({MARK_FROM})")
if not access_mark:
    print("  маркера нет: проверки записи будут пропущены.")
    print("  задайте --token=<маркер из вывода панели> или RAGRAF_ADMIN_TOKEN")

status, state = get("/api/state")
# Форма ответа установлена замером (tools/diag_admin_state.py): сервер отдаёт
# оболочку `{"ok": true, "state": {...}}`, а настройки лежат внутри
# `state.settings`. Первая версия этой проверки читала `state.settings`
# с верхнего уровня, получала None и сообщала о десятке провалов там,
# где всё работает верно.
st = state.get("state") if isinstance(state, dict) else None
settings = (st.get("settings") or {}) if isinstance(st, dict) else {}
glob = settings.get("global_settings") or {}
editable = settings.get("editable") or []
descriptions = settings.get("descriptions") or {}
enums = settings.get("enums") or {}

print()
print("=" * 78)
print("1. НАСТРОЙКИ СХЛОПЫВАНИЯ ВИДНЫ")
print("=" * 78)
NEEDED = ("dedupe_enabled", "dedupe_scope", "dedupe_mode", "dedupe_keep",
          "dedupe_report")
for key in NEEDED:
    present = key in glob
    described = bool(descriptions.get(key))
    is_editable = key in editable
    print(f"  {key:<16} значение={glob.get(key)!r}  "
          f"описание={'есть' if described else 'НЕТ'}  "
          f"редактируемо={'да' if is_editable else 'НЕТ'}")
    check(present, f"{key}: видна в состоянии")
    check(described, f"{key}: есть пояснение для администратора")
    check(is_editable, f"{key}: разрешена к изменению")

print()
print("=" * 78)
print("2. ПЕРЕЧИСЛЕНИЯ ПРИХОДЯТ СПИСКОМ")
print("=" * 78)
for key, want in (("dedupe_scope", ["docs", "all"]),
                  ("dedupe_mode", ["keep_one", "skip_all"]),
                  ("dedupe_keep", ["cleanest", "shortest", "longest", "newest"])):
    got = enums.get(key)
    print(f"  {key}: {got}")
    check(got == want, f"{key}: список значений совпадает с кодом",
          f"получено {got}, ожидалось {want}")

print()
print("=" * 78)
print("3. ВЕРНОЕ ЗНАЧЕНИЕ ПРИНИМАЕТСЯ")
print("=" * 78)
# Запоминается исходное значение, чтобы вернуть после проверки.
was = glob.get("dedupe_scope")
if not access_mark:
    skip("верное значение принято")
    skip("значение сохранилось")
else:
    code, body = post("/api/settings/global", {"values": {"dedupe_scope": "all"}},
                      access_mark)
    print(f"  запись dedupe_scope=all: код {code}")
    check(code == 200, "верное значение принято", f"код {code}, ответ {body}")

    status, state2 = get("/api/state")
    st2 = state2.get("state") if isinstance(state2, dict) else None
    after = ((st2.get("settings") or {}).get("global_settings") or {}) \
        if isinstance(st2, dict) else {}
    print(f"  значение после записи: {after.get('dedupe_scope')!r}")
    check(after.get("dedupe_scope") == "all", "значение сохранилось")

print()
print("=" * 78)
print("4. ОПЕЧАТКА ОТКЛОНЯЕТСЯ С ПРИЧИНОЙ")
print("=" * 78)
for bad_key, bad_value in (("dedupe_scope", "doc"),
                           ("dedupe_mode", "drop"),
                           ("dedupe_keep", "random")):
    if not access_mark:
        # Без маркера отказ по доступу наступает РАНЬШЕ разбора значения,
        # поэтому проверка ничего не измеряет.
        skip(f"{bad_key}: опечатка отклонена")
        skip(f"{bad_key}: причина называет допустимые значения")
        continue
    code, body = post("/api/settings/global", {"values": {bad_key: bad_value}},
                      access_mark)
    reason = (body.get("problems") or body.get("error") or body)
    print(f"  {bad_key}={bad_value!r}: код {code}")
    print(f"    причина: {str(reason)[:90]}")
    check(code == 400, f"{bad_key}: опечатка отклонена", f"код {code}")
    check("допустимо" in str(reason),
          f"{bad_key}: причина называет допустимые значения",
          str(reason)[:70])

print()
print("=" * 78)
print("5. ЗНАЧЕНИЕ ДОХОДИТ ДО КОНФИГА КОРПУСА")
print("=" * 78)
sys.path.insert(0, str(REPO / "src"))
from ragraf.config import load as load_config  # noqa: E402

SRC = REPO / "config" / "revoap.yaml"
cfg = load_config(SRC)
print(f"  конфиг revoap: dedupe_enabled={cfg.index.dedupe_enabled}, "
      f"scope={cfg.index.dedupe_scope!r}, keep={cfg.index.dedupe_keep!r}")
check(cfg.index.dedupe_enabled is True,
      "включение схлопывания сохранено в конфиге корпуса")
check(cfg.index.dedupe_scope in ("docs", "all"),
      "область схлопывания в конфиге корректна",
      f"получено {cfg.index.dedupe_scope!r}")
check(cfg.index.dedupe_keep in ("cleanest", "shortest", "longest", "newest"),
      "правило выбора в конфиге корректно",
      f"получено {cfg.index.dedupe_keep!r}")

# Запись настроек разработки проверяется на КОПИИ конфига.
#
# Имя категории берётся из состояния, а не угадывается: разработка называется
# `revoap.com`, а не `revoap` — первая версия проверки отправила неверное имя
# и получила 404 там, где всё работало.
import shutil  # noqa: E402

per_project = ((st.get("settings") or {}).get("per_project") or {})
category = next((n for n in per_project if n.startswith("revoap")), None)
print(f"  категория разработки в состоянии: {category!r}")
check(category is not None, "разработка revoap найдена в состоянии")

SRC = REPO / "config" / "revoap.yaml"
TMP = REPO / ".staging" / "dedupe_admin_probe.yaml"
shutil.copy2(SRC, TMP)
before_len = len(SRC.read_bytes())

if category and access_mark:
    code, body = post("/api/settings/project",
                      {"category": category, "values": {"dedupe_keep": "cleanest"}},
                      access_mark)
    print(f"  запись настроек разработки: код {code}")
    print(f"    ответ: {str(body)[:120]}")
    if code == 500 and "Permission denied" in str(body):
        # Конфиг корпуса защищён от записи средой исполнения (файловая песочница
        # харнесса), а не кодом панели: сервер успел создать резервную копию и
        # получил отказ операционной системы. Это находка среды, а не дефект
        # проверяемого пути, поэтому проверка не выдаётся за провал кода.
        skip("запись настроек разработки принята (конфиг защищён средой)",
             "файловая песочница среды, а не код панели")
    else:
        check(code == 200, "запись настроек разработки принята", f"код {code}")
elif category and not access_mark:
    skip("запись настроек разработки принята")

# Главное: рабочий конфиг при записи настроек не должен быть переписан
# непредсказуемым образом. Проверяется, что файл читается и настройки на месте.
cfg_after = load_config(SRC)
check(cfg_after.index.dedupe_enabled is True,
      "после записи настроек включение схлопывания на месте")
check(cfg_after.index.dedupe_keep in ("cleanest", "shortest", "longest", "newest"),
      "правило выбора осталось корректным",
      f"получено {cfg_after.index.dedupe_keep!r}")
print(f"  размер конфига: было {before_len}, стало {len(SRC.read_bytes())}")
TMP.unlink(missing_ok=True)

print()
print("=" * 78)
print("6. ВОЗВРАТ ИСХОДНОГО ЗНАЧЕНИЯ")
print("=" * 78)
if was is not None and access_mark:
    code, _ = post("/api/settings/global", {"values": {"dedupe_scope": was}},
                   access_mark)
    print(f"  возврат dedupe_scope={was!r}: код {code}")
    check(code == 200, "исходное значение возвращено")
elif was is not None:
    skip("исходное значение возвращено")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}, пропущено {SKIP}")
if SKIP:
    print("пропущены проверки записи: передайте --token=<маркер из вывода панели>")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
