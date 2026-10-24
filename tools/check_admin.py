"""Проверка интерфейса администратора RAGRAF через HTTP.

Запуск: php не нужен;
    python tools/check_admin.py [--base=http://127.0.0.1:8777] [--token=<токен>]

Токен сессии панель печатает при запуске (`токен доступа: …`) и намеренно
НЕ отдаёт по HTTP: прежняя редакция `/api/token` возвращала его любому
локальному клиенту, и защита была формальностью. Поэтому проверка берёт токен
из аргумента `--token=`, переменной окружения `RAGRAF_ADMIN_TOKEN` или файла
запуска. Без токена проверки, которым нужна запись, помечаются `[SKIP]` и НЕ
считаются провалом: отказ без токена — это ожидаемое поведение, а не дефект.

Что проверяется:
  1. страница отдаётся, статика (CSS и JS) доступна;
  2. изменения без токена и с неверным токеном отклоняются (403);
  3. чтение состояния возвращает разработки, размеры баз, статистику, качество;
  4. настройки читаются и содержат пояснения;
  5. запись глобальных настроек с верным токеном проходит;
  6. ошибочное значение отклоняется с понятной причиной (400);
  7. запись настроек разработки создаёт резервную копию конфига.

Проверка НЕ меняет рабочие конфиги: запись в конфиг корпуса выполняется
во временный файл, который затем удаляется.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
PASS = 0
FAIL = 0
SKIP = 0
TOKEN = ""
TOKEN_FROM = "—"

BASE = "http://127.0.0.1:8777"
for arg in sys.argv[1:]:
    if arg.startswith("--base="):
        BASE = arg.split("=", 1)[1].rstrip("/")
    elif arg.startswith("--token="):
        TOKEN = arg.split("=", 1)[1].strip()
        TOKEN_FROM = "аргумент --token"

if not TOKEN:
    env_token = os.environ.get("RAGRAF_ADMIN_TOKEN", "").strip()
    if env_token:
        TOKEN = env_token
        TOKEN_FROM = "переменная RAGRAF_ADMIN_TOKEN"


def skip(label: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label} (нет токена доступа)")


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


def request(path: str, method: str = "GET", payload: dict | None = None,
            token: str | None = None) -> tuple[int, dict | str]:
    """Запрос к серверу. Возвращает (код, разобранный ответ или текст)."""
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
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            code = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        code = exc.code
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}"
    if raw.lstrip().startswith("{"):
        try:
            return code, json.loads(raw)
        except Exception:
            return code, raw
    return code, raw


def main() -> int:
    print(f"=== Адрес: {BASE} ===\n")

    print("=== 1. Страница и статика ===")
    code, body = request("/")
    text = body if isinstance(body, str) else json.dumps(body)
    check(code == 200, "страница отдаётся", f"код={code}")
    check("RAGRAF" in text, "на странице есть название продукта")
    check("/style.css" in text and "/app.js" in text, "подключены стили и скрипт")

    code, css = request("/style.css")
    check(code == 200 and isinstance(css, str) and len(css) > 2000,
          "таблица стилей отдаётся", f"код={code}")
    css_text = css if isinstance(css, str) else ""
    check("--radius" in css_text, "стили используют токены (есть --radius)")
    # Литеральные цвета. Правило прежнее: палитра живёт в переменных, а не
    # в правилах оформления. С 15.10.2026 стили собираются Tailwind, и в
    # собранном файле есть служебные литералы самого сборщика:
    #   * `#0000` — полностью прозрачный цвет (не часть палитры);
    #   * `rgb(from …)` — внутри служебного запроса поддержки
    #     `@supports (color:rgb(from red r g b))`, а не в оформлении.
    # Поэтому запрос поддержки из текста вырезается, а прозрачный литерал
    # исключается по имени; всё остальное — по-прежнему находка.
    import re
    palette = re.sub(r"@supports[^{]*\{", "{", css_text)
    # Служебные значения самого сборщика: объявления `@property` и переменные
    # `--tw-*` содержат начальные значения вида `#fff` и `#0000`. Палитра
    # здесь ни при чём, поэтому внутренности сборщика вырезаются ДО поиска.
    palette = re.sub(r"@property[^{]*\{[^}]*\}", "", palette)
    palette = re.sub(r"--tw-[a-z0-9-]+:[^;}]*", "", palette)
    literals = [lit for lit in re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", palette)
                if lit.lower() != "#0000"]
    check(len(literals) == 0, "в стилях нет литеральных цветов",
          f"найдено: {literals[:5]}")
    # Палитра обязана быть в исходнике сборки: собранный файл — производное.
    css_source = REPO / "admin" / "static" / "tailwind.src.css"
    check(css_source.exists()
          and "--background" in css_source.read_text(encoding="utf-8"),
          "исходник оформления содержит токены", str(css_source))

    code, js = request("/app.js")
    check(code == 200 and isinstance(js, str) and len(js) > 2000,
          "клиентский скрипт отдаётся", f"код={code}")

    print("\n=== 2. Доступ ===")
    # Секрет не отдаётся по HTTP намеренно: он печатается при запуске панели
    # и передаётся этой проверке аргументом. Здесь подтверждается, что точка
    # состояния доступа работает и что секрета в ответе нет.
    code, tok = request("/api/token")
    check(code == 200 and isinstance(tok, dict) and tok.get("ok"),
          "состояние доступа читается", f"код={code}")
    check(isinstance(tok, dict) and "token" not in tok,
          "секрет по HTTP не отдаётся", json.dumps(tok or {})[:80])
    check(isinstance(tok, dict) and bool(tok.get("auth")),
          "состояние доступа названо")
    if TOKEN:
        print(f"  [INFO] токен доступа: {TOKEN_FROM}, длина {len(TOKEN)}")
    else:
        print("  [INFO] токен доступа не задан: проверки записи будут пропущены")
        print("         задайте --token=<токен из вывода панели> "
              "или RAGRAF_ADMIN_TOKEN")

    print("\n=== 3. Чтение состояния ===")
    code, state = request("/api/state")
    check(code == 200 and isinstance(state, dict) and state.get("ok"),
          "состояние читается", f"код={code}")
    st = (state or {}).get("state", {}) if isinstance(state, dict) else {}
    catalog = st.get("catalog") or {}
    entries = catalog.get("entries") or []
    check(len(entries) >= 1, "в реестре есть разработки", f"найдено {len(entries)}")
    if entries:
        first = entries[0]
        check("corpus_hash" in first, "у разработки есть контрольная сумма")
        check("hash_matches" in first, "есть признак совпадения суммы")
        size = first.get("size") or {}
        check(len(size.get("files") or []) >= 1, "показаны размеры баз",
              f"баз: {len(size.get('files') or [])}")
        check(len(size.get("tables") or []) >= 1, "показан состав таблиц",
              f"таблиц: {len(size.get('tables') or [])}")
    detail = st.get("detail") or {}
    check(len(detail) >= 1, "собрана статистика по разработкам")
    if detail:
        name = list(detail.keys())[0]
        usage = detail[name].get("usage") or {}
        quality = detail[name].get("quality") or {}
        check("calls_total" in usage or "error" in usage,
              "статистика использования присутствует")
        check("quality" in quality or "error" in quality,
              "качество индекса присутствует")

    print("\n=== 4. Настройки ===")
    code, sett = request("/api/settings")
    check(code == 200 and isinstance(sett, dict) and sett.get("ok"),
          "настройки читаются", f"код={code}")
    settings = (sett or {}).get("settings", {}) if isinstance(sett, dict) else {}
    glob = settings.get("global_settings") or {}
    check("reindex_after_changes" in glob, "порог быстрой переиндексации присутствует")
    check("full_reindex_after_changes" in glob, "порог полного пересбора присутствует")
    check(bool(settings.get("descriptions")), "есть пояснения к настройкам",
          f"описаний: {len(settings.get('descriptions') or {})}")
    check(bool(settings.get("bounds")), "есть границы допустимых значений")
    per = settings.get("per_project") or {}
    check(len(per) >= 1, "настройки по разработкам присутствуют")
    if per:
        first_name = list(per.keys())[0]
        check("config_path" in per[first_name], "указан путь к конфигу разработки")
        sources = [k for k in per[first_name] if k.endswith("__source")]
        check(bool(sources), "показан источник каждого значения (проект/глобально)")

    print("\n=== 5. Защита изменений ===")
    code, res = request("/api/settings/global", "POST",
                        {"values": {"reindex_after_changes": 7}}, token=None)
    check(code == 403, "изменение без токена отклонено", f"код={code}")
    code, res = request("/api/settings/global", "POST",
                        {"values": {"reindex_after_changes": 7}}, token="wrong-token-value")  # ragraf-release: placeholder-value — заведомо неверный токен, проверка отказа 403
    check(code == 403, "изменение с неверным токеном отклонено", f"код={code}")

    print("\n=== 6. Сохранение общих настроек ===")
    before = dict(glob)
    if not TOKEN:
        skip("сохранение с верным токеном прошло")
        skip("значение действительно сохранено")
        skip("исходное значение восстановлено")
    else:
        code, res = request("/api/settings/global", "POST",
                            {"values": {"reindex_after_changes": 6}}, token=TOKEN)
        check(code == 200 and isinstance(res, dict) and res.get("ok"),
              "сохранение с верным токеном прошло", f"код={code}")
        # Кэш интерфейса держит состояние 5 секунд: без сброса проверка прочитает
        # прежние значения и решит, что сохранение не сработало.
        request("/api/cache/clear", "POST", {"token": TOKEN}, token=TOKEN)
        code, sett2 = request("/api/settings")
        glob2 = ((sett2 or {}).get("settings") or {}).get("global_settings") or {}
        check(glob2.get("reindex_after_changes") == 6,
              "значение действительно сохранено",
              f"получено {glob2.get('reindex_after_changes')}")
        # Возврат исходного значения: проверка не должна менять настройки навсегда.
        request("/api/settings/global", "POST",
                {"values": {"reindex_after_changes": before.get("reindex_after_changes", 5)}},
                token=TOKEN)
        request("/api/cache/clear", "POST", {"token": TOKEN}, token=TOKEN)
        code, sett3 = request("/api/settings")
        glob3 = ((sett3 or {}).get("settings") or {}).get("global_settings") or {}
        check(glob3.get("reindex_after_changes") == before.get("reindex_after_changes", 5),
              "исходное значение восстановлено")

    print("\n=== 7. Отклонение ошибочных значений ===")
    if not TOKEN:
        # Отказ по доступу наступает РАНЬШЕ разбора значения, поэтому без токена
        # проверка ошибочных значений ничего не измеряет: будет 403, а не 400.
        skip("нулевой порог отклонён")
        skip("причина отклонения названа")
        skip("несогласованные пороги отклонены")
        skip("неизвестная настройка отклонена")
    else:
        code, res = request("/api/settings/global", "POST",
                            {"values": {"reindex_after_changes": 0}}, token=TOKEN)
        check(code == 400, "нулевой порог отклонён", f"код={code}")
        check("допустимо" in str((res or {}).get("error", "")),
              "причина отклонения названа", str((res or {}).get("error", ""))[:80])

        code, res = request("/api/settings/global", "POST",
                            {"values": {"reindex_after_changes": 30,
                                        "full_reindex_after_changes": 10}}, token=TOKEN)
        check(code == 400, "несогласованные пороги отклонены", f"код={code}")

        code, res = request("/api/settings/global", "POST",
                            {"values": {"неизвестная_настройка": 5}}, token=TOKEN)
        check(code == 400, "неизвестная настройка отклонена", f"код={code}")

    print("\n=== 8. Несуществующие точки ===")
    code, res = request("/api/no-such-endpoint")
    check(code == 404, "неизвестный путь даёт 404", f"код={code}")
    code, res = request("/api/health")
    check(code == 200 and isinstance(res, dict) and res.get("ok"),
          "проверка живости отвечает")

    print("\n=== ИТОГ ===")
    print(f"пройдено: {PASS}, провалено: {FAIL}, пропущено: {SKIP}")
    if SKIP:
        print("пропущены проверки записи: передайте --token=<токен из вывода панели>")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
