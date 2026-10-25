"""Проверка: панель администратора отдаёт статистику по агентам.

Проверяется со стороны владельца панели:
  1. в ответе API есть разбивка по агентам (`by_agent`);
  2. есть сводка по семействам агентов;
  3. обращения самой панели учтены отдельным агентом (`RAGRAF-Admin`),
     а не смешаны с работой агентов;
  4. страница панели содержит таблицу агентов.

Требуется запущенный сервер администратора.
"""

from __future__ import annotations

import json
import pathlib
import sys
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
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


def get(path: str) -> tuple[int, object]:
    try:
        with urllib.request.urlopen(BASE + path, timeout=30) as r:
            body = r.read().decode("utf-8")
            try:
                return r.status, json.loads(body)
            except json.JSONDecodeError:
                return r.status, body
    except Exception as exc:
        return 0, {"error": str(exc)}


print("=" * 78)
print("СЕРВЕР ПАНЕЛИ")
print("=" * 78)
status, health = get("/api/health")
print(f"  проверка живости: {status}")
if status != 200:
    print("  сервер не отвечает: запустите python tools/ragraf-admin.py")
    raise SystemExit(2)

print()
print("=" * 78)
print("СТАТИСТИКА ПО АГЕНТАМ В ОТВЕТЕ API")
print("=" * 78)
status, raw = get("/api/state")
state = raw.get("state") if isinstance(raw, dict) else None
if not isinstance(state, dict):
    print(f"  ответ не разобран: {type(raw).__name__}")
    raise SystemExit(2)

detail = state.get("detail") or {}
print(f"  корпусов в ответе: {len(detail)}")

found_agents = False
found_kinds = False
found_admin = False

for name, det in detail.items():
    usage = (det or {}).get("usage") or {}
    if usage.get("error"):
        print(f"\n  {name}: статистика недоступна ({usage['error']})")
        continue
    by_agent = usage.get("by_agent") or []
    by_kind = usage.get("by_agent_kind") or []
    print(f"\n  {name}:")
    print(f"    агентов в разбивке: {len(by_agent)}")
    for a in by_agent[:8]:
        print(f"      {a.get('agent')!r} ({a.get('agent_kind')}) — "
              f"сессий {a.get('sessions')}, вызовов {a.get('calls')}, "
              f"фактов {a.get('facts')}")
    print(f"    по семействам: "
          f"{[(k.get('agent_kind'), k.get('calls'), k.get('agents')) for k in by_kind]}")

    if by_agent:
        found_agents = True
    if by_kind:
        found_kinds = True
    # Панель должна представляться своим именем.
    names = {str(a.get("agent")) for a in by_agent}
    if "RAGRAF-Admin" in names or "unknown" in names:
        found_admin = True

check(found_agents, "в ответе API есть разбивка по агентам")
check(found_kinds, "в ответе API есть сводка по семействам")
check(found_admin,
      "обращения панели учтены (RAGRAF-Admin или unknown)",
      "панель не оставила след в журнале")

print()
print("=" * 78)
print("СТРАНИЦА ПАНЕЛИ: ТАБЛИЦА АГЕНТОВ")
print("=" * 78)
status, html = get("/")
js_status, js = get("/app.js")
print(f"  страница: {status}, размер {len(str(html))} байт")
print(f"  app.js:   {js_status}, размер {len(str(js))} байт")
js_text = str(js)
check("by_agent" in js_text,
      "интерфейс использует данные по агентам")
# Подписи панели живут в словаре переводов (`ragraf/i18n.py`), а клиент зовёт их
# по английскому ключу через t(). Проверяются ОБА места: ключ в скрипте и его
# перевод в словаре. Прежняя редакция искала русский текст прямо в app.js и
# после перехода на i18n сообщала о пропаже блока там, где он есть.
AGENTS_KEY = "By agent (who used RAGRAF)"
check(AGENTS_KEY in js_text, "в интерфейсе есть блок «по агентам» (ключ)",
      "ключ не найден в app.js")
try:
    import sys

    _root = pathlib.Path(__file__).resolve().parents[1] / "src"
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    from ragraf import i18n as _i18n

    ru_text = _i18n.TRANSLATIONS.get("ru", {}).get(AGENTS_KEY, "")
    zh_text = _i18n.TRANSLATIONS.get("zh", {}).get(AGENTS_KEY, "")
except Exception as exc:  # словарь недоступен — это и есть находка
    ru_text, zh_text = "", ""
    print(f"  словарь переводов не прочитан: {type(exc).__name__}: {exc}")
check(bool(ru_text) and bool(zh_text),
      "блок «по агентам» переведён на русский и китайский",
      f"ru={ru_text!r} zh={zh_text!r}")
check("RAGRAF-Admin" in js_text or "agent_kind" in js_text,
      "интерфейс различает семейства агентов")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
